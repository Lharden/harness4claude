import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
spec = importlib.util.spec_from_file_location("claude_command_policy", ROOT / "scripts" / "command_policy.py")
policy = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules["claude_command_policy"] = policy
spec.loader.exec_module(policy)


def test_policy_distinguishes_quoted_text_from_execution():
    assert policy.evaluate_command('python -c "print(\'git reset --hard\')"').action == "allow"


def test_policy_denies_destructive_chain_and_gates_plugin_mutation():
    assert policy.evaluate_command("echo ok && git clean -fd").action == "deny"
    assert policy.evaluate_command("claude plugin install example").action == "require_approval"
