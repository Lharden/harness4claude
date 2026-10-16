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
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
INICIO = ROOT / "hooks" / "harness-session-start.sh"
CLASSIFICA = ROOT / "hooks" / "harness-classify.sh"

pytestmark = pytest.mark.skipif(not INICIO.is_file() or not CLASSIFICA.is_file(), reason="hook ausente")


def _mh_falso(raiz: Path, corpo: str | None) -> Path:
    """Um `mh` com so `mh.maquina.avisos` (ou sem `mh.maquina`, com `corpo=None`)."""
    pacote = raiz / "mh"
    pacote.mkdir(parents=True)
    (pacote / "__init__.py").write_text("", encoding="utf-8")
    if corpo is not None:
        (pacote / "maquina").mkdir()
        (pacote / "maquina" / "__init__.py").write_text("", encoding="utf-8")
        (pacote / "maquina" / "avisos.py").write_text(corpo, encoding="utf-8")
    return raiz


def _casa(tmp_path: Path, raiz_mh: Path | None) -> Path:
    casa = tmp_path / "casa"
    casa.mkdir(exist_ok=True)
    if raiz_mh is not None:
        (casa / "mh-root").write_text(str(raiz_mh) + "\n", encoding="utf-8")
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


def _iniciar(tmp_path: Path, casa: Path, sessao: str = "s-maquina") -> subprocess.CompletedProcess:
    cwd = _repo(tmp_path)
    return subprocess.run(
        ["bash", str(INICIO)],
        input=json.dumps({"session_id": sessao, "cwd": str(cwd), "hook_event_name": "SessionStart",
                          "source": "startup"}),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=False, env=_env(tmp_path, casa), cwd=str(cwd), timeout=120,
    )


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
