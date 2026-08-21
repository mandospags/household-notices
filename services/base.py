from dataclasses import dataclass
from datetime import date as Date


def is_notable(status: str) -> bool:
    """Whether an alert_status() status is worth surfacing - "clear" and
    "on time..." (which may carry a platform suffix) are nominal, everything
    else (delayed, cancelled, not in timetable, ...) is notable. Shared by
    digest.py's Alerts block and alerts.py's new-key suppression so both
    apply the same nominal/notable line."""
    return status != "clear" and not status.startswith("on time")


@dataclass
class Notice:
    source: str
    title: str
    date: Date | None
    detail: str | None = None
    # Overrides the default date-based bucketing ("today" if date == today,
    # "upcoming" if date is later). Only trains uses this - its preview rows
    # are date-stamped for display but belong in a specific section
    # regardless of what that date compares to.
    section: str | None = None
    # Per-notice emoji override, for a source whose Notices don't all share
    # one icon (only bins.py needs this - its four collection types share
    # SOURCE="bins", so render.py's source->emoji lookup can't tell them
    # apart; bins.py sets this directly instead). Everything else leaves it
    # unset and gets its icon from render.py's source-keyed lookup.
    emoji: str | None = None
