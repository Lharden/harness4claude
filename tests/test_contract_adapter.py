import importlib.util
import io
import json
import os
import sys
from pathlib import Path

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_contract_snapshot_lock_and_capability_report_are_complete():
    adapter = _load("contract_adapter", ROOT / "scripts" / "contract_adapter.py")
    report = adapter.build_capability_report(ROOT)

    assert report["contract_version"] == "1.1.0"
    assert report["adapter"] == "harness4claude"
    assert report["snapshot_lock_valid"] is True
    assert report["conformant"] is True
    assert len(report["capabilities"]) == 22
    assert all(item["status"] in {"native", "equivalent"} for item in report["capabilities"].values())


def test_claude_pipelines_are_the_canonical_contract_pipelines():
    adapter = _load("contract_adapter_pipelines", ROOT / "scripts" / "contract_adapter.py")
    configured = json.loads((ROOT / "scripts" / "pipelines.json").read_text(encoding="utf-8"))["pipelines"]

    assert configured == adapter.load_contract()["pipelines"]["pipelines"]


def test_transactional_state_and_policy_engines_are_shipped():
    assert (ROOT / "scripts" / "transactional_state.py").exists()
    assert (ROOT / "scripts" / "command_policy.py").exists()
    assert (ROOT / "scripts" / "state_cli.py").exists()


def test_release_version_and_lifecycle_are_synchronized():
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    marketplace = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]

    assert plugin["version"] == marketplace["version"] == marketplace["plugins"][0]["version"] == "4.0.0"
    assert {
        "SessionStart",
        "UserPromptSubmit",
        "PreToolUse",
        "PostToolUse",
        "PostToolUseFailure",
        "PreCompact",
        "PostCompact",
        "SubagentStart",
        "SubagentStop",
        "Stop",
        "SessionEnd",
    } <= set(hooks)
    assert (ROOT / "skills" / "science-evidence" / "SKILL.md").exists()


def test_science_intent_routes_evidence_prompts(monkeypatch, capsys):
    hook = _load("science_intent", ROOT / "hooks" / "science_intent.py")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"prompt": "Review scientific evidence"})))

    assert hook.main() == 0

    response = json.loads(capsys.readouterr().out)
    # A chave mudou em 2026-09-01: `systemMessage` e canal de UI e nao entra no
    # contexto do modelo — nos 343 transcripts, 100% das linhas com ele no
    # stdout tem `content` vazio. Este assert le so a chave nova de proposito:
    # aceitar as duas deixaria a regressao passar despercebida.
    assert "systemMessage" not in response
    message = response["hookSpecificOutput"]["additionalContext"]
    assert "science-evidence" in message
    assert "read-only" in message
    assert "provenance" in message


def test_classifier_core_is_portable_and_used_by_the_hook():
    classifier = _load("classify_prompt", ROOT / "scripts" / "classify_prompt.py")

    assert classifier.classify_prompt("corrija este bug inesperado") == ("L1", "bug")
    assert classifier.classify_prompt("crie um novo sistema completo") == ("L2", "feature")
    hook = (ROOT / "hooks" / "harness-classify.sh").read_text(encoding="utf-8")
    assert "from classify_prompt import classify_prompt" in hook
    assert "level, task_type = classify_prompt(msg)" in hook


def test_workflow_encodes_drop_constrain_retain_gate():
    workflow = (ROOT / "skills" / "harness-workflow" / "SKILL.md").read_text(encoding="utf-8")

    assert "DROP / CONSTRAIN / RETAIN" in workflow
    assert "Antes de propagar qualquer artefato" in workflow
    assert "fronteira" in workflow
    assert "evidência" in workflow
