"""SSEN Distribution power cuts, via the same API that backs their
consumer-facing PowerTrack map:
https://external.distribution.prd.ssen.co.uk/opendataportal-prd/v4/api/getallfaults
(found via the CKAN dataset metadata at
https://data-api.ssen.co.uk/api/3/action/package_show?id=realtime_outage_dataset
- the "Real Time Outage Dataset" resource list, not the map tool itself).

No key/auth, but a bare/default `requests` User-Agent gets HTTP 403
(Cloudflare bot-block) - same gotcha as mass.py, same fix (a browser UA).

One feed covers both planned and unplanned outages, distinguished by `type`/
`jobStatus` rather than a single flag: `type == "PSI"` (Public Supply
Interruption, numeric reference) or `jobStatus == "P"` on an LV/HV entry is
planned/scheduled work; `jobStatus` in `I` (in progress) / `FI` (fault
investigation) on an LV/HV entry (TT/TV-prefixed reference) is a live
unplanned fault. `jobStatus == "R"` (restored) exists but is fleeting -
confirmed live that a restored entry drops out of the feed within about a
minute, well inside any reasonable poll cadence, so in practice a fault is
never observed sitting in "restored" state; it just disappears. alerts.py's
existing "a key that disappears is dropped silently" rule means a power cut
alerts once at onset and then goes quiet with no explicit "restored" line -
accepted as consistent with how trains.py's daily key rollover already
works, not worth extra state to change.

No server-side filtering (postcode/area query params are silently ignored -
confirmed live) - always returns the full GB-wide fault list (SEPD + SHEPD
license areas, ~40 entries at any time), so fetch() always pulls everything
and filters client-side against HOME_POSTCODE. `affectedAreas` mixes full
postcodes ("SP11 0BY") and bare outward codes ("SP11") - matched as exact
equality against either form, not a prefix/startswith, since a prefix match
on the outward code alone would also catch unrelated postcodes sharing the
same digits (SP1 vs SP11) and a full-district match is already a fairly
wide net.

Always-on (no ACTIVE_HOURS) - unlike trains/traffic/weather, a power cut is
exactly the kind of thing worth knowing about overnight.

Alert-only: no fetch()/Notice output, deliberately deviating from the usual
"Adding a source" pattern (which assumes fetch() exists). A same-day planned
outage notice for the digest's "today" bucket would be easy to add later
(PSI/`P` entries already carry estimated switch-off/restoration times), but
isn't built yet - SSEN's planned notices are same-day operational (posted
that morning for that afternoon), not the multi-day-ahead heads-up bins/mass
give, so the digest value is weaker than the alert value.
"""

import os
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from .base import Notice

SOURCE = "powercuts"

FAULTS_URL = "https://external.distribution.prd.ssen.co.uk/opendataportal-prd/v4/api/getallfaults"
HEADERS = {
    "Accept": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
}


def _fetch_faults() -> list[dict]:
    resp = requests.get(FAULTS_URL, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.json()["faults"]


def _local_faults(home_postcode: str) -> list[dict]:
    outward = home_postcode.split()[0]
    return [
        fault
        for fault in _fetch_faults()
        if any(area in (home_postcode, outward) for area in fault["affectedAreas"])
    ]


def _status(fault: dict) -> str:
    if fault["type"] == "PSI" or fault["jobStatus"] == "P":
        return "planned"
    return "fault"


def _local_time(iso_utc: str) -> str:
    dt = datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
    return dt.astimezone(ZoneInfo("Europe/London")).strftime("%H:%M")


def _summary(fault: dict) -> str:
    kind = "Planned power cut" if _status(fault) == "planned" else "Power cut"
    where = fault["title"]
    customers = fault["customerCount"]
    text = f"{kind}: {where} ({fault['reference']})"
    if customers:
        text += f", {customers} customers"
    restoration = fault.get("estimatedRestorationTimeUtc")
    if restoration:
        text += f", est. restored {_local_time(restoration)}"
    return text


def alert_status(now: datetime) -> dict[str, dict]:
    home_postcode = os.environ["HOME_POSTCODE"]
    statuses = {}
    for fault in _local_faults(home_postcode):
        key = f"{SOURCE}:{fault['reference']}"
        statuses[key] = {"status": _status(fault), "summary": _summary(fault)}
    return statuses
