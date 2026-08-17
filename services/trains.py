"""Realtime Trains (RTT) commute rows for Andover <-> London Waterloo.

https://api-portal.rtt.io - free tier, bearer auth. A long-life refresh
token (RTT_REFRESH_TOKEN) is exchanged for a short-life access token via
/api/get_access_token on every run.

Split at noon: before noon, "today" is the next 2 live Andover->Waterloo
departures and "upcoming" is a preview of this evening's Waterloo->Andover
window; from noon on, "today" is the next 2 live Waterloo->Andover arrivals
and "upcoming" is a preview of tomorrow morning's Andover->Waterloo window.
Returned as ordinary Notices (section="today"/"upcoming" overrides the
harness's date-based bucketing) rather than a separate board.

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

On the return leg, Andover is a mid-route stop, not the schedule's final
destination (many of these continue to Salisbury, Yeovil Junction, etc) -
so the board_destination shown is that true final destination (matching
what's actually printed on the departure board at Waterloo), while the
arrival time/name is Andover's own, kept separate rather than conflated.
"""

import os
from dataclasses import dataclass
from datetime import datetime, time, timedelta

import requests

from .base import Notice

API_BASE = "https://data.rtt.io"

SOURCE = "trains"


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
    operator: str
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
    operator = service["scheduleMetadata"]["operator"]["name"]
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
            operator=operator,
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
        operator=operator,
        is_cancelled=bool(home_temporal.get("isCancelled")),
    )


def _sort_key(row: TrainRow, focus: str) -> datetime:
    return row.from_planned if focus == "departure" else row.arrival_planned


def _next_trains(
    access_token: str, home: str, other: str, focus: str, now: datetime, limit: int
) -> list[TrainRow]:
    home_name, services = _location_lineup(
        access_token, home, other, focus, now, now + timedelta(hours=4)
    )
    rows = sorted(
        (_parse_row(s, focus, home_name) for s in services), key=lambda r: _sort_key(r, focus)
    )
    # Filter on departure time even in arrival focus - a train that's
    # already left Waterloo isn't one you can still catch, regardless of
    # when it's due into Andover.
    return [r for r in rows if r.from_planned >= now][:limit]


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

    return f"{departs} to {row.board_destination} - {platform} - {arrives} - {row.operator}"


def fetch(now: datetime) -> list[Notice]:
    home = os.environ["RTT_ORIGIN"]
    other = os.environ["RTT_DESTINATION"]
    morning_from = time.fromisoformat(os.environ["TRAINS_MORNING_FROM"])
    morning_to = time.fromisoformat(os.environ["TRAINS_MORNING_TO"])
    evening_from = time.fromisoformat(os.environ["TRAINS_EVENING_FROM"])
    evening_to = time.fromisoformat(os.environ["TRAINS_EVENING_TO"])

    access_token = _get_access_token()
    today = now.date()

    if now.hour < 12:
        today_rows = _next_trains(access_token, home, other, "departure", now, limit=2)
        window_from = datetime.combine(today, evening_from)
        window_to = datetime.combine(today, evening_to)
        preview_rows = _trains_in_window(access_token, home, other, "arrival", window_from, window_to)
        preview_date = today
    else:
        today_rows = _next_trains(access_token, home, other, "arrival", now, limit=2)
        tomorrow = today + timedelta(days=1)
        window_from = datetime.combine(tomorrow, morning_from)
        window_to = datetime.combine(tomorrow, morning_to)
        preview_rows = _trains_in_window(access_token, home, other, "departure", window_from, window_to)
        preview_date = tomorrow

    notices = [
        Notice(source=SOURCE, title=_format_line(r), date=today, section="today")
        for r in today_rows
    ]
    notices += [
        Notice(source=SOURCE, title=_format_line(r), date=preview_date, section="upcoming")
        for r in preview_rows
    ]
    return notices
