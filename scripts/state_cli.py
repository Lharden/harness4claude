#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
from pathlib import Path

from transactional_state import HarnessDatabase, StateTransitionError


def _pipelines() -> dict[str, list[str]]:
    root = Path(__file__).resolve().parents[1]
    return json.loads((root / "contract" / "pipelines.json").read_text(encoding="utf-8"))["pipelines"]


def _sync(home: Path, task: dict) -> None:
    path = home / "state.json"
    try:
        projection = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        projection = {}
    projection.update(
        {
            "task_id": task["task_id"],
            "classification": f"{task['tier']}-{task['kind']}",
            "status": task["status"],
            "pipeline": task["pipeline"],
            "current_step": task["phase"],
            "revision": task["revision"],
            "code_revision": task["code_revision"],
            "owner_epoch": task["owner_epoch"],
            "verified": task["verified"],
            "pending_gate": task["pending_gate"],
            "scope_id": task["scope_id"],
            # Sem esta linha, `state_cli artifact` saia com 0, gravava no banco
            # e deixava `artifacts_so_far: []` no state — sucesso silencioso que
            # parecia perda de dado.
            "artifacts_so_far": [a["path"] for a in task.get("artifacts", [])],
        }
    )
    temporary = path.with_suffix(".json.tmp")
    home.mkdir(parents=True, exist_ok=True)
    temporary.write_text(json.dumps(projection, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Harness4Claude transactional state CLI")
    parser.add_argument("--home", type=Path, required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--scope", required=True)
    init.add_argument("--task")
    init.add_argument("--classification", required=True)
    init.add_argument("--prompt", default="")
    confirm = sub.add_parser("confirm")
    confirm.add_argument("--task", required=True)
    confirm.add_argument("--classification", required=True)
    confirm.add_argument("--source", choices=["semantic", "human_override"], default="semantic")
    confirm.add_argument("--confidence", type=float, required=True)
    artifact = sub.add_parser("artifact")
    artifact.add_argument("--task", required=True)
    artifact.add_argument("--type", required=True)
    artifact.add_argument("--path", required=True)
    artifact.add_argument("--hash")
    transition = sub.add_parser("transition")
    transition.add_argument("--task", required=True)
    transition.add_argument("--to", required=True)
    transition.add_argument("--expect-revision", type=int, required=True)
    evidence = sub.add_parser("evidence")
    evidence.add_argument("--task", required=True)
    evidence.add_argument("--type", required=True)
    evidence.add_argument("--command-text")
    evidence.add_argument("--exit-code", type=int)
    evidence.add_argument("--tests-collected", type=int)
    evidence.add_argument("--tests-passed", type=int)
    evidence.add_argument("--output-hash")
    touch = sub.add_parser("touch")
    touch.add_argument("--task", required=True)
    touch.add_argument("--path", required=True)
    gate = sub.add_parser("gate")
    gate.add_argument("--task", required=True)
    gate.add_argument("--type", required=True)
    gate.add_argument("--decision", choices=["approve"], required=True)
    gate.add_argument("--expect-revision", type=int, required=True)
    complete = sub.add_parser("complete")
    complete.add_argument("--task", required=True)
    complete.add_argument("--expect-revision", type=int, required=True)
    args = parser.parse_args(argv)
    db = HarnessDatabase(args.home)
    try:
        if args.command == "init":
            tier, kind = args.classification.split("-", 1)
            task = db.start_task(
                scope_id=args.scope,
                legacy_level=args.classification,
                tier=tier,
                kind=kind,
                pipeline=_pipelines()[args.classification],
                prompt=args.prompt,
                task_id=args.task,
            )
        elif args.command == "confirm":
            tier, kind = args.classification.split("-", 1)
            task = db.confirm_classification(
                args.task,
                tier=tier,
                kind=kind,
                pipeline=_pipelines()[args.classification],
                source=args.source,
                confidence=args.confidence,
            )
        elif args.command == "artifact":
            task = db.record_artifact(args.task, args.type, args.path, args.hash)
        elif args.command == "transition":
            task = db.transition(args.task, args.to, expected_revision=args.expect_revision)
        elif args.command == "evidence":
            task = db.record_evidence(
                args.task,
                evidence_type=args.type,
                command=args.command_text,
                exit_code=args.exit_code,
                tests_collected=args.tests_collected,
                tests_passed=args.tests_passed,
                output_hash=args.output_hash,
            )
        elif args.command == "touch":
            task = db.touch_file(args.task, args.path)
        elif args.command == "gate":
            task = db.resolve_gate(args.task, args.type, args.decision, expected_revision=args.expect_revision)
        else:
            task = db.complete(args.task, expected_revision=args.expect_revision)
    except (StateTransitionError, KeyError, ValueError) as exc:
        print(f"erro: {exc}")
        return 2
    _sync(args.home, task)
    print(json.dumps(task, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
