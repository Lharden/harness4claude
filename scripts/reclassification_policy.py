from __future__ import annotations

from typing import Any


LOCKING_SOURCES = {"semantic", "human_override"}


def should_promote(state: dict[str, Any], file_count: int) -> bool:
    """Return whether an L0 task should enter the canonical L1 edit pipeline."""
    classification = str(state.get("classification") or "")
    metadata = state.get("classification_meta") or {}
    semantically_locked = (
        metadata.get("agreed") is True
        and metadata.get("source") in LOCKING_SOURCES
    )
    return (
        file_count >= 3
        and classification.startswith("L0")
        and state.get("status") != "active"
        and not semantically_locked
    )
