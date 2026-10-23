"""A suite nunca alcanca a casa real do master-harness (D-139).

O defeito (22/10): o hook de inicio de sessao le `<casa>/mh-root` e chama `mh.saude.ao_iniciar_sessao(casa)`; com o
cache de saude velho (> 6 h) o `mh` lanca um `mh saude --registrar` destacado que HERDA o ambiente. Um teste que rodou
o hook com o vault falso do pytest, sem `MASTER_HARNESS_HOME` isolada, regravou o `~/.master-harness/saude/ultima.json`
real. O conserto e do conftest (casa temporaria da sessao + assert no `pytest_runtest_setup`); aqui se mede as duas
metades, sem tocar a casa real: a "casa real" dos testes e uma pasta falsa sob `tmp_path`, apontada por `HOME`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
HOOK = ROOT / "hooks" / "harness-session-start.sh"

exige_bash = pytest.mark.skipif(shutil.which("bash") is None or not HOOK.is_file(), reason="bash ou hook ausente")

SAUDE_QUE_REGRAVA = '''
import pathlib
SENTINELA = pathlib.Path(r"{sentinela}")
def ao_iniciar_sessao(casa, *, python):
    SENTINELA.write_text(str(casa), encoding="utf-8")
    pathlib.Path(casa, "saude", "ultima.json").write_text('{{"regravado": true}}', encoding="utf-8")
    return ""
'''

ULTIMA_VELHA = '{"velho": true}'


def _casa_real_falsa(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Imita `~/.master-harness`: `mh-root` para um `mh` minimo cujo `ao_iniciar_sessao` deixa uma sentinela, e o
    `ultima.json` velho. Devolve (home, casa, sentinela)."""
    home = tmp_path / "home"
    casa = home / ".master-harness"
    (casa / "saude").mkdir(parents=True)
    (casa / "saude" / "ultima.json").write_text(ULTIMA_VELHA, encoding="utf-8")
    mh_falso = tmp_path / "mh-falso"
    (mh_falso / "mh").mkdir(parents=True)
    (mh_falso / "mh" / "__init__.py").write_text("", encoding="utf-8")
    sentinela = tmp_path / "sentinela.txt"
    (mh_falso / "mh" / "saude.py").write_text(
        SAUDE_QUE_REGRAVA.format(sentinela=sentinela), encoding="utf-8")
    (casa / "mh-root").write_text(str(mh_falso) + "\n", encoding="utf-8")
    return home, casa, sentinela


def _rodar_hook(tmp_path: Path, home: Path) -> subprocess.CompletedProcess:
    """O hook de verdade, no ambiente DO TESTE (herdado: e ele que o isolamento da sessao protege), com `HOME` na casa
    falsa que imita a real."""
    cwd = tmp_path / "repo"
    cwd.mkdir(exist_ok=True)
    env = {
        **os.environ,
        "CLAUDE_PLUGIN_ROOT": str(ROOT),
        "HOME": str(home),
        "USERPROFILE": str(home),
        "HARNESS_DIR": str(tmp_path / "harness"),
        "HARNESS_SKIP_DEPCHECK": "1",
    }
    env.pop("AI_BRAIN_PATH", None)
    env.pop("VAULT_PATH", None)
    env.pop("PYTHONPATH", None)
    return subprocess.run(
        ["bash", str(HOOK)],
        input=json.dumps({"session_id": "s-casa", "cwd": str(cwd), "hook_event_name": "SessionStart",
                          "source": "startup"}),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=False, env=env, cwd=str(cwd), timeout=120,
    )


def test_a_sessao_roda_numa_casa_temporaria_com_o_interpretador_da_suite():
    casa = Path(os.environ["MASTER_HARNESS_HOME"])
    assert casa.is_dir()
    assert casa.resolve() != (Path.home() / ".master-harness").resolve()
    assert (casa / "interpretador").read_text(encoding="utf-8").strip() == Path(sys.executable).as_posix()
    assert not (casa / "mh-root").exists(), "sem mh-root o digest de saude fica em silencio"


@exige_bash
def test_com_o_isolamento_da_sessao_a_casa_que_seria_a_real_nao_e_alcancada(tmp_path):
    """Metade 'protege'."""
    home, casa, sentinela = _casa_real_falsa(tmp_path)

    r = _rodar_hook(tmp_path, home)

    assert r.returncode == 0, r.stderr
    assert not sentinela.exists(), "o hook alcancou a casa que imita a real"
    assert (casa / "saude" / "ultima.json").read_text(encoding="utf-8") == ULTIMA_VELHA


@exige_bash
def test_sem_o_isolamento_a_casa_que_seria_a_real_e_alcancada_e_regravada(tmp_path, monkeypatch):
    """Metade 'pega': desfeito o isolamento, o mesmo hook alcanca e regrava a casa. Sem ela, a anterior nao prova
    nada (passaria igual se o hook nunca lesse a casa)."""
    monkeypatch.delenv("MASTER_HARNESS_HOME")
    home, casa, sentinela = _casa_real_falsa(tmp_path)

    r = _rodar_hook(tmp_path, home)

    assert r.returncode == 0, r.stderr
    assert sentinela.is_file(), "o controle falhou: o hook nao leu a casa sem MASTER_HARNESS_HOME"
    assert json.loads((casa / "saude" / "ultima.json").read_text(encoding="utf-8")) == {"regravado": True}


# ---------------------------------------------------------------------------
# O assert de seguranca do `pytest_runtest_setup`, medido pelo conftest de verdade (pytest filho).
# ---------------------------------------------------------------------------
@pytest.fixture
def sintetico():
    criados: list[Path] = []

    def _fazer(nome: str, fonte: str) -> Path:
        f = Path(__file__).parent / f"_synthetic_casa_{nome}.py"
        f.write_text(fonte, encoding="utf-8")
        criados.append(f)
        return f

    yield _fazer
    for f in criados:
        f.unlink(missing_ok=True)


def _pytest_filho(alvo: Path, tmp_path: Path, *, casa_do_chamador: Path | None) -> subprocess.CompletedProcess:
    """pytest filho com `HOME` numa pasta falsa (a `~/.master-harness` dele e a que o conftest trata como real) e,
    opcionalmente, `MASTER_HARNESS_HOME` do chamador (tambem tratada como real)."""
    home = tmp_path / "home-filho"
    home.mkdir(exist_ok=True)
    env = os.environ.copy()
    env.update(HARNESS_PLUGIN_ROOT=str(ROOT), PYTHONUTF8="1", HOME=str(home), USERPROFILE=str(home))
    for k in ("HARNESS_DIR", "AI_BRAIN_PATH", "VAULT_PATH", "MASTER_HARNESS_HOME"):
        env.pop(k, None)
    if casa_do_chamador is not None:
        env["MASTER_HARNESS_HOME"] = str(casa_do_chamador)
    return subprocess.run(
        [sys.executable, "-m", "pytest", str(alvo), "-q", "-p", "no:cacheprovider"],
        capture_output=True, text=True, timeout=300, env=env, cwd=str(ROOT), stdin=subprocess.DEVNULL,
    )


class TestGuardaDaCasa:
    _ESCAPE_ENV = '''
import os
os.environ["MASTER_HARNESS_HOME"] = os.environ["_CASA_SENTINELA"]

def test_noop():
    assert True
'''
    _ESCAPE_HOME = '''
import os
os.environ.pop("MASTER_HARNESS_HOME", None)

def test_noop():
    assert True
'''
    _ESCAPE_MARCADO = '''
import os
import pytest
os.environ["MASTER_HARNESS_HOME"] = os.environ["_CASA_SENTINELA"]

@pytest.mark.touches_real
def test_noop():
    assert True
'''

    @pytest.mark.parametrize("nome,fonte,com_casa", [
        ("env", _ESCAPE_ENV, True),
        ("home", _ESCAPE_HOME, False),
        ("marcado", _ESCAPE_MARCADO, True),
    ])
    def test_reprova_no_setup_quem_volta_a_casa_real(self, sintetico, tmp_path, nome, fonte, com_casa, monkeypatch):
        """Metade 'pega': apontar `MASTER_HARNESS_HOME` (ou a HOME) para a casa real reprova, com ou sem marca."""
        sentinela = tmp_path / "casa-do-usuario"
        sentinela.mkdir()
        monkeypatch.setenv("_CASA_SENTINELA", str(sentinela))
        f = sintetico(nome, fonte)

        proc = _pytest_filho(f, tmp_path, casa_do_chamador=sentinela if com_casa else None)

        saida = proc.stdout + proc.stderr
        assert proc.returncode != 0, f"o assert deveria ter reprovado '{nome}'.\n{saida[-1500:]}"
        assert "CASA REAL" in saida.upper(), saida[-1500:]

    def test_deixa_passar_quem_nao_escapa(self, sintetico, tmp_path):
        """Metade 'libera': mesmo ambiente, teste sem escape passa."""
        sentinela = tmp_path / "casa-do-usuario"
        sentinela.mkdir()
        f = sintetico("limpo", "def test_noop():\n    assert True\n")

        proc = _pytest_filho(f, tmp_path, casa_do_chamador=sentinela)

        assert proc.returncode == 0, proc.stdout[-1500:]
