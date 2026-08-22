"""The one JSON-file cache the polled sources share (mass, forecast, feasts,
bank_holidays all wrap their live fetch in `cached()`).

Mechanics only - the cache file stores whatever JSON-able value the caller
hands back, and each service keeps its own conversion to/from that shape
(mass stores notice dicts, forecast a date-keyed dict, feasts and
bank_holidays plain lists). Deliberately not a base class or a registry:
services stay independent modules, this is just the copy-pasted TTL/stale
logic pulled into one place.

Behaviour, same as the four hand-written versions it replaces: a cache
younger than `ttl` short-circuits the fetch entirely; otherwise the fetch
runs, and if it fails with a RequestException a still-present (if stale)
cache is served rather than failing the whole digest. It only raises when
there's no cache at all to fall back on. A malformed/truncated cache file
reads as "no cache" rather than blowing up.

Deliberately catches only RequestException, not bare Exception - a parse
error is a real bug worth surfacing, not something to paper over by serving
last hour's data forever.
"""

import json
import os
from datetime import datetime, timedelta
from typing import Any, Callable

import requests


def _read(path: str) -> tuple[datetime, Any] | None:
    try:
        with open(path) as f:
            raw = json.load(f)
        return datetime.fromisoformat(raw["fetched_at"]), raw["value"]
    except (OSError, json.JSONDecodeError, KeyError, ValueError):
        return None


def _write(path: str, now: datetime, value: Any) -> None:
    with open(path, "w") as f:
        json.dump({"fetched_at": now.isoformat(), "value": value}, f)


def cached(
    env_var: str,
    default: str,
    ttl: timedelta,
    now: datetime,
    fetch_fn: Callable[[], Any],
) -> Any:
    """Return `fetch_fn()`'s value, cached in the file named by `env_var`
    (falling back to `default`) for `ttl`. See the module docstring for the
    stale-serve and raise behaviour."""
    path = os.environ.get(env_var, default)
    stale = _read(path)
    if stale is not None and now - stale[0] < ttl:
        return stale[1]

    try:
        value = fetch_fn()
    except requests.exceptions.RequestException:
        if stale is not None:
            return stale[1]
        raise

    _write(path, now, value)
    return value
