"""Met Office severe weather warnings, via the National Severe Weather
Warning Service (NSWWS) Public API on DataHub (a separate subscription from
forecast.py's BPF product, its own key, NSWWS_API_KEY). Replaces an earlier
RSS-based version - see git history - that only had per-region granularity
and no structured severity/validity fields to work with.

API shape (confirmed live against the real endpoints, though never against a
populated feed - see below):
- Base `https://data.hub.api.metoffice.gov.uk/nswws/v1.1`, auth via `apikey`
  header (confirmed by trial - the docs describe the header name as
  `x-api-key`/`ApiKey` inconsistently, but HTTP header names are
  case-insensitive and the gateway only actually accepts `apikey`/`ApiKey`,
  not `x-api-key`).
- Two-hop fetch, every call, no shortcut: `GET /objects/feed` returns a small
  Atom document whose `<link rel="related">` href points at the *current*
  full GeoJSON snapshot of all issued warnings - that href's UUID rotates
  every time the warning list changes, so it must be re-discovered from the
  feed on every fetch, per the API's own docs (a stale/cached "issued" URL
  just keeps serving the same snapshot, it doesn't 404, so there's no cheap
  way to detect staleness other than always re-fetching the feed first).
- The GeoJSON snapshot's `properties` per warning (real shape, from a Met
  Office-supplied sample - see below) includes `warningId`, `weatherType`
  (list, e.g. ["WIND"]), `validFromDate`/`validToDate` (ISO UTC),
  `warningLevel` (YELLOW/AMBER/RED), `warningStatus` (e.g. "ISSUED"),
  `warningHeadline`, and `affectedAreas` - a list of
  `{regionName, regionCode, subRegions: [county names]}`. `geometry` is a
  MultiPolygon and is ignored entirely - not needed for a postcode-scale
  match.
- Filters to warnings whose `affectedAreas[].subRegions` contains
  WEATHER_HOME_COUNTY (exact string match, e.g. "Hampshire" - county-level,
  a real improvement over the old RSS's whole-region granularity, e.g. a
  Kent-only warning no longer shows up just because Kent shares the SE
  region with Hampshire).
- Also drops anything with `validToDate` in the past (a warning that's
  merely still sitting in the snapshot after its window closed) and
  anything whose `warningStatus != "ISSUED"` - the feed is documented to
  carry issued/updated/cancelled/expired warnings, but every field here
  (including whether a cancelled warning even stays in the snapshot, and
  under what status) is unconfirmed against a live warning: at the time
  this was written the live snapshot was empty (no active UK warnings) -
  same "never observed live" caveat the RSS version carried. Parsing was
  built and tested against a real multi-warning sample the Met Office
  provided directly (Storm Eowyn, Jan 2025), trimmed into a fixture and
  exercised in place of a live populated response.
- `validFromDate` is clamped to `now`'s Europe/London date if the window has
  already started (`max(valid_from_local_date, today)`) - using the raw
  start date would put a currently-active multi-day warning that started
  yesterday into a date digest.py's bucketing already drops as "in the
  past", making it vanish from the digest mid-warning. This mirrors why the
  old RSS version's pubDate-based dating was flagged as a bug in the first
  place.
- No cache - the API's rate limit (spike-arrest ~100 req/sec per the token's
  tier info) is far more generous than forecast.py's 55/day, so a plain
  fetch on every digest/alert run is fine.
- ACTIVE_HOURS unchanged from the RSS version (daytime-only) - whether a RED
  warning deserves overnight paging is a real question but a separate one
  from this source swap, not decided here.
"""

import os
from datetime import date, datetime
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import requests

from .base import TIMEOUT, Notice

SOURCE = "weather"
ACTIVE_HOURS = (6, 20)  # daytime only - see alerts.py's ACTIVE_HOURS gate

API_BASE = "https://data.hub.api.metoffice.gov.uk/nswws/v1.1"
FEED_URL = f"{API_BASE}/objects/feed"
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}
LONDON = ZoneInfo("Europe/London")


def _headers() -> dict:
    return {"apikey": os.environ["NSWWS_API_KEY"]}


def _current_warnings() -> list[dict]:
    feed_resp = requests.get(FEED_URL, headers=_headers(), timeout=TIMEOUT)
    feed_resp.raise_for_status()
    root = ElementTree.fromstring(feed_resp.content)

    related = root.find("atom:link[@rel='related']", ATOM_NS)
    if related is None:
        raise ValueError("NSWWS feed has no rel='related' link to the issued-warnings snapshot")

    issued_resp = requests.get(related.get("href"), headers=_headers(), timeout=TIMEOUT)
    issued_resp.raise_for_status()
    return issued_resp.json()["features"]


def _local_date(iso_utc: str) -> date:
    return datetime.fromisoformat(iso_utc.replace("Z", "+00:00")).astimezone(LONDON).date()


def _matches_county(warning: dict, county: str) -> bool:
    return any(
        county in area.get("subRegions", []) for area in warning.get("affectedAreas", [])
    )


def _relevant_warnings(now: datetime, county: str) -> list[dict]:
    today = now.date()
    warnings = []
    for feature in _current_warnings():
        props = feature["properties"]
        if props.get("warningStatus") != "ISSUED":
            continue
        if _local_date(props["validToDate"]) < today:
            continue
        if not _matches_county(props, county):
            continue
        warnings.append(props)
    return warnings


def _summary(props: dict) -> str:
    weather_types = "/".join(props.get("weatherType", []))
    return f"{props['warningLevel']} {weather_types} warning: {props['warningHeadline']}"


def alert_status(now: datetime) -> dict[str, dict]:
    county = os.environ["WEATHER_HOME_COUNTY"]
    statuses = {}
    for props in _relevant_warnings(now, county):
        key = f"{SOURCE}:{props['warningId']}"
        statuses[key] = {"status": props["warningLevel"], "summary": _summary(props)}
    return statuses


def fetch(now: datetime) -> list[Notice]:
    county = os.environ["WEATHER_HOME_COUNTY"]
    today = now.date()
    notices = []
    for props in _relevant_warnings(now, county):
        valid_from = _local_date(props["validFromDate"])
        notices.append(
            Notice(
                source=SOURCE,
                title=props["warningHeadline"],
                date=max(valid_from, today),
                detail=f"{props['warningLevel']} {'/'.join(props.get('weatherType', []))}",
            )
        )
    return notices
