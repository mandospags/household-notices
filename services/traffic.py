"""TomTom live traffic for the two household commutes (Station run and
School run), via the Routing API:
https://developer.tomtom.com/routing-api/documentation/tomtom-maps/calculate-route

Direction flips at noon, same split as trains.py: before 12:00 the routes
run from Home (to Station / to School), from 12:00 they run back Home.

Locations are postcode centroids geocoded once via postcodes.io and pinned
as lat,lon in .env (TRAFFIC_HOME/STATION/SCHOOL) - same resolve-once
pattern as TVBC_UPRN. Centroid accuracy is fine for route-level traffic;
re-run https://api.postcodes.io/postcodes/<pc> if a location changes.

The summary block of routes[0] carries travelTimeInSeconds (live, includes
traffic) and trafficDelayInSeconds (delay vs current no-incident time on
the chosen route). Delay under a minute is shown as a plain time with no
suffix - the wobble isn't information.
"""

import os
from datetime import datetime

import requests

from .base import Notice

SOURCE = "traffic"

API_URL_TEMPLATE = (
    "https://api.tomtom.com/routing/1/calculateRoute/{start}:{end}/json"
)


def _route_summary(api_key: str, start: str, end: str) -> dict:
    resp = requests.get(
        API_URL_TEMPLATE.format(start=start, end=end),
        params={"key": api_key, "traffic": "true"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["routes"][0]["summary"]


def _line(name: str, summary: dict) -> str:
    minutes = round(summary["travelTimeInSeconds"] / 60)
    delay_min = round(summary.get("trafficDelayInSeconds", 0) / 60)
    text = f"{name}: {minutes} min"
    if delay_min > 0:
        text += f" (+{delay_min} traffic)"
    return text


def _routes(now: datetime) -> list[tuple[str, str, str]]:
    home = os.environ["TRAFFIC_HOME"]
    station = os.environ["TRAFFIC_STATION"]
    school = os.environ["TRAFFIC_SCHOOL"]

    if now.hour < 12:
        return [("Station run", home, station), ("School run", home, school)]
    return [("Station run", station, home), ("School run", school, home)]


def fetch(now: datetime) -> list[Notice]:
    api_key = os.environ["TOMTOM_API_KEY"]
    return [
        Notice(
            source=SOURCE,
            title=_line(name, _route_summary(api_key, start, end)),
            date=now.date(),
        )
        for name, start, end in _routes(now)
    ]


def alert_status(now: datetime) -> dict[str, dict]:
    """Keyed by run name only, not direction - so the noon direction flip
    doesn't spawn [new] keys twice a day, at the cost of one (informative)
    alert at the flip if the two directions' states differ. Delay under the
    threshold reads as a single "clear" state, which silences sub-threshold
    wobble; above it, each whole-minute change re-alerts (band it later if
    that proves chatty in practice)."""
    api_key = os.environ["TOMTOM_API_KEY"]
    threshold_min = int(os.environ["TRAFFIC_DELAY_ALERT_MIN"])

    statuses = {}
    for name, start, end in _routes(now):
        summary_data = _route_summary(api_key, start, end)
        delay_min = round(summary_data.get("trafficDelayInSeconds", 0) / 60)
        status = "clear" if delay_min < threshold_min else f"+{delay_min} min"
        statuses[f"{SOURCE}:{name}"] = {
            "status": status,
            "summary": _line(name, summary_data),
        }
    return statuses
