#!/usr/bin/env python3
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from hashlib import sha256
from typing import Literal

"""Thin harness-lite supervisor adapter (harness-lite P100-T04).

Translates a classification, a spec and a plan into one request the execution
plane understands. It does not route: the router lives in the control plane,
and two adapters that each decided routing would be two routers, with the
looser of them being the one that gets used.

Generated under harness-lite decision B10, which says this arrives as a patch
so it is reviewable before it touches the tool doing the reviewing.

Standard library only, like every hook in this repository.
"""

Level = Literal["L0", "L1", "L2"]
Stage = Literal["classify", "spec", "plan", "execute", "verify"]
Verdict = Literal["passed", "failed", "provisional"]


def _digest(value: str) -> str:
    return "sha256:" + sha256(value.encode("utf-8")).hexdigest()


def _canonical(value: object) -> str:
    """JSON-ish, sorted by code point, never by locale.

    A locale-sorted key differs between machines, and a key that differs
    between machines is not an identity.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_canonical(item) for item in value) + "]"
    if isinstance(value, dict):
        pairs = sorted(((key, inner) for key, inner in value.items() if inner is not None), key=lambda item: item[0])
        return "{" + ",".join(_canonical(key) + ":" + _canonical(inner) for key, inner in pairs) + "}"
    raise TypeError(f"cannot canonicalise {type(value).__name__}")


@dataclass(frozen=True)
class SupervisorRequest:
    """The identity and routing projection a supervisor produces.

    Not the plane's own task envelope, which is a full submission with an
    objective, a workspace, acceptance criteria and an execution policy. The
    adapter turns this into that one.
    """

    schema_version: str
    level: Level
    stage: Stage
    project_fingerprint: str
    session_id: str
    plan_sha256: str | None
    spec_sha256: str | None
    requested_alias: str | None
    idempotency_key: str


@dataclass(frozen=True)
class EvidenceBundle:
    task_id: str
    artifacts: tuple[tuple[str, str], ...]
    verdict: Verdict


def reaches_the_plane(level: str) -> bool:
    """L0 never reaches the plane: it is the level the supervisor answers itself.

    Submitting one would pay for a round trip to be told what it already knew.
    """
    return level != "L0"


def build_supervisor_request(
    *,
    level: Level,
    stage: Stage,
    spec_text: str | None,
    plan_text: str | None,
    project_fingerprint: str,
    session_id: str,
    requested_alias: str | None,
) -> SupervisorRequest:
    # None rather than a hash of the empty string. A zero hash reads as "a plan
    # whose content is empty", which is a different and false claim.
    plan_sha256 = None if plan_text is None else _digest(plan_text)
    spec_sha256 = None if spec_text is None else _digest(spec_text)

    # Everything that decides what the work is, and nothing that decides when
    # it ran. A timestamp here would make every replay a new task, which is the
    # opposite of what an idempotency key is for. The requested alias is out
    # too: what was asked for is not what the work is, and policy may route it
    # elsewhere without changing the task.
    identity = _canonical(
        {
            "level": level,
            "stage": stage,
            "projectFingerprint": project_fingerprint,
            "sessionId": session_id,
            "planSha256": plan_sha256,
            "specSha256": spec_sha256,
        }
    )

    return SupervisorRequest(
        schema_version="1",
        level=level,
        stage=stage,
        project_fingerprint=project_fingerprint,
        session_id=session_id,
        plan_sha256=plan_sha256,
        spec_sha256=spec_sha256,
        requested_alias=requested_alias,
        idempotency_key=_digest(identity),
    )


def checkpoint_points(stage: str, premium: bool) -> tuple[str, ...]:
    """Before and after every premium call, and nowhere else.

    Before, because a crash mid-call must not lose the fact that the call was
    about to happen — that is how a second charge is born. After, because the
    result is the expensive thing and losing it means buying it twice.
    """
    if not premium:
        return ()
    return ("before", "after") if stage in ("spec", "execute", "verify") else ()


def bundle_is_acceptable(bundle: EvidenceBundle) -> bool:
    """`provisional` is not a pass.

    A bundle that did not finish is an absent answer, and treating it as a
    wrong one is how a truncation becomes a verdict.
    """
    return bundle.verdict == "passed" and len(bundle.artifacts) > 0


def is_replay(first: SupervisorRequest, second: SupervisorRequest) -> bool:
    """Two requests are the same work exactly when their keys match."""
    return first.idempotency_key == second.idempotency_key


def to_wire(request: SupervisorRequest) -> dict[str, object]:
    """The shape the control plane reads, with the plane's own field names."""
    return {
        "schemaVersion": request.schema_version,
        "level": request.level,
        "stage": request.stage,
        "projectFingerprint": request.project_fingerprint,
        "sessionId": request.session_id,
        "planSha256": request.plan_sha256,
        "specSha256": request.spec_sha256,
        "requestedAlias": request.requested_alias,
        "idempotencyKey": request.idempotency_key,
    }


def project_fingerprint_for(paths: Iterable[str]) -> str:
    """Derived from what the project is, sorted so two orders are one project.

    Never from a working directory: the same project checked out twice would
    otherwise be two projects, and two tasks that are the same work.
    """
    return _digest("\n".join(sorted(paths)))
