"""O SessionStart precisa injetar o digest do vault sem quebrar o resume do pipeline.

Os dois textos dividem um unico systemMessage: se o hook imprimir dois objetos JSON, ou
quebrar quando o vault nao existe, o harness perde o resume — que e comportamento
historico e mais importante que o digest.
"""

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
HOOK = PLUGIN_ROOT / "hooks" / "harness-session-start.sh"

needs_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="bash ausente no PATH")


def run_hook(tmp_path: Path, *, cwd: str | None = None, **env_extra: str) -> str:
    """Roda o hook com HOME e HARNESS_DIR isolados, sem tocar o state real."""
    env = {
        **os.environ,
        "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT),
        "HOME": str(tmp_path / "home"),
        "USERPROFILE": str(tmp_path / "home"),
        "HARNESS_DIR": str(tmp_path / "home" / ".claude" / "harness"),
        "HARNESS_SKIP_DEPCHECK": "1",
        **env_extra,
    }
    env.pop("AI_BRAIN_PATH", None)
    env.pop("VAULT_PATH", None)
    env.update(env_extra)
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"cwd": cwd or str(tmp_path / "projeto")})
    proc = subprocess.run(
        ["bash", str(HOOK)], input=payload, capture_output=True, text=True,
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


def semear_pipeline(tmp_path: Path, cwd: str, *, started_at: str | None) -> None:
    """Escreve um pipeline no bucket que o hook vai resolver para este cwd.

    O estado passou a ser por projeto (`harness/projects/<bucket>/state.json`); gravar
    na raiz do HARNESS_DIR faria o hook cair no caminho de bucket novo. `started_at`
    controla o TTL: recente => RESUMING, antigo/None => EXPIRED.
    """
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))
    from harness_paths import ensure_state_dir

    raiz = tmp_path / "home" / ".claude" / "harness"
    destino = Path(ensure_state_dir(str(raiz), cwd))
    destino.mkdir(parents=True, exist_ok=True)
    (destino / "state.json").write_text(json.dumps({
        "task_id": "t-teste", "schema_version": 3, "classification": "L2-feature",
        "status": "active", "pipeline": ["discuss", "tdd"], "current_step": "discuss",
        "artifacts_so_far": [], "started_at": started_at,
    }), encoding="utf-8")


def agora_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
def test_digest_nao_vaza_carriage_return(tmp_path: Path) -> None:
    """CRLF do print() do Windows vazando para dentro do JSON — bug silencioso."""
    raiz = montar_vault(tmp_path)

    payload = json.loads(run_hook(tmp_path, AI_BRAIN_PATH=str(raiz)))

    assert "\r" not in payload["systemMessage"]


@needs_bash
def test_bucket_novo_ainda_recebe_o_digest(tmp_path: Path) -> None:
    """Primeira sessao de um projeto sai cedo ao criar o state — e onde o vault mais rende."""
    raiz = montar_vault(tmp_path)
    projeto_novo = tmp_path / "projeto-inedito"
    projeto_novo.mkdir()

    payload = json.loads(run_hook(tmp_path, cwd=str(projeto_novo), AI_BRAIN_PATH=str(raiz)))

    assert "VAULT AI-Brain disponivel" in payload["systemMessage"]
    assert "HARNESS v3" not in payload["systemMessage"]  # nao ha pipeline a retomar


@needs_bash
def test_resume_do_pipeline_sobrevive_ao_digest(tmp_path: Path) -> None:
    raiz = montar_vault(tmp_path)
    projeto = str(tmp_path / "projeto")
    Path(projeto).mkdir(exist_ok=True)
    semear_pipeline(tmp_path, projeto, started_at=agora_iso())

    payload = json.loads(run_hook(tmp_path, cwd=projeto, AI_BRAIN_PATH=str(raiz)))
    mensagem = payload["systemMessage"]

    assert "HARNESS v3 RESUMING" in mensagem
    assert "VAULT AI-Brain disponivel" in mensagem
    assert mensagem.index("HARNESS v3 RESUMING") < mensagem.index("VAULT AI-Brain")


@needs_bash
def test_pipeline_expirado_tambem_carrega_o_digest(tmp_path: Path) -> None:
    """Terceiro caminho de saida do hook — tambem sai cedo, tambem precisa do vault."""
    raiz = montar_vault(tmp_path)
    projeto = str(tmp_path / "projeto")
    Path(projeto).mkdir(exist_ok=True)
    semear_pipeline(tmp_path, projeto, started_at=None)

    mensagem = json.loads(run_hook(tmp_path, cwd=projeto, AI_BRAIN_PATH=str(raiz)))["systemMessage"]

    assert "HARNESS v3 EXPIRED" in mensagem
    assert "VAULT AI-Brain disponivel" in mensagem
