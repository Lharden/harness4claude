from __future__ import annotations

from typing import Any

CONTINUABLE_STATUSES = {"active", "awaiting_gate"}


def should_continue(state: dict[str, Any]) -> bool:
    return state.get("status") in CONTINUABLE_STATUSES and bool(state.get("pipeline"))
