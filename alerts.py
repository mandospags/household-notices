"""Poll-and-diff alerts for sudden items: one line per change, silence
otherwise.

Purely stateful, deliberately time-blind: each run compares the current
fetch against whatever ALERTS_STATE_FILE recorded last run, however long ago
that was. Cadence belongs to the invoker (manual now, cron/scheduler in
Phase 2).

Contract with services: an alert-capable service exposes
alert_status(now) -> dict mapping a stable key (prefixed "<SOURCE>:") to
{"status": <comparable string>, "summary": <printable line>}. A key not seen
before prints as [new]; a key whose status changed prints its summary; a key
that disappears is dropped silently (train keys roll over daily by design,
so disappearance is not treated as an event). If a service errors, its
previous keys are carried forward untouched so a transient failure doesn't
re-alert everything as [new] on recovery.
"""

import json
import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from services import traffic, trains, weather

ALERT_SERVICES = [trains, weather, traffic]


def main() -> None:
    load_dotenv()
    now = datetime.now()

    state_path = Path(os.environ["ALERTS_STATE_FILE"])
    previous: dict[str, str] = (
        json.loads(state_path.read_text()) if state_path.exists() else {}
    )

    current: dict[str, dict] = {}
    failed_sources = []
    for service in ALERT_SERVICES:
        try:
            current.update(service.alert_status(now))
        except Exception as exc:
            print(f"[{service.SOURCE}] failed: {exc}")
            failed_sources.append(service.SOURCE)

    for key, status in previous.items():
        if key.split(":", 1)[0] in failed_sources:
            current[key] = {"status": status, "summary": ""}

    changes = []
    for key, entry in current.items():
        old = previous.get(key)
        if old is None:
            changes.append(f"[new] {entry['summary']}")
        elif old != entry["status"]:
            changes.append(entry["summary"])

    if changes:
        for line in changes:
            print(line)
    else:
        print(f"{now:%H:%M} - no changes.")

    state_path.write_text(
        json.dumps({k: v["status"] for k, v in current.items()}, indent=2)
    )


if __name__ == "__main__":
    main()
