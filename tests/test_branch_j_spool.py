"""Testes do spool da camada J em modo sombra (`scripts/branch_j_spool.py` e a ligação no `branch_sensor.main`).

Spec: `docs/specs/branch-camada-j-ligacao-spec.md` (US-1, REQ-001…REQ-003); design: `…-ligacao-design.md`.

O que fica travado aqui: o hook só grava com a flag ligada **e** uma janela aberta que ele não criou; nunca levanta,
nunca faz rede, nunca abre processo, nunca lê a chave; só grava turno humano; só cria o arquivo da sessão no primeiro
prompt dela (a âncora), e por isso sessões anteriores à janela, retomadas ou compactadas ficam fora.
"""
from __future__ import annotations

import builtins
import importlib.util
import io
import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
SID = "sessao-de-teste-0001"


def _carregar(nome):
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(nome, ROOT / "scripts" / f"{nome}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[nome] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def spool(monkeypatch, tmp_path):
    casa = tmp_path / "casa-mh"
    monkeypatch.setenv("MASTER_HARNESS_HOME", str(casa))
    monkeypatch.setenv("HARNESS_BRANCH_LAYER_J", "1")
    return _carregar("branch_j_spool")


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def abrir(mod, **troca):
    agora = datetime.now(timezone.utc)
    janela = {"schema": 1, "estado": "aberta", "inicio": _iso(agora - timedelta(hours=1)),
              "fim": _iso(agora + timedelta(days=14)), "tau": 0.77, "semente": 20261010,
              "sha_filtro": mod.SHA_FILTRO, "spec_commit": "b9a21f2"}
    janela.update(troca)
    p = mod.pasta()
    p.mkdir(parents=True, exist_ok=True)
    (p / "janela.json").write_text(json.dumps(janela), encoding="utf-8")
    return p


def payload(prompt, *, sid=SID, evento="UserPromptSubmit", cwd=None):
    return {"hook_event_name": evento, "session_id": sid, "prompt": prompt, "cwd": cwd or os.getcwd()}


def linhas(mod, sid=SID):
    arq = mod.pasta() / f"{sid}.jsonl"
    if not arq.exists():
        return None
    return [json.loads(x) for x in arq.read_text(encoding="utf-8").splitlines() if x.strip()]


def contagens(mod, sid=SID):
    arq = mod.pasta() / f"{sid}.contagens.json"
    return json.loads(arq.read_text(encoding="utf-8")) if arq.exists() else {}


HUMANO = "vamos revisar o plano da fase seguinte com calma"


# --- AC-1.1: portões -----------------------------------------------------------------------------------------------

def test_flag_desligada_nao_escreve_nada(spool, monkeypatch):
    abrir(spool)
    monkeypatch.setenv("HARNESS_BRANCH_LAYER_J", "0")
    spool.registrar(payload(HUMANO), ancora=True)
    assert sorted(x.name for x in spool.pasta().iterdir()) == ["janela.json"]


def test_sem_pasta_nem_janela_nao_cria_nada(spool):
    spool.registrar(payload(HUMANO), ancora=True)
    assert not spool.pasta().exists()


@pytest.mark.parametrize("troca", [
    {"estado": "encerrada"},
    {"fim": "2020-01-01T00:00:00Z"},
    {"sha_filtro": "0" * 64},
    {"fim": "nao-e-data"},
])
def test_janela_que_nao_esta_aberta_nao_escreve(spool, troca):
    abrir(spool, **troca)
    spool.registrar(payload(HUMANO), ancora=True)
    assert sorted(x.name for x in spool.pasta().iterdir()) == ["janela.json"]


def test_janela_ilegivel_vale_como_ausente(spool):
    p = abrir(spool)
    (p / "janela.json").write_text("{nao e json", encoding="utf-8")
    spool.registrar(payload(HUMANO), ancora=True)
    assert sorted(x.name for x in p.iterdir()) == ["janela.json"]


# --- AC-1.2 / AC-1.3: âncora e turnos ------------------------------------------------------------------------------

def test_ancora_cria_o_arquivo_com_a_linha_zero(spool):
    abrir(spool)
    spool.registrar(payload(HUMANO), ancora=True)
    ls = linhas(spool)
    assert len(ls) == 1
    l0 = ls[0]
    assert (l0["tipo"], l0["turno_idx"], l0["sessao"], l0["texto"]) == ("ancora", 0, SID, HUMANO)
    assert l0["instante"].endswith("Z") and l0["projeto"]


def test_texto_e_gravado_cru_e_inteiro(spool):
    abrir(spool)
    cru = "  " + "x" * 30_000 + "\n"
    spool.registrar(payload(cru), ancora=True)
    assert linhas(spool)[0]["texto"] == cru


def test_turnos_seguintes_acrescentam_com_indice(spool):
    abrir(spool)
    spool.registrar(payload(HUMANO), ancora=True)
    spool.registrar(payload("agora troque o nome da funcao"), ancora=False)
    spool.registrar(payload("e rode os testes de novo"), ancora=False)
    ls = linhas(spool)
    assert [(x["tipo"], x["turno_idx"]) for x in ls] == [("ancora", 0), ("turno", 1), ("turno", 2)]


def test_turno_sem_arquivo_da_sessao_nao_cria(spool):
    """Sessão que começou antes da janela (ou retomada, ou compactada) não tem âncora: fica fora."""
    abrir(spool)
    spool.registrar(payload(HUMANO), ancora=False)
    assert linhas(spool) is None


def test_ancora_nao_recriada_se_o_arquivo_ja_existe(spool):
    abrir(spool)
    spool.registrar(payload(HUMANO), ancora=True)
    spool.registrar(payload("outra abertura qualquer de sessao"), ancora=True)
    ls = linhas(spool)
    assert ls[0]["tipo"] == "ancora" and ls[0]["texto"] == HUMANO          # a âncora não muda
    assert [x["tipo"] for x in ls] == ["ancora", "turno"]                  # e o prompt não some


def test_sessao_excluida_nao_recebe_mais_linhas(spool):
    p = abrir(spool)
    (p / f"{SID}.jsonl").write_text(json.dumps({"excluida": "2026-10-10T00:00:00Z"}) + "\n", encoding="utf-8")
    spool.registrar(payload("mais um turno da sessao excluida"), ancora=False)
    assert len(linhas(spool)) == 1


# --- AC-1.4: filtro de turno humano --------------------------------------------------------------------------------

def test_lista_de_prefixos_e_a_da_spec(spool):
    assert spool.PREFIXOS == (
        "<task-notification>", "<command-", "<cross-session-message", "<local-command",
        "Base directory for this skill:", "Caveat: The messages below", "This session is being continued",
        "[Request interrupted", "Continue from where you left off.",
    )
    assert spool.MIN_LEN == 12


@pytest.mark.parametrize("i", range(9))
def test_prefixo_nao_humano_nao_grava_e_conta(spool, i):
    abrir(spool)
    prefixo = spool.PREFIXOS[i]
    spool.registrar(payload("  \n" + prefixo + " resto do texto injetado pelo host"), ancora=True)
    assert linhas(spool) is None
    c = contagens(spool)
    assert c.get(prefixo) == 1 and c.get("sessao_sem_ancora_humana") == 1


def test_prefixo_em_outra_caixa_e_humano(spool):
    abrir(spool)
    spool.registrar(payload("continue from where you left off. e agora o resto"), ancora=True)
    assert linhas(spool)[0]["tipo"] == "ancora"


def test_curto_conta_e_doze_code_points_grava(spool):
    abrir(spool)
    spool.registrar(payload("  " + "a" * 11 + "  "), ancora=True)
    assert linhas(spool) is None and contagens(spool).get("curto") == 1
    spool.registrar(payload("ã" * 12), ancora=True)
    assert linhas(spool)[0]["texto"] == "ã" * 12


def test_turno_filtrado_nao_desloca_o_indice(spool):
    abrir(spool)
    spool.registrar(payload(HUMANO), ancora=True)
    spool.registrar(payload("<task-notification> x </task-notification>"), ancora=False)
    spool.registrar(payload("seguindo com o trabalho real"), ancora=False)
    assert [x["turno_idx"] for x in linhas(spool)] == [0, 1]


# --- AC-1.5: entradas inválidas ------------------------------------------------------------------------------------

@pytest.mark.parametrize("p", [
    payload(HUMANO, sid=""),
    payload(HUMANO, sid="../fora"),
    payload(HUMANO, sid="a" * 65),
    payload(HUMANO, sid="curto"),
    payload(HUMANO, evento="Stop"),
    {"hook_event_name": "UserPromptSubmit", "session_id": SID, "prompt": None},
    {"hook_event_name": "UserPromptSubmit", "session_id": SID},
    {"hook_event_name": "UserPromptSubmit", "session_id": SID, "prompt": ["lista"]},
])
def test_entrada_invalida_nao_grava(spool, p):
    abrir(spool)
    spool.registrar(p, ancora=True)
    assert sorted(x.name for x in spool.pasta().iterdir()) == ["janela.json"]


# --- AC-1.6: nunca levanta -----------------------------------------------------------------------------------------

def test_falha_de_escrita_e_engolida_e_contada(spool, monkeypatch):
    abrir(spool)
    real = os.open

    def falha(caminho, *a, **k):
        if str(caminho).endswith(".jsonl"):
            raise OSError("disco cheio")
        return real(caminho, *a, **k)

    monkeypatch.setattr(spool.os, "open", falha)
    spool.registrar(payload(HUMANO), ancora=True)
    assert contagens(spool).get("falha_spool") == 1


def test_contagens_tambem_falhando_nao_levanta(spool, monkeypatch):
    abrir(spool)
    monkeypatch.setattr(spool.os, "open", lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    monkeypatch.setattr(spool.os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("y")))
    spool.registrar(payload(HUMANO), ancora=True)      # não levanta


def test_payload_que_nao_e_dict_nao_levanta(spool):
    abrir(spool)
    spool.registrar(None, ancora=True)
    spool.registrar("texto", ancora=False)


# --- AC-1.7: teto de tamanho ---------------------------------------------------------------------------------------

def test_spool_cheio_nao_grava_e_conta(spool, monkeypatch):
    p = abrir(spool)
    (p / "outra-sessao-000001.jsonl").write_bytes(b"x" * 100)
    monkeypatch.setattr(spool, "LIMITE_BYTES", 100)
    spool.registrar(payload(HUMANO), ancora=True)
    assert linhas(spool) is None and contagens(spool).get("spool_cheio") == 1


def test_limite_padrao_e_200_mib(spool):
    assert spool.LIMITE_BYTES == 200 * 2 ** 20


# --- AC-1.8: sem rede, sem processo, sem chave, rápido -------------------------------------------------------------

def test_sem_rede_sem_processo_sem_ler_segredos(spool, monkeypatch):
    abrir(spool)
    segredos = spool.casa() / "segredos"
    segredos.mkdir(parents=True)
    (segredos / "typesafe.key").write_text("ts-nao-pode-ser-lida", encoding="utf-8")

    def proibido(*a, **k):
        raise AssertionError("rede ou processo no hook")

    monkeypatch.setattr(socket, "socket", proibido)
    monkeypatch.setattr(socket, "create_connection", proibido)
    monkeypatch.setattr(subprocess, "Popen", proibido)
    abertos = []
    real_open, real_os_open = builtins.open, os.open

    def vigia_open(f, *a, **k):
        abertos.append(str(f))
        return real_open(f, *a, **k)

    def vigia_os_open(f, *a, **k):
        abertos.append(str(f))
        return real_os_open(f, *a, **k)

    monkeypatch.setattr(builtins, "open", vigia_open)
    monkeypatch.setattr(io, "open", vigia_open)
    monkeypatch.setattr(os, "open", vigia_os_open)
    spool.registrar(payload(HUMANO), ancora=True)
    spool.registrar(payload("mais um turno qualquer aqui"), ancora=False)
    assert len(linhas(spool)) == 2
    assert not [x for x in abertos if "segredos" in x]


def test_p95_do_registrar_abaixo_de_20_ms(spool):
    abrir(spool)
    spool.registrar(payload(HUMANO), ancora=True)
    tempos = []
    for i in range(1000):
        t0 = time.perf_counter()
        spool.registrar(payload(f"turno numero {i} da medida"), ancora=False)
        tempos.append(time.perf_counter() - t0)
    tempos.sort()
    assert tempos[949] <= 0.020, f"p95 = {tempos[949] * 1000:.1f} ms"


# --- ligação no branch_sensor.main ---------------------------------------------------------------------------------

@pytest.fixture
def sensor(spool):
    return _carregar("branch_sensor")


def _main(sensor, monkeypatch, capsys, p):
    monkeypatch.setattr(sensor.sys, "stdin", io.StringIO(json.dumps(p)))
    rc = sensor.main()
    return rc, capsys.readouterr().out


def test_main_primeiro_prompt_cria_a_ancora_do_spool(sensor, spool, monkeypatch, capsys, tmp_path):
    abrir(spool)
    monkeypatch.setattr(sensor, "embed", lambda _t: None)
    rc, _ = _main(sensor, monkeypatch, capsys, payload(HUMANO, cwd=str(tmp_path)))
    assert rc == 0
    assert linhas(spool)[0]["tipo"] == "ancora"
    rc, _ = _main(sensor, monkeypatch, capsys, payload("seguindo o trabalho normal agora", cwd=str(tmp_path)))
    assert [x["tipo"] for x in linhas(spool)] == ["ancora", "turno"]


def test_main_sessao_anterior_a_janela_nao_grava(sensor, spool, monkeypatch, capsys, tmp_path):
    sensor.set_anchor(cwd=str(tmp_path), text="objetivo de antes", source="first-prompt", session_id=SID)
    abrir(spool)
    _main(sensor, monkeypatch, capsys, payload("seguindo o trabalho normal agora", cwd=str(tmp_path)))
    assert linhas(spool) is None


def test_main_saida_identica_com_e_sem_flag(sensor, spool, monkeypatch, capsys, tmp_path):
    abrir(spool)
    monkeypatch.setattr(sensor, "embed", lambda _t: None)
    saidas = []
    for flag, sub in (("0", "a"), ("1", "b")):
        monkeypatch.setenv("HARNESS_BRANCH_LAYER_J", flag)
        cwd = tmp_path / sub
        cwd.mkdir()
        s = "sessao-saida-" + sub * 6
        _main(sensor, monkeypatch, capsys, payload(HUMANO, sid=s, cwd=str(cwd)))
        saidas.append(_main(sensor, monkeypatch, capsys, payload("e se a gente fizesse outra coisa", sid=s,
                                                                  cwd=str(cwd))))
    assert saidas[0] == saidas[1]


def test_main_nao_levanta_se_o_spool_explode(sensor, spool, monkeypatch, capsys, tmp_path):
    abrir(spool)
    monkeypatch.setattr(sensor, "embed", lambda _t: None)
    monkeypatch.setattr(sensor.branch_j_spool, "registrar", lambda *a, **k: 1 / 0)
    rc, _ = _main(sensor, monkeypatch, capsys, payload(HUMANO, cwd=str(tmp_path)))
    assert rc == 0


# --- gap-closure da verify-multimodel (2026-10-10) ------------------------------------------------------------------

def _transcript(tmp_path, nome, prompts):
    """Transcript com `prompts` humanos anteriores (formato do Claude Code: content em lista de itens `text`)."""
    arq = tmp_path / f"{nome}.jsonl"
    linhas = [json.dumps({"type": "user", "message": {"content": [{"type": "text", "text": p}]}}) for p in prompts]
    linhas.append(json.dumps({"type": "user", "message": {"content": [{"type": "tool_result", "content": "x"}]}}))
    arq.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    return str(arq)


def test_ancora_com_arquivo_existente_vira_turno(spool):
    """Sessões no mesmo cwd se alternam e o sensor re-ancora: o turno não pode sumir sem contagem."""
    abrir(spool)
    spool.registrar(payload(HUMANO), ancora=True)
    spool.registrar(payload("segundo turno depois da reancoragem"), ancora=True)
    assert [(x["tipo"], x["turno_idx"]) for x in linhas(spool)] == [("ancora", 0), ("turno", 1)]


def test_reancoragem_de_sessao_antiga_nao_cria_ancora(spool, tmp_path):
    abrir(spool)
    p = payload("um turno do meio de uma sessao antiga")
    p["transcript_path"] = _transcript(tmp_path, "antiga", ["primeiro prompt de ontem", "segundo prompt de ontem"])
    spool.registrar(p, ancora=True)
    assert linhas(spool) is None and contagens(spool).get("sessao_preexistente") == 1


def test_primeiro_prompt_com_transcript_so_dele_cria_ancora(spool, tmp_path):
    abrir(spool)
    p = payload(HUMANO)
    p["transcript_path"] = _transcript(tmp_path, "nova", [HUMANO])
    spool.registrar(p, ancora=True)
    assert linhas(spool)[0]["tipo"] == "ancora"


def test_main_duas_sessoes_no_mesmo_cwd_nao_perdem_turnos(sensor, spool, monkeypatch, capsys, tmp_path):
    abrir(spool)
    monkeypatch.setattr(sensor, "embed", lambda _t: None)
    a, b = "sessao-aaaa-aaaa", "sessao-bbbb-bbbb"
    cwd = str(tmp_path / "mesmo")
    (tmp_path / "mesmo").mkdir()
    pa = payload("objetivo da sessao A agora", sid=a, cwd=cwd)
    pa["transcript_path"] = _transcript(tmp_path, "a1", [])
    _main(sensor, monkeypatch, capsys, pa)
    pb = payload("objetivo da sessao B agora", sid=b, cwd=cwd)
    pb["transcript_path"] = _transcript(tmp_path, "b1", [])
    _main(sensor, monkeypatch, capsys, pb)
    pa2 = payload("segundo turno da sessao A", sid=a, cwd=cwd)
    pa2["transcript_path"] = _transcript(tmp_path, "a2", ["objetivo da sessao A agora"])
    _main(sensor, monkeypatch, capsys, pa2)
    assert [x["tipo"] for x in linhas(spool, a)] == ["ancora", "turno"]
    assert [x["tipo"] for x in linhas(spool, b)] == ["ancora"]


def test_surrogate_solto_e_gravado(spool):
    abrir(spool)
    spool.registrar(payload("texto com surrogate \ud800 solto no meio"), ancora=True)
    assert linhas(spool)[0]["texto"] == "texto com surrogate \ud800 solto no meio"


def test_p95_com_muitas_sessoes_na_pasta(spool):
    p = abrir(spool)
    for i in range(300):
        (p / f"sessao-velha-{i:06d}.jsonl").write_bytes(b"x" * 2000)
    spool.registrar(payload(HUMANO), ancora=True)
    tempos = []
    for i in range(300):
        t0 = time.perf_counter()
        spool.registrar(payload(f"turno numero {i} da medida"), ancora=False)
        tempos.append(time.perf_counter() - t0)
    tempos.sort()
    assert tempos[284] <= 0.020, f"p95 = {tempos[284] * 1000:.1f} ms"


def test_import_quebrado_do_spool_nao_quebra_o_sensor(spool, monkeypatch, capsys, tmp_path):
    real = builtins.__import__

    def importar(nome, *a, **k):
        if nome == "branch_j_spool":
            raise ImportError("spool quebrado")
        return real(nome, *a, **k)

    sys.modules.pop("branch_j_spool", None)
    monkeypatch.setattr(builtins, "__import__", importar)
    sensor = _carregar("branch_sensor")
    monkeypatch.setattr(builtins, "__import__", real)
    monkeypatch.setattr(sensor, "embed", lambda _t: None)
    rc, _ = _main(sensor, monkeypatch, capsys, payload(HUMANO, cwd=str(tmp_path)))
    assert rc == 0
