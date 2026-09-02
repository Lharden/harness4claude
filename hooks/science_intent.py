#!/usr/bin/env python
from __future__ import annotations

import importlib.util
import json
import os
import re
import sys


def _emit(payload: dict, texto: str) -> None:
    """Entrega pelo emissor central, com o canal provado como rede."""
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "emit.py")
        spec = importlib.util.spec_from_file_location("harness_emit", path)
        if spec is None or spec.loader is None:
            raise ImportError
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.Emitter(
            payload.get("hook_event_name") or "UserPromptSubmit",
            hook="science_intent",
            session_id=payload.get("session_id"),
            cwd=payload.get("cwd"),
        ).add("science", texto).flush()
    except Exception:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": payload.get("hook_event_name") or "UserPromptSubmit",
            "additionalContext": texto,
        }}, ensure_ascii=False))


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    prompt = str(payload.get("prompt") or payload.get("user_prompt") or "")
    if re.search(
        r"\b(scientific|science|evidence|paper|papers|claim|claims|estudo|evid[eê]ncia|artigo)\b", prompt, re.I
    ):
        # UserPromptSubmit + instrucao => additionalContext. Por `systemMessage`
        # esta linha nunca chegou ao modelo; por stdout cru chegaria sem marca
        # de proveniencia, indistinguivel de fala do usuario.
        _emit(
            payload,
            "SCIENCE HARNESS: invoke skill='science-evidence'; use "
            "science_harness read-only and preserve corpus provenance.",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
