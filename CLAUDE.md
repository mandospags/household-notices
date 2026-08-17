# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

Pre-implementation. No source code, dependency manifest, or test setup exists
yet — only `SPEC.md` (full requirements) and this file. There are no build,
lint, or test commands to run because there is nothing to build yet.

`.gitignore` (`.venv/`, `__pycache__/`, `*.pyc`, `*.db`, `*.sqlite`) implies a
Python implementation with local SQLite state, but this isn't confirmed by
any committed code — verify against what's actually in the repo before
assuming it.

## What this project is

See SPEC.md for full requirements. In short: a personal digest tool that
polls/scrapes a handful of local UK services (bin collection, rail
disruptions, roadworks, planning applications, power/water/mobile outages,
weather and flood alerts) for one postcode, and surfaces only what's new or
relevant via Telegram and a Home Assistant dashboard.

Key design constraints from the spec, worth keeping in mind for any
implementation work:

- **Two cadences, not one loop**: a daily digest (planned/dated items —
  bins, engineering works, roadworks, planning apps — for today/tomorrow/day
  after) and a separate frequent poll-and-diff check (sudden items — power,
  water, mobile, weather, flood — notify only on state change, no
  repeat-noise).
- **Sources are pluggable one at a time** — the tool should have one
  consistent way to add a new source without restructuring existing ones.
  Not all sources need to exist at once.
- **Phase 1 (now)**: standalone scripts run manually, no Telegram/HA
  integration — notifications can just print/log locally.
- **Phase 2 (later)**: containerized service with Telegram + Home Assistant
  outputs and a scheduler. Phase 1 should be built so Phase 2 is a
  relocation/config change, not a rewrite: externalize config (postcode,
  API keys/tokens, notification targets) via `.env` from the start, and
  keep "last seen state" (for diffing) in a form that's easy to relocate to
  persistent storage later.

Config via `.env` (see `.env.example` once it exists). Do not hardcode
postcode/tokens/paths.