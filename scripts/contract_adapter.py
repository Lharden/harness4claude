#!/usr/bin/env python
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


EVIDENCE = {
    "classification.deterministic-suggestion": ["hooks/harness-classify.sh", "tests/test_harness.py"],
    "classification.semantic-confirmation": ["scripts/confirm_classification.py", "tests/test_confirm_classification.py"],
    "classification.human-override": ["scripts/confirm_classification.py", "skills/harness-workflow/SKILL.md"],
    "state.session-worktree-isolation": ["scripts/harness_paths.py", "tests/test_harness_paths.py"],
    "state.transactional-fsm": ["scripts/transactional_state.py", "tests/test_transactional_state.py"],
    "state.ttl-signals": ["scripts/expire_stale_pipeline.py", "tests/test_pipeline_ttl.py"],
    "workflow.sdd-v3": ["skills/harness-workflow/SKILL.md", "scripts/pipelines.json"],
    "workflow.human-gates": ["scripts/transactional_state.py", "skills/harness-workflow/SKILL.md"],
    "workflow.adversarial-agents": ["scripts/workflows/wf-grill.js", "tests/test_workflow_returns.py"],
    "workflow.spec-verification": ["skills/verify-against-spec/SKILL.md", "tests/test_harness.py"],
    "context.graphify": ["skills/graph-context/SKILL.md", "scripts/setup-graphify.sh"],
    "context.skill-router": ["hooks/skill_router.py", "tests/test_skill_router.py"],
    "capability.arsenal": ["tools/arsenal.py", "tests/test_arsenal.py"],
    "memory.wiki-vault": ["tools/wiki_query.py", "tests/test_wiki_query.py"],
    "memory.operational-search": ["scripts/build_wiki_index.py", "tests/test_wiki_index.py"],
    "conversation.branch-keeper": ["scripts/branch_state.py", "tests/test_branch_state.py"],
    "safety.command-policy": ["scripts/command_policy.py", "tests/test_command_policy.py"],
    "integration.harness-lite": ["hooks/harness_lite_adapter.py", "tests/test_harness_lite_adapter.py"],
    "integration.science-harness": ["skills/science-evidence/SKILL.md", "hooks/science_intent.py"],
    "lifecycle.full-hooks": ["hooks/hooks.json", "hooks/harness-lifecycle.py"],
    "observability.health-telemetry": ["scripts/health-check.sh", "scripts/check_hook_liveness.py"],
    "editorial.drop-constrain-retain": ["skills/harness-workflow/SKILL.md", "tests/test_contract_adapter.py"],
}


def load_contract(root: str | Path | None = None) -> dict[str, Any]:
    plugin = Path(root or Path(__file__).resolve().parents[1])
    contract = plugin / "contract"
    return {
        "capabilities": json.loads((contract / "capabilities.json").read_text(encoding="utf-8")),
        "pipelines": json.loads((contract / "pipelines.json").read_text(encoding="utf-8")),
        "lock": json.loads((contract / "contract.lock.json").read_text(encoding="utf-8")),
        "root": contract,
    }


def verify_lock(contract: dict[str, Any]) -> bool:
    lock = contract["lock"]
    digest = hashlib.sha256()
    for relative in lock.get("files", []):
        path = contract["root"] / relative
        if not path.is_file():
            return False
        digest.update(Path(relative).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest() == lock.get("sha256")


def build_capability_report(root: str | Path | None = None) -> dict[str, Any]:
    plugin = Path(root or Path(__file__).resolve().parents[1]).resolve()
    contract = load_contract(plugin)
    required = [item["id"] for item in contract["capabilities"]["capabilities"] if item["level"] == "required"]
    capabilities = {}
    for capability in required:
        records = EVIDENCE.get(capability, [])
        valid = records and all((plugin / record.split("#", 1)[0]).exists() for record in records)
        capabilities[capability] = {
            "status": "equivalent" if valid else "degraded",
            "evidence": records if valid else ["missing conformance evidence"],
        }
    lock_valid = verify_lock(contract)
    canonical_pipelines = json.dumps(
        contract["pipelines"].get("pipelines") or {},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "contract_version": contract["capabilities"]["contract_version"],
        "adapter": "harness4claude",
        "capabilities": capabilities,
        "snapshot_lock_valid": lock_valid,
        "pipeline_fingerprint": hashlib.sha256(canonical_pipelines.encode("utf-8")).hexdigest(),
        "conformant": lock_valid and all(item["status"] == "equivalent" for item in capabilities.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("check", nargs="?")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    report = build_capability_report(args.root)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["conformant"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
