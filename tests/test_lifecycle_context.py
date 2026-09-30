from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
sys.path.insert(0, str(ROOT / "scripts"))
from harness_paths import ensure_state_dir  # type: ignore[import-not-found]
from transactional_state import HarnessDatabase  # type: ignore[import-not-found]


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
    """Task L2 viva com gate pendente, no banco e na projecao.

    O `harness.db` e a autoridade: o SessionStart pergunta a ele
    (`continuation_policy.task_viva`) se ha task viva. O `state.json` e so a
    projecao, que o lifecycle ainda le. Mesmo caminho de `TestPerguntaUnica` em
    test_ciclo_de_vida_da_task.py: `scope_id` e o proprio balde.
    """
    bucket = ensure_state_dir(harness_root, cwd, session_id=session_id)
    db = HarnessDatabase(bucket)
    db.start_task(
        scope_id=str(bucket), legacy_level="L2-feature", tier="L2", kind="feature",
        pipeline=["write-spec", "approve-spec", "design-doc"], prompt="x",
        task_id="t-scoped",
    )
    db.open_gate("t-scoped", "approve-spec")
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


# --- SubagentStart registra e fica calado (incidente 2026-09-28) -------------
#
# O SubagentStart mandava todo subagente "Invoke skill='harness-workflow' and
# continue from this exact state". No run wf_8ec3454a-257 (wf-grill), 3 das 5
# lentes carregaram o orquestrador antes de ler a spec: de 13,2 k a 16,4 k
# tokens a mais por lente, medidos pelo `usage` do transcript, e a regra 4 de
# fan-out ("Descontaminar a aresta") desfeita — o juiz recebia o estado da task.
# Em 1 525 transcripts de subagente da maquina, 49 carregaram a skill e dois
# (um `Plan`, um `general-purpose`) rodaram `confirm_classification`/`state_cli`
# sobre a task do PAI.
#
# Nao ha como emitir "so para subagente de pipeline": o payload traz apenas
# `agent_id` e `agent_type`, e `analise-complexa` chega igual de um Workflow
# (wf-verify-multimodel) e da ferramenta Agent. E nenhum subagente executa fase
# — as fases rodam na sessao principal. Decisao do usuario em 2026-09-30:
# nunca. A retomada da sessao principal continua pelo SessionStart
# (`test_retomada_pos_compact_chega_pelo_session_start`, acima).

# Ultimo commit antes da mudanca: o controle roda o hook que emitia RESUMING.
OLD_REF = "18ec682eedd2340c66d41dcb0c5d6e3cdb38c7fa"

TIPOS_DE_SUBAGENTE = (
    "workflow-subagent",  # Workflow sem agentType (wf-grill)
    "analise-complexa",   # Workflow com agentType E ferramenta Agent
    "juiz-alto-risco",    # adjudicador do wf-verify-multimodel
    "general-purpose",    # ferramenta Agent; rodou confirm_classification no pai
    "Plan",               # ferramenta Agent; rodou state_cli no pai
)


def _rodar_subagent_start(hook: Path, harness_root: Path, cwd: Path, agent_type: str):
    env = os.environ.copy()
    env["HARNESS_DIR"] = str(harness_root)
    env["PYTHONPATH"] = str(ROOT / "scripts")
    return subprocess.run(
        [sys.executable, str(hook), "--event", "SubagentStart"],
        input=json.dumps({
            "session_id": "session-b", "cwd": str(cwd),
            "hook_event_name": "SubagentStart",
            "agent_id": "a-1", "agent_type": agent_type,
        }),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _task_viva(tmp_path: Path) -> tuple[Path, Path, Path]:
    harness_root = tmp_path / "harness"
    cwd = tmp_path / "repo"
    cwd.mkdir()
    bucket = _bucket_com_gate_pendente(harness_root, cwd, "session-b")
    return harness_root, cwd, bucket


@pytest.mark.parametrize("agent_type", TIPOS_DE_SUBAGENTE)
def test_subagent_start_registra_e_nao_emite(tmp_path: Path, agent_type: str):
    harness_root, cwd, bucket = _task_viva(tmp_path)

    result = _rodar_subagent_start(
        ROOT / "hooks" / "harness-lifecycle.py", harness_root, cwd, agent_type)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "", (
        f"SubagentStart emitiu para {agent_type}: {result.stdout[:200]}")
    with sqlite3.connect(bucket / "lifecycle.db") as conexao:
        eventos = [linha[0] for linha in conexao.execute("SELECT event FROM lifecycle_events")]
    assert eventos == ["SubagentStart"], "o evento tem que continuar registrado"
    assert (harness_root / "heartbeats" / "SubagentStart").exists()


def test_CONTROLE_codigo_antigo_mandava_subagente_carregar_o_orquestrador(tmp_path: Path):
    """Metade de falsificacao: o mesmo cenario, no hook de OLD_REF, emite RESUMING.

    Sem isto o teste acima poderia passar por outro motivo (hook que nao roda,
    balde errado, estado nao lido) e seria indistinguivel de um conserto.
    """
    antigo = tmp_path / "antigo" / "hooks"
    antigo.mkdir(parents=True)
    for nome in ("harness-lifecycle.py", "emit.py"):
        conteudo = subprocess.run(
            ["git", "show", f"{OLD_REF}:hooks/{nome}"],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=True,
        ).stdout
        (antigo / nome).write_text(conteudo, encoding="utf-8")
    harness_root, cwd, _ = _task_viva(tmp_path)

    result = _rodar_subagent_start(
        antigo / "harness-lifecycle.py", harness_root, cwd, "workflow-subagent")

    assert result.returncode == 0, result.stderr
    mensagem = _mensagem(result.stdout)
    assert "HARNESS v3 RESUMING: scoped task t-scoped." in mensagem
    assert "Invoke skill='harness-workflow'" in mensagem


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
