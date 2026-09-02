#!/usr/bin/env python
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from harness_paths import ensure_state_dir  # type: ignore[import-not-found]
from transactional_state import HarnessDatabase, StateTransitionError  # type: ignore[import-not-found]

VERIFICATION_PATTERNS = (
    r"^\s*(?:py|python(?:\.exe)?)\s+-m\s+(?:pytest|unittest)\b",
    r"^\s*pytest(?:\.exe)?\b",
    r"^\s*(?:npm|pnpm)\s+(?:run\s+)?test\b",
    r"^\s*yarn\s+test\b",
    r"^\s*cargo\s+test\b",
    r"^\s*go\s+test\b",
)
SHELL_TOOLS = {"bash", "powershell", "shell", "shell_command"}


def _event_name(payload: dict[str, Any], explicit: str | None = None) -> str:
    return str(explicit or payload.get("hook_event_name") or payload.get("hookEventName") or "")


def _tool_input(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("tool_input") or payload.get("toolInput") or {}
    return value if isinstance(value, dict) else {}


def _command(payload: dict[str, Any]) -> str:
    value = _tool_input(payload)
    return str(value.get("command") or value.get("cmd") or value.get("script") or "")


def _has_unquoted_shell_composition(command: str) -> bool:
    quote: str | None = None
    escaped = False
    for index, character in enumerate(command):
        if escaped:
            escaped = False
            continue
        if quote:
            if character == "\\" and quote == '"':
                escaped = True
            elif character == quote:
                quote = None
            continue
        if character in {"'", '"'}:
            quote = character
            continue
        if character in {";", "|", "&", "\r", "\n", "`"}:
            return True
        if character == "$" and index + 1 < len(command) and command[index + 1] == "(":
            return True
    return quote is not None


def is_trusted_verification(command: str) -> bool:
    if not command or _has_unquoted_shell_composition(command):
        return False
    return any(re.search(pattern, command, re.IGNORECASE) for pattern in VERIFICATION_PATTERNS)


#: O proprio CLI de estado do harness. Ver `is_state_management`.
STATE_CLI_PATTERNS = (
    r"\bstate_cli\.py\b",
    r"\bbranch_state\.py\b",
    r"\bconfirm_classification\.py\b",
)


def is_state_management(command: str) -> bool:
    """O comando so mexe no estado do harness, nao no codigo do projeto.

    `_handle_post_tool` trata todo comando de shell como possivel alteracao de
    codigo e chama `touch_file`, que zera `verified` e sobe `code_revision`. A
    heuristica e conservadora e correta para `sed -i`, `npm install`, `git
    checkout`. Mas ela criava um deadlock estrutural: `state_cli.py complete`
    so pode ser invocado por shell, e a propria invocacao invalidava, no mesmo
    PostToolUse, a evidencia que o `complete` exige. **Nenhuma task podia ser
    concluida pelo caminho previsto.**

    Medido em 2026-09-02: `code_revision` foi 501 -> 507 -> 511 entre gravar a
    evidencia e tentar fechar, sem uma linha de codigo mudar. `verified` foi
    para True tres vezes e voltou para False no comando seguinte, todas.

    A isencao e estreita de proposito: so o CLI do proprio harness, e so
    quando o comando nao tem composicao de shell — `state_cli.py ... && sed -i
    ...` continua contando como alteracao, porque a segunda metade e.
    """
    if not command or _has_unquoted_shell_composition(command):
        return False
    return any(re.search(p, command, re.IGNORECASE) for p in STATE_CLI_PATTERNS)


def _response(payload: dict[str, Any]) -> Any:
    return payload.get("tool_response") or payload.get("toolResponse") or payload.get("output") or ""


def _walk(value: Any):
    if isinstance(value, dict):
        for nested in value.values():
            yield from _walk(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            yield from _walk(nested)
    else:
        yield value


def _explicit_exit_code(value: Any) -> int | None:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = re.sub(r"[^a-z]", "", str(key).casefold())
            if normalized in {"exitcode", "returncode"}:
                if isinstance(nested, int) and not isinstance(nested, bool):
                    return nested
                if isinstance(nested, str) and re.fullmatch(r"-?\d+", nested.strip()):
                    return int(nested)
        for nested in value.values():
            result = _explicit_exit_code(nested)
            if result is not None:
                return result
    elif isinstance(value, (list, tuple)):
        for nested in value:
            result = _explicit_exit_code(nested)
            if result is not None:
                return result
    return None


def _exit_code(payload: dict[str, Any]) -> int | None:
    explicit = _explicit_exit_code(_response(payload))
    if explicit is not None:
        return explicit
    event = _event_name(payload)
    if event == "PostToolUse":
        return 0
    if event == "PostToolUseFailure":
        text = str(payload.get("error") or "")
        for pattern in (
            r"(?:status|exit)\s+code\s*[:=]?\s*(-?\d+)",
            r"exit(?:ed)?\s+with\s+(?:non-zero\s+)?(?:status\s+)?(?:code\s+)?(-?\d+)",
            r"exit\s+status\s+(-?\d+)",
        ):
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return int(match.group(1))
        return 1
    return None


def _response_text(payload: dict[str, Any]) -> str:
    values = [value for value in _walk(_response(payload)) if isinstance(value, str)]
    error = payload.get("error")
    if isinstance(error, str) and error:
        values.append(error)
    return "\n".join(values)


def _write_heartbeat(
    payload: dict[str, Any], event: str, harness_root: str | Path | None
) -> None:
    if not event:
        return
    root = Path(harness_root or os.environ.get("HARNESS_DIR") or Path.home() / ".claude" / "harness")
    try:
        heartbeats = root / "heartbeats"
        heartbeats.mkdir(parents=True, exist_ok=True)
        temporary = heartbeats / f".{event}.tmp"
        temporary.write_text(str(time.time()), encoding="utf-8")
        temporary.replace(heartbeats / event)
    except OSError:
        pass


def _test_counts(payload: dict[str, Any]) -> tuple[int | None, int | None, str | None]:
    text = _response_text(payload)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest() if text else None
    if re.search(r"\b(no tests ran|collected 0 items|0 tests? (?:run|passed|total))\b", text, re.IGNORECASE):
        return 0, 0, digest
    passed = [int(value) for value in re.findall(r"\b(\d+)\s+passed\b", text, re.IGNORECASE)]
    failed = [int(value) for value in re.findall(r"\b(\d+)\s+failed\b", text, re.IGNORECASE)]
    errors = [int(value) for value in re.findall(r"\b(\d+)\s+errors?\b", text, re.IGNORECASE)]
    if passed or failed or errors:
        passed_count = max(passed, default=0)
        return passed_count + max(failed, default=0) + max(errors, default=0), passed_count, digest
    if re.search(r"\btest result:\s*ok\b", text, re.IGNORECASE) or re.search(r"(?m)^ok\s+\S+", text):
        return 1, 1, digest
    return None, None, digest


def _projection(bucket: Path) -> dict[str, Any]:
    try:
        value = json.loads((bucket / "state.json").read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _sync_projection(bucket: Path, projection: dict[str, Any], task: dict[str, Any]) -> None:
    projection.update(
        {
            "task_id": task["task_id"],
            "status": task["status"],
            "pipeline": task["pipeline"],
            "current_step": task["phase"],
            "revision": task["revision"],
            "code_revision": task["code_revision"],
            "verified": task["verified"],
            "stop_continuations": task["stop_continuations"],
            "pending_gate": task["pending_gate"],
            "scope_id": task["scope_id"],
        }
    )
    temporary = bucket / "state.json.transactional.tmp"
    temporary.write_text(json.dumps(projection, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(bucket / "state.json")


def _database_for_payload(
    payload: dict[str, Any], harness_root: str | Path | None
) -> tuple[Path, HarnessDatabase, dict[str, Any], dict[str, Any]] | None:
    root = Path(harness_root or os.environ.get("HARNESS_DIR") or Path.home() / ".claude" / "harness")
    bucket = ensure_state_dir(
        root,
        payload.get("cwd") or None,
        session_id=payload.get("session_id") or payload.get("sessionId") or None,
    )
    projection = _projection(bucket)
    task_id = projection.get("task_id")
    if not task_id or not (bucket / "harness.db").is_file():
        return None
    database = HarnessDatabase(bucket)
    try:
        task = database.task(str(task_id))
    except StateTransitionError:
        return None
    return bucket, database, projection, task


def _handle_post_tool(payload: dict[str, Any], context) -> str:
    bucket, database, projection, task = context
    tool_name = str(payload.get("tool_name") or payload.get("toolName") or "").casefold()
    command = _command(payload)
    if tool_name in SHELL_TOOLS and not is_state_management(command):
        task = database.touch_file(task["task_id"], "shell-command")
    if is_trusted_verification(command):
        collected, passed, output_hash = _test_counts(payload)
        task = database.record_evidence(
            task["task_id"],
            evidence_type="test",
            command=command,
            exit_code=_exit_code(payload),
            tests_collected=collected,
            tests_passed=passed,
            output_hash=output_hash,
        )
    _sync_projection(bucket, projection, task)
    return ""


def _handle_stop(payload: dict[str, Any], context) -> str:
    if payload.get("stop_hook_active") or payload.get("stopHookActive"):
        return ""
    bucket, database, projection, task = context
    if task["status"] != "active" or not task["pipeline"] or task["verified"]:
        return ""
    task = database.register_stop_continuation(task["task_id"], limit=2)
    _sync_projection(bucket, projection, task)
    if task["pending_gate"] == "escalation":
        reason = (
            "HARNESS v3 escalation gate: verification remains incomplete after two continuations. "
            "Ask the user for direction with the concrete blocker and evidence."
        )
    else:
        reason = (
            "HARNESS v3 verification gate: continue the active harness-workflow pipeline and attach "
            "fresh test evidence before the final response."
        )
    return json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False)


def handle_payload(
    payload: dict[str, Any], *, harness_root: str | Path | None = None, event: str | None = None
) -> str:
    name = _event_name(payload, event)
    _write_heartbeat(payload, name, harness_root)
    if name == "Stop" and (payload.get("stop_hook_active") or payload.get("stopHookActive")):
        return ""
    context = _database_for_payload(payload, harness_root)
    if context is None:
        return ""
    if name in {"PostToolUse", "PostToolUseFailure"}:
        return _handle_post_tool(payload, context)
    if name == "Stop":
        return _handle_stop(payload, context)
    return ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event")
    args = parser.parse_args()
    try:
        payload = json.load(sys.stdin)
    except (OSError, ValueError):
        payload = {}
    output = handle_payload(payload, event=args.event)
    if output:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
