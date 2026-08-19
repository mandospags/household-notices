"""TomTom live traffic for the two household commutes (Station run and
School run), via the Routing API:
https://developer.tomtom.com/routing-api/documentation/tomtom-maps/calculate-route
and the Traffic Incidents API for incidents (roadworks, closures, jams,
accidents, ...) on those routes:
https://developer.tomtom.com/traffic-api/documentation/tomtom-maps/traffic-incidents/incident-details

Direction flips at noon (independent of trains.py's own cutoff, which
tracks the usual commute times rather than a fixed hour): before 12:00 the
routes run from Home (to Station / to School), from 12:00 they run back
Home. The printed line shows the actual direction (e.g. "Home -> Station");
alert_status keys stay direction-free ("Station run") so the noon flip
doesn't spawn a duplicate [new] alert.

Locations are postcode centroids geocoded once via postcodes.io and pinned
as lat,lon in .env (TRAFFIC_HOME/STATION/SCHOOL) - same resolve-once
pattern as TVBC_UPRN. Centroid accuracy is fine for route-level traffic;
re-run https://api.postcodes.io/postcodes/<pc> if a location changes.

The summary block of routes[0] carries travelTimeInSeconds (live, includes
traffic) and trafficDelayInSeconds (delay vs current no-incident time on
the chosen route). Delay under a minute is shown as a plain time with no
suffix - the wobble isn't information.

Incidents: naive approach was to bbox the route polyline and query
incidentDetails within that box, but a bbox is the *rectangle* the route
fits inside, not the route itself - for a winding town route that rectangle
covers plenty of streets never actually driven (confirmed live: 10 bbox
hits on the Home-Station run, only 1 actually on the route). Instead,
calculateRoute itself (with sectionType=traffic) returns a "sections" list
of TRAFFIC entries with startPointIndex/endPointIndex into the route
polyline and an eventId - i.e. it already knows which incidents affect
*this* route. We still bbox-query incidentDetails (same buffered-polyline
box as before) for from/to/description text, but only keep incidents whose
id ends with an on-route eventId - so the bbox is just a wide net for
lookup, the sections list is what actually filters to "on my route". No
categoryFilter/simpleCategory allow-list - every on-route TRAFFIC section is
reported (roadworks, closures, jams, accidents, ...) rather than guessing
at TomTom's category taxonomy; "on the route" is already the filter that
matters. Present-only (no timeValidityFilter=future): calculateRoute only
ever reflects *live* routing state, so a not-yet-started closure can never
appear in sections regardless - the day it goes live it becomes a normal
present, on-route entry with no extra code, so there's no point querying
for planned/future incidents here. delay on an incident is usually 0/null
even while it's actively closed - the incident existing and it causing
measurable delay are different things, and this is deliberately reported
regardless of delay so a stuck-for-a-week closure doesn't go unmentioned
just because traffic has adapted around it.
"""

import os
from datetime import datetime

import requests

from .base import Notice

SOURCE = "traffic"

ROUTE_URL_TEMPLATE = (
    "https://api.tomtom.com/routing/1/calculateRoute/{start}:{end}/json"
)
INCIDENTS_URL = "https://api.tomtom.com/traffic/services/5/incidentDetails"
INCIDENT_FIELDS = (
    "{incidents{properties{id,events{description},delay,from,to}}}"
)
INCIDENT_BBOX_PAD_DEG = 0.003  # ~300m, buffer around the route polyline


def _calculate_route(api_key: str, start: str, end: str) -> dict:
    resp = requests.get(
        ROUTE_URL_TEMPLATE.format(start=start, end=end),
        params={"key": api_key, "traffic": "true", "sectionType": "traffic"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["routes"][0]


def _on_route_event_ids(route: dict) -> set[str]:
    return {
        section["eventId"]
        for section in route.get("sections", [])
        if section.get("sectionType") == "TRAFFIC"
    }


def _route_summary(api_key: str, start: str, end: str) -> dict:
    return _calculate_route(api_key, start, end)["summary"]


def _line(name: str, summary: dict) -> str:
    minutes = round(summary["travelTimeInSeconds"] / 60)
    delay_min = round(summary.get("trafficDelayInSeconds", 0) / 60)
    text = f"{name}: {minutes} min"
    if delay_min > 0:
        text += f" (+{delay_min} traffic)"
    return text


def _incidents(api_key: str, points: list[dict], on_route_event_ids: set[str]) -> list[dict]:
    if not on_route_event_ids:
        return []

    lats = [p["latitude"] for p in points]
    lons = [p["longitude"] for p in points]
    pad = INCIDENT_BBOX_PAD_DEG
    bbox = (
        f"{min(lons) - pad},{min(lats) - pad},"
        f"{max(lons) + pad},{max(lats) + pad}"
    )
    resp = requests.get(
        INCIDENTS_URL,
        params={
            "key": api_key,
            "bbox": bbox,
            "fields": INCIDENT_FIELDS,
            "language": "en-GB",
        },
        timeout=15,
    )
    resp.raise_for_status()

    seen = set()
    deduped = []
    for incident in resp.json().get("incidents", []):
        props = incident["properties"]
        if not props["id"].endswith(tuple(on_route_event_ids)):
            continue
        key = frozenset({props.get("from", ""), props.get("to", "")})
        if key in seen:
            continue
        seen.add(key)
        deduped.append(props)
    return deduped


def _incident_line(name: str, props: dict) -> str:
    where = " to ".join(part for part in (props.get("from"), props.get("to")) if part)
    kind = " & ".join(e["description"].lower() for e in props["events"])
    text = f"{name} incident: {where or kind}"
    if where:
        text += f", {kind}"
    delay_s = props.get("delay")
    if delay_s:
        text += f" (+{round(delay_s / 60)} min)"
    return text


def _routes(now: datetime) -> list[tuple[str, str, str, str]]:
    """Each entry is (key_name, display_name, start, end). key_name is
    direction-free ("Station run") and used for alert_status keys, so the
    noon flip doesn't spawn a new [new] key twice a day - display_name
    carries the direction (e.g. "Home -> Station") for the printed line."""
    home = os.environ["TRAFFIC_HOME"]
    station = os.environ["TRAFFIC_STATION"]
    school = os.environ["TRAFFIC_SCHOOL"]

    if now.hour < 12:
        return [
            ("Station run", "Home → Station", home, station),
            ("School run", "Home → School", home, school),
        ]
    return [
        ("Station run", "Station → Home", station, home),
        ("School run", "School → Home", school, home),
    ]


def fetch(now: datetime) -> list[Notice]:
    api_key = os.environ["TOMTOM_API_KEY"]
    notices = []
    for _, display_name, start, end in _routes(now):
        route = _calculate_route(api_key, start, end)
        notices.append(
            Notice(source=SOURCE, title=_line(display_name, route["summary"]), date=now.date())
        )
        on_route_event_ids = _on_route_event_ids(route)
        for props in _incidents(api_key, route["legs"][0]["points"], on_route_event_ids):
            notices.append(
                Notice(source=SOURCE, title=_incident_line(display_name, props), date=now.date())
            )
    return notices


def alert_status(now: datetime) -> dict[str, dict]:
    """Keyed by run name only, not direction - so the noon direction flip
    doesn't spawn [new] keys twice a day, at the cost of one (informative)
    alert at the flip if the two directions' states differ. status is
    deliberately just "clear"/"delayed" (not the exact minutes) so
    alerts.py's diff fires once when a route crosses the threshold, not on
    every whole-minute wobble while it stays delayed - exact minutes still
    show up in summary. Incidents (see fetch) are digest-only for now - not
    wired into alerts."""
    api_key = os.environ["TOMTOM_API_KEY"]
    threshold_min = int(os.environ["TRAFFIC_DELAY_ALERT_MIN"])

    statuses = {}
    for key_name, display_name, start, end in _routes(now):
        summary_data = _route_summary(api_key, start, end)
        delay_min = round(summary_data.get("trafficDelayInSeconds", 0) / 60)
        status = "clear" if delay_min < threshold_min else "delayed"
        statuses[f"{SOURCE}:{key_name}"] = {
            "status": status,
            "summary": _line(display_name, summary_data),
        }
    return statuses
