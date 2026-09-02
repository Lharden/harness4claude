from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
sys.path.insert(0, str(ROOT / "scripts"))
from harness_paths import ensure_state_dir  # type: ignore[import-not-found]


def _mensagem(saida: str) -> str:
    """Texto entregue ao modelo.

    A chave mudou em 2026-09-01: `systemMessage` e canal de UI e nao entra no
    contexto do modelo — nos 343 transcripts desta maquina, 100% das linhas
    com systemMessage no stdout tem `content` vazio. Aceitar as duas chaves
    aqui deixaria a regressao passar despercebida.
    """
    payload = json.loads(saida)
    assert "systemMessage" not in payload, (
        "regressao: systemMessage nao chega ao modelo"
    )
    return payload["hookSpecificOutput"]["additionalContext"]


def test_postcompact_reloads_the_exact_scoped_task(tmp_path: Path):
    harness_root = tmp_path / "harness"
    cwd = tmp_path / "repo"
    cwd.mkdir()
    session_id = "session-a"
    bucket = ensure_state_dir(harness_root, cwd, session_id=session_id)
    (bucket / "state.json").write_text(
        json.dumps(
            {
                "task_id": "t-scoped",
                "classification": "L2-feature",
                "status": "awaiting_gate",
                "pipeline": ["write-spec", "approve-spec", "design-doc"],
                "current_step": "approve-spec",
                "pending_gate": "approve-spec",
                "artifacts_so_far": ["docs/specs/demo-spec.md"],
            }
        ),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["HARNESS_DIR"] = str(harness_root)

    result = subprocess.run(
        [sys.executable, str(ROOT / "hooks" / "harness-lifecycle.py"), "--event", "PostCompact"],
        input=json.dumps({"session_id": session_id, "cwd": str(cwd)}),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    message = _mensagem(result.stdout)
    assert "t-scoped" in message
    assert "approve-spec" in message
    assert "docs/specs/demo-spec.md" in message
    assert (bucket / "lifecycle.db").exists()
    assert not (harness_root / "lifecycle.db").exists()
    assert (harness_root / "heartbeats" / "PostCompact").exists()


def test_subagent_start_includes_scoped_node_contract(tmp_path: Path):
    harness_root = tmp_path / "harness"
    cwd = tmp_path / "repo"
    cwd.mkdir()
    bucket = ensure_state_dir(harness_root, cwd, session_id="session-b")
    (bucket / "state.json").write_text(
        json.dumps({"task_id": "t-node", "status": "active", "pipeline": ["grill-me"]}),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["HARNESS_DIR"] = str(harness_root)

    result = subprocess.run(
        [sys.executable, str(ROOT / "hooks" / "harness-lifecycle.py"), "--event", "SubagentStart"],
        input=json.dumps({"session_id": "session-b", "cwd": str(cwd)}),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    message = _mensagem(result.stdout)
    assert "t-node" in message
    assert "NodeResult" in message
