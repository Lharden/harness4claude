from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
SPEC = importlib.util.spec_from_file_location(
    "post_tool_policy",
    ROOT / "scripts" / "post_tool_policy.py",
)
policy = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(policy)


def test_edit_uses_real_path_and_shell_uses_non_counting_revision_marker():
    assert policy.touch_target("Edit", "src/app.py") == "src/app.py"
    assert policy.touch_target("Bash", "") == "<shell-command>"
    assert policy.counts_as_modified_file("Edit", "src/app.py") is True
    assert policy.counts_as_modified_file("Bash", "") is False


def test_post_tool_hook_matches_shell_commands_for_conservative_invalidation():
    hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    commands = hooks["hooks"]["PostToolUse"]
    reclassify = next(
        entry for entry in commands
        if any("harness-reclassify.sh" in hook["command"] for hook in entry["hooks"])
    )

    assert "Bash" in reclassify["matcher"]
