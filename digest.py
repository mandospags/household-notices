from datetime import datetime

from dotenv import load_dotenv

from render import render_digest
from services import air_quality, bins, trains, weather
from services.base import Notice

SERVICES = [bins, air_quality, weather, trains]


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

    render_digest(now, today_notices, upcoming_notices)


if __name__ == "__main__":
    main()
