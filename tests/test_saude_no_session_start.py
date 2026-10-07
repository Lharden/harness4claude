"""A saude do ecossistema chega pelo SessionStart.

Medido em 2026-10-07: os portoes do Release 1.0.0 do master-harness nao tinham
chamador automatico. `mh identidade check` ficou vermelho de 30/09 a 07/10 (uma
copia derivada deste repositorio foi editada a mao) e `mh contrato check`
morria num link pendente, e nada percebeu. O digest do SessionStart e o lugar
que alguem le — o mesmo do dreno do canal.

Este hook nao mede nada: pergunta a `mh.saude.ao_iniciar_sessao`, que le o
ultimo resultado gravado e dispara a medicao em segundo plano quando ele esta
velho. O que se trava aqui e o contrato do hook:

1. a linha que o `mh` devolve chega ao modelo, e linha vazia nao vira ruido;
2. sem `master-harness`, ou com um anterior a este chamador, o hook nao quebra
   nem fala;
3. um `mh` que LEVANTA nao vira silencio: silencio ali e o defeito que este
   chamador existe para acabar.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
HOOK = ROOT / "hooks" / "harness-session-start.sh"

pytestmark = pytest.mark.skipif(not HOOK.is_file(), reason="hook ausente")


def _mh_falso(raiz: Path, corpo: str) -> Path:
    """Um `mh` com so o `saude` que o hook chama."""
    pacote = raiz / "mh"
    pacote.mkdir(parents=True)
    (pacote / "__init__.py").write_text("", encoding="utf-8")
    (pacote / "saude.py").write_text(corpo, encoding="utf-8")
    return raiz


def _rodar(tmp_path: Path, casa: Path, sessao: str = "s-saude") -> subprocess.CompletedProcess:
    cwd = tmp_path / "repo"
    cwd.mkdir(exist_ok=True)
    env = {
        **os.environ,
        "CLAUDE_PLUGIN_ROOT": str(ROOT),
        "HOME": str(tmp_path / "home"),
        "USERPROFILE": str(tmp_path / "home"),
        "HARNESS_DIR": str(tmp_path / "home" / ".claude" / "harness"),
        "HARNESS_SKIP_DEPCHECK": "1",
        "MASTER_HARNESS_HOME": str(casa),
    }
    env.pop("AI_BRAIN_PATH", None)
    env.pop("VAULT_PATH", None)
    env.pop("PYTHONPATH", None)
    return subprocess.run(
        ["bash", str(HOOK)],
        input=json.dumps({"session_id": sessao, "cwd": str(cwd), "hook_event_name": "SessionStart",
                          "source": "startup"}),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=False, env=env, cwd=str(cwd), timeout=120,
    )


def _contexto(r: subprocess.CompletedProcess) -> str:
    if not r.stdout.strip():
        return ""
    return json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]


def _casa(tmp_path: Path, raiz_mh: Path | None) -> Path:
    casa = tmp_path / "casa"
    casa.mkdir(exist_ok=True)
    if raiz_mh is not None:
        (casa / "mh-root").write_text(str(raiz_mh) + "\n", encoding="utf-8")
    return casa


DEVOLVE = '''
import json, os, pathlib
def ao_iniciar_sessao(casa, *, python):
    pathlib.Path(casa, "chamado.json").write_text(json.dumps({"casa": str(casa), "python": python}))
    return os.environ.get("LINHA_FALSA", "SAUDE DO ECOSSISTEMA: DOENTE (medido ha 2 h). identidade: claude/repo divergente.")
'''


def test_a_linha_do_mh_chega_ao_modelo(tmp_path: Path) -> None:
    casa = _casa(tmp_path, _mh_falso(tmp_path / "mh-falso", DEVOLVE))

    r = _rodar(tmp_path, casa)

    assert r.returncode == 0, r.stderr
    assert "SAUDE DO ECOSSISTEMA: DOENTE" in _contexto(r)


def test_a_linha_chega_tambem_no_caminho_de_bucket_existente(tmp_path: Path) -> None:
    """O hook tem tres saidas (bucket novo, expirado, comum); a segunda execucao cai na comum."""
    casa = _casa(tmp_path, _mh_falso(tmp_path / "mh-falso", DEVOLVE))
    _rodar(tmp_path, casa)

    r = _rodar(tmp_path, casa)

    assert "SAUDE DO ECOSSISTEMA: DOENTE" in _contexto(r)


def test_o_hook_passa_a_casa_e_o_proprio_python(tmp_path: Path) -> None:
    casa = _casa(tmp_path, _mh_falso(tmp_path / "mh-falso", DEVOLVE))

    _rodar(tmp_path, casa)

    chamado = json.loads((casa / "chamado.json").read_text(encoding="utf-8"))
    assert Path(chamado["casa"]) == casa
    assert Path(chamado["python"]).name.lower().startswith("python")


def test_linha_vazia_e_silencio(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LINHA_FALSA", "")
    casa = _casa(tmp_path, _mh_falso(tmp_path / "mh-falso", DEVOLVE))

    r = _rodar(tmp_path, casa)

    assert r.returncode == 0, r.stderr
    assert "SAUDE" not in _contexto(r)


def test_sem_master_harness_o_hook_nao_quebra_nem_fala(tmp_path: Path) -> None:
    r = _rodar(tmp_path, _casa(tmp_path, None))

    assert r.returncode == 0, r.stderr
    assert "SAUDE" not in _contexto(r)


def test_mh_anterior_a_este_chamador_e_silencio(tmp_path: Path) -> None:
    casa = _casa(tmp_path, _mh_falso(tmp_path / "mh-falso", "# sem ao_iniciar_sessao\n"))

    r = _rodar(tmp_path, casa)

    assert r.returncode == 0, r.stderr
    assert "SAUDE" not in _contexto(r)


def test_mh_que_levanta_nao_vira_silencio(tmp_path: Path) -> None:
    corpo = "def ao_iniciar_sessao(casa, *, python):\n    raise RuntimeError('registro corrompido')\n"
    casa = _casa(tmp_path, _mh_falso(tmp_path / "mh-falso", corpo))

    r = _rodar(tmp_path, casa)

    assert r.returncode == 0, r.stderr
    contexto = _contexto(r)
    assert "SAUDE DO ECOSSISTEMA: NAO VERIFICADO" in contexto
    assert "RuntimeError" in contexto
