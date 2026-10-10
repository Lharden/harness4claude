"""Todo hook Python do hooks.json respeita o interpretador nomeado.

Origem (2026-10-09): uma atualizacao do App Installer recriou os aliases
`python.exe` e `python3.exe` em `%LOCALAPPDATA%\\Microsoft\\WindowsApps` como stub
da Microsoft Store, antes do Python real no PATH. Os hooks `.sh` seguiram
funcionando, porque desde `347f801` leem o marcador `~/.master-harness/interpretador`.
As entradas do hooks.json que chamavam `python` cru — o portao transacional
(PostToolUse, PostToolUseFailure, Stop), o lifecycle e o science_intent — cairam
no stub e falharam a cada evento com "Python was not found".

O teste reproduz esse PATH: `python` e `python3` sao stubs que falham com a mesma
mensagem, e o marcador aponta para o interpretador real. Com o marcador, todo
comando do hooks.json que roda um `.py` tem que partir. Sem o marcador, a queda
para `python` continua a historica — e visivel.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
HOOKS_JSON = ROOT / "hooks" / "hooks.json"
TIMEOUT = 60
MENSAGEM_DO_STUB = "Python was not found; run without arguments to install from the Microsoft Store"


def _achar_bash() -> str | None:
    achado = shutil.which("bash")
    if achado:
        return achado
    for c in (
        Path(r"C:\Program Files\Git\bin\bash.exe"),
        Path(r"C:\Program Files\Git\usr\bin\bash.exe"),
    ):
        if c.exists():
            return str(c)
    return None


BASH = _achar_bash()
exige_bash = pytest.mark.skipif(BASH is None, reason="Bash integration runtime is not installed on this host")


def _comandos_python() -> list[tuple[str, str]]:
    """(evento, comando) de cada hook registrado que executa um arquivo `.py`."""
    registrados = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))["hooks"]
    achados = []
    for evento, grupos in registrados.items():
        for grupo in grupos:
            for hook in grupo["hooks"]:
                if re.search(r"\.py\b", hook["command"]):
                    achados.append((evento, hook["command"]))
    return achados


COMANDOS_PYTHON = _comandos_python()


def test_censo_dos_comandos_python():
    """Sem isto, um hooks.json reescrito zeraria a parametrizacao em silencio."""
    assert len(COMANDOS_PYTHON) >= 8


def _stubs(tmp_path: Path) -> Path:
    """Diretorio com `python` e `python3` que se comportam como o alias da Store."""
    d = tmp_path / "windowsapps"
    d.mkdir()
    for nome in ("python", "python3"):
        stub = d / nome
        stub.write_text(f'#!/bin/sh\necho "{MENSAGEM_DO_STUB}" >&2\nexit 49\n', encoding="utf-8", newline="\n")
        stub.chmod(0o755)
    return d


def _env(tmp_path: Path, *, marcador: bool) -> dict:
    harness = tmp_path / "harness"
    harness.mkdir(exist_ok=True)
    mh = tmp_path / "mh"
    mh.mkdir(exist_ok=True)
    if marcador:
        (mh / "interpretador").write_text(Path(sys.executable).as_posix() + "\n", encoding="utf-8")
    env = {
        **os.environ,
        "PATH": str(_stubs(tmp_path)) + os.pathsep + os.environ.get("PATH", ""),
        "PYTHONUTF8": "1",
        "HARNESS_DIR": str(harness),
        "AI_BRAIN_PATH": str(tmp_path / "ai-brain"),
        "MASTER_HARNESS_HOME": str(mh),
        "HARNESS_SKIP_DEPCHECK": "1",
        "HARNESS_ROUTER": "0",
        "HARNESS_BRANCH": "0",
        "HARNESS_BRANCH_LAYER_B": "0",
        "HARNESS_OLLAMA_URL": "http://127.0.0.1:9",
        "CLAUDE_PLUGIN_ROOT": str(ROOT),
    }
    env.pop("VAULT_PATH", None)
    return env


def _roda(tmp_path: Path, evento: str, comando: str, *, marcador: bool) -> subprocess.CompletedProcess:
    cwd = tmp_path / "repo"
    (cwd / ".git").mkdir(parents=True, exist_ok=True)
    payload = {
        "session_id": "s-interpretador", "cwd": str(cwd), "hook_event_name": evento,
        "prompt": "ola", "source": "startup",
        "tool_name": "Bash", "tool_input": {"command": "echo oi"},
        "tool_response": {"stdout": "oi"},
    }
    # Como o host roda: a linha do hooks.json num shell, com a variavel expandida.
    linha = comando.replace("${CLAUDE_PLUGIN_ROOT}", ROOT.as_posix())
    assert BASH is not None
    return subprocess.run(
        [BASH, "-c", linha],
        input=json.dumps(payload), capture_output=True, text=True, encoding="utf-8",
        timeout=TIMEOUT, env=_env(tmp_path, marcador=marcador), cwd=cwd,
    )


@exige_bash
def test_stub_reproduz_o_alias_da_store(tmp_path):
    """Controle: no PATH do teste, `python` cru cai no stub. Sem isto, o teste abaixo nao prova nada."""
    assert BASH is not None
    res = subprocess.run(
        [BASH, "-c", "python -c 'print(1)'"],
        capture_output=True, text=True, encoding="utf-8", timeout=TIMEOUT,
        env=_env(tmp_path, marcador=True),
    )
    assert res.returncode != 0
    assert MENSAGEM_DO_STUB in res.stderr


@exige_bash
@pytest.mark.parametrize(("evento", "comando"), COMANDOS_PYTHON, ids=[f"{e}:{c.split('/')[-1]}" for e, c in COMANDOS_PYTHON])
def test_hook_python_usa_o_interpretador_nomeado(tmp_path, evento, comando):
    res = _roda(tmp_path, evento, comando, marcador=True)
    assert MENSAGEM_DO_STUB not in res.stderr + res.stdout, (
        f"{evento}: `{comando}` resolveu `python` pelo PATH em vez do marcador"
    )
    assert res.returncode == 0, f"{evento}: rc={res.returncode}\n{res.stderr[-400:]}"
    assert "Traceback" not in res.stderr, res.stderr[-400:]


@exige_bash
def test_sem_marcador_a_queda_para_python_e_a_historica(tmp_path):
    """Sem marcador o shim chama `python` do PATH, como antes de 347f801 — e a falha aparece."""
    evento, comando = next((e, c) for e, c in COMANDOS_PYTHON if "harness-transactional.py" in c)
    res = _roda(tmp_path, evento, comando, marcador=False)
    assert res.returncode != 0
    assert MENSAGEM_DO_STUB in res.stderr
