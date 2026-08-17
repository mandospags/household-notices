from dataclasses import dataclass
from datetime import date as Date


@dataclass
class Notice:
    source: str
    title: str
    date: Date | None
    detail: str | None = None
