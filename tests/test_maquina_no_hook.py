"""O aviso da sincronia entre as maquinas chega pelo SessionStart, e o censo e disparado pelo UserPromptSubmit.

Spec da sincronia do master-harness, US-4 (REQ-F23, REQ-F24, AC-4.16). Este repositorio so recebe o leitor do aviso:
o `mh` e achado pelo marcador `~/.master-harness/mh-root`, como no digest da saude, e quem mede, le os censos e decide
e `mh.maquina.avisos`. O que se trava aqui e o contrato dos dois hooks:

1. a linha que `ao_iniciar_sessao` devolve chega ao modelo nos tres caminhos de saida do SessionStart, com a casa, o
   proprio python e o cwd da sessao (que casa o projeto, AC-4.2);
2. sem master-harness, ou com um `mh` sem `mh.maquina`, silencio (AC-4.16);
3. um `mh.maquina` que levanta no import ou na execucao vira `MAQUINA: NAO VERIFICADO` (AC-4.16);
4. o `UserPromptSubmit` chama `disparar_se_velho` com a casa e o proprio python, e um `mh` que levanta nao derruba o
   hook (REQ-F24).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
INICIO = ROOT / "hooks" / "harness-session-start.sh"
CLASSIFICA = ROOT / "hooks" / "harness-classify.sh"

pytestmark = pytest.mark.skipif(not INICIO.is_file() or not CLASSIFICA.is_file(), reason="hook ausente")


def _mh_falso(raiz: Path, corpo: str | None, *, init_mh: str = "", init_maquina: str = "") -> Path:
    """Um `mh` com so `mh.maquina.avisos` (ou sem `mh.maquina`, com `corpo=None`).

    `init_mh` e `init_maquina` sao o corpo dos `__init__` dos dois pacotes: e onde o `find_spec` executa codigo.
    """
    pacote = raiz / "mh"
    pacote.mkdir(parents=True)
    (pacote / "__init__.py").write_text(init_mh, encoding="utf-8")
    if corpo is not None:
        (pacote / "maquina").mkdir()
        (pacote / "maquina" / "__init__.py").write_text(init_maquina, encoding="utf-8")
        (pacote / "maquina" / "avisos.py").write_text(corpo, encoding="utf-8")
    return raiz


def _casa(tmp_path: Path, raiz_mh: Path | None) -> Path:
    casa = tmp_path / "casa"
    casa.mkdir(exist_ok=True)
    if raiz_mh is not None:
        (casa / "mh-root").write_text(str(raiz_mh) + "\n", encoding="utf-8")
    # O hook usa o interpretador nomeado na casa. Sem isso cai no `python` do PATH, que numa maquina com o
    # master-harness instalado em modo editavel enxerga o `mh` real por cima do falso (LL-02).
    (casa / "interpretador").write_text(sys.executable + "\n", encoding="utf-8")
    return casa


def _env(tmp_path: Path, casa: Path) -> dict[str, str]:
    env = {
        **os.environ,
        "CLAUDE_PLUGIN_ROOT": str(ROOT),
        "HOME": str(tmp_path / "home"),
        "USERPROFILE": str(tmp_path / "home"),
        "HARNESS_DIR": str(tmp_path / "home" / ".claude" / "harness"),
        "HARNESS_SKIP_DEPCHECK": "1",
        "MASTER_HARNESS_HOME": str(casa),
        "PYTHONUTF8": "1",
    }
    for k in ("AI_BRAIN_PATH", "VAULT_PATH", "PYTHONPATH"):
        env.pop(k, None)
    return env


def _repo(tmp_path: Path) -> Path:
    cwd = tmp_path / "repo"
    cwd.mkdir(exist_ok=True)
    return cwd


def _iniciar(tmp_path: Path, casa: Path, sessao: str = "s-maquina",
             cwd_do_processo: Path | None = None) -> subprocess.CompletedProcess:
    """O cwd do payload e `_repo`; o do processo e o mesmo, salvo `cwd_do_processo` (para provar de onde o hook le)."""
    cwd = _repo(tmp_path)
    return subprocess.run(
        ["bash", str(INICIO)],
        input=json.dumps({"session_id": sessao, "cwd": str(cwd), "hook_event_name": "SessionStart",
                          "source": "startup"}),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=False, env=_env(tmp_path, casa), cwd=str(cwd_do_processo or cwd), timeout=120,
    )


def _semear_pipeline_expirado(tmp_path: Path, sessao: str) -> None:
    """Um pipeline ativo sem `started_at` no balde que o hook vai resolver (conta como expirado: TTL vencido)."""
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        from harness_paths import ensure_state_dir
    finally:
        sys.path.remove(str(ROOT / "scripts"))
    balde = Path(ensure_state_dir(str(tmp_path / "home" / ".claude" / "harness"), str(_repo(tmp_path)),
                                  session_id=sessao))
    balde.mkdir(parents=True, exist_ok=True)
    (balde / "state.json").write_text(json.dumps({
        "task_id": "t-expirada", "schema_version": 3, "classification": "L2-feature", "status": "active",
        "pipeline": ["discuss", "tdd"], "current_step": "discuss", "artifacts_so_far": [], "started_at": None,
    }), encoding="utf-8")


def _classificar(tmp_path: Path, casa: Path, sessao: str = "s-maquina-ups") -> subprocess.CompletedProcess:
    cwd = _repo(tmp_path)
    return subprocess.run(
        ["bash", str(CLASSIFICA)],
        input=json.dumps({"session_id": sessao, "cwd": str(cwd), "hook_event_name": "UserPromptSubmit",
                          "prompt": "implementar uma feature nova de autenticacao com refatoracao de arquitetura"}),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=False, env=_env(tmp_path, casa), cwd=str(cwd), timeout=120,
    )


def _contexto(r: subprocess.CompletedProcess) -> str:
    if not r.stdout.strip():
        return ""
    return json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]


AVISA = '''
import json, os, pathlib
def ao_iniciar_sessao(casa, *, python, cwd):
    pathlib.Path(casa, "chamado.json").write_text(json.dumps({"casa": str(casa), "python": python, "cwd": str(cwd)}))
    return os.environ.get("LINHA_FALSA",
                          "MAQUINA: fotos de desktop a absorver em fastslr (censo de 12 min): rode `mh maquina chegar`")
def disparar_se_velho(casa, *, python):
    pathlib.Path(casa, "disparou.json").write_text(json.dumps({"casa": str(casa), "python": python}))
    return 4242
'''


def test_a_linha_do_aviso_chega_ao_modelo(tmp_path: Path) -> None:
    casa = _casa(tmp_path, _mh_falso(tmp_path / "mh-falso", AVISA))

    r = _iniciar(tmp_path, casa)

    assert r.returncode == 0, r.stderr
    assert "MAQUINA: fotos de desktop a absorver em fastslr" in _contexto(r)


def test_a_linha_chega_nos_outros_caminhos_de_saida(tmp_path: Path) -> None:
    """Bucket novo na primeira execucao, bucket existente (caminho comum) na segunda."""
    casa = _casa(tmp_path, _mh_falso(tmp_path / "mh-falso", AVISA))
    _iniciar(tmp_path, casa)

    r = _iniciar(tmp_path, casa)

    assert "MAQUINA: fotos de desktop" in _contexto(r)


def test_o_caminho_expirado_tambem_junta_o_aviso() -> None:
    """O terceiro caminho (pipeline expirado pelo TTL) junta as partes num bloco proprio: as tres juncoes leem
    MAQUINA_DIGEST."""
    texto = INICIO.read_text(encoding="utf-8")
    assert texto.count("os.environ.get('MAQUINA_DIGEST', '')") == 3


def test_o_pipeline_expirado_entrega_a_linha_do_aviso_ao_modelo(tmp_path: Path) -> None:
    """Comportamento do terceiro caminho (a juncao do `_harness_emit resuming`): o pipeline vencido sai como EXPIRED e a
    linha do aviso chega no MESMO contexto. Contar o texto `MAQUINA_DIGEST` nao prova que a linha sai."""
    casa = _casa(tmp_path, _mh_falso(tmp_path / "mh-falso", AVISA))
    _semear_pipeline_expirado(tmp_path, "s-expirada")

    r = _iniciar(tmp_path, casa, sessao="s-expirada")

    assert r.returncode == 0, r.stderr
    contexto = _contexto(r)
    assert "HARNESS v3 EXPIRED" in contexto and "t-expirada" in contexto, contexto  # o caminho certo foi o tomado
    assert "MAQUINA: fotos de desktop a absorver em fastslr" in contexto, contexto


AVISA_O_CWD = '''
def ao_iniciar_sessao(casa, *, python, cwd):
    return "MAQUINA: cwd recebido=" + str(cwd)
'''


def test_o_aviso_usa_o_cwd_do_payload_e_nao_o_do_processo(tmp_path: Path) -> None:
    """AC-4.2: e o cwd do payload que casa o projeto da sessao. Os dois valores diferem de proposito: o hook roda com o
    cwd do processo em outra pasta, e o aviso so pode ver a do payload."""
    casa = _casa(tmp_path, _mh_falso(tmp_path / "mh-falso", AVISA_O_CWD))
    outro = tmp_path / "outro-cwd"
    outro.mkdir()

    r = _iniciar(tmp_path, casa, cwd_do_processo=outro)

    assert r.returncode == 0, r.stderr
    contexto = _contexto(r)
    linha = next((x for x in contexto.splitlines() if x.startswith("MAQUINA: cwd recebido=")), "")
    assert linha, contexto
    recebido = Path(linha.removeprefix("MAQUINA: cwd recebido=").strip())
    assert recebido == _repo(tmp_path), linha
    assert recebido != outro, linha


def test_o_hook_passa_a_casa_o_proprio_python_e_o_cwd_da_sessao(tmp_path: Path) -> None:
    casa = _casa(tmp_path, _mh_falso(tmp_path / "mh-falso", AVISA))

    _iniciar(tmp_path, casa)

    chamado = json.loads((casa / "chamado.json").read_text(encoding="utf-8"))
    assert Path(chamado["casa"]) == casa
    assert Path(chamado["python"]).name.lower().startswith("python")
    assert Path(chamado["cwd"]) == _repo(tmp_path)


def test_linha_vazia_e_silencio(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LINHA_FALSA", "")
    casa = _casa(tmp_path, _mh_falso(tmp_path / "mh-falso", AVISA))

    r = _iniciar(tmp_path, casa)

    assert r.returncode == 0, r.stderr
    assert "MAQUINA" not in _contexto(r)


def test_sem_master_harness_silencio(tmp_path: Path) -> None:
    r = _iniciar(tmp_path, _casa(tmp_path, None))

    assert r.returncode == 0, r.stderr
    assert "MAQUINA" not in _contexto(r)


def test_mh_sem_o_modulo_maquina_e_silencio(tmp_path: Path) -> None:
    casa = _casa(tmp_path, _mh_falso(tmp_path / "mh-falso", None))

    r = _iniciar(tmp_path, casa)

    assert r.returncode == 0, r.stderr
    assert "MAQUINA" not in _contexto(r)


def test_modulo_que_levanta_no_import_vira_linha(tmp_path: Path) -> None:
    casa = _casa(tmp_path, _mh_falso(tmp_path / "mh-falso", "raise RuntimeError('import quebrado')\n"))

    r = _iniciar(tmp_path, casa)

    assert r.returncode == 0, r.stderr
    assert "MAQUINA: NAO VERIFICADO - RuntimeError: import quebrado" in _contexto(r)


def test_dependencia_ausente_dentro_do_modulo_vira_linha(tmp_path: Path) -> None:
    """Um `ModuleNotFoundError` de OUTRO modulo (nao `mh.maquina`) e defeito do `mh`, nao ausencia dele."""
    casa = _casa(tmp_path, _mh_falso(tmp_path / "mh-falso", "import modulo_que_nao_existe\n"))

    r = _iniciar(tmp_path, casa)

    assert "MAQUINA: NAO VERIFICADO - ModuleNotFoundError" in _contexto(r)


def test_execucao_que_levanta_vira_linha(tmp_path: Path) -> None:
    corpo = "def ao_iniciar_sessao(casa, *, python, cwd):\n    raise ValueError('censo podre')\n"
    casa = _casa(tmp_path, _mh_falso(tmp_path / "mh-falso", corpo))

    r = _iniciar(tmp_path, casa)

    assert "MAQUINA: NAO VERIFICADO - ValueError: censo podre" in _contexto(r)


def test_user_prompt_submit_dispara_o_censo(tmp_path: Path) -> None:
    casa = _casa(tmp_path, _mh_falso(tmp_path / "mh-falso", AVISA))

    r = _classificar(tmp_path, casa)

    assert r.returncode == 0, r.stderr
    disparo = json.loads((casa / "disparou.json").read_text(encoding="utf-8"))
    assert Path(disparo["casa"]) == casa
    assert Path(disparo["python"]).name.lower().startswith("python")


def test_user_prompt_submit_com_mh_que_levanta_segue(tmp_path: Path) -> None:
    corpo = ("import pathlib\n"
             "def disparar_se_velho(casa, *, python):\n"
             "    pathlib.Path(casa, 'tentou').write_text('1')\n"
             "    raise RuntimeError('quebrou')\n")
    casa = _casa(tmp_path, _mh_falso(tmp_path / "mh-falso", corpo))

    r = _classificar(tmp_path, casa)

    assert r.returncode == 0, r.stderr
    assert (casa / "tentou").is_file()  # o disparo foi chamado e levantou
    assert "HARNESS v3" in _contexto(r)  # e a classificacao saiu assim mesmo


# AC-4.16: silencio so quando o modulo NAO EXISTE. Os 8 casos medidos no lote 20261016-081709-c78f.
AVISOS_OK = "def ao_iniciar_sessao(casa, *, python, cwd):\n    return 'MAQUINA: ok'\n"
SEM_ENTRADA = "def outra_coisa():\n    return 1\n"
NAO_VERIFICADO = "MAQUINA: NAO VERIFICADO - "


@pytest.mark.parametrize(
    ("nome", "montar", "esperado"),
    [
        ("avisos_normal", lambda r: _mh_falso(r, AVISOS_OK), "MAQUINA: ok"),
        ("sem_mh_maquina", lambda r: _mh_falso(r, None), ""),
        ("runtime_no_init_maquina",
         lambda r: _mh_falso(r, AVISOS_OK, init_maquina="raise RuntimeError('init quebrado')\n"),
         NAO_VERIFICADO + "RuntimeError: init quebrado"),
        ("avisos_sem_ao_iniciar_sessao", lambda r: _mh_falso(r, SEM_ENTRADA), ""),
        ("submodulo_inexistente_no_init_maquina",
         lambda r: _mh_falso(r, AVISOS_OK, init_maquina="from mh.maquina.nao_existe import x\n"),
         NAO_VERIFICADO + "ModuleNotFoundError"),
        ("modulo_externo_no_init_maquina",
         lambda r: _mh_falso(r, AVISOS_OK, init_maquina="import modulo_externo_que_falta\n"),
         NAO_VERIFICADO + "ModuleNotFoundError"),
        ("import_quebrado_no_init_mh",
         lambda r: _mh_falso(r, AVISOS_OK, init_mh="from mh.nao_existe import y\n"),
         NAO_VERIFICADO + "ModuleNotFoundError"),
        ("attributeerror_no_import_do_avisos",
         lambda r: _mh_falso(r, "import os\nos.nao_existe\n"),
         NAO_VERIFICADO + "AttributeError"),
    ],
)
def test_os_oito_casos_do_ac_4_16(tmp_path: Path, nome: str, montar, esperado: str) -> None:
    casa = _casa(tmp_path, montar(tmp_path / "mh-falso"))

    r = _iniciar(tmp_path, casa)

    assert r.returncode == 0, r.stderr
    contexto = _contexto(r)
    if esperado:
        assert esperado in contexto, f"{nome}: {contexto!r}"
    else:
        assert "MAQUINA" not in contexto, f"{nome}: {contexto!r}"


# ------------------------------------------------------------------ monitor entre as maquinas (fase 3) ----
# Spec do monitor (master-harness, `docs/specs/monitor-maquinas-spec.md`), REQ-F5 e AC-2.6; design, componente 8 e
# "Saida dos hooks". O `UserPromptSubmit` chama `avisos.no_prompt` quando o `mh` o tem e entrega o texto num JSON so,
# ao modelo (`additionalContext`) e a tela (`systemMessage`), em todo caminho de saida, inclusive os que antes saiam
# calados. Com o `mh` antigo, a chamada e a saida de antes.

TEXTO_MONITOR = ("MAQUINA: fastslr: 1 commit atras do remoto → `git -C '~/fastslr' pull --ff-only` "
                 "— sugestões para o usuário; não execute sem pedido")

MONITOR = f'''
import json, os, pathlib
def no_prompt(casa, *, python, cwd, sessao, agora=None, lancar=None, env=None, raiz=None):
    pathlib.Path(casa, "no_prompt.json").write_text(json.dumps(
        {{"casa": str(casa), "python": python, "cwd": str(cwd), "sessao": sessao}}))
    return os.environ.get("TEXTO_MONITOR_FALSO", {TEXTO_MONITOR!r})
def disparar_se_velho(casa, *, python):
    pathlib.Path(casa, "disparou.json").write_text("{{}}")
    return 1
def ao_iniciar_sessao(casa, *, python, cwd, sessao=None, origem=None, agora=None, lancar=None, env=None, raiz=None):
    pathlib.Path(casa, "inicio.json").write_text(json.dumps(
        {{"casa": str(casa), "cwd": str(cwd), "sessao": sessao, "origem": origem}}))
    return {TEXTO_MONITOR!r}
'''

ANTIGO = '''
import pathlib
def disparar_se_velho(casa, *, python):
    pathlib.Path(casa, "disparou.json").write_text("{}")
    return 1
'''

PROMPT_L1 = "implementar uma feature nova de autenticacao com refatoracao de arquitetura"
PROMPT_L0 = "o que e isso?"
PROMPT_AUTOMACAO = "<task-notification> o comando de fundo terminou </task-notification>"


def _prompt(tmp_path: Path, casa: Path, prompt: str, *,
            sessao: str | None = "s-monitor") -> subprocess.CompletedProcess:
    cwd = _repo(tmp_path)
    payload = {"cwd": str(cwd), "hook_event_name": "UserPromptSubmit", "prompt": prompt}
    if sessao is not None:
        payload["session_id"] = sessao
    return subprocess.run(
        ["bash", str(CLASSIFICA)], input=json.dumps(payload),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=False, env=_env(tmp_path, casa), cwd=str(cwd), timeout=120,
    )


def _um_json(r: subprocess.CompletedProcess) -> dict:
    """A saida inteira e UM objeto JSON (`json.loads` reprova dois objetos ou texto cru em volta)."""
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _nos_dois_canais(saida: dict, texto: str = TEXTO_MONITOR) -> str:
    assert saida["systemMessage"] == texto
    assert saida["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    contexto = saida["hookSpecificOutput"]["additionalContext"]
    assert texto in contexto
    return contexto


def test_monitor_l1_um_json_com_o_texto_no_contexto_e_na_tela(tmp_path: Path) -> None:
    casa = _casa(tmp_path, _mh_falso(tmp_path / "mh-falso", MONITOR))

    contexto = _nos_dois_canais(_um_json(_prompt(tmp_path, casa, PROMPT_L1)))

    assert "HARNESS v3 CLASSIFIED" in contexto
    chamado = json.loads((casa / "no_prompt.json").read_text(encoding="utf-8"))
    assert chamado["sessao"] == "s-monitor" and Path(chamado["cwd"]) == _repo(tmp_path)
    assert Path(chamado["casa"]) == casa and Path(chamado["python"]).name.lower().startswith("python")
    assert not (casa / "disparou.json").exists()  # o `no_prompt` substitui o `disparar_se_velho` (AC-2.9)


def test_monitor_l0_o_bloco_de_classificacao_entra_no_mesmo_json(tmp_path: Path) -> None:
    casa = _casa(tmp_path, _mh_falso(tmp_path / "mh-falso", MONITOR))

    contexto = _nos_dois_canais(_um_json(_prompt(tmp_path, casa, PROMPT_L0)))

    assert "<harness-classification>" in contexto and "level: L0" in contexto


def test_monitor_na_saida_antecipada_que_antes_era_calada(tmp_path: Path) -> None:
    casa = _casa(tmp_path, _mh_falso(tmp_path / "mh-falso", MONITOR))

    saida = _um_json(_prompt(tmp_path, casa, PROMPT_AUTOMACAO))

    assert _nos_dois_canais(saida) == TEXTO_MONITOR  # so o monitor: a automacao continua sem classificacao
    assert (casa / "no_prompt.json").is_file()


def test_monitor_no_caminho_de_continuacao(tmp_path: Path) -> None:
    casa = _casa(tmp_path, _mh_falso(tmp_path / "mh-falso", MONITOR))
    _prompt(tmp_path, casa, PROMPT_L1)

    contexto = _nos_dois_canais(_um_json(_prompt(tmp_path, casa, "e agora o resto")))

    assert "HARNESS v3 CONTINUING" in contexto


def test_monitor_sem_id_de_sessao_chama_com_none(tmp_path: Path) -> None:
    """AC-2.11: sem id, o `mh` so dispara; o hook nao inventa um id (nem o escopo do projeto)."""
    casa = _casa(tmp_path, _mh_falso(tmp_path / "mh-falso", MONITOR))

    _prompt(tmp_path, casa, PROMPT_L1, sessao=None)

    assert json.loads((casa / "no_prompt.json").read_text(encoding="utf-8"))["sessao"] is None


def _saida_de_antes(r: subprocess.CompletedProcess, prompt: str) -> None:
    """A forma da saida sem o monitor: automacao calada, L0 em stdout cru, L1+ num JSON so com `hookSpecificOutput`."""
    assert r.returncode == 0, r.stderr
    assert "systemMessage" not in r.stdout and "MAQUINA" not in r.stdout
    if prompt == PROMPT_AUTOMACAO:
        assert r.stdout == ""
    elif prompt == PROMPT_L0:
        assert r.stdout.startswith("<harness-classification>")
    else:
        assert list(json.loads(r.stdout)) == ["hookSpecificOutput"]


@pytest.mark.parametrize("prompt", [PROMPT_L1, PROMPT_L0, PROMPT_AUTOMACAO])
def test_monitor_vazio_deixa_a_saida_de_antes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, prompt: str) -> None:
    monkeypatch.setenv("TEXTO_MONITOR_FALSO", "")
    casa = _casa(tmp_path, _mh_falso(tmp_path / "mh-falso", MONITOR))

    r = _prompt(tmp_path, casa, prompt)

    assert (casa / "no_prompt.json").is_file()
    _saida_de_antes(r, prompt)


@pytest.mark.parametrize("prompt", [PROMPT_L1, PROMPT_L0, PROMPT_AUTOMACAO])
def test_mh_antigo_sem_no_prompt_mantem_o_disparo_e_a_saida_de_antes(tmp_path: Path, prompt: str) -> None:
    casa = _casa(tmp_path, _mh_falso(tmp_path / "mh-falso", ANTIGO))

    r = _prompt(tmp_path, casa, prompt)

    assert (casa / "disparou.json").is_file()
    _saida_de_antes(r, prompt)


def test_no_prompt_que_levanta_vira_nao_verificado_nos_dois_canais(tmp_path: Path) -> None:
    corpo = ("def no_prompt(casa, *, python, cwd, sessao):\n"
             "    raise RuntimeError('memoria\\npodre')\n")
    casa = _casa(tmp_path, _mh_falso(tmp_path / "mh-falso", corpo))

    saida = _um_json(_prompt(tmp_path, casa, PROMPT_L1))

    contexto = _nos_dois_canais(saida, "MAQUINA: NAO VERIFICADO - RuntimeError: memoria podre")
    assert "HARNESS v3 CLASSIFIED" in contexto


def test_session_start_passa_sessao_e_origem_e_entrega_na_tela(tmp_path: Path) -> None:
    casa = _casa(tmp_path, _mh_falso(tmp_path / "mh-falso", MONITOR))
    cwd = _repo(tmp_path)

    r = subprocess.run(
        ["bash", str(INICIO)],
        input=json.dumps({"session_id": "s-retomada", "cwd": str(cwd), "hook_event_name": "SessionStart",
                          "source": "resume"}),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=False, env=_env(tmp_path, casa), cwd=str(cwd), timeout=120,
    )

    assert r.returncode == 0, r.stderr
    saida = json.loads(r.stdout)
    assert saida["systemMessage"] == TEXTO_MONITOR
    assert TEXTO_MONITOR in saida["hookSpecificOutput"]["additionalContext"]
    inicio = json.loads((casa / "inicio.json").read_text(encoding="utf-8"))
    assert inicio["sessao"] == "s-retomada" and inicio["origem"] == "resume"


def test_session_start_com_mh_de_assinatura_antiga_chama_como_antes(tmp_path: Path) -> None:
    """`AVISA` tem `ao_iniciar_sessao(casa, *, python, cwd)`: passar `sessao=` a ele levantaria TypeError."""
    casa = _casa(tmp_path, _mh_falso(tmp_path / "mh-falso", AVISA))

    r = _iniciar(tmp_path, casa)

    saida = json.loads(r.stdout)
    assert "NAO VERIFICADO" not in r.stdout
    assert "MAQUINA: fotos de desktop" in saida["hookSpecificOutput"]["additionalContext"]
    assert saida["systemMessage"].startswith("MAQUINA: fotos de desktop")
