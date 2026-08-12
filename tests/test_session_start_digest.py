"""O SessionStart precisa injetar o digest do vault sem quebrar o resume do pipeline.

Os dois textos dividem um unico systemMessage: se o hook imprimir dois objetos JSON, ou
quebrar quando o vault nao existe, o harness perde o resume — que e comportamento
historico e mais importante que o digest.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
HOOK = PLUGIN_ROOT / "hooks" / "harness-session-start.sh"

needs_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="bash ausente no PATH")


def run_hook(tmp_path: Path, **env_extra: str) -> str:
    """Roda o hook com HOME isolado, para nao tocar o state real."""
    env = {
        **os.environ,
        "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT),
        "HOME": str(tmp_path / "home"),
        "USERPROFILE": str(tmp_path / "home"),
        **env_extra,
    }
    env.pop("AI_BRAIN_PATH", None)
    env.pop("VAULT_PATH", None)
    env.update(env_extra)
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["bash", str(HOOK)], capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=env, timeout=90,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def montar_vault(tmp_path: Path) -> Path:
    raiz = tmp_path / "vault" / "AI-Brain"
    pagina = raiz / "wiki" / "decisions" / "assimilacoes.md"
    pagina.parent.mkdir(parents=True, exist_ok=True)
    pagina.write_text(
        "---\ntype: decision\ncreated: 2026-01-01\nupdated: 2026-01-01\n"
        "status: active\ntags: [x]\n---\n\n# Assimilacoes\n\nO que veio de fora.\n",
        encoding="utf-8",
    )
    return raiz


@needs_bash
def test_hook_sem_vault_nao_emite_nada(tmp_path: Path) -> None:
    saida = run_hook(tmp_path, AI_BRAIN_PATH=str(tmp_path / "inexistente"))

    assert saida == ""


@needs_bash
def test_hook_emite_um_unico_json_com_o_digest(tmp_path: Path) -> None:
    raiz = montar_vault(tmp_path)

    saida = run_hook(tmp_path, AI_BRAIN_PATH=str(raiz))

    payload = json.loads(saida)  # falha se houver mais de um objeto
    assert "VAULT AI-Brain disponivel" in payload["systemMessage"]
    assert "[[decisions/assimilacoes]]" in payload["systemMessage"]


@needs_bash
def test_resume_do_pipeline_sobrevive_ao_digest(tmp_path: Path) -> None:
    raiz = montar_vault(tmp_path)
    harness = tmp_path / "home" / ".claude" / "harness"
    harness.mkdir(parents=True, exist_ok=True)
    (harness / "state.json").write_text(json.dumps({
        "task_id": "t-teste", "schema_version": 3, "classification": "L2-feature",
        "status": "active", "pipeline": ["discuss", "tdd"], "current_step": "discuss",
        "artifacts_so_far": [], "started_at": None,
    }), encoding="utf-8")

    payload = json.loads(run_hook(tmp_path, AI_BRAIN_PATH=str(raiz)))
    mensagem = payload["systemMessage"]

    assert "HARNESS v3 RESUMING" in mensagem
    assert "VAULT AI-Brain disponivel" in mensagem
    assert mensagem.index("HARNESS v3 RESUMING") < mensagem.index("VAULT AI-Brain")
