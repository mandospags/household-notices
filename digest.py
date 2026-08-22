"""Daily-digest entry point: fetch every source, bucket what comes back into
today/upcoming, render, print, and send to Telegram.

The "Alerts" block at the top is a second, independent read of the
alert-capable services' alert_status() alongside their fetch() - it costs
some extra API calls, is stateless (non-nominal statuses only, "clear"/"on
time" filtered out), and neither shares state with nor affects alerts.py's
diff cadence. The two entry points are deliberately separate; don't merge
them into one pipeline.

_merge_feasts() is the one piece of cross-source work here: feasts.py's
notices are folded into mass.py's same-date line rather than rendered
standalone, so neither service has to know about the other.
"""

from datetime import datetime

from dotenv import load_dotenv

import telegram
from render import render_digest
from services import (
    air_quality,
    bank_holidays,
    bins,
    feasts,
    forecast,
    mass,
    powercuts,
    traffic,
    trains,
    weather,
)
from services.base import Notice, is_notable

SERVICES = [
    bins,
    air_quality,
    weather,
    trains,
    traffic,
    mass,
    feasts,
    bank_holidays,
    forecast,
]
ALERT_SERVICES = [trains, weather, traffic, powercuts]


def _alert_lines(now: datetime) -> list[str]:
    lines = []
    for service in ALERT_SERVICES:
        try:
            statuses = service.alert_status(now)
        except Exception as exc:
            print(f"[{service.SOURCE}] failed: {exc}")
            continue
        lines.extend(
            entry["summary"] for entry in statuses.values() if is_notable(entry["status"])
        )
    return lines


def _section(notice: Notice, today) -> str | None:
    if notice.section is not None:
        return notice.section
    if notice.date == today:
        return "today"
    if notice.date is not None and notice.date > today:
        return "upcoming"
    return None


def _merge_feasts(notices: list[Notice]) -> list[Notice]:
    """feasts.py notices never render on their own line - a same-date mass.py
    notice gets the feast title appended as its `detail` (renders in
    brackets); a feast with no matching mass notice for that date is dropped
    silently rather than shown standalone. Sets `detail` outright rather than
    appending - no mass notice sets one of its own today."""
    feast_titles = {n.date: n.title for n in notices if n.source == feasts.SOURCE}
    merged = []
    for notice in notices:
        if notice.source == feasts.SOURCE:
            continue
        if notice.source == mass.SOURCE and notice.date in feast_titles:
            notice.detail = feast_titles[notice.date]
        merged.append(notice)
    return merged


def main() -> None:
    load_dotenv()

    now = datetime.now()
    today = now.date()

    all_notices: list[Notice] = []
    for service in SERVICES:
        try:
            all_notices.extend(service.fetch(now))
        except Exception as exc:
            print(f"[{service.SOURCE}] failed: {exc}")

    all_notices = _merge_feasts(all_notices)

    today_notices = sorted(
        (n for n in all_notices if _section(n, today) == "today"), key=lambda n: n.source
    )
    upcoming_notices = sorted(
        (n for n in all_notices if _section(n, today) == "upcoming"),
        key=lambda n: (n.date, n.source),
    )

    alert_lines = _alert_lines(now)

    text = render_digest(now, alert_lines, today_notices, upcoming_notices)
    print(text)

    try:
        telegram.send(text)
    except Exception as exc:
        print(f"[telegram] failed: {exc}")


if __name__ == "__main__":
    main()
