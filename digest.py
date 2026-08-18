from datetime import datetime

from dotenv import load_dotenv

from render import render_digest
from services import air_quality, bins, traffic, trains, weather
from services.base import Notice

SERVICES = [bins, air_quality, weather, trains, traffic]
ALERT_SERVICES = [trains, weather, traffic]


def _is_notable(status: str) -> bool:
    return status != "clear" and not status.startswith("on time")


def _alert_lines(now: datetime) -> list[str]:
    lines = []
    for service in ALERT_SERVICES:
        try:
            statuses = service.alert_status(now)
        except Exception as exc:
            print(f"[{service.SOURCE}] failed: {exc}")
            continue
        lines.extend(
            entry["summary"] for entry in statuses.values() if _is_notable(entry["status"])
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

    today_notices = sorted(
        (n for n in all_notices if _section(n, today) == "today"), key=lambda n: n.source
    )
    upcoming_notices = sorted(
        (n for n in all_notices if _section(n, today) == "upcoming"),
        key=lambda n: (n.date, n.source),
    )

    alert_lines = _alert_lines(now)

    render_digest(now, alert_lines, today_notices, upcoming_notices)


if __name__ == "__main__":
    main()
