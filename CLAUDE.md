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

Working Phase 1 digest with ten sources. **Each module's docstring is the
detail** — feed quirks, auth model, why a thing is the way it is. This
section is the index, not a second copy of them; read the docstring before
changing a module.

| File | What it is |
| --- | --- |
| `digest.py` | Daily-digest entry point: fetch all sources, bucket today/upcoming, render, print, Telegram. Also owns `_merge_feasts()`. |
| `alerts.py` | Poll-and-diff entry point for sudden items. Time-blind by design; a sibling of `digest.py`, not to be merged with it. |
| `render.py` | Plain-text digest layout and the per-line emoji rules. |
| `telegram.py` | Send-only delivery to the shared "Home" bot (`sendMessage` only, never `getUpdates`). |
| `services/base.py` | The `Notice` dataclass, `is_notable()`, and the shared `BROWSER_UA`/`TIMEOUT` constants. |
| `services/cache.py` | The shared JSON-file cache (TTL, stale-serve-on-failure) used by mass/forecast/feasts/bank_holidays. |
| `services/bins.py` | Test Valley bin collections (iTouchVision). Sets `Notice.emoji` per collection type. |
| `services/air_quality.py` | DEFRA DAQI forecast RSS. |
| `services/weather.py` | Met Office severe warnings (NSWWS Public API), filtered by `WEATHER_HOME_COUNTY`. Alert-capable. |
| `services/forecast.py` | Met Office BPF daily temp range + rain chance — today, and tomorrow-vs-today. Separate key from `weather.py`. |
| `services/trains.py` | Realtime Trains commute boards, weekdays only, skipping bank holidays; LDBWS layered onto the watch. Alert-capable. |
| `services/traffic.py` | TomTom live traffic for the Station and School runs, plus on-route incidents. Alert-capable. |
| `services/mass.py` | Weekly Mass times for Burghclere, scraped from the FSSPX bulletin page. Flaky upstream (Cloudflare 520s) — leans on the cache. |
| `services/feasts.py` | 1962 Roman calendar feast days (Missale Meum API), `rank <= 2`. Never renders its own line — merged into mass.py's. |
| `services/bank_holidays.py` | UK bank holidays (gov.uk JSON). Also exposes `is_bank_holiday()`, used by trains.py. |
| `services/powercuts.py` | SSEN power cuts, filtered on `HOME_POSTCODE`. Alert-only (no `fetch()`) and always-on. |

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
5. If the source needs caching (slow, flaky, or rate-limited), wrap the live
   fetch in `services/cache.py`'s `cached()` rather than writing another
   TTL/stale-serve loop. It stores plain JSON, so convert to/from `Notice`
   in the service itself.

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
`DAQI_STATION`, `WEATHER_HOME_COUNTY`, `RTT_ORIGIN`, `TRAFFIC_HOME/STATION/
SCHOOL`) are derived from location but require a manual re-derivation step
on a move — see the checklist at the bottom of `.env.example`.

**Write a real module docstring.** This matters more than it looks: the
docstrings are the only detailed record, and the "Current state" table above
is deliberately just an index pointing at them. Capture what an agent can't
rediscover cheaply — feed quirks, auth model, timezone behavior, things
unconfirmed because they haven't been observed live yet. See
`services/bins.py` and `services/trains.py` for the standard. Don't restate
a docstring in this file; add a table row and leave it at that.

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
- Keep this file's "Current state" table and SPEC.md's "Backlog" true —
  update them in the same change when facts drift. Detail belongs in the
  module docstring, not here. Don't rewrite the rest of `SPEC.md`.
- Keep secrets out of git: real values in `.env` (ignored), documented
  placeholders in `.env.example`.
- Small focused commits when asked to commit.
