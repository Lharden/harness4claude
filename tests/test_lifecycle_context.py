from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

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


def _bucket_com_gate_pendente(harness_root: Path, cwd: Path, session_id: str) -> Path:
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
                # sem isto o TTL do SessionStart expira o pipeline antes de retomar
                "started_at": datetime.now(timezone.utc).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    return bucket


def test_postcompact_registra_e_nao_emite(tmp_path: Path):
    """PostCompact nao tem canal para o modelo, entao registra e fica calado.

    https://code.claude.com/docs/en/hooks, "Decision control": PostCompact esta
    em "None — No decision control" e o host descarta `systemMessage` e
    `continue`. Ate 2026-09-24 este teste exigia `hookSpecificOutput` daqui —
    era a especificacao do defeito: o host rejeitava a saida com "Hook JSON
    output validation failed" e a retomada nunca chegava. A retomada agora e
    verificada no canal que entrega: SessionStart com source "compact" (abaixo).
    """
    harness_root = tmp_path / "harness"
    cwd = tmp_path / "repo"
    cwd.mkdir()
    session_id = "session-a"
    bucket = _bucket_com_gate_pendente(harness_root, cwd, session_id)
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
    assert result.stdout.strip() == "", result.stdout
    assert (bucket / "lifecycle.db").exists()
    assert not (harness_root / "lifecycle.db").exists()
    assert (harness_root / "heartbeats" / "PostCompact").exists()


def test_session_start_dispara_na_compactacao():
    """O hook de SessionStart nao pode filtrar fora o source "compact".

    E ele que carrega a retomada depois de /compact; um matcher como "startup"
    calaria a retomada sem erro nenhum.
    """
    grupos = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]["SessionStart"]
    com_session_start = [
        g for g in grupos
        if any("harness-session-start.sh" in h.get("command", "") for h in g.get("hooks", []))
    ]
    assert com_session_start
    for grupo in com_session_start:
        matcher = grupo.get("matcher", "")
        assert matcher in ("", "*") or re.fullmatch(matcher, "compact"), matcher


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash ausente no PATH")
def test_retomada_pos_compact_chega_pelo_session_start(tmp_path: Path):
    harness_root = tmp_path / "home" / ".claude" / "harness"
    cwd = tmp_path / "repo"
    cwd.mkdir()
    session_id = "session-a"
    _bucket_com_gate_pendente(harness_root, cwd, session_id)
    env = {
        **os.environ,
        "CLAUDE_PLUGIN_ROOT": str(ROOT),
        "HOME": str(tmp_path / "home"),
        "USERPROFILE": str(tmp_path / "home"),
        "HARNESS_DIR": str(harness_root),
        "HARNESS_SKIP_DEPCHECK": "1",
    }
    env.pop("AI_BRAIN_PATH", None)
    env.pop("VAULT_PATH", None)

    result = subprocess.run(
        ["bash", str(ROOT / "hooks" / "harness-session-start.sh")],
        input=json.dumps({
            "session_id": session_id,
            "cwd": str(cwd),
            "hook_event_name": "SessionStart",
            "source": "compact",
        }),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        env=env,
        cwd=str(cwd),
        timeout=90,
    )

    assert result.returncode == 0, result.stderr
    bloco = json.loads(result.stdout)["hookSpecificOutput"]
    assert bloco["hookEventName"] == "SessionStart"
    message = _mensagem(result.stdout)
    assert "HARNESS v3 RESUMING" in message
    assert "t-scoped" in message
    assert "Pending human gate: approve-spec" in message


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


# --- Pagina escrita no vault tem que entrar no indice (incidente 2026-09-03) --
#
# `_fechar_sessao` escreve o cartao direto em `wiki/sessions/` e nao registra
# nada. `index.md` e gerado a partir do disco por `tools/wiki_index.py`, e
# ninguem o roda depois de escrever.
#
# Medido em 2026-09-03: `wiki_lint` acusou 45 paginas fora do index — 42 cartoes
# de sessao e specs espelhadas, mais as tres escritas nesta sessao. `ready:
# False`, 90 erros. A pagina existia e a wiki nao sabia dela: para quem consulta
# pelo indice, ela nao existe.
#
# Regerar e barato e nao depende de Ollama: le markdown do disco, sem embedding.


def _load_lifecycle():
    import importlib.util
    caminho = ROOT / "hooks" / "harness-lifecycle.py"
    spec = importlib.util.spec_from_file_location("harness_lifecycle_para_teste", caminho)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_cartao_de_sessao_entra_no_index(tmp_path: Path, monkeypatch):
    vault = tmp_path / "vault" / "AI-Brain"
    (vault / "wiki").mkdir(parents=True)
    (vault / "wiki" / "index.md").write_text("# Index" + chr(10), encoding="utf-8")
    monkeypatch.setenv("AI_BRAIN_PATH", str(vault))
    monkeypatch.setenv("HARNESS_DIR", str(tmp_path / "harness"))

    hook = _load_lifecycle()
    hook._fechar_sessao({"session_id": "abcdef1234", "cwd": str(tmp_path / "proj")},
                        str(tmp_path / "harness"))

    cartoes = list((vault / "wiki" / "sessions").glob("*.md"))
    assert cartoes, "o cartao nao foi escrito"
    indice = (vault / "wiki" / "index.md").read_text(encoding="utf-8")
    assert cartoes[0].stem in indice, (
        "cartao escrito e fora do index: a pagina existe e a wiki nao sabe dela"
    )


def test_falha_ao_regerar_o_index_nao_derruba_o_encerramento(tmp_path: Path, monkeypatch):
    """Fechar sessao nao pode quebrar porque a wiki esta em estado ruim."""
    vault = tmp_path / "vault" / "AI-Brain"
    (vault / "wiki").mkdir(parents=True)
    monkeypatch.setenv("AI_BRAIN_PATH", str(vault))
    monkeypatch.setenv("HARNESS_DIR", str(tmp_path / "harness"))

    hook = _load_lifecycle()

    def explode(_vault):
        raise RuntimeError("sonda: wiki em estado ruim")

    monkeypatch.setattr(hook, "_regerar_index_do_vault", explode)
    hook._fechar_sessao({"session_id": "abcdef1234", "cwd": str(tmp_path / "proj")},
                        str(tmp_path / "harness"))
    assert list((vault / "wiki" / "sessions").glob("*.md")), "o cartao tem que sobreviver"
