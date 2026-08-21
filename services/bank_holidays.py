"""UK bank holidays - keyless JSON from `https://www.gov.uk/bank-holidays.json`
(one bare array per division: england-and-wales, scotland,
northern-ireland - `BANK_HOLIDAYS_DIVISION` picks which).

Digest-only, no alert_status - a bank holiday is never sudden.

Cached like mass.py (BANK_HOLIDAYS_CACHE_FILE, ~1 day TTL, stale-served-on-
failure): trains.py checks is_bank_holiday() on every alerts.py poll (every
10 min, daytime), and this feed only changes a couple of times a year, so a
live fetch each time would just be needless load on gov.uk.

fetch() only emits holidays within BANK_HOLIDAYS_HORIZON_DAYS - the feed
carries a year-plus of future dates and digest.py's "upcoming" bucket is
unbounded, so without a cap every digest would list every holiday to next
Christmas.

is_bank_holiday() fails open (returns False) on a fetch error with no cache
to fall back on - it's a nicety trains.py consults, not a required source;
worst case is a train board shown on a holiday, not the trains watch dying
over an unrelated feed being briefly down. fetch() itself still raises on
failure, per the usual contract.
"""

import json
import os
from datetime import date as Date
from datetime import datetime, timedelta
from pathlib import Path

import requests

from .base import Notice

SOURCE = "bank_holidays"

FEED_URL = "https://www.gov.uk/bank-holidays.json"
HORIZON_DAYS = 14
CACHE_MAX_AGE = timedelta(days=1)


def _fetch_events() -> list[dict]:
    resp = requests.get(FEED_URL, timeout=15)
    resp.raise_for_status()
    division = os.environ["BANK_HOLIDAYS_DIVISION"]
    return resp.json()[division]["events"]


def _cache_path() -> Path:
    return Path(os.environ["BANK_HOLIDAYS_CACHE_FILE"])


def _cached_events() -> list[dict] | None:
    path = _cache_path()
    if not path.exists():
        return None
    cached = json.loads(path.read_text())
    fetched_at = datetime.fromisoformat(cached["fetched_at"])
    if datetime.now() - fetched_at > CACHE_MAX_AGE:
        return None
    return cached["events"]


def _events(now: datetime) -> list[dict]:
    cached = _cached_events()
    if cached is not None:
        return cached
    try:
        events = _fetch_events()
    except Exception:
        stale = _cache_path()
        if stale.exists():
            return json.loads(stale.read_text())["events"]
        raise
    _cache_path().write_text(json.dumps({"fetched_at": now.isoformat(), "events": events}))
    return events


def is_bank_holiday(d: Date) -> bool:
    try:
        events = _events(datetime.now())
    except Exception:
        return False
    return any(e["date"] == d.isoformat() for e in events)


def fetch(now: datetime) -> list[Notice]:
    today = now.date()
    horizon = today + timedelta(days=HORIZON_DAYS)
    notices = []
    for event in _events(now):
        event_date = Date.fromisoformat(event["date"])
        if today <= event_date <= horizon:
            notices.append(Notice(source=SOURCE, title=event["title"], date=event_date))
    return notices
