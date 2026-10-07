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


def test_git_global_options_do_not_hide_the_subcommand():
    """REQ-7 (2026-10-07): `-C <dir>` era lido como subcomando, e 12 `branch -D`
    reais passaram como allow nos transcripts."""
    assert policy.evaluate_command("git -C repo reset --hard").action == "deny"
    assert policy.evaluate_command('git -C "$M" branch -D tmp/x').action == "deny"
    assert policy.evaluate_command("git --no-pager push --force").action == "deny"
    assert policy.evaluate_command("git -c core.x=y clean -fd").action == "deny"
    assert policy.evaluate_command("git --git-dir=/r/.git --work-tree /r restore .").action == "deny"
    assert policy.evaluate_command("git -C repo push origin main").action == "warn"


def test_git_global_options_alone_or_benign_still_allow():
    assert policy.evaluate_command("git --version").action == "allow"
    assert policy.evaluate_command("git -C repo status").action == "allow"
    assert policy.evaluate_command("git -C repo branch -d merged").action == "allow"
    assert policy.evaluate_command("git -c user.name=x commit -m msg").action == "allow"
