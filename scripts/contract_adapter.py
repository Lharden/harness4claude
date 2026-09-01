#!/usr/bin/env python
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

EVIDENCE = {
    "classification.deterministic-suggestion": [
        "tests/test_contract_adapter.py#test_classifier_core_is_portable_and_used_by_the_hook"
    ],
    "classification.semantic-confirmation": [
        "tests/test_confirm_classification.py#test_divergencia_corrige_classificacao_e_pipeline"
    ],
    "classification.human-override": ["tests/test_confirm_classification.py#test_human_override_registrado_na_source"],
    "state.session-worktree-isolation": [
        "tests/test_harness_paths.py#test_duas_sessoes_no_mesmo_worktree_tem_estado_independente"
    ],
    "state.transactional-fsm": ["tests/test_transactional_state.py#test_revision_evidence_and_scope_invariants"],
    "state.ttl-signals": ["tests/test_transactional_state.py#test_stale_task_ttl_abandons_pipeline_and_releases_scope"],
    "workflow.sdd-v3": ["tests/test_contract_adapter.py#test_claude_pipelines_are_the_canonical_contract_pipelines"],
    "workflow.human-gates": [
        "tests/test_transactional_branches.py#test_each_branch_approval_resolves_only_its_subject_gate"
    ],
    "workflow.adversarial-agents": ["tests/test_workflow_returns.py#test_todo_fan_out_tem_censo_de_nos"],
    "workflow.spec-verification": ["tests/test_workflow_returns.py#test_verify_nao_aprova_com_cobertura_incompleta"],
    "context.graphify": ["tests/test_graph_lint.py#test_grafo_saudavel_passa_limpo"],
    "context.skill-router": ["tests/test_skill_router.py#test_main_runs_layer_b_when_layer_a_empty"],
    "capability.arsenal": ["tests/test_arsenal.py#test_registry_minimo_valido_passa"],
    "memory.wiki-vault": ["tests/test_vault_sync.py#test_spec_crua_chega_ao_vault_com_frontmatter"],
    "memory.operational-search": ["tests/test_wiki_query.py#test_camada_a_acha_por_alias_curado_e_e_confiavel"],
    "conversation.branch-keeper": [
        "tests/test_transactional_branches.py#test_open_branch_requires_its_approval_and_enforces_open_limit"
    ],
    "safety.command-policy": [
        "tests/test_command_policy.py#test_policy_denies_destructive_chain_and_gates_plugin_mutation"
    ],
    "integration.harness-lite": [
        "tests/test_harness_lite_adapter.py#test_a_passed_bundle_with_artifacts_is_acceptable"
    ],
    "integration.science-harness": ["tests/test_contract_adapter.py#test_science_intent_routes_evidence_prompts"],
    "lifecycle.full-hooks": ["tests/test_contract_adapter.py#test_release_version_and_lifecycle_are_synchronized"],
    "observability.health-telemetry": ["tests/test_hook_liveness.py#test_tudo_disparando_sai_zero"],
    "editorial.drop-constrain-retain": [
        "tests/test_contract_adapter.py#test_workflow_encodes_drop_constrain_retain_gate"
    ],
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
        digest.update(_snapshot_bytes(path))
        digest.update(b"\0")
    return digest.hexdigest() == lock.get("sha256")


def _snapshot_bytes(path: Path) -> bytes:
    if path.suffix.lower() != ".json":
        return path.read_bytes()
    value = json.loads(path.read_text(encoding="utf-8"))
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


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
