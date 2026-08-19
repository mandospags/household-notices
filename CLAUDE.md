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
(containerized, Home Assistant output, scheduler). Phase 1 (now) is manual
local runs; Telegram delivery has landed early, ahead of the rest of
Phase 2.

## Current state

Working Phase 1 digest with six sources:

- `digest.py` — daily-digest entry point. Loads `.env`, calls each
  service's `fetch(now)`, buckets notices into "today"/"upcoming", also
  calls `alert_status(now)` on the alert-capable services for a stateless
  "Alerts" block at the top (non-nominal statuses only — "clear"/"on time"
  filtered out); renders. This is a second, independent read of
  `alert_status()` alongside `fetch()` (some extra API calls) — it does not
  share state with or affect `alerts.py`'s diff cadence.
- `alerts.py` — poll-and-diff entry point for sudden items: one line per
  change vs last-seen state (`ALERTS_STATE_FILE`, JSON, gitignored),
  silence otherwise. Time-blind by design — cadence belongs to the invoker
  (manual now, cron in Phase 2). Kept as a sibling of `digest.py` sharing
  `services/`; don't merge the two cadences into one pipeline.
- `render.py` — plain-text digest output (Phase 1 stand-in for
  Telegram/HA).
- `services/base.py` — the `Notice` dataclass (the whole inter-module
  contract).
- `services/bins.py` — Test Valley bin collections (iTouchVision API).
- `services/air_quality.py` — DEFRA DAQI forecast RSS.
- `services/weather.py` — Met Office severe weather warnings RSS.
- `services/trains.py` — Realtime Trains commute rows (Andover ⇄ Waterloo);
  also alert-capable (watches the two usual commute trains for
  delay/cancellation/platform changes).
- `services/traffic.py` — TomTom live traffic for the Station run and
  School run (from Home before noon, back Home after); also reports
  roadworks/closures actually on the calculated route (via calculateRoute's
  own `sections`, not a bounding-box guess — see module docstring for why
  that distinction matters).
- `services/mass.py` — weekly Mass times for Burghclere (FSSPX district
  bulletin page), scraped (no API/RSS); table is date-specific per week,
  not a generic recurring schedule, so feast-day exceptions are already
  reflected. Needs a browser-like User-Agent (bare requests UA gets a 403).
  The page also throws frequent Cloudflare 520s (network/IP-dependent, not
  a UA thing — confirmed with curl too), so results are cached to
  `MASS_CACHE_FILE` (JSON, gitignored) and only re-fetched once the cache
  is over an hour old; a stale cache is served if a re-fetch fails outright,
  so `fetch` only raises with no cache at all to fall back on. Digest-only.

`weather`, `trains`, and `traffic` are alert-capable (`alert_status(now)`);
the others are digest-only.

Deliberately **not** built yet: any scheduler, Telegram/HA output. Don't
add infrastructure for these speculatively.

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
module to `ALERT_SERVICES` in `alerts.py`. The key must survive re-fetches
of the same underlying thing (see alerts.py's docstring for the diff
semantics).

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
