from dataclasses import dataclass
from datetime import date as Date


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
