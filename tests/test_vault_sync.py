"""Testes de regressão do vault_sync — bug VAULT_PATH (2026-06-12).

Após a migração MCP, VAULT_PATH passou a apontar para a RAIZ do vault
Obsidian (consumida por NODE_EXTRA_CA_CERTS/MCP). O sync usava essa env
como override e duplicou a árvore wiki/ na raiz. O override correto é
AI_BRAIN_PATH.
"""

import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "vault_sync.py"
TRACE = "trace-regressao.md"


def _run_sync(env_extra: dict[str, str], tmp_path: Path) -> None:
    """Executa o vault_sync em subprocess com env controlado e 1 trace semeado."""
    harness_dir = tmp_path / "harness"
    (harness_dir / "traces").mkdir(parents=True, exist_ok=True)
    (harness_dir / "traces" / TRACE).write_text("# trace de teste\n", encoding="utf-8")
    env = {**os.environ, **env_extra}
    subprocess.run(
        [sys.executable, str(SCRIPT), "--quiet", "--harness-dir", str(harness_dir)],
        check=True,
        env=env,
        cwd=tmp_path,
        timeout=60,
    )


def test_ai_brain_path_define_destino(tmp_path: Path) -> None:
    """AI_BRAIN_PATH é o override válido: o trace é espelhado dentro dele."""
    alvo = tmp_path / "ai-brain"
    alvo.mkdir()
    _run_sync({"AI_BRAIN_PATH": str(alvo)}, tmp_path)
    assert (alvo / "wiki" / "sessions" / TRACE).exists()


def test_vault_path_nao_e_consumido(tmp_path: Path) -> None:
    """VAULT_PATH (raiz do vault, semântica MCP) NUNCA pode virar destino do sync."""
    raiz_vault = tmp_path / "vault-root"
    raiz_vault.mkdir()
    alvo = tmp_path / "ai-brain"
    alvo.mkdir()
    _run_sync({"VAULT_PATH": str(raiz_vault), "AI_BRAIN_PATH": str(alvo)}, tmp_path)
    assert not (raiz_vault / "wiki").exists(), "regressão: sync escreveu na raiz do vault"
    assert (alvo / "wiki" / "sessions" / TRACE).exists()
