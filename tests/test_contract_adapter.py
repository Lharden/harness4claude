import importlib.util
import json
import os
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

    assert report["contract_version"] == "1.0.0"
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
    assert {"SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "PreCompact", "PostCompact", "SubagentStart", "SubagentStop", "Stop", "SessionEnd"} <= set(hooks)
    assert (ROOT / "skills" / "science-evidence" / "SKILL.md").exists()
