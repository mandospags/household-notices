# CLAUDE.md

Guidance for Claude Code and other agents working in this repo. This file is
the source of truth for **current state**; `SPEC.md` holds the original
requirements/intent plus the backlog of candidate sources, and is not kept
in sync with implementation details.

Personal solo tool, not an enterprise platform — prefer the simple, flat,
obvious approach over abstractions, frameworks, or process.

## What this is

A personal digest tool for one UK postcode: polls a handful of local
services (bins, air quality, weather warnings, trains so far) and prints a
daily digest of what's relevant today/upcoming. See `SPEC.md` for the full
original requirements, including the Phase 2 goal (containerized, Telegram +
Home Assistant outputs, scheduler). Phase 1 (now) is manual local runs with
print output.

## Current state

Working Phase 1 digest with four sources:

- `digest.py` — entry point. Loads `.env`, calls each service's
  `fetch(now)`, buckets notices into "today"/"upcoming", renders. Runs on
  demand; a future `alerts.py` (poll-and-diff one-liners for sudden items)
  will be a sibling entry point sharing `services/`, not merged into it.
- `render.py` — plain-text digest output (Phase 1 stand-in for
  Telegram/HA).
- `services/base.py` — the `Notice` dataclass (the whole inter-module
  contract).
- `services/bins.py` — Test Valley bin collections (iTouchVision API).
- `services/air_quality.py` — DEFRA DAQI forecast RSS.
- `services/weather.py` — Met Office severe weather warnings RSS.
- `services/trains.py` — Realtime Trains commute rows (Andover ⇄ Waterloo).

Deliberately **not** built yet: last-seen-state/diffing, the frequent
poll-and-diff cadence for sudden alerts, any scheduler, Telegram/HA output.
Don't add infrastructure for these speculatively.

## Run it

```
python digest.py
```

Setup: `python -m venv .venv`, install `requirements.txt`, copy
`.env.example` to `.env` and fill it in (only `RTT_REFRESH_TOKEN` is a real
credential; the rest are location/tuning values with working defaults).

No tests or linter yet — deliberate at this size. Verify changes by running
the digest.

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

**Write a real module docstring**: capture what an agent can't rediscover
cheaply — feed quirks, auth model, timezone behavior, things unconfirmed
because they haven't been observed live yet. See `services/bins.py` and
`services/trains.py` for the standard.

## Backlog

Lives in `SPEC.md` ("Backlog" section) — keep it there, remove items when
built. Completed work is just git history — no changelog file.

## Ways of working

- Keep this file's "Current state" section and SPEC.md's "Backlog" true —
  update them in the same change when facts drift. Don't rewrite the rest
  of `SPEC.md`.
- Keep secrets out of git: real values in `.env` (ignored), documented
  placeholders in `.env.example`.
- Small focused commits when asked to commit.
