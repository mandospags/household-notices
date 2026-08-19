from datetime import datetime

from services.base import Notice


def _line(notice: Notice, show_date: bool) -> str:
    prefix = f"{notice.date:%a %d %b}: " if show_date else ""
    suffix = f" ({notice.detail})" if notice.detail else ""
    return f"- {prefix}{notice.title}{suffix}"


def render_digest(
    now: datetime,
    alert_lines: list[str],
    today_notices: list[Notice],
    upcoming_notices: list[Notice],
) -> str:
    lines = [f"Household digest - {now:%A %d %B %Y, %H:%M}", ""]

    lines += ["Alerts", "------"]
    if alert_lines:
        lines += [f"- {line}" for line in alert_lines]
    else:
        lines.append("- No active alerts.")
    lines.append("")

    today_heading = f"Today ({now:%A})"
    lines += [today_heading, "-" * len(today_heading)]
    if today_notices:
        lines += [_line(n, show_date=False) for n in today_notices]
    else:
        lines.append("- Nothing due today.")
    lines.append("")

    lines += ["Upcoming", "--------"]
    if upcoming_notices:
        lines += [_line(n, show_date=True) for n in upcoming_notices]
    else:
        lines.append("- Nothing upcoming.")

    return "\n".join(lines)
