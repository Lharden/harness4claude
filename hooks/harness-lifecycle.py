#!/usr/bin/env python
from __future__ import annotations

import json
import os
import sqlite3
import sys
import argparse
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", default=None)
    args = parser.parse_args()
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        payload = {}
    event = str(args.event or payload.get("hook_event_name") or os.environ.get("CLAUDE_HOOK_EVENT") or "Lifecycle")
    root = Path(os.environ.get("HARNESS_DIR") or Path.home() / ".claude" / "harness")
    root.mkdir(parents=True, exist_ok=True)
    heartbeats = root / "heartbeats"
    heartbeats.mkdir(parents=True, exist_ok=True)
    (heartbeats / event).write_text(str(datetime.now(timezone.utc).timestamp()), encoding="utf-8")
    database = root / "lifecycle.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS lifecycle_events(id INTEGER PRIMARY KEY, event TEXT, session_id TEXT, cwd TEXT, created_at TEXT)"
        )
        connection.execute(
            "INSERT INTO lifecycle_events(event, session_id, cwd, created_at) VALUES (?, ?, ?, ?)",
            (event, payload.get("session_id"), payload.get("cwd"), datetime.now(timezone.utc).isoformat()),
        )
    if event in {"PostCompact", "SubagentStart"}:
        print(json.dumps({"systemMessage": "HARNESS v3 RESUMING: reload the scoped transactional task and invoke skill='harness-workflow'."}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
