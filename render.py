from datetime import datetime

from services.base import Notice


def _line(notice: Notice, show_date: bool) -> str:
    prefix = f"{notice.date:%a %d %b}: " if show_date else ""
    suffix = f" ({notice.detail})" if notice.detail else ""
    return f"- {prefix}{notice.title}{suffix}"


def render_digest(
    now: datetime, today_notices: list[Notice], upcoming_notices: list[Notice]
) -> None:
    print(f"Household digest - {now:%A %d %B %Y}")
    print()

    today_heading = f"Today ({now:%A %d %B %Y}, {now:%H:%M})"
    print(today_heading)
    print("-" * len(today_heading))
    if today_notices:
        for n in today_notices:
            print(_line(n, show_date=False))
    else:
        print("- Nothing due today.")
    print()

    print("Upcoming")
    print("--------")
    if upcoming_notices:
        for n in upcoming_notices:
            print(_line(n, show_date=True))
    else:
        print("- Nothing upcoming.")
