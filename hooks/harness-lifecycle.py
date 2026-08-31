#!/usr/bin/env python
from __future__ import annotations

import json
import os
import sqlite3
import sys
import argparse
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from harness_paths import ensure_state_dir  # type: ignore[import-not-found]


def _load_state(bucket: Path) -> dict:
    try:
        value = json.loads((bucket / "state.json").read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _resume_message(event: str, state: dict) -> str:
    task_id = state.get("task_id") or "none"
    pipeline = state.get("pipeline") or []
    artifacts = state.get("artifacts_so_far") or []
    parts = [
        f"HARNESS v3 RESUMING: scoped task {task_id}.",
        f"Classification: {state.get('classification') or 'unknown'}; status: {state.get('status') or 'idle'}.",
        f"Current step: {state.get('current_step') or (pipeline[0] if pipeline else 'none')}.",
        f"Pipeline: {' -> '.join(str(item) for item in pipeline) if pipeline else 'none'}.",
    ]
    if state.get("pending_gate"):
        parts.append(f"Pending human gate: {state['pending_gate']}.")
    if artifacts:
        parts.append("Artifacts: " + ", ".join(str(item) for item in artifacts) + ".")
    parts.append("Invoke skill='harness-workflow' and continue from this exact state.")
    if event == "SubagentStart":
        parts.append("Return a NodeResult with role, status, findings, evidence_refs, coverage, and errors.")
    return " ".join(parts)


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
    try:
        bucket = ensure_state_dir(
            root,
            payload.get("cwd") or None,
            session_id=payload.get("session_id") or None,
        )
    except (OSError, ValueError):
        bucket = root
    database = bucket / "lifecycle.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS lifecycle_events(id INTEGER PRIMARY KEY, event TEXT, session_id TEXT, cwd TEXT, created_at TEXT)"
        )
        connection.execute(
            "INSERT INTO lifecycle_events(event, session_id, cwd, created_at) VALUES (?, ?, ?, ?)",
            (event, payload.get("session_id"), payload.get("cwd"), datetime.now(timezone.utc).isoformat()),
        )
    if event in {"PostCompact", "SubagentStart"}:
        print(json.dumps({"systemMessage": _resume_message(event, _load_state(bucket))}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
