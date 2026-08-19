# Local Status & Alerts Digest — Project Spec

> **Status note:** this document holds the original requirements/intent and
> the living Backlog section at the bottom. The rest is deliberately not kept
> in sync with implementation — for current state (what's built, how sources
> actually work) see `CLAUDE.md`.

## Overview
A personal tool that monitors a handful of local services and conditions
(for a specific UK postcode/address) and surfaces what's relevant, so I don't
have to manually check multiple council/utility/transport websites.

## Goals
- Reduce manual checking of scattered local sources to one daily digest plus
  occasional urgent notifications.
- Only surface what's new or relevant — not a firehose of raw data.
- Start as simple local scripts I can run and iterate on manually, with a
  clear path to unattended scheduled operation later.
- Sources should be addable one at a time — the tool should have a simple,
  consistent way to plug in a new source without restructuring what's
  already working. Not all sources need to be built at once; start with
  one or two and expand incrementally.

## Sources to cover
Two categories, based on how the underlying information behaves. Feasibility
(API vs scrape vs opt-in push) is noted where already known — this needs
confirming/testing per source, and may change the approach for that source.

### Planned / known-ahead-of-time items
(things with a date attached — feed the daily digest)

- **Bin/refuse collection** — Test Valley Borough Council. Address-based.
  Currently only found as a one-time `.ics` calendar export, not a live
  feed — may just need annual re-download rather than polling.
- **Train disruptions (planned engineering works)** — National Rail /
  South Western Railway. `https://www.nationalrail.co.uk/service_disruptions/`
  and `https://www.southwesternrailway.com/travel-information/service-disruption`
- **Road closures / roadworks** — DfT Street Manager (the underlying open
  data feed, likely has a real API):
  `https://www.gov.uk/guidance/find-and-use-roadworks-data`
  Also Hampshire County Council's One Network map and public notices
  (statutory closure orders) as HTML-only alternatives:
  `https://www.hants.gov.uk/community/publicnotices`
- **New local planning applications** — Test Valley planning portal,
  searchable by postcode/address:
  `https://view-applications.testvalley.gov.uk/online-applications/`

### Sudden / unplanned alerts
(things that can happen without warning — feed the frequent poll-and-diff check)

- **Power outages** — SSEN (the actual electricity network operator for
  Hampshire, not Octopus which is just the supplier). Postcode-based
  checker: `https://www.ssen.co.uk/power-cuts/`
- **Water supply issues** — Southern Water service status by postcode:
  `https://www.southernwater.co.uk/service-status`
- **Mobile/broadband network status** — Vodafone (4G broadband).
  Postcode-based status checker:
  `https://www.vodafone.co.uk/network/status-checker`
- **Weather warnings** — Met Office Weather DataHub (has a real API,
  requires a free key): `https://datahub.metoffice.gov.uk/`
- **Flood alerts/warnings** — Environment Agency Flood Monitoring API
  (free, no registration, real API): 
  `https://environment.data.gov.uk/flood-monitoring/doc/reference`

### Also identified, not yet categorized/committed
- **Hampshire Alert** — police/community messaging system. This is
  opt-in push (email/text), not something to poll — may just mean
  registering directly rather than building anything:
  `https://www.hampshirealert.co.uk/`
- **National Highways** — for A303/A34 specifically, if relevant to
  regular routes rather than local roads:
  `https://nationalhighways.co.uk/travel-updates/`

## Behavior
Two distinct cadences:

1. **Daily digest** — fires once in the evening, unconditionally (even if
   there's nothing notable, so it always includes a baseline weather
   summary for the next couple of days). Includes anything from the
   "planned" category relevant to today, tomorrow, or the day after —
   nothing further out.

2. **Frequent check** — runs multiple times a day, only for the "sudden"
   category. Compares current state to last-seen state, and only sends a
   notification when something has actually changed (no repeat/no-change
   noise).

## Outputs
- Telegram message (for both the daily digest and any urgent alert)
- Home Assistant dashboard — current status should also be visible/glanceable
  there, not just pushed via Telegram

Exact delivery mechanism is not the focus of this phase — see setup notes
below.

## Development approach
- **Phase 1 (now):** standalone scripts, run manually on my laptop (or
  anywhere), no Telegram/HA integration yet — focus on getting each source
  working and the digest/alert logic right, one source at a time.
  Notifications can just print or log locally for now.
- **Phase 2 (later):** move into the homelab as its own small containerized
  service (its own project/stack, not part of any existing service), with
  Telegram and Home Assistant integration added, and scheduled execution
  instead of manual runs.

Design Phase 1 so that moving to Phase 2 is a relocation/config change, not
a rewrite — e.g. configuration (postcode, API keys/tokens, notification
targets) should be externalized from the start rather than hardcoded, and
whatever tracks "last seen state" for diffing should be easy to relocate
to persistent storage later.

## Non-goals for now
- No implementation/library choices yet — that's for the next stage of
  discussion.
- No production deployment, container, or scheduler setup yet — that's
  Phase 2.
- Not trying to cover every possible local source or build all sources at
  once — starting with one or two from the list above and expanding
  incrementally.

## Backlog

(Living section — candidate next sources, none researched yet; remove when
built. Everything else in this doc is frozen original intent.)

- Road closures/roadworks — DfT Street Manager open data (real API).
- Met Office day-to-day forecast (DataHub, separate from the warnings feed
  already built) — would fill the "baseline weather summary" requirement,
  currently uncovered.
- Test Valley planning applications — HTML scrape, no known API.
- St Michael's School, Burghclere — upcoming events.
- 1962 Ordo / today's liturgical calendar day.

Deferred trains ideas (waiting on real commute experience — don't implement
unprompted): scheduled digest runs (e.g. 3pm/4pm); anchoring on usual
departures instead of "next 2 from now"; ±30 min window showing 3 trains.

Telegram output: both digest.py and alerts.py now send via `telegram.py` (send-only
`sendMessage`, shared "Home" bot token also used by homelab-mcp — own process, not shared
code; never polls `getUpdates`, so it's safe to share). Still open: splitting digest → group
chat vs alerts → DM once there's more than one recipient (both currently go to the same
chat). Full design (including the separate, isolated command-bot for homelab queries) lives
in the homelab repo's `docs/specs/roadmap.md`, "Telegram Bot (shared platform)" section,
since it spans repos.