# CLAUDE.md

Guidance for Claude Code and other agents working in this repo. This file is
the source of truth for **current state**; `SPEC.md` holds the original
requirements/intent plus the backlog of candidate sources, and is not kept
in sync with implementation details.

Personal solo tool, not an enterprise platform — prefer the simple, flat,
obvious approach over abstractions, frameworks, or process.

## What this is

A personal digest tool for one UK postcode: polls a handful of local
services (bins, air quality, weather warnings, trains so far) and prints
and Telegram-sends a daily digest of what's relevant today/upcoming. See
`SPEC.md` for the full original requirements, including the Phase 2 goal
(containerized, Home Assistant output, scheduler). Phase 1 (now) is the
implementation; Telegram delivery and scheduled execution have both landed
early, ahead of the rest of Phase 2 (containerization, HA output).

Scheduling lives outside this repo, in the homelab repo's
`uv1/scripts/household-notices-{digest,alerts}.{service,timer}` (systemd
timers, installed on `uv1`) — not tracked or run from here. `digest.py`
runs 06:00 daily plus 14:00 weekdays (Europe/London, DST-aware); `alerts.py`
runs every 10 min, 24/7 (widened from 06:00-20:00 on 2026-08-19 so
`services/powercuts.py`'s always-on check — see `ACTIVE_HOURS` below —
actually gets polled overnight; daytime-only services stay gated in code
either way). Both invoke `.venv/bin/python` directly with `WorkingDirectory`
set to this repo, so `.env` still loads the normal cwd-relative way — no
code changes were needed for this repo to become schedulable.

## Current state

Working Phase 1 digest with nine sources:

- `digest.py` — daily-digest entry point. Loads `.env`, calls each
  service's `fetch(now)`, buckets notices into "today"/"upcoming", also
  calls `alert_status(now)` on the alert-capable services for a stateless
  "Alerts" block at the top (non-nominal statuses only — "clear"/"on time"
  filtered out); renders. This is a second, independent read of
  `alert_status()` alongside `fetch()` (some extra API calls) — it does not
  share state with or affect `alerts.py`'s diff cadence.
- `alerts.py` — poll-and-diff entry point for sudden items: one line per
  change vs last-seen state (`ALERTS_STATE_FILE`, JSON, gitignored),
  silence otherwise, on both stdout and Telegram (sends the batch via
  `telegram.py` only when there's something to say). A newly-seen key that's
  already nominal (`is_notable()` false) records silently instead of
  printing `[new]` — a fresh key starting out fine isn't news. Relies on
  alert-capable services keeping `status` categorical (see trains.py/
  traffic.py) so a train or route stuck delayed/late alerts once at the
  transition, not on every live-estimate wobble. Time-blind by design —
  cadence belongs to the invoker (manual now, cron in Phase 2). Kept as a
  sibling of `digest.py` sharing `services/`; don't merge the two cadences
  into one pipeline. A service can set a module-level `ACTIVE_HOURS =
  (start_hour, end_hour)` (end exclusive) to only be polled/alerted inside
  that window — `trains`/`weather`/`traffic` are `(6, 20)` (daytime-only,
  no point paging about a train at 3am); `powercuts` has none (always-on).
  A service outside its window has its previous state carried forward
  unchanged (same mechanism as a failed fetch) rather than dropped, so an
  overnight run doesn't erase yesterday's daytime status and cause a bogus
  `[new]` when it wakes up.
- `render.py` — builds the plain-text digest as a string (`digest.py`
  prints it and also sends it via `telegram.py`). No bullet characters (`-`)
  in front of lines — they didn't render as bullets in Telegram anyway,
  just added noise. Every Today/Upcoming line leads with an emoji: a
  `source -> emoji` table (`_SOURCE_EMOJI`) covers sources with one icon;
  `traffic` is the one source with two (travel-time vs incident lines,
  split on `" incident:"` in the title, since both share `SOURCE="traffic"`)
  handled as a special case rather than a second table entry; `bins` sets
  `Notice.emoji` directly per-collection-type instead of going through
  either table, since all four bin types share `SOURCE="bins"` and only
  bins.py itself knows which type a given Notice is. An empty Alerts
  section is omitted entirely (no heading, no "No active alerts." filler)
  rather than printed every run.
- `telegram.py` — send-only delivery to the shared "Home" Telegram bot
  (`TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`, same token as sibling repo
  `homelab-mcp`, provisioned there — this repo only ever calls
  `sendMessage`, never `getUpdates`, which is what makes sharing the token
  safe). Plain text only, no `parse_mode` — avoids the HTML-escaping trap.
  No 4096-char handling yet; an oversized digest surfaces as a loud
  Telegram 400 rather than silently truncating. `digest.py` sends
  unconditionally on every run (no on/off toggle yet — deliberately kept
  simple, add one if that becomes a problem).
- `services/base.py` — the `Notice` dataclass (the whole inter-module
  contract) and `is_notable(status)`, the nominal/notable line shared by
  digest.py's Alerts block and alerts.py's new-key suppression. `Notice.emoji`
  is an optional per-notice override for render.py's display icon, used only
  by bins.py (see below) — everything else leaves it unset and gets its icon
  from render.py's source-keyed lookup instead.
- `services/bins.py` — Test Valley bin collections (iTouchVision API). Each
  Notice sets `emoji` per collection type (`BIN_EMOJI`: ⬛ household, 🟫
  recycling, 🟩 garden, ▪️ food) since all four types share `SOURCE="bins"`,
  so render.py's source-keyed lookup can't tell them apart on its own.
- `services/bank_holidays.py` — UK bank holidays (`BANK_HOLIDAYS_DIVISION`,
  keyless gov.uk JSON, cached like mass.py). Digest-only, `fetch()` capped
  to the next 14 days (the feed itself covers a year+ ahead). Also exposes
  `is_bank_holiday(date)`, imported directly by trains.py to skip holidays
  the same way it already skips weekends — fails open (`False`) if the
  check itself can't be answered, so an unrelated feed hiccup never breaks
  the trains watch.
- `services/forecast.py` — daily temp range + rain chance ("what to wear/
  bring"), Met Office Site-Specific Blended Probabilistic Forecast (BPF), a
  separate subscription from `weather.py`'s severe-warnings feed. Two
  digest lines: today (absolute "cold"/"warm"/"wet"/"dry" thresholds) and
  tomorrow (compared against today — "warmer and drier than today"). The
  API has no plain max/min/rain% fields — temperature comes back as 15
  percentile bands (uses the 50th/median) and rain as a probability per
  rainfall threshold (uses the ~0.25mm band, closest to the usual
  "measurable rain" cutoff — the `>0.0` "any trace" band badly over-reads).
  Both are 3-hourly series bucketed into calendar days client-side (by
  Europe/London date, not raw UTC). Cached like mass.py (~1hr TTL) to stay
  well under the free tier's 55 calls/day. Digest-only — a forecast drifts
  rather than flipping between discrete states, so there's nothing clean
  to diff for `alert_status()`. Reuses `TRAFFIC_HOME`'s geocoded point
  rather than a new coordinate var.
- `services/air_quality.py` — DEFRA DAQI forecast RSS.
- `services/weather.py` — Met Office severe weather warnings RSS.
- `services/trains.py` — Realtime Trains commute rows (Andover ⇄ Waterloo),
  weekdays only, and skips bank holidays too (see bank_holidays.py above).
  Each digest board is the usual commute train
  (`TRAINS_USUAL_MORNING`/`EVENING`) plus the nearest one either side
  (~3 total, including already-departed ones — useful context if the usual
  one's running late). Split at `TRAINS_MORNING_CUTOFF` (not noon — "the
  latest I'd still count as arriving today"): before it, today = morning
  board, upcoming = today's evening board; from it on, today = evening
  board, upcoming = tomorrow morning's board (skips to Monday over a
  weekend). Also alert-capable (watches the two usual commute trains for
  delay/cancellation/platform changes) — that watch needs the *exact*
  scheduled time, unlike the boards' nearest-match tolerance; its status is
  categorical (`on time`/`late`/`CANCELLED` + platform, no minutes) so
  alerts.py fires once per transition, not on every live-estimate wobble.
  The watch also layers in National Rail's own Live Departure Boards
  (LDBWS, `LDBWS_API_KEY` from raildata.org.uk) once a watched departure is
  within LDBWS's confirmed-live ~120min lookahead — worse-status-wins
  against RTT's category (never a downgrade), added after RTT's app lagged
  a station board showing a real cancellation on 2026-08-20. LDBWS can't
  replace RTT for the boards themselves (no schedule-query support, only
  "what's coming up now").
- `services/traffic.py` — TomTom live traffic for the Station run and
  School run (direction flips at noon: Home→Station/School before, back
  Home after; the printed line shows the actual direction, e.g.
  "Home → Station", though `alert_status` keys stay direction-free so the
  noon flip doesn't double-fire); also reports roadworks/closures actually
  on the calculated route (via calculateRoute's own `sections`, not a
  bounding-box guess — see module docstring for why that distinction
  matters).
- `services/mass.py` — weekly Mass times for Burghclere (FSSPX district
  bulletin page), scraped (no API/RSS); table is date-specific per week,
  not a generic recurring schedule, so feast-day exceptions are already
  reflected. Needs a browser-like User-Agent (bare requests UA gets a 403).
  The page also throws frequent Cloudflare 520s (network/IP-dependent, not
  a UA thing — confirmed with curl too), so results are cached to
  `MASS_CACHE_FILE` (JSON, gitignored) and only re-fetched once the cache
  is over an hour old; a stale cache is served if a re-fetch fails outright,
  so `fetch` only raises with no cache at all to fall back on. Digest-only.
- `services/powercuts.py` — SSEN Distribution power cuts, via the API
  behind their consumer PowerTrack tool (found through the CKAN dataset
  metadata, not the map tool itself; needs a browser User-Agent like
  mass.py, same 403-without-one gotcha). No server-side postcode filter —
  fetches the full GB-wide fault list every call and filters client-side on
  exact match against `HOME_POSTCODE` (full postcode or bare outward code,
  not a prefix match). One feed covers planned and unplanned outages,
  distinguished by `type`/`jobStatus`. Alert-only (no `fetch()`/digest
  notices yet — a deliberate deviation from the usual pattern, see the
  module docstring) and always-on (no `ACTIVE_HOURS`). SSEN's feed drops a
  restored fault within about a minute — faster than any sane poll cadence
  — so alerts.py's existing "disappearance is silent" rule means a power
  cut alerts once at onset with no explicit "restored" line; accepted as
  consistent with trains.py's daily key rollover rather than adding state
  to change it.

`weather`, `trains`, `traffic`, and `powercuts` are alert-capable
(`alert_status(now)`); the others are digest-only.

Deliberately **not** built yet: containerization, Home Assistant output.
Don't add infrastructure for these speculatively.

## Run it

```
python digest.py    # the daily/on-demand digest
python alerts.py    # one line per change since last run, else "no changes"
```

Setup: `python -m venv .venv`, install `requirements.txt`, copy
`.env.example` to `.env` and fill it in (only `RTT_REFRESH_TOKEN` is a real
credential; the rest are location/tuning values with working defaults).

No tests or linter yet — deliberate at this size. Verify changes by running
the entry point you touched (for alerts.py, run twice: changes then
"no changes"; hand-edit the state file to simulate a change).

## Adding a source

One consistent pattern, no registry or base class:

1. New module `services/<name>.py` with a module docstring, `SOURCE = "<name>"`,
   and `fetch(now: datetime) -> list[Notice]` (ignore `now` if unneeded).
2. Add the module to `SERVICES` in `digest.py`.
3. Config via `os.environ` — add documented entries to `.env.example`
   (and `.env`). Never hardcode postcode/UPRN/tokens/region codes.
4. Raise on failure — the digest catches per-service exceptions and keeps
   going, so no defensive try/except inside services.

By default a `Notice` is bucketed by `date` (today vs upcoming, anything
past dropped); set `section=` explicitly only when that's wrong (trains does
this for its preview rows).

To make a source alert-capable, additionally expose
`alert_status(now) -> dict` mapping a stable key (`"<SOURCE>:..."`) to
`{"status": comparable string, "summary": printable line}`, and add the
module to `ALERT_SERVICES` in `alerts.py` (and usually `digest.py` too, for
free Alerts-block visibility — it reads its own `ALERT_SERVICES` list
independently). The key must survive re-fetches of the same underlying
thing (see alerts.py's docstring for the diff semantics). Keep `status` a
coarse category (e.g. `"on time"`/`"late"`, not an exact delay in minutes)
— see trains.py/traffic.py — so alerts.py fires once per real transition
instead of on every poll's live-value wobble; put the precise detail in
`summary` instead, where it doesn't affect the diff.

To scope an alert-capable source to particular hours (e.g. it'd be noise
overnight), set a module-level `ACTIVE_HOURS = (start_hour, end_hour)` (end
exclusive) — `alerts.py` skips it outside that window and carries its last
state forward untouched. Omit it (or leave `None`) for something worth
knowing about any time, like a power cut.

`HOME_POSTCODE` is the shared postcode config — reuse it rather than adding
a new per-source postcode var. It is not a full "one var updates
everything if we move" story though: several other configs (`TVBC_UPRN`,
`DAQI_STATION`, `METOFFICE_REGION`, `RTT_ORIGIN`, `TRAFFIC_HOME/STATION/
SCHOOL`) are derived from location but require a manual re-derivation step
on a move — see the checklist at the bottom of `.env.example`.

**Write a real module docstring**: capture what an agent can't rediscover
cheaply — feed quirks, auth model, timezone behavior, things unconfirmed
because they haven't been observed live yet. See `services/bins.py` and
`services/trains.py` for the standard.

## Backlog

Lives in `SPEC.md` ("Backlog" section) — keep it there, remove items when
built. Completed work is just git history — no changelog file.

## Ways of working

- "Lets discuss" means discuss only — no code/file edits until the user
  explicitly says to proceed.
- For any non-trivial change (new behavior, new files, new env vars/config,
  anything beyond a one-line fix) propose the approach first and get a
  go-ahead before writing code — don't jump straight from "here's a
  problem" to an implementation. Trivial fixes (typos, obvious one-liners)
  don't need this.
- Keep this file's "Current state" section and SPEC.md's "Backlog" true —
  update them in the same change when facts drift. Don't rewrite the rest
  of `SPEC.md`.
- Keep secrets out of git: real values in `.env` (ignored), documented
  placeholders in `.env.example`.
- Small focused commits when asked to commit.
