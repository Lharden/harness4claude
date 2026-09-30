"""Roteamento de modelo e esforco para sessoes criadas por chip.

Spec: docs/specs/roteamento-de-sessoes-spec-light.md (1 AC = 1 teste).

O defeito de hoje (AC-1): `spawn_task` aceita um chip sem nenhum roteamento, e
a filha nasce no nivel da mae. O hook `PreToolUse` fecha isso. Cada teste roda
o hook de verdade, como subprocesso, com o JSON que o Claude Code manda, uma
pasta de agentes FALSA e um HARNESS_DIR temporario.

Controle: `HARNESS_ROTEAMENTO=0` desliga o gate. O AC-1 herda o ambiente de
quem chamou o pytest; com a variavel em 0 ele fica vermelho (o chip passa), que
e a prova de que ele mede o defeito. `test_controle_gate_desligado_deixa_passar`
trava o mesmo no proprio teste.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
HOOK = ROOT / "hooks" / "harness-roteamento.py"
SH = ROOT / "hooks" / "harness-roteamento.sh"
sys.path.insert(0, str(ROOT / "scripts"))

TOOL = "mcp__ccd_session__spawn_task"
AGENTES = {
    "juiz-alto-risco": ("opus", "high"),
    "analise-complexa": ("opus", "medium"),
    "execucao-mecanica": ("sonnet", "medium"),
    "busca-leve": ("haiku", "low"),
}


@pytest.fixture()
def agentes(tmp_path):
    d = tmp_path / "agents"
    d.mkdir()
    for tipo, (modelo, esforco) in AGENTES.items():
        (d / f"{tipo}.md").write_text(
            f"---\nname: {tipo}\ndescription: x\nmodel: {modelo}\neffort: {esforco}\n---\n\ncorpo\n",
            encoding="utf-8",
        )
    # um agente que NAO e da tabela de roteamento
    (d / "outro.md").write_text("---\nname: outro\nmodel: opus\neffort: max\n---\n", encoding="utf-8")
    return d


@pytest.fixture()
def amb(tmp_path, agentes):
    harness = tmp_path / "harness"
    harness.mkdir()
    env = os.environ.copy()
    env["HARNESS_DIR"] = str(harness)
    env["HARNESS_AGENTS_DIR"] = str(agentes)
    return env, harness, tmp_path


def _rodar(env, payload, evento):
    return subprocess.run(
        [sys.executable, str(HOOK), "--event", evento],
        input=json.dumps(payload), capture_output=True, text=True,
        encoding="utf-8", env=env, check=False,
    )


def _pre(prompt, sid="mae-1", cwd=None):
    return {"session_id": sid, "cwd": str(cwd or ROOT), "hook_event_name": "PreToolUse",
            "tool_name": TOOL, "tool_input": {"prompt": prompt, "title": "t"}}


def _ups(prompt, sid, cwd=None):
    return {"session_id": sid, "cwd": str(cwd or ROOT), "hook_event_name": "UserPromptSubmit",
            "prompt": prompt}


def _registro(env, sid="mae-1", cwd=None):
    import harness_paths
    d = harness_paths.state_dir(env["HARNESS_DIR"], cwd or ROOT, session_id=sid)
    p = d / "roteamento-chips.jsonl"
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def _saida(proc):
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout.strip()
    return json.loads(out) if out else {}


def _decisao(proc):
    return _saida(proc).get("hookSpecificOutput", {}).get("permissionDecision")


def _sha(prompt_ate_a_linha):
    return hashlib.sha256(prompt_ate_a_linha.strip().encode("utf-8")).hexdigest()[:12]


# ---- AC-1 -----------------------------------------------------------------

def test_ac1_chip_sem_linha_de_roteamento_e_negado(amb):
    env, _, _ = amb
    proc = _rodar(env, _pre("Faca a coisa X no repo."), "PreToolUse")
    saida = _saida(proc)
    bloco = saida["hookSpecificOutput"]
    assert bloco["hookEventName"] == "PreToolUse"
    assert bloco["permissionDecision"] == "deny"
    motivo = bloco["permissionDecisionReason"]
    assert "Roteamento:" in motivo and "execucao-mecanica" in motivo  # motivo + tabela
    assert _registro(env) == []


def test_controle_gate_desligado_deixa_passar(amb):
    """O defeito de hoje, reproduzido: sem o gate o chip sem roteamento passa."""
    env, _, _ = amb
    env["HARNESS_ROTEAMENTO"] = "0"
    proc = _rodar(env, _pre("Faca a coisa X no repo."), "PreToolUse")
    assert _decisao(proc) != "deny"
    assert _registro(env) == []


# ---- AC-2 -----------------------------------------------------------------

def test_ac2_par_fora_do_cabecalho_e_negado(amb):
    env, _, _ = amb
    p = "Analise Y.\n\nRoteamento: opus · xhigh (analise-complexa)"
    proc = _rodar(env, _pre(p), "PreToolUse")
    assert _decisao(proc) == "deny"
    assert "medium" in _saida(proc)["hookSpecificOutput"]["permissionDecisionReason"]
    assert _registro(env) == []


def test_tipo_fora_dos_quatro_e_negado(amb):
    env, _, _ = amb
    # `outro` existe na pasta de agentes, mas nao e um dos 4; `livre` nao existe (NC-1)
    for tipo, par in (("outro", "opus · max"), ("livre", "opus · medium")):
        proc = _rodar(env, _pre(f"X.\nRoteamento: {par} ({tipo})"), "PreToolUse")
        assert _decisao(proc) == "deny", tipo


# ---- AC-3 -----------------------------------------------------------------

def test_ac3_linha_valida_passa_e_anota_o_chip(amb):
    env, _, _ = amb
    p = "Implemente Z.\n\nRoteamento: sonnet · medium (execucao-mecanica)"
    proc = _rodar(env, _pre(p), "PreToolUse")
    assert _decisao(proc) != "deny"
    linhas = _registro(env)
    assert len(linhas) == 1
    r = linhas[0]
    assert (r["tipo"], r["modelo"], r["esforco"]) == ("execucao-mecanica", "sonnet", "medium")
    assert r["prompt_sha"] == _sha(p)
    assert r["ts"]


def test_chip_com_ponto_medio_passa_com_stdin_na_codificacao_do_locale(amb):
    """Como o Claude Code chama: bytes UTF-8 no pipe, sem PYTHONUTF8 nem PYTHONIOENCODING.

    No Windows o stdin em pipe e decodificado em cp1252 por padrao e o "·"
    (C2 B7) chegava como "Â·": parse_linha devolvia None e todo chip era negado.
    Medido em 2026-09-30.
    """
    env, _, _ = amb
    env = {k: v for k, v in env.items() if k not in ("PYTHONUTF8", "PYTHONIOENCODING")}
    p = "Implemente Z.\n\nRoteamento: opus · medium (analise-complexa)"
    corpo = json.dumps(_pre(p), ensure_ascii=False).encode("utf-8")
    proc = subprocess.run(
        [sys.executable, str(HOOK), "--event", "PreToolUse"],
        input=corpo, capture_output=True, env=env, check=False,
    )
    saida = proc.stdout.decode("utf-8").strip()
    dec = (json.loads(saida) if saida else {}).get("hookSpecificOutput", {}).get("permissionDecision")
    assert dec != "deny", saida


def test_linha_so_vale_no_fim_do_prompt(amb):
    env, _, _ = amb
    p = "Roteamento: sonnet · medium (execucao-mecanica)\nmas o prompt continua depois"
    assert _decisao(_rodar(env, _pre(p), "PreToolUse")) == "deny"


def test_cabecalho_ausente_nega_com_motivo(amb, agentes):
    env, _, _ = amb
    (agentes / "execucao-mecanica.md").unlink()
    p = "Z.\nRoteamento: sonnet · medium (execucao-mecanica)"
    proc = _rodar(env, _pre(p), "PreToolUse")
    assert _decisao(proc) == "deny"
    assert _registro(env) == []


def test_outra_ferramenta_passa_sem_tocar_em_nada(amb):
    env, _, _ = amb
    payload = _pre("sem linha")
    payload["tool_name"] = "Bash"
    proc = _rodar(env, payload, "PreToolUse")
    assert _saida(proc) == {}


# ---- AC-4 -----------------------------------------------------------------

def test_ac4_filha_recebe_a_instrucao_uma_vez_por_sessao(amb):
    env, _, _ = amb
    prompt = "Implemente Z.\n\nRoteamento: sonnet · medium (execucao-mecanica)"
    p1 = _rodar(env, _ups(prompt, "filha-1"), "UserPromptSubmit")
    bloco = _saida(p1)["hookSpecificOutput"]
    assert bloco["hookEventName"] == "UserPromptSubmit"
    ctx = bloco["additionalContext"]
    assert "sonnet" in ctx and "medium" in ctx and "execucao-mecanica" in ctx
    assert _sha(prompt) in ctx
    assert "get_session" in ctx and "nascimento" in ctx
    p2 = _rodar(env, _ups(prompt, "filha-1"), "UserPromptSubmit")
    assert _saida(p2) == {}
    # outra sessao recebe de novo
    p3 = _rodar(env, _ups(prompt, "filha-2"), "UserPromptSubmit")
    assert "additionalContext" in _saida(p3)["hookSpecificOutput"]


def test_filha_com_par_fora_da_tabela_nao_recebe_instrucao(amb):
    env, _, _ = amb
    p = _rodar(env, _ups("X\nRoteamento: opus · max (analise-complexa)", "filha-3"), "UserPromptSubmit")
    assert _saida(p) == {}


def test_nc2_instrucao_manda_avisar_e_seguir_sem_mae(amb):
    env, _, _ = amb
    prompt = "Z.\nRoteamento: haiku · low (busca-leve)"
    ctx = _saida(_rodar(env, _ups(prompt, "filha-4"), "UserPromptSubmit"))[
        "hookSpecificOutput"]["additionalContext"]
    assert "nivel herdado" in ctx


# ---- AC-5 -----------------------------------------------------------------

def _msg_mae(par, sha):
    return (f'<cross-session-message from="filha-1">[roteamento] {par} prompt_sha={sha}'
            "</cross-session-message>")


def test_ac5_mae_aplica_o_que_ela_registrou_e_recusa_o_desconhecido(amb):
    env, _, _ = amb
    p = "Implemente Z.\n\nRoteamento: sonnet · medium (execucao-mecanica)"
    _rodar(env, _pre(p, sid="mae-9"), "PreToolUse")
    sha = _sha(p)
    par = "sonnet · medium (execucao-mecanica)"

    ok = _saida(_rodar(env, _ups(_msg_mae(par, sha), "mae-9"), "UserPromptSubmit"))
    ctx = ok["hookSpecificOutput"]["additionalContext"]
    assert "set_session_model" in ctx and "set_session_effort" in ctx
    assert "[roteamento-ok]" in ctx and "sonnet" in ctx and "medium" in ctx

    no = _saida(_rodar(env, _ups(_msg_mae(par, "deadbeef0000"), "mae-9"), "UserPromptSubmit"))
    ctx = no["hookSpecificOutput"]["additionalContext"]
    assert "[roteamento-recusado" in ctx
    assert "set_session_model" not in ctx and "set_session_effort" not in ctx


def test_mae_nao_aplica_par_que_difere_do_registro(amb):
    env, _, _ = amb
    p = "Z.\nRoteamento: sonnet · medium (execucao-mecanica)"
    _rodar(env, _pre(p, sid="mae-8"), "PreToolUse")
    # a filha pede outro par com o sha certo: recusa
    pedido = _msg_mae("opus · max (analise-complexa)", _sha(p))
    ctx = _saida(_rodar(env, _ups(pedido, "mae-8"), "UserPromptSubmit"))[
        "hookSpecificOutput"]["additionalContext"]
    assert "[roteamento-recusado" in ctx
    assert "set_session_effort" not in ctx


# ---- AC-6 -----------------------------------------------------------------

@pytest.mark.parametrize("resposta", [
    '<cross-session-message from="mae-9">[roteamento-ok]</cross-session-message>',
    '<cross-session-message from="mae-9">[roteamento-recusado: x]</cross-session-message>',
])
def test_ac6_respostas_nao_disparam_nada(amb, resposta):
    env, _, _ = amb
    assert _saida(_rodar(env, _ups(resposta, "filha-1"), "UserPromptSubmit")) == {}
    assert _saida(_rodar(env, _ups(resposta, "mae-9"), "UserPromptSubmit")) == {}


def test_prompt_comum_nao_emite_nada(amb):
    env, _, _ = amb
    assert _saida(_rodar(env, _ups("oi, tudo bem?", "s-1"), "UserPromptSubmit")) == {}


# ---- NC-4: telemetria de nascimento --------------------------------------

def test_nc4_nascimento_grava_modelo_e_esforco(amb):
    env, harness, tmp = amb
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "roteamento.py"), "nascimento",
         "--session-id", "filha-7", "--cwd", str(ROOT), "--modelo", "claude-opus-4-7",
         "--esforco", "max", "--tipo", "busca-leve"],
        capture_output=True, text=True, encoding="utf-8", env=env, check=False)
    assert proc.returncode == 0, proc.stderr
    import harness_paths
    d = harness_paths.state_dir(str(harness), ROOT, session_id="filha-7")
    linhas = [json.loads(x) for x in (d / "roteamento-nascimento.jsonl").read_text(
        encoding="utf-8").splitlines()]
    assert linhas[0]["modelo"] == "claude-opus-4-7" and linhas[0]["esforco"] == "max"
    assert linhas[0]["session_id"] == "filha-7" and linhas[0]["tipo"] == "busca-leve"


# ---- leitura dos cabecalhos (scripts/roteamento.py) ------------------------

def test_tabela_le_so_os_quatro_tipos_do_cabecalho(agentes):
    import roteamento
    assert roteamento.carregar_tabela(agentes) == AGENTES


def test_parse_da_linha():
    import roteamento
    r = roteamento.parse_linha("a\nb\nRoteamento: Opus · High (juiz-alto-risco)\n")
    assert r == ("opus", "high", "juiz-alto-risco")
    assert roteamento.parse_linha("sem linha") is None


# ---- filtro bash -----------------------------------------------------------

def _bash():
    from conftest import _bash_executable
    return _bash_executable()


@pytest.mark.skipif(_bash() is None, reason="sem bash")
def test_filtro_bash_so_chama_python_com_marcador(amb):
    env, _, _ = amb

    def sh(payload, evento):
        return subprocess.run([_bash(), str(SH), evento], input=json.dumps(payload),
                              capture_output=True, text=True, encoding="utf-8", env=env)

    assert sh(_ups("oi", "s-2"), "UserPromptSubmit").stdout.strip() == ""
    com = sh(_ups("X\nRoteamento: haiku · low (busca-leve)", "s-3"), "UserPromptSubmit")
    assert "additionalContext" in com.stdout
    neg = sh(_pre("sem linha"), "PreToolUse")
    assert '"permissionDecision": "deny"' in neg.stdout
    env["HARNESS_ROTEAMENTO"] = "0"
    assert sh(_pre("sem linha"), "PreToolUse").stdout.strip() == ""


# ---- hooks.json ------------------------------------------------------------

def test_hooks_json_registra_os_dois_eventos():
    dados = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    pre = [g for g in dados["PreToolUse"]
           if any("harness-roteamento.sh" in h["command"] for h in g["hooks"])]
    assert len(pre) == 1 and pre[0]["matcher"] == TOOL
    ups = [g for g in dados["UserPromptSubmit"]
           if any("harness-roteamento.sh" in h["command"] for h in g["hooks"])]
    assert len(ups) == 1
