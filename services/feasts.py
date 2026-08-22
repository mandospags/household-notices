"""1962 Roman calendar feast days, alongside mass.py's weekly bulletin -
Missale Meum's public API (missalemeum.com), not Divinum Officium directly.
Discussed and ruled out before building this: Divinum Officium's own
data/precedence engine (what actually resolves a date to a feast) is spread
across several Perl modules plus per-day office directories, backed by a
~330MB data submodule - not a "pull one or two files" job. Missale Meum's
own repo pulls that same submodule in as its own dependency, so vendoring
it doesn't shrink the problem either. A plain API call sidesteps both.

Keyless, public API, no auth. `GET /en/api/v5/calendar/range?from=&until=`
returns one entry per day (`id` the ISO date, `title`, `rank` 1-4,
`colors`, `tags`, `commemorations`, `displaced` - only `id`/`title`/`rank`
are used here). Both `from` and `until` are inclusive, but the endpoint
400s if `from` is not strictly earlier than `until` - so even a single-day
window needs `until` set past `from`; fetched a WINDOW_DAYS-wide window
from today rather than one day at a time, both to save calls and because
"upcoming" needs more than one day of data anyway.

Rank is the 1962 calendar's traditional class (1=highest ... 4=lowest,
ferias/simple commemorations). Every Sunday is at least rank 2 (Class I in
Advent/Lent/Passiontide/Eastertide, Class II otherwise per annum), so
filtering to rank <= MAX_RANK naturally surfaces "the Sunday" alongside
genuinely major feasts without also naming every daily minor saint (rank
3/4) - that filtering-out-the-noise was the actual ask this was built for,
not a "days of obligation" flag (obligation is a separate, smaller,
bishops'-conference-defined list that doesn't map cleanly onto rank alone).

Digest-only - no alert_status (a feast can't turn "notable" mid-day the way
a delayed train can) and no ACTIVE_HOURS (the calendar doesn't care what
local hour it is).

Notices from this module never render as their own digest line - they
exist to be folded into mass.py's same-date line. digest.py's
_merge_feasts() appends a feast's title into the matching mass.py Notice's
`detail` (shown in brackets) and drops the feast Notice; a feast with no
matching mass Notice for that date is dropped silently rather than shown
standalone (rare - Burghclere's bulletin covers every day in the window
feasts.py fetches). This module still returns plain, independent Notices
from fetch() and knows nothing about mass.py itself - the merge lives in
digest.py precisely so this module doesn't have to.

Cached like mass.py (FEASTS_CACHE_FILE, ~1hr TTL, stale-served-on-fetch-
failure, raises only with no cache at all) - mostly to be a good citizen of
a small personal API rather than because of any observed rate limit.

Caveat, not solvable in code: this is the general Roman calendar. FSSPX's
own Burghclere bulletin (mass.py) may reflect a local/patronal feast this
API has no way to know about - the two sources are fetched independently
and can legitimately disagree on a given day.
"""

import json
import os
from datetime import date, datetime, timedelta

import requests

from .base import Notice

SOURCE = "feasts"

API_URL = "https://www.missalemeum.com/en/api/v5/calendar/range"
WINDOW_DAYS = 6  # today + 6 more days = one full week, i.e. one Sunday
MAX_RANK = 2  # Sundays and major feasts; 3/4 is daily-saint noise
CACHE_TTL = timedelta(hours=1)


def _cache_path() -> str:
    return os.environ.get("FEASTS_CACHE_FILE", "feasts_cache.json")


def _read_cache(path: str) -> tuple[datetime, list[dict]] | None:
    try:
        with open(path) as f:
            raw = json.load(f)
        return datetime.fromisoformat(raw["fetched_at"]), raw["days"]
    except (FileNotFoundError, json.JSONDecodeError, KeyError, ValueError):
        return None


def _write_cache(path: str, now: datetime, days: list[dict]) -> None:
    with open(path, "w") as f:
        json.dump({"fetched_at": now.isoformat(), "days": days}, f)


def _fetch_live(now: datetime) -> list[dict]:
    today = now.date()
    until = today + timedelta(days=WINDOW_DAYS)
    resp = requests.get(
        API_URL,
        params={"from": today.isoformat(), "until": until.isoformat()},
        timeout=15,
    )
    resp.raise_for_status()
    return [
        {"date": item["id"], "title": item["title"], "rank": item["rank"]}
        for item in resp.json()
    ]


def _days(now: datetime) -> list[dict]:
    cache_path = _cache_path()
    cached = _read_cache(cache_path)
    if cached is not None:
        fetched_at, days = cached
        if now - fetched_at < CACHE_TTL:
            return days

    try:
        days = _fetch_live(now)
    except requests.exceptions.RequestException:
        if cached is not None:
            return cached[1]
        raise

    _write_cache(cache_path, now, days)
    return days


def fetch(now: datetime) -> list[Notice]:
    today = now.date()
    return [
        Notice(source=SOURCE, title=day["title"], date=date.fromisoformat(day["date"]))
        for day in _days(now)
        if day["rank"] <= MAX_RANK and date.fromisoformat(day["date"]) >= today
    ]
