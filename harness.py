from datetime import datetime, timedelta

from dotenv import load_dotenv

from render import render_digest
from services import air_quality, bins
from services.base import Notice

SERVICES = [bins, air_quality]


def main() -> None:
    load_dotenv()

    now = datetime.now()
    today = now.date()
    window = {today, today + timedelta(days=1), today + timedelta(days=2)}

    all_notices: list[Notice] = []
    for service in SERVICES:
        try:
            all_notices.extend(service.fetch())
        except Exception as exc:
            print(f"[{service.SOURCE}] failed: {exc}")

    today_notices = sorted(
        (n for n in all_notices if n.date == today), key=lambda n: n.source
    )
    upcoming_notices = sorted(
        (n for n in all_notices if n.date in window and n.date != today),
        key=lambda n: (n.date, n.source),
    )

    render_digest(now, today_notices, upcoming_notices)


if __name__ == "__main__":
    main()
