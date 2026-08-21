from datetime import datetime

from services.base import Notice

# source -> emoji, for sources where one icon covers every Notice they
# produce. bins.py is deliberately not here - its four collection types
# share SOURCE="bins", so it embeds its own per-type emoji directly in
# each Notice's title instead (see services/bins.py's BIN_EMOJI).
_SOURCE_EMOJI = {
    "air_quality": "🌫️",
    "mass": "✝️",
    "trains": "🚆",
    "traffic": "🚗",
    "bank_holidays": "🎉",
}


def _emoji(notice: Notice) -> str:
    if notice.emoji:
        return notice.emoji
    # traffic.py's incident lines share SOURCE="traffic" with its travel-time
    # lines but read " incident:" in the title (see _incident_line) - the one
    # source with two icons, split on that rather than a second source name.
    if notice.source == "traffic" and " incident:" in notice.title:
        return "🚧"
    return _SOURCE_EMOJI.get(notice.source, "")


def _line(notice: Notice, show_date: bool) -> str:
    emoji = _emoji(notice)
    prefix = f"{emoji} " if emoji else ""
    date_prefix = f"{notice.date:%a %d %b}: " if show_date else ""
    suffix = f" ({notice.detail})" if notice.detail else ""
    return f"{prefix}{date_prefix}{notice.title}{suffix}"


def render_digest(
    now: datetime,
    alert_lines: list[str],
    today_notices: list[Notice],
    upcoming_notices: list[Notice],
) -> str:
    lines = [f"Household digest - {now:%A %d %B %Y, %H:%M}", ""]

    # No "no active alerts" filler line - an empty Alerts section (the
    # common case) is just omitted entirely rather than printed as
    # reassuring noise every run.
    if alert_lines:
        lines += ["Alerts", "------"]
        lines += alert_lines
        lines.append("")

    today_heading = f"Today ({now:%A})"
    lines += [today_heading, "-" * len(today_heading)]
    if today_notices:
        lines += [_line(n, show_date=False) for n in today_notices]
    else:
        lines.append("Nothing due today.")
    lines.append("")

    lines += ["Upcoming", "--------"]
    if upcoming_notices:
        lines += [_line(n, show_date=True) for n in upcoming_notices]
    else:
        lines.append("Nothing upcoming.")

    return "\n".join(lines)
