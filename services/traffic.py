"""TomTom live traffic for the two household commutes (Station run and
School run), via the Routing API:
https://developer.tomtom.com/routing-api/documentation/tomtom-maps/calculate-route
and the Traffic Incidents API for roadworks/closures on those routes:
https://developer.tomtom.com/traffic-api/documentation/tomtom-maps/traffic-incidents/incident-details

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

Roadworks/closures: naive approach was to bbox the route polyline and query
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
lookup, the sections list is what actually filters to "on my route".
categoryFilter 8/9 = closed/roadworks; timeValidityFilter=present,future so
planned-but-not-started works show up too (flagged "planned", dated by
startTime so they land in "upcoming" rather than "today"). delay on an
incident is usually 0/null even while it's actively closed - roadworks
existing and roadworks causing measurable delay are different things, and
this is deliberately reported regardless of delay so a stuck-for-a-week
closure doesn't go unmentioned just because traffic has adapted around it.
"""

import os
from datetime import datetime, timezone

import requests

from .base import Notice

SOURCE = "traffic"

ROUTE_URL_TEMPLATE = (
    "https://api.tomtom.com/routing/1/calculateRoute/{start}:{end}/json"
)
INCIDENTS_URL = "https://api.tomtom.com/traffic/services/5/incidentDetails"
INCIDENT_CATEGORIES = "8,9"  # road closed, roadworks
INCIDENT_FIELDS = (
    "{incidents{properties{id,iconCategory,events{description},delay,"
    "from,to,timeValidity,startTime}}}"
)
INCIDENT_BBOX_PAD_DEG = 0.003  # ~300m, buffer around the route polyline
ROADWORK_SECTION_CATEGORIES = {"ROAD_WORK", "ROAD_CLOSURE"}


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
        and section.get("simpleCategory") in ROADWORK_SECTION_CATEGORIES
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
            "categoryFilter": INCIDENT_CATEGORIES,
            "timeValidityFilter": "present,future",
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
    planned = props["timeValidity"] == "future"
    text = f"{name} {'planned ' if planned else ''}works: {where or kind}"
    if where:
        text += f" - {kind}"
    delay_s = props.get("delay")
    if delay_s:
        text += f" (+{round(delay_s / 60)} min)"
    return text


def _incident_date(now: datetime, props: dict):
    if props["timeValidity"] == "future" and props.get("startTime"):
        start = datetime.fromisoformat(props["startTime"].replace("Z", "+00:00"))
        return start.astimezone(timezone.utc).date()
    return now.date()


def _routes(now: datetime) -> list[tuple[str, str, str]]:
    home = os.environ["TRAFFIC_HOME"]
    station = os.environ["TRAFFIC_STATION"]
    school = os.environ["TRAFFIC_SCHOOL"]

    if now.hour < 12:
        return [("Station run", home, station), ("School run", home, school)]
    return [("Station run", station, home), ("School run", school, home)]


def fetch(now: datetime) -> list[Notice]:
    api_key = os.environ["TOMTOM_API_KEY"]
    notices = []
    for name, start, end in _routes(now):
        route = _calculate_route(api_key, start, end)
        notices.append(
            Notice(source=SOURCE, title=_line(name, route["summary"]), date=now.date())
        )
        on_route_event_ids = _on_route_event_ids(route)
        for props in _incidents(api_key, route["legs"][0]["points"], on_route_event_ids):
            notices.append(
                Notice(
                    source=SOURCE,
                    title=_incident_line(name, props),
                    date=_incident_date(now, props),
                )
            )
    return notices


def alert_status(now: datetime) -> dict[str, dict]:
    """Keyed by run name only, not direction - so the noon direction flip
    doesn't spawn [new] keys twice a day, at the cost of one (informative)
    alert at the flip if the two directions' states differ. Delay under the
    threshold reads as a single "clear" state, which silences sub-threshold
    wobble; above it, each whole-minute change re-alerts (band it later if
    that proves chatty in practice). Roadworks/closures (see fetch) are
    digest-only for now - not wired into alerts."""
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
