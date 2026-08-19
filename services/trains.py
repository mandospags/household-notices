"""Realtime Trains (RTT) commute rows for Andover <-> London Waterloo.

https://api-portal.rtt.io - free tier, bearer auth. A long-life refresh
token (RTT_REFRESH_TOKEN) is exchanged for a short-life access token via
/api/get_access_token on every run.

Weekdays only - fetch() and alert_status() both return empty (no commute
to watch) on Saturday/Sunday.

Split at TRAINS_MORNING_CUTOFF (not noon - "the latest I'd still
count as arriving today"): before the cutoff, "today" is a fixed board of
the trains around the usual morning departure and "upcoming" is a preview
of the trains around the usual evening return, both for today; from the
cutoff on, "today" is the board around the usual evening return and
"upcoming" previews tomorrow morning's board - skipping to Monday if
tomorrow would be a weekend. Returned as ordinary Notices
(section="today"/"upcoming" overrides the digest's date-based bucketing)
rather than a separate board.

Each board is TRAINS_BOARD_SIZE trains nearest the usual time
(TRAINS_USUAL_MORNING/EVENING), not "next N from now" - it always shows
the same handful of trains (the usual one plus one either side, given ~30
min spacing) including any already departed, on purpose: if the usual one
was late, the one before it is useful context for whether that's a pattern
today. See _board_around for why "nearest" and not "windowed query".

The RTT API returns and accepts naive local (Europe/London) timestamps, e.g.
"2026-08-17T18:06:00" with no offset - not UTC. Everything here stays naive
and is compared/formatted as local time throughout; do not introduce
timezone-aware datetimes into this module.

Querying direction matters: /rtt/location only gives *live* times for the
station you queried - so we always query *at Andover* (our home station):
filterTo for the outbound leg (Andover's own live departure + destination's
planned arrival) and filterFrom for the return leg (Andover's own live
arrival + origin's planned departure). That keeps every "home end" time
live for one call, with no need for a second /rtt/service lookup.

alert_status() (for alerts.py) watches the two *usual* commute trains
(TRAINS_USUAL_MORNING from home, TRAINS_USUAL_EVENING back) rather than
"next from now" - alerts need a stable subject to diff, and these are
queried at their departing station so the live time and platform are the
departure's own (evening platform = Waterloo's, which is the useful one).
Keyed by scheduleMetadata.uniqueIdentity (schedule identity + departure
date), so keys roll over naturally each day.

On the return leg, Andover is a mid-route stop, not the schedule's final
destination (many of these continue to Salisbury, Yeovil Junction, etc) -
so the board_destination shown is that true final destination (matching
what's actually printed on the departure board at Waterloo), while the
arrival time/name is Andover's own, kept separate rather than conflated.
"""

import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

import requests

from .base import Notice

API_BASE = "https://data.rtt.io"

SOURCE = "trains"

TRAINS_BOARD_SIZE = 3
# Wide enough that journey-time drift between the anchor (always a
# departure time, from TRAINS_USUAL_MORNING/EVENING) and an arrival-focus
# query's own window (bound to the home station's *arrival* time, not the
# other end's departure) can never push the desired trains outside it -
# ~70 min Waterloo->Andover journeys mean a naive +/-45min window centered
# on the departure anchor misses the actual arrivals entirely. See
# _board_around.
TRAINS_SEARCH_WINDOW = timedelta(hours=2)


@dataclass
class TrainRow:
    from_name: str
    from_planned: datetime
    from_estimate: datetime | None
    board_destination: str
    arrival_name: str
    arrival_planned: datetime | None
    arrival_estimate: datetime | None
    platform: str | None
    is_cancelled: bool


def _get_access_token() -> str:
    refresh_token = os.environ["RTT_REFRESH_TOKEN"]
    resp = requests.get(
        f"{API_BASE}/api/get_access_token",
        headers={"Authorization": f"Bearer {refresh_token}"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["token"]


def _location_lineup(
    access_token: str,
    home: str,
    other: str,
    focus: str,
    time_from: datetime,
    time_to: datetime,
) -> tuple[str, list[dict]]:
    filter_param = "filterTo" if focus == "departure" else "filterFrom"
    resp = requests.get(
        f"{API_BASE}/rtt/location",
        headers={"Authorization": f"Bearer {access_token}"},
        params={
            "code": f"gb-nr:{home}",
            filter_param: f"gb-nr:{other}",
            "timeFrom": f"{time_from:%Y-%m-%dT%H:%M:%S}",
            "timeTo": f"{time_to:%Y-%m-%dT%H:%M:%S}",
        },
        timeout=15,
    )
    resp.raise_for_status()
    if resp.status_code == 204:
        return home, []
    data = resp.json()
    home_name = data.get("query", {}).get("location", {}).get("description", home)
    return home_name, data.get("services", [])


def _live_time(temporal: dict) -> datetime | None:
    if temporal.get("realtimeActual"):
        return datetime.fromisoformat(temporal["realtimeActual"])
    if temporal.get("realtimeForecast"):
        return datetime.fromisoformat(temporal["realtimeForecast"])
    return None


def _scheduled(pair: dict) -> datetime | None:
    advertised = pair.get("temporalData", {}).get("scheduleAdvertised")
    return datetime.fromisoformat(advertised) if advertised else None


def _parse_row(service: dict, focus: str, home_name: str) -> TrainRow:
    platform = service.get("locationMetadata", {}).get("platform") or {}
    destination_pair = service["destination"][0]

    if focus == "departure":
        home_temporal = service["temporalData"]["departure"]
        return TrainRow(
            from_name=home_name,
            from_planned=datetime.fromisoformat(home_temporal["scheduleAdvertised"]),
            from_estimate=_live_time(home_temporal),
            board_destination=destination_pair["location"]["description"],
            arrival_name=destination_pair["location"]["description"],
            arrival_planned=_scheduled(destination_pair),
            arrival_estimate=None,
            platform=platform.get("forecast") or platform.get("planned"),
            is_cancelled=bool(home_temporal.get("isCancelled")),
        )

    # focus == "arrival": home's own arrival (Andover) is the live end;
    # scheduleAdvertised is a genuine invariant of a filterFrom line-up
    # response, so it's read directly rather than defaulted to None.
    home_temporal = service["temporalData"]["arrival"]
    origin_pair = service["origin"][0]
    return TrainRow(
        from_name=origin_pair["location"]["description"],
        from_planned=_scheduled(origin_pair) or datetime.fromisoformat(home_temporal["scheduleAdvertised"]),
        from_estimate=None,
        board_destination=destination_pair["location"]["description"],
        arrival_name=home_name,
        arrival_planned=datetime.fromisoformat(home_temporal["scheduleAdvertised"]),
        arrival_estimate=_live_time(home_temporal),
        platform=platform.get("forecast") or platform.get("planned"),
        is_cancelled=bool(home_temporal.get("isCancelled")),
    )


def _sort_key(row: TrainRow, focus: str) -> datetime:
    return row.from_planned if focus == "departure" else row.arrival_planned


def _trains_in_window(
    access_token: str,
    home: str,
    other: str,
    focus: str,
    window_from: datetime,
    window_to: datetime,
) -> list[TrainRow]:
    home_name, services = _location_lineup(access_token, home, other, focus, window_from, window_to)
    return sorted(
        (_parse_row(s, focus, home_name) for s in services), key=lambda r: _sort_key(r, focus)
    )


def _board_around(
    access_token: str, home: str, other: str, focus: str, anchor: datetime
) -> list[TrainRow]:
    """The usual train plus the ones either side of it, by proximity to
    `anchor` (always a departure time, at whichever end is the outbound
    origin) - not by the query window itself, since that window is bound to
    the *queried* station's own event time (Andover's arrival, for the
    return leg), a different quantity than the anchor. `from_planned` is
    always the outbound-departure field regardless of focus, so it's what
    both the anchor and the nearest-neighbour selection compare against."""
    candidates = _trains_in_window(
        access_token, home, other, focus, anchor - TRAINS_SEARCH_WINDOW, anchor + TRAINS_SEARCH_WINDOW
    )
    nearest = sorted(candidates, key=lambda r: abs((r.from_planned - anchor).total_seconds()))
    board = nearest[:TRAINS_BOARD_SIZE]
    board.sort(key=lambda r: _sort_key(r, focus))
    return board


def _next_weekday(d: date) -> date:
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _format_line(row: TrainRow) -> str:
    if row.is_cancelled:
        return f"{row.from_planned:%H:%M} to {row.board_destination}: CANCELLED"

    departs = f"{row.from_planned:%H:%M}"
    if row.from_estimate and row.from_estimate != row.from_planned:
        departs += f" (exp {row.from_estimate:%H:%M})"

    if row.arrival_planned is None:
        arrives = "arr unknown"
    else:
        arrives = f"arr {row.arrival_planned:%H:%M}"
        if row.arrival_name != row.board_destination:
            arrives = f"arr {row.arrival_name} {row.arrival_planned:%H:%M}"
        if row.arrival_estimate and row.arrival_estimate != row.arrival_planned:
            arrives += f" (exp {row.arrival_estimate:%H:%M})"

    platform = f"plat {row.platform}" if row.platform else "plat ?"

    return f"{departs} to {row.board_destination}, {platform}, {arrives}"


def _watched_status(
    access_token: str, depart: str, dest: str, planned: datetime, threshold_min: int
) -> tuple[str, dict]:
    """Status of the one scheduled service departing `depart` for `dest` at
    `planned` - queried at the departing station (unlike fetch(), which always
    queries at home), so the live time and platform are the departure's own.
    After departure realtimeActual takes over from the forecast, so the status
    settles rather than churning."""
    home_name, services = _location_lineup(
        access_token,
        depart,
        dest,
        "departure",
        planned - timedelta(minutes=10),
        planned + timedelta(minutes=10),
    )
    for svc in services:
        row = _parse_row(svc, "departure", home_name)
        if row.from_planned != planned:
            continue
        if row.is_cancelled:
            state = "CANCELLED"
        else:
            delay_min = 0
            if row.from_estimate:
                delay_min = int((row.from_estimate - row.from_planned).total_seconds() // 60)
            if delay_min >= threshold_min:
                state = f"exp {row.from_estimate:%H:%M} (+{delay_min}m)"
            else:
                state = "on time"
        status = f"{state}, plat {row.platform or '?'}"
        # uniqueIdentity is e.g. "gb-nr:L79428:2026-08-18" - schedule identity
        # plus departure date, so keys roll over naturally each day.
        key = f"{SOURCE}:{svc['scheduleMetadata']['uniqueIdentity']}"
        summary = f"{planned:%H:%M} {home_name} to {row.board_destination}: {status}"
        return key, {"status": status, "summary": summary}

    # The usual train not being in the timetable at all is itself an alert
    # (engineering works, weekend timetable, short-notice removal).
    key = f"{SOURCE}:{depart}-{dest}:{planned:%Y-%m-%d-%H%M}"
    summary = f"{planned:%H:%M} {depart} to {dest}: not in today's timetable"
    return key, {"status": "not in timetable", "summary": summary}


def alert_status(now: datetime) -> dict[str, dict]:
    if now.weekday() >= 5:
        return {}

    home = os.environ["RTT_ORIGIN"]
    other = os.environ["RTT_DESTINATION"]
    morning = time.fromisoformat(os.environ["TRAINS_USUAL_MORNING"])
    evening = time.fromisoformat(os.environ["TRAINS_USUAL_EVENING"])
    threshold_min = int(os.environ["TRAINS_DELAY_ALERT_MIN"])

    access_token = _get_access_token()
    today = now.date()

    statuses = {}
    for depart, dest, planned_time in (
        (home, other, morning),
        (other, home, evening),
    ):
        key, entry = _watched_status(
            access_token, depart, dest, datetime.combine(today, planned_time), threshold_min
        )
        statuses[key] = entry
    return statuses


def fetch(now: datetime) -> list[Notice]:
    if now.weekday() >= 5:
        return []

    home = os.environ["RTT_ORIGIN"]
    other = os.environ["RTT_DESTINATION"]
    morning = time.fromisoformat(os.environ["TRAINS_USUAL_MORNING"])
    evening = time.fromisoformat(os.environ["TRAINS_USUAL_EVENING"])
    cutoff = time.fromisoformat(os.environ["TRAINS_MORNING_CUTOFF"])

    access_token = _get_access_token()
    today = now.date()

    if now.time() < cutoff:
        today_rows = _board_around(access_token, home, other, "departure", datetime.combine(today, morning))
        preview_rows = _board_around(access_token, home, other, "arrival", datetime.combine(today, evening))
        preview_date = today
    else:
        today_rows = _board_around(access_token, home, other, "arrival", datetime.combine(today, evening))
        next_day = _next_weekday(today + timedelta(days=1))
        preview_rows = _board_around(access_token, home, other, "departure", datetime.combine(next_day, morning))
        preview_date = next_day

    notices = [
        Notice(source=SOURCE, title=_format_line(r), date=today, section="today")
        for r in today_rows
    ]
    notices += [
        Notice(source=SOURCE, title=_format_line(r), date=preview_date, section="upcoming")
        for r in preview_rows
    ]
    return notices
