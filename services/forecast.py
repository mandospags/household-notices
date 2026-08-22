"""Daily weather forecast (temperature range + rain chance) for home, via
Met Office's Site-Specific Blended Probabilistic Forecast (BPF) API
(https://datahub.metoffice.gov.uk/, "Site Specific" > BPF product on the CDA
Configurator - a separate subscription from weather.py's NSWWS severe
warnings feed, with its own key, MET_OFFICE_FORECAST_API_KEY).

Fills the "baseline weather summary" gap noted in SPEC.md's backlog -
weather.py only covers *severe* warnings, not day-to-day conditions.

One digest line per day: "Wet and cold today, max 9C / min 3C, 65% chance
of rain" (today, absolute thresholds) and "Warmer and drier tomorrow, max
14C / min 6C, 20% chance of rain" (tomorrow, compared against today - reads
more naturally than a second set of absolute adjectives, at the cost of
needing both days' numbers in hand before rendering either line). Digest-only,
no alert_status() - a forecast drifts gradually rather than flipping between
discrete states, so there's no clean "changed" to diff on the way weather.py's
warnings or trains.py's delays do.

API shape (confirmed live, not from the docs alone - the product page
describes the *system*, not the wire format):
- Base `https://data.hub.api.metoffice.gov.uk/mo-blended-prob-forecast-feature-svc/2.0.0`,
  auth via `apikey` header (not documented in the portal's guide pages, found
  by trial).
- Response is CoverageJSON, one point ("position" query,
  `coords=POINT(lon lat)` - lon first) against a single fixed instance
  ("blended") of two collections:
  - `uk-spot-percentiles`: `airTemperature1p5mMaximumPt12h` /
    `...MinimumPt12h`, each returned as a full spread of 15 percentile
    bands (5th-95th), in Kelvin, at ~3-hourly steps. No plain single-value
    max/min field exists in this collection - only percentiles. Uses the
    50th percentile (median) as "the" deterministic value, per the Met
    Office's own guidance for turning this into a single number.
  - `uk-spot-probabilities`: `probabilityOfThicknessOfRainfallAmountAboveThresholdSumPt03h`,
    returned as a probability *per rainfall threshold*
    (">0.0", ">3.0E-5", ... ">0.4" metres) rather than one "% chance of
    rain" field - there is no such field in this product. The ">0.0"
    (any measurable trace) threshold badly over-reads versus the familiar
    "chance of rain" figure - confirmed live, it showed 50%+ on an
    otherwise unremarkable week. Uses the ">2.5E-4" (~0.25mm) threshold
    instead, the closest available to the standard ~0.2mm "measurable rain"
    cutoff other forecast providers use.
- Both parameters come back as flat arrays over ~14 days of 3-hourly
  timesteps, not pre-bucketed into calendar days - fetch() buckets each
  timestep by its Europe/London calendar date (the `t` values are UTC;
  bucketing by raw UTC date would put a 00:00Z step on the wrong side of
  the date line during BST) and reduces each day to max(daytime-max-temp
  series), min(daytime-min-temp series), max(rain-probability series) - i.e.
  each day's rain chance is its worst single 3-hour window, not an average.
  The *Pt12h temperature windows themselves straddle midnight (each one is a
  rolling 12h max/min ending at its timestamp, not aligned to the calendar
  day), so a day's bucketed max/min can be off by up to one boundary
  window's worth - accepted as within the "heads up what to wear" use case's
  tolerance rather than something worth a second query to correct.
- At the 14:00 digest run, "today" only covers timesteps from now onward (the
  feed starts at the current hour) - the morning's low has already dropped
  out of today's min by then. Not a bug: the 14:00 run is explicitly framed
  as FYI-only (the actionable decision already happened at 06:00), so a
  same-day figure that only reflects "what's left of today" is fine.
- Reuses TRAFFIC_HOME's lat,lon (traffic.py's geocoded home point) rather
  than a new coordinate pair - see .env.example.

Cached via cache.py (FORECAST_CACHE_FILE, TTL below) since the free tier is
55 calls/day - three API calls (max temp, min temp, rain) per digest run is
nowhere near that even uncached, but caching also protects against burning
quota during manual testing/dev.
"""

import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests

from .base import TIMEOUT, Notice
from .cache import cached

SOURCE = "forecast"

API_BASE = "https://data.hub.api.metoffice.gov.uk/mo-blended-prob-forecast-feature-svc/2.0.0"
COLLECTION = "uk-spot-percentiles"
PROB_COLLECTION = "uk-spot-probabilities"
INSTANCE = "blended"

TEMP_MAX_PARAM = "airTemperature1p5mMaximumPt12h"
TEMP_MIN_PARAM = "airTemperature1p5mMinimumPt12h"
RAIN_PARAM = "probabilityOfThicknessOfRainfallAmountAboveThresholdSumPt03h"
RAIN_THRESHOLD = ">2.5E-4"  # ~0.25mm - closest available to "measurable rain"
MEDIAN_PERCENTILE = "50"

CACHE_TTL = timedelta(hours=1)
LONDON = ZoneInfo("Europe/London")

COLD_MAX_C = 10  # below this, today's max counts as "cold"
WARM_MAX_C = 20  # above this, today's max counts as "warm"
WET_RAIN_PCT = 60  # at/above this, "wet"
DRY_RAIN_PCT = 20  # at/below this, "dry"
TEMP_DIFF_C = 2  # tomorrow vs today must differ by at least this to call out
RAIN_DIFF_PCT = 20  # tomorrow vs today must differ by at least this to call out


def _position(collection: str, parameter: str) -> dict:
    lat, lon = os.environ["TRAFFIC_HOME"].split(",")
    resp = requests.get(
        f"{API_BASE}/collections/{collection}/instances/{INSTANCE}/position",
        headers={"apikey": os.environ["MET_OFFICE_FORECAST_API_KEY"]},
        params={
            "coords": f"POINT({lon.strip()} {lat.strip()})",
            "parameter-name": parameter,
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["coverages"][0]


def _series_by_date(coverage: dict, parameter: str, axis_name: str, axis_value: str) -> dict[date, list[float]]:
    axes = coverage["domain"]["axes"]
    times = axes["t"]["values"]
    values = coverage["ranges"][parameter]["values"]
    shape = coverage["ranges"][parameter]["shape"]  # [<axis_name>, "t"]
    n_t = shape[1]

    axis_values = axes[axis_name]["values"]
    row = axis_values.index(axis_value)
    row_values = values[row * n_t : (row + 1) * n_t]

    by_date: dict[date, list[float]] = {}
    for t_str, value in zip(times, row_values):
        if value is None:
            continue
        local_date = datetime.fromisoformat(t_str).astimezone(LONDON).date()
        by_date.setdefault(local_date, []).append(value)
    return by_date


def _fetch_live() -> dict[date, dict]:
    max_cov = _position(COLLECTION, TEMP_MAX_PARAM)
    min_cov = _position(COLLECTION, TEMP_MIN_PARAM)
    rain_cov = _position(PROB_COLLECTION, RAIN_PARAM)

    max_by_date = _series_by_date(max_cov, TEMP_MAX_PARAM, "percentiles", MEDIAN_PERCENTILE)
    min_by_date = _series_by_date(min_cov, TEMP_MIN_PARAM, "percentiles", MEDIAN_PERCENTILE)
    rain_by_date = _series_by_date(
        rain_cov, RAIN_PARAM, f"{RAIN_PARAM}Values", RAIN_THRESHOLD
    )

    days: dict[date, dict] = {}
    for d in set(max_by_date) & set(min_by_date):
        days[d] = {
            "max_c": round(max(max_by_date[d]) - 273.15),
            "min_c": round(min(min_by_date[d]) - 273.15),
            "rain_pct": round(max(rain_by_date.get(d, [0])) * 100),
        }
    return days


def _temp_word(max_c: int) -> str | None:
    if max_c < COLD_MAX_C:
        return "cold"
    if max_c > WARM_MAX_C:
        return "warm"
    return None


def _rain_word(rain_pct: int) -> str | None:
    if rain_pct >= WET_RAIN_PCT:
        return "wet"
    if rain_pct <= DRY_RAIN_PCT:
        return "dry"
    return None


def _today_title(day: dict) -> str:
    words = [w for w in (_rain_word(day["rain_pct"]), _temp_word(day["max_c"])) if w]
    lead = f"{' and '.join(words).capitalize()} today" if words else "Today"
    return f"{lead}, max {day['max_c']}°C / min {day['min_c']}°C, {day['rain_pct']}% chance of rain"


def _tomorrow_title(tomorrow: dict, today: dict) -> str:
    comparisons = []
    temp_diff = tomorrow["max_c"] - today["max_c"]
    if abs(temp_diff) >= TEMP_DIFF_C:
        comparisons.append("warmer" if temp_diff > 0 else "colder")
    rain_diff = tomorrow["rain_pct"] - today["rain_pct"]
    if abs(rain_diff) >= RAIN_DIFF_PCT:
        comparisons.append("wetter" if rain_diff > 0 else "drier")

    if comparisons:
        lead = f"{' and '.join(comparisons).capitalize()} than today"
    else:
        lead = "Similar to today"
    return (
        f"{lead}, max {tomorrow['max_c']}°C / min {tomorrow['min_c']}°C, "
        f"{tomorrow['rain_pct']}% chance of rain"
    )


def fetch(now: datetime) -> list[Notice]:
    # cache.py stores plain JSON, so the date keys go in/out as ISO strings.
    raw = cached(
        "FORECAST_CACHE_FILE",
        "forecast_cache.json",
        CACHE_TTL,
        now,
        lambda: {d.isoformat(): v for d, v in _fetch_live().items()},
    )
    days = {date.fromisoformat(k): v for k, v in raw.items()}

    today = now.date()
    tomorrow = today + timedelta(days=1)
    notices = []
    if today in days:
        notices.append(Notice(source=SOURCE, title=_today_title(days[today]), date=today))
    if tomorrow in days and today in days:
        notices.append(
            Notice(source=SOURCE, title=_tomorrow_title(days[tomorrow], days[today]), date=tomorrow)
        )
    return notices
