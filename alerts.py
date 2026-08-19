"""Poll-and-diff alerts for sudden items: one line per change, silence
otherwise. Non-empty runs also send the batch to Telegram (same "Home" bot
as digest.py, via telegram.py) - empty runs neither print a Telegram
message nor send one, keeping the silent-otherwise contract on both
channels.

Purely stateful, deliberately time-blind: each run compares the current
fetch against whatever ALERTS_STATE_FILE recorded last run, however long ago
that was. Cadence belongs to the invoker (manual now, cron/scheduler in
Phase 2).

Contract with services: an alert-capable service exposes
alert_status(now) -> dict mapping a stable key (prefixed "<SOURCE>:") to
{"status": <comparable string>, "summary": <printable line>}. A key not seen
before and already nominal (is_notable() false, e.g. "on time"/"clear")
records silently rather than printing [new] - a fresh key starting out fine
isn't news, only one starting out already delayed/cancelled/etc is. A key
whose status changed prints its summary; a key that disappears is dropped
silently (train keys roll over daily by design, so disappearance is not
treated as an event - powercuts.py relies on this too: SSEN's feed drops a
restored fault within about a minute, well inside our poll cadence, so a
power cut alerts once at onset and then just goes quiet rather than getting
an explicit "restored" line). Services are expected to keep their compared
`status` coarse/categorical (see trains.py and traffic.py) so a train or
route sitting continuously late/delayed alerts once at the transition, not
on every live-estimate wobble in between. If a service errors, its previous
keys are carried forward untouched so a transient failure doesn't re-alert
everything as [new] on recovery.

A service may optionally set a module-level ACTIVE_HOURS = (start_hour,
end_hour) (end exclusive) to only be polled/alerted within that window -
e.g. trains/traffic/weather are daytime-only (6-20) so a 10-min cadence
doesn't page overnight about things that don't matter then. Omit
ACTIVE_HOURS (or leave it None) for an always-on service like powercuts,
which is exactly the kind of thing you want to hear about at 3am. This is
the whole mechanism for scoping a new alert source to particular hours -
no scheduler/timer changes needed for that.
"""

import json
import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

import telegram
from services import powercuts, traffic, trains, weather
from services.base import is_notable

ALERT_SERVICES = [trains, weather, traffic, powercuts]


def _active(service, now: datetime) -> bool:
    active_hours = getattr(service, "ACTIVE_HOURS", None)
    if active_hours is None:
        return True
    start, end = active_hours
    return start <= now.hour < end


def main() -> None:
    load_dotenv()
    now = datetime.now()

    state_path = Path(os.environ["ALERTS_STATE_FILE"])
    previous: dict[str, str] = (
        json.loads(state_path.read_text()) if state_path.exists() else {}
    )

    current: dict[str, dict] = {}
    carry_forward_sources = []
    for service in ALERT_SERVICES:
        if not _active(service, now):
            # Outside this service's window - leave its last-known state
            # alone (see carry-forward below) rather than dropping it, so
            # the state file doesn't lose it over the inactive stretch and
            # its next active run doesn't misread a real "no change" as
            # [new].
            carry_forward_sources.append(service.SOURCE)
            continue
        try:
            current.update(service.alert_status(now))
        except Exception as exc:
            print(f"[{service.SOURCE}] failed: {exc}")
            carry_forward_sources.append(service.SOURCE)

    for key, status in previous.items():
        if key.split(":", 1)[0] in carry_forward_sources:
            current[key] = {"status": status, "summary": ""}

    changes = []
    for key, entry in current.items():
        old = previous.get(key)
        if old is None:
            if is_notable(entry["status"]):
                changes.append(f"[new] {entry['summary']}")
        elif old != entry["status"]:
            changes.append(entry["summary"])

    if changes:
        text = "\n".join(changes)
        for line in changes:
            print(line)
        try:
            telegram.send(text)
        except Exception as exc:
            print(f"[telegram] failed: {exc}")
    else:
        print(f"{now:%H:%M} - no changes.")

    state_path.write_text(
        json.dumps({k: v["status"] for k, v in current.items()}, indent=2)
    )


if __name__ == "__main__":
    main()
