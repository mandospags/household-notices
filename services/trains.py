"""Realtime Trains (RTT) commute rows for Andover <-> London Waterloo.

https://api-portal.rtt.io - free tier, bearer auth. A long-life refresh
token (RTT_REFRESH_TOKEN) is exchanged for a short-life access token via
/api/get_access_token on every run.

Weekdays only, and skips bank holidays too (via bank_holidays.is_bank_holiday,
fails open to False if that check itself can't be answered) - fetch() and
alert_status() both return empty (no commute to watch), and the tomorrow-
preview board (_next_commute_day) skips a holiday Monday the same way it
already skips weekends.

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

Querying direction matters: /rtt/location only gives *live* times (and
platform) for the station you queried - the other end's `origin`/
`destination` entries carry only a scheduled time, confirmed live to have
no locationMetadata/platform at all. The morning board queries *at
Andover* (filterTo Waterloo) since that's also where you board, so one
call gives everything useful. The evening board's "home end" is Andover's
*arrival*, not where you board - an early version queried only that one
call (Andover, filterFrom Waterloo) for both the arrival time and,
mistakenly, the platform/live-departure-delay too, which surfaced
Andover's arrival platform in a slot meant to answer "which platform do I
board at Waterloo" (a real, reported mismatch - Waterloo's actual
platform is a different number to Andover's). `_board_around` now issues a
second, departure-focus query at Waterloo for the same window and merges
its real platform/live-estimate into the arrival-focus rows, matched by
scheduled departure time - the extra call is worth it since a wrong
boarding platform is actively misleading, not just imprecise.

alert_status() (for alerts.py) watches the two *usual* commute trains
(TRAINS_USUAL_MORNING from home, TRAINS_USUAL_EVENING back) rather than
"next from now" - alerts need a stable subject to diff, and these are
queried at their departing station so the live time and platform are the
departure's own (evening platform = Waterloo's, which is the useful one).
Keyed by scheduleMetadata.uniqueIdentity (schedule identity + departure
date), so keys roll over naturally each day.

Layered on top of RTT, once a watched departure is within
LDBWS_LOOKAHEAD_MIN: National Rail's own Live Departure Boards (LDBWS,
`api1.raildata.org.uk`, `LDBWS_API_KEY` from raildata.org.uk - the old
self-service realtime.nationalrail.co.uk token portal was retired in
2026). Motivated by a real miss on 2026-08-20: the station board showed a
service CANCELLED before RTT's own app reflected it - LDBWS is the direct
Darwin-backed feed boards are built from, RTT is a third party sitting on
top of it, so it's plausible for RTT to lag. `GetDepartureBoard` is a live
"what's coming up now" view, not a schedule query - confirmed live it has
a fixed ~120min lookahead (tested up to a 600min `timeWindow` param with no
effect), so it cannot replace RTT for fetch()'s multi-day board-building,
only supplement the same-day watch once a departure is close. Same CRS
codes as RTT_ORIGIN/RTT_DESTINATION (confirmed "ADV"/"WAT" match exactly),
so no new location config needed. Worse-status-wins: LDBWS's own category (derived the same coarse way as
RTT's - CANCELLED, or late if its `etd` implies a delay past
TRAINS_DELAY_ALERT_MIN, else on time) is compared against RTT's, and the
worse of the two is kept - never a downgrade (RTT already saying "late"
stays "late" even if LDBWS's `etd` briefly reads "On time"). The whole
point of adding this second source is that it can reflect reality before
RTT does, and that applies to delays exactly as much as cancellations, so
both escalate. LDBWS's free-text `delayReason` (e.g. "delayed by
trespassers on the railway" - richer than anything RTT exposes) is
appended to the summary whenever present, regardless of category, since
summary isn't diffed. Platform stays RTT's throughout - not worth two
sources fighting over half of the status string. A failed/empty LDBWS
lookup is swallowed rather than raised, since it's a best-effort layer on
top of the primary RTT-based status, not a required source (unlike a
genuine fetch()/alert_status() failure, which is still expected to raise
per the module contract).

LDBWS also returns a `length` field (coach count) - confirmed live, e.g.
"4" on an actual service - relevant to the separate short-formation/
standing-room question raised earlier, but `length: 0` appears to mean
"not yet known" rather than "zero coaches" (unconfirmed how early it
populates), so that's tracked as a SPEC.md backlog item, not built here.

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

from . import bank_holidays
from .base import Notice

API_BASE = "https://data.rtt.io"
LDBWS_BASE = "https://api1.raildata.org.uk/1010-live-departure-board-dep1_2/LDBWS/api/20220120"

SOURCE = "trains"
ACTIVE_HOURS = (6, 20)  # daytime only - see alerts.py's ACTIVE_HOURS gate

# GetDepartureBoard's confirmed-live fixed lookahead - see module docstring.
LDBWS_LOOKAHEAD = timedelta(minutes=120)

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

    if focus == "arrival":
        # An arrival-focus query (`other`, i.e. wherever you actually board)
        # is queried at `home`, and RTT's /rtt/location only returns live
        # data (platform, delay) for the *queried* station - confirmed live
        # that the `origin` entry it returns for the other end carries only
        # a scheduled time, nothing else. So `row.platform`/`from_estimate`
        # from the arrival-focus parse above are Andover's own arrival
        # platform/(always None) - not useful (you don't board at the
        # arrival end) and not even live for the departure. Fetch a second,
        # departure-focus board queried at `other` (the boarding station)
        # for the same window, and overwrite platform/from_estimate with
        # its real, live values - matched by scheduled departure time
        # (exact, same service). Leaves a row's Andover-side fields
        # (arrival_planned/arrival_estimate/arrival_name) untouched - those
        # are still correctly Andover's own and still useful ("when do I
        # get home").
        boarding = {
            row.from_planned: row
            for row in _trains_in_window(
                access_token, other, home, "departure", anchor - TRAINS_SEARCH_WINDOW, anchor + TRAINS_SEARCH_WINDOW
            )
        }
        for row in board:
            live = boarding.get(row.from_planned)
            if live is not None:
                row.platform = live.platform
                row.from_estimate = live.from_estimate

    return board


def _next_commute_day(d: date) -> date:
    while d.weekday() >= 5 or bank_holidays.is_bank_holiday(d):
        d += timedelta(days=1)
    return d


def _format_line(row: TrainRow) -> str:
    # Ordered as "where to go and what to look for" (departure time,
    # station, platform, destination) first, "when you get there" second -
    # the departure half is the actionable part, so it leads.
    departs = f"{row.from_planned:%H:%M}"
    if row.from_estimate and row.from_estimate != row.from_planned:
        departs += f" (exp {row.from_estimate:%H:%M})"
    platform = f"plat {row.platform}" if row.platform else "plat ?"
    header = f"{departs} {row.from_name} {platform} to {row.board_destination}"

    if row.is_cancelled:
        return f"{header}: CANCELLED"

    if row.arrival_planned is None:
        arrives = "arr unknown"
    else:
        arrives = f"arr {row.arrival_planned:%H:%M}"
        if row.arrival_name != row.board_destination:
            arrives = f"arr {row.arrival_name} {row.arrival_planned:%H:%M}"
        if row.arrival_estimate and row.arrival_estimate != row.arrival_planned:
            arrives += f" (exp {row.arrival_estimate:%H:%M})"

    return f"{header}, {arrives}"


_CATEGORY_RANK = {"on time": 0, "late": 1, "CANCELLED": 2}


def _ldbws_entry(depart: str, dest: str, planned: datetime) -> dict | None:
    """The LDBWS board is a live "what's coming up" view with a fixed
    ~120min lookahead (see module docstring), not a schedule query - so a
    planned departure outside that window simply won't be present, and
    that's expected/normal, not a failure to raise on."""
    resp = requests.get(
        f"{LDBWS_BASE}/GetDepartureBoard/{depart}",
        headers={
            "x-apikey": os.environ["LDBWS_API_KEY"],
            # Bare/default requests UA gets a 403 (Cloudflare bot-block) -
            # same gotcha as mass.py/powercuts.py, same fix.
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
        },
        params={"filterCrs": dest, "filterType": "to"},
        timeout=15,
    )
    resp.raise_for_status()
    for svc in resp.json().get("trainServices") or []:
        if svc["std"] == f"{planned:%H:%M}":
            return svc
    return None


def _ldbws_category(
    entry: dict, planned: datetime, threshold_min: int
) -> tuple[str, str | None] | None:
    """Derive the same coarse category RTT's own logic uses, from LDBWS's
    fields, so the two are comparable for worse-status-wins in
    _watched_status. Returns None for an `etd` value that isn't one of
    Darwin's known forms ("On time" / "Delayed" / an HH:MM estimate) -
    deliberately not escalating on a string we haven't seen and don't
    understand, rather than guessing."""
    if entry.get("isCancelled"):
        return "CANCELLED", None
    etd = entry.get("etd")
    if etd == "On time":
        return "on time", None
    if etd == "Delayed":
        return "late", None
    try:
        estimate = datetime.combine(planned.date(), time.fromisoformat(etd))
    except (TypeError, ValueError):
        return None
    delay_min = int((estimate - planned).total_seconds() // 60)
    if delay_min >= threshold_min:
        return "late", f"exp {estimate:%H:%M} (+{delay_min}m) via LDBWS"
    return "on time", None


def _watched_status(
    access_token: str,
    depart: str,
    dest: str,
    planned: datetime,
    threshold_min: int,
    now: datetime,
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
            category = "CANCELLED"
            detail = "CANCELLED"
        else:
            delay_min = 0
            if row.from_estimate:
                delay_min = int((row.from_estimate - row.from_planned).total_seconds() // 60)
            if delay_min >= threshold_min:
                category = "late"
                detail = f"exp {row.from_estimate:%H:%M} (+{delay_min}m)"
            else:
                category = "on time"
                detail = "on time"

        # Once within LDBWS's lookahead, let it escalate (never downgrade)
        # the RTT-derived category - see module docstring for why. A failed
        # or empty lookup just means "no second opinion available", not an
        # error.
        reason = None
        if timedelta(0) <= planned - now <= LDBWS_LOOKAHEAD:
            try:
                entry = _ldbws_entry(depart, dest, planned)
            except Exception:
                entry = None
            if entry is not None:
                reason = entry.get("delayReason")
                ldbws_result = _ldbws_category(entry, planned, threshold_min)
                if ldbws_result is not None:
                    ldbws_category, ldbws_detail = ldbws_result
                    if _CATEGORY_RANK[ldbws_category] > _CATEGORY_RANK[category]:
                        category = ldbws_category
                        detail = ldbws_detail or ldbws_category

        # status is deliberately coarse (category + platform, no minutes) so
        # alerts.py's diff fires once per category/platform change, not on
        # every minute of live-estimate wobble while a train stays late -
        # the exact delay still shows up in summary, just isn't compared.
        status = f"{category}, plat {row.platform or '?'}"
        # uniqueIdentity is e.g. "gb-nr:L79428:2026-08-18" - schedule identity
        # plus departure date, so keys roll over naturally each day.
        key = f"{SOURCE}:{svc['scheduleMetadata']['uniqueIdentity']}"
        summary = f"{planned:%H:%M} {home_name} plat {row.platform or '?'} to {row.board_destination}: {detail}"
        if reason:
            summary += f" - {reason}"
        return key, {"status": status, "summary": summary}

    # The usual train not being in the timetable at all is itself an alert
    # (engineering works, weekend timetable, short-notice removal).
    key = f"{SOURCE}:{depart}-{dest}:{planned:%Y-%m-%d-%H%M}"
    summary = f"{planned:%H:%M} {depart} to {dest}: not in today's timetable"
    return key, {"status": "not in timetable", "summary": summary}


def alert_status(now: datetime) -> dict[str, dict]:
    if now.weekday() >= 5 or bank_holidays.is_bank_holiday(now.date()):
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
            access_token, depart, dest, datetime.combine(today, planned_time), threshold_min, now
        )
        statuses[key] = entry
    return statuses


def fetch(now: datetime) -> list[Notice]:
    if now.weekday() >= 5 or bank_holidays.is_bank_holiday(now.date()):
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
        next_day = _next_commute_day(today + timedelta(days=1))
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
