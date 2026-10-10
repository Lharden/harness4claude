"""Testes do spool da camada J em modo sombra (`scripts/branch_j_spool.py`, `hooks/branch_j_inicio.py` e a ligação no
`branch_sensor.main`). Spec `docs/specs/branch-camada-j-ligacao-spec.md` v4 (US-1, REQ-001…REQ-003).

O que fica travado: a sessão entra na amostra só se o `SessionStart` dela (startup/clear) aconteceu com a janela
aberta e a flag ligada; a âncora é o primeiro prompt humano depois disso; o hook nunca levanta, nunca faz rede, nunca
abre processo, nunca lê a chave, nunca cria diretório, e só acrescenta a um spool que já existe.
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
HUMANO = "vamos revisar o plano da fase seguinte com calma"


def _carregar(nome):
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(nome, ROOT / "scripts" / f"{nome}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[nome] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def spool(monkeypatch, tmp_path):
    monkeypatch.setenv("MASTER_HARNESS_HOME", str(tmp_path / "casa-mh"))
    monkeypatch.setenv("HARNESS_BRANCH_LAYER_J", "1")
    return _carregar("branch_j_spool")


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def abrir(mod, **troca):
    agora = datetime.now(timezone.utc)
    janela = {"schema": 1, "estado": "aberta", "inicio": _iso(agora - timedelta(hours=1)),
              "fim": _iso(agora + timedelta(days=14)), "sha_filtro": mod.SHA_FILTRO}
    janela.update(troca)
    p = mod.pasta()
    p.mkdir(parents=True, exist_ok=True)
    (p / "janela.json").write_text(json.dumps(janela), encoding="utf-8")
    return p


def inicio(sid=SID, source="startup"):
    return {"hook_event_name": "SessionStart", "session_id": sid, "source": source}


def payload(prompt, *, sid=SID, evento="UserPromptSubmit", cwd=None):
    return {"hook_event_name": evento, "session_id": sid, "prompt": prompt, "cwd": cwd or os.getcwd()}


def linhas(mod, sid=SID):
    arq = mod.pasta() / f"{sid}.jsonl"
    if not arq.exists():
        return None
    return [json.loads(x) for x in arq.read_text(encoding="utf-8").splitlines() if x.strip()]


def contagens(mod, sid=SID):
    arq = mod.pasta() / f"{sid}.contagens.json"
    return json.loads(arq.read_text(encoding="utf-8")) if arq.exists() else None


def nomes(mod):
    return sorted(x.name for x in mod.pasta().iterdir())


def sessao_nova(mod, sid=SID):
    mod.iniciar_sessao(inicio(sid))
    mod.registrar(payload(HUMANO, sid=sid))


# --- AC-1.0: o marcador do SessionStart -----------------------------------------------------------------------------

@pytest.mark.parametrize("source", ["startup", "clear"])
def test_sessionstart_novo_cria_o_marcador(spool, source):
    abrir(spool)
    spool.iniciar_sessao(inicio(source=source))
    assert (spool.pasta() / f"{SID}.nova").is_file()


@pytest.mark.parametrize("source", ["resume", "compact", None, ""])
def test_sessionstart_de_retomada_nao_cria_marcador(spool, source):
    abrir(spool)
    spool.iniciar_sessao(inicio(source=source))
    assert nomes(spool) == ["janela.json"]


def test_sessionstart_sem_flag_ou_sem_janela_nao_cria(spool, monkeypatch):
    spool.iniciar_sessao(inicio())
    assert not spool.pasta().exists()
    abrir(spool, estado="encerrada")
    spool.iniciar_sessao(inicio())
    abrir(spool)
    monkeypatch.setenv("HARNESS_BRANCH_LAYER_J", "0")
    spool.iniciar_sessao(inicio())
    assert nomes(spool) == ["janela.json"]


@pytest.mark.parametrize("p", [inicio(sid="../fora"), inicio(sid="curto"), None, "x",
                               {"hook_event_name": "SessionStart", "source": "startup"}])
def test_sessionstart_invalido_nao_cria_nem_levanta(spool, p):
    abrir(spool)
    spool.iniciar_sessao(p)
    assert nomes(spool) == ["janela.json"]


# --- AC-1.1: portões do registrar ----------------------------------------------------------------------------------

def test_flag_desligada_nao_escreve_nada(spool, monkeypatch):
    abrir(spool)
    spool.iniciar_sessao(inicio())
    monkeypatch.setenv("HARNESS_BRANCH_LAYER_J", "0")
    spool.registrar(payload(HUMANO))
    assert linhas(spool) is None


def test_sem_pasta_nem_janela_nao_cria_nada(spool):
    spool.iniciar_sessao(inicio())
    spool.registrar(payload(HUMANO))
    assert not spool.pasta().exists()


@pytest.mark.parametrize("troca", [
    {"estado": "encerrada"},
    {"fim": "2020-01-01T00:00:00Z"},
    {"sha_filtro": "0" * 64},
    {"fim": "nao-e-data"},
])
def test_janela_que_nao_esta_aberta_nao_escreve(spool, troca):
    p = abrir(spool)
    (p / f"{SID}.nova").write_text("", encoding="utf-8")
    abrir(spool, **troca)
    spool.registrar(payload(HUMANO))
    assert linhas(spool) is None and contagens(spool) is None


def test_janela_ilegivel_vale_como_ausente(spool):
    p = abrir(spool)
    (p / f"{SID}.nova").write_text("", encoding="utf-8")
    (p / "janela.json").write_text("{nao e json", encoding="utf-8")
    spool.registrar(payload(HUMANO))
    assert linhas(spool) is None


# --- AC-1.2 / AC-1.3: âncora e turnos ------------------------------------------------------------------------------

def test_primeiro_prompt_humano_depois_do_sessionstart_e_a_ancora(spool):
    abrir(spool)
    sessao_nova(spool)
    ls = linhas(spool)
    assert len(ls) == 1
    l0 = ls[0]
    assert (l0["tipo"], l0["turno_idx"], l0["sessao"], l0["texto"]) == ("ancora", 0, SID, HUMANO)
    assert l0["instante"].endswith("Z")
    assert not (spool.pasta() / f"{SID}.nova").exists()


def test_projeto_e_o_slug_de_harness_paths(spool, tmp_path):
    abrir(spool)
    import harness_paths

    spool.iniciar_sessao(inicio())
    spool.registrar(payload(HUMANO, cwd=str(tmp_path)))
    assert linhas(spool)[0]["projeto"] == harness_paths.project_slug(str(tmp_path))


def test_texto_e_gravado_cru_e_inteiro(spool):
    abrir(spool)
    cru = "  " + "x" * 30_000 + "\n"
    spool.iniciar_sessao(inicio())
    spool.registrar(payload(cru))
    assert linhas(spool)[0]["texto"] == cru


def test_turnos_seguintes_acrescentam_com_indice(spool):
    abrir(spool)
    sessao_nova(spool)
    spool.registrar(payload("agora troque o nome da funcao"))
    spool.registrar(payload("e rode os testes de novo"))
    assert [(x["tipo"], x["turno_idx"]) for x in linhas(spool)] == [("ancora", 0), ("turno", 1), ("turno", 2)]


def test_sessao_sem_marcador_nao_entra_nem_conta(spool):
    """Sessão que começou antes da janela, retomada ou compactada: fora da amostra, e sem rastro."""
    abrir(spool)
    spool.registrar(payload(HUMANO))
    spool.registrar(payload("<task-notification> x"))
    assert nomes(spool) == ["janela.json"]


def test_slash_command_antes_do_primeiro_prompt_nao_consome_o_marcador(spool):
    abrir(spool)
    spool.iniciar_sessao(inicio())
    spool.registrar(payload("<command-name>/model</command-name>"))
    spool.registrar(payload("<local-command-stdout>Set model to opus</local-command-stdout>"))
    spool.registrar(payload(HUMANO))
    assert linhas(spool)[0]["texto"] == HUMANO
    assert contagens(spool) == {"<command-": 1, "<local-command": 1}


def test_turno_nao_recria_spool_apagado(spool):
    """excluir-sessao/purgar apagam o arquivo; o hook acrescenta sem O_CREAT e não o recria."""
    abrir(spool)
    sessao_nova(spool)
    (spool.pasta() / f"{SID}.jsonl").unlink()
    spool.registrar(payload("turno depois da exclusao"))
    assert linhas(spool) is None


def test_turno_em_corrida_com_a_exclusao_nao_recria_o_arquivo(spool, monkeypatch):
    """A corrida: o hook viu o spool, e o excluir-sessao apagou antes da escrita. Sem O_CREAT, nada renasce."""
    abrir(spool)
    sessao_nova(spool)
    real = spool._linha

    def apaga_no_meio(linha):            # depois de ler o spool e antes de abrir para escrever
        (spool.pasta() / f"{SID}.jsonl").unlink()
        return real(linha)

    monkeypatch.setattr(spool, "_linha", apaga_no_meio)
    spool.registrar(payload("turno que chegou junto com a exclusao"))
    assert linhas(spool) is None


def test_ancora_do_sensor_nao_participa(spool, tmp_path):
    """Duas sessões no mesmo cwd: cada uma tem o próprio spool, com todos os turnos, em qualquer ordem."""
    abrir(spool)
    a, b = "sessao-aaaa-aaaa", "sessao-bbbb-bbbb"
    spool.iniciar_sessao(inicio(a))
    spool.iniciar_sessao(inicio(b))
    for sid, texto in ((a, "objetivo da sessao A"), (b, "objetivo da sessao B"), (a, "segundo de A agora"),
                       (b, "segundo de B agora"), (a, "terceiro de A agora")):
        spool.registrar(payload(texto, sid=sid))
    assert [x["turno_idx"] for x in linhas(spool, a)] == [0, 1, 2]
    assert [x["turno_idx"] for x in linhas(spool, b)] == [0, 1]


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
    sessao_nova(spool)
    prefixo = spool.PREFIXOS[i]
    spool.registrar(payload("  \n" + prefixo + " resto do texto injetado pelo host"))
    assert len(linhas(spool)) == 1 and contagens(spool) == {prefixo: 1}


def test_prefixo_em_outra_caixa_e_humano(spool):
    abrir(spool)
    spool.iniciar_sessao(inicio())
    spool.registrar(payload("continue from where you left off. e agora o resto"))
    assert linhas(spool)[0]["tipo"] == "ancora"


def test_curto_conta_e_doze_code_points_grava(spool):
    abrir(spool)
    spool.iniciar_sessao(inicio())
    spool.registrar(payload("  " + "a" * 11 + "  "))
    assert linhas(spool) is None and contagens(spool) == {"curto": 1}
    spool.registrar(payload("ã" * 12))
    assert linhas(spool)[0]["texto"] == "ã" * 12


def test_turno_filtrado_nao_desloca_o_indice(spool):
    abrir(spool)
    sessao_nova(spool)
    spool.registrar(payload("<task-notification> x </task-notification>"))
    spool.registrar(payload("seguindo com o trabalho real"))
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
    None,
    "texto",
])
def test_entrada_invalida_nao_grava(spool, p):
    abrir(spool)
    spool.iniciar_sessao(inicio())
    spool.registrar(p)
    assert linhas(spool) is None


# --- AC-1.6: nunca levanta -----------------------------------------------------------------------------------------

def test_falha_de_escrita_e_engolida_e_contada(spool, monkeypatch):
    abrir(spool)
    spool.iniciar_sessao(inicio())
    real = os.open

    def falha(caminho, *a, **k):
        if str(caminho).endswith(".jsonl"):
            raise OSError("disco cheio")
        return real(caminho, *a, **k)

    monkeypatch.setattr(spool.os, "open", falha)
    spool.registrar(payload(HUMANO))
    assert contagens(spool).get("falha_spool") == 1


def test_contagens_tambem_falhando_nao_levanta(spool, monkeypatch):
    abrir(spool)
    spool.iniciar_sessao(inicio())
    monkeypatch.setattr(spool.os, "open", lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    monkeypatch.setattr(spool.os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("y")))
    spool.registrar(payload(HUMANO))


def test_contagem_com_valor_estragado_e_recomecada(spool):
    abrir(spool)
    sessao_nova(spool)
    (spool.pasta() / f"{SID}.contagens.json").write_text(json.dumps({"curto": None, "x": "y"}), encoding="utf-8")
    spool.registrar(payload("oi"))
    assert contagens(spool)["curto"] == 1


def test_surrogate_solto_e_gravado(spool):
    abrir(spool)
    spool.iniciar_sessao(inicio())
    spool.registrar(payload("texto com surrogate \ud800 solto no meio"))
    assert linhas(spool)[0]["texto"] == "texto com surrogate \ud800 solto no meio"


# --- AC-1.7: teto de tamanho ---------------------------------------------------------------------------------------

def test_spool_cheio_nao_grava_e_conta(spool, monkeypatch):
    p = abrir(spool)
    (p / "outra-sessao-000001.jsonl").write_bytes(b"x" * 100)
    monkeypatch.setattr(spool, "LIMITE_BYTES", 100)
    spool.iniciar_sessao(inicio())
    spool.registrar(payload(HUMANO))
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
    spool.iniciar_sessao(inicio())
    spool.registrar(payload(HUMANO))
    spool.registrar(payload("mais um turno qualquer aqui"))
    assert len(linhas(spool)) == 2
    assert not [x for x in abertos if "segredos" in x]


def _p95(spool, n):
    tempos = []
    for i in range(n):
        t0 = time.perf_counter()
        spool.registrar(payload(f"turno numero {i} da medida"))
        tempos.append(time.perf_counter() - t0)
    tempos.sort()
    return tempos[int(n * 0.95) - 1]


def test_p95_do_registrar_abaixo_de_20_ms(spool):
    abrir(spool)
    sessao_nova(spool)
    p95 = _p95(spool, 1000)
    assert p95 <= 0.020, f"p95 = {p95 * 1000:.1f} ms"


def test_p95_com_muitas_sessoes_na_pasta(spool):
    p = abrir(spool)
    for i in range(300):
        (p / f"sessao-velha-{i:06d}.jsonl").write_bytes(b"x" * 2000)
    sessao_nova(spool)
    p95 = _p95(spool, 300)
    assert p95 <= 0.020, f"p95 = {p95 * 1000:.1f} ms"


# --- hook do SessionStart -------------------------------------------------------------------------------------------

def test_hook_de_inicio_esta_no_hooks_json():
    h = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]["SessionStart"]
    comandos = [x["command"] for g in h for x in g["hooks"]]
    assert any("py.sh" in c and "branch_j_inicio.py" in c for c in comandos)


def test_hook_de_inicio_cria_o_marcador_e_sai_mudo(spool, tmp_path):
    abrir(spool)
    env = dict(os.environ, MASTER_HARNESS_HOME=str(tmp_path / "casa-mh"), HARNESS_BRANCH_LAYER_J="1")
    r = subprocess.run([sys.executable, str(ROOT / "hooks" / "branch_j_inicio.py")], input=json.dumps(inicio()),
                       capture_output=True, text=True, env=env, timeout=30)
    assert r.returncode == 0 and r.stdout == ""
    assert (spool.pasta() / f"{SID}.nova").is_file()


def test_hook_de_inicio_com_entrada_quebrada_sai_zero(tmp_path):
    env = dict(os.environ, MASTER_HARNESS_HOME=str(tmp_path / "casa-mh"), HARNESS_BRANCH_LAYER_J="1")
    r = subprocess.run([sys.executable, str(ROOT / "hooks" / "branch_j_inicio.py")], input="{nao e json",
                       capture_output=True, text=True, env=env, timeout=30)
    assert r.returncode == 0 and r.stdout == ""


# --- ligação no branch_sensor.main ---------------------------------------------------------------------------------

@pytest.fixture
def sensor(spool):
    return _carregar("branch_sensor")


def _main(sensor, monkeypatch, capsys, p):
    monkeypatch.setattr(sensor.sys, "stdin", io.StringIO(json.dumps(p)))
    rc = sensor.main()
    return rc, capsys.readouterr().out


def test_main_grava_ancora_e_turnos_da_sessao_nova(sensor, spool, monkeypatch, capsys, tmp_path):
    abrir(spool)
    monkeypatch.setattr(sensor, "embed", lambda _t: None)
    spool.iniciar_sessao(inicio())
    for texto in (HUMANO, "seguindo o trabalho normal agora", "e mais um passo"):
        rc, _ = _main(sensor, monkeypatch, capsys, payload(texto, cwd=str(tmp_path)))
        assert rc == 0
    assert [x["tipo"] for x in linhas(spool)] == ["ancora", "turno", "turno"]


def test_main_sessao_anterior_a_janela_nao_grava(sensor, spool, monkeypatch, capsys, tmp_path):
    abrir(spool)
    monkeypatch.setattr(sensor, "embed", lambda _t: None)
    _main(sensor, monkeypatch, capsys, payload("seguindo o trabalho normal agora", cwd=str(tmp_path)))
    assert linhas(spool) is None


def test_main_reancoragem_do_branch_out_nao_afeta(sensor, spool, monkeypatch, capsys, tmp_path):
    """O branch-out reancora com session_id None: com o SessionStart, a amostra não depende disso."""
    abrir(spool)
    monkeypatch.setattr(sensor, "embed", lambda _t: None)
    sensor.set_anchor(cwd=str(tmp_path), text="objetivo do ramo", source="branch-out", session_id=None)
    spool.iniciar_sessao(inicio())
    _main(sensor, monkeypatch, capsys, payload(HUMANO, cwd=str(tmp_path)))
    _main(sensor, monkeypatch, capsys, payload("segundo turno aqui mesmo", cwd=str(tmp_path)))
    assert [x["tipo"] for x in linhas(spool)] == ["ancora", "turno"]


def test_main_saida_identica_com_e_sem_flag(sensor, spool, monkeypatch, capsys, tmp_path):
    abrir(spool)
    monkeypatch.setattr(sensor, "embed", lambda _t: None)
    saidas = []
    for flag, sub in (("0", "a"), ("1", "b")):
        monkeypatch.setenv("HARNESS_BRANCH_LAYER_J", flag)
        cwd = tmp_path / sub
        cwd.mkdir()
        s = "sessao-saida-" + sub * 6
        spool.iniciar_sessao(inicio(s))
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
