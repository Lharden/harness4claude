"""O SessionStart nao instala nada (Onda 3 do master-harness, lote 3-A; REQ-F11 e AC-2.7 da spec do ambiente isolado).

Ate a Onda 3, o primeiro SessionStart de cada `HARNESS_DIR` rodava `pip install --user -r requirements.txt`: o `pip`
do PATH escrevia no site do usuario, e o site do usuario entrava no `pytest` de todo projeto da maquina (D-026). O
bloco saiu junto com o `requirements.txt`.

As duas metades: (a) o detector funciona: com um `pip` falso na frente do PATH, o bloco antigo, rodado no mesmo
ambiente, chama o `pip` falso; (b) o SessionStart de hoje, num `HARNESS_DIR` novo e sem `HARNESS_SKIP_DEPCHECK`, nao o
chama.
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

BASH = "bash"
if sys.platform == "win32":
    for candidate in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe"):
        if Path(candidate).exists():
            BASH = candidate
            break

#: O bloco que saiu (harness-session-start.sh ate o lote 3-A), reduzido ao que chama o pip.
BLOCO_ANTIGO = """
PLUGIN_DIR="$1"
if command -v pip >/dev/null 2>&1; then
    if [ -f "$PLUGIN_DIR/requirements.txt" ]; then
        pip install --user -q -r "$PLUGIN_DIR/requirements.txt" 2>/dev/null || true
    fi
fi
"""


@pytest.fixture
def pip_falso(tmp_path: Path) -> tuple[dict[str, str], Path]:
    """Um `pip` falso na frente do PATH que anota cada chamada; o ambiente sem `HARNESS_SKIP_DEPCHECK`."""
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    marca = tmp_path / "pip-chamado.txt"
    (bin_ / "pip").write_text(f'#!/bin/sh\necho "$@" >> "{marca.as_posix()}"\n', encoding="utf-8", newline="\n")
    (bin_ / "pip").chmod(0o755)
    env = os.environ.copy()
    env.pop("HARNESS_SKIP_DEPCHECK", None)
    env["PATH"] = str(bin_) + os.pathsep + env.get("PATH", "")
    env["PYTHONUTF8"] = "1"
    return env, marca


def _bash(argv: list[str], env: dict[str, str], stdin: str = "") -> subprocess.CompletedProcess[str]:
    # Os tres fluxos explicitos (stdin pelo `input`), nunca `capture_output`: a regra do guarda de stdin.
    return subprocess.run(  # noqa: UP022
        [BASH, *argv], input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        encoding="utf-8", errors="replace", env=env, timeout=120)


def test_falsificacao_o_bloco_antigo_chamaria_o_pip_falso(pip_falso, tmp_path: Path) -> None:
    """Metade (a): o detector pega o defeito. O bloco antigo, com um `requirements.txt` presente, chama o `pip`."""
    env, marca = pip_falso
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    (plugin / "requirements.txt").write_text("pytest\n", encoding="utf-8")

    _bash(["-c", BLOCO_ANTIGO, "bloco", plugin.as_posix()], env)

    assert marca.is_file() and "install --user" in marca.read_text(encoding="utf-8")


def test_session_start_num_harness_dir_novo_nao_chama_o_pip(pip_falso, tmp_path: Path) -> None:
    """Metade (b): o SessionStart de hoje, no primeiro run de um `HARNESS_DIR`, avisa e nao instala nada."""
    env, marca = pip_falso
    env["HARNESS_DIR"] = str(tmp_path / "h")

    r = _bash([str(HOOK)], env, stdin=json.dumps({"session_id": "teste-sem-pip"}))

    assert (tmp_path / "h" / ".bootstrap-done").is_file(), r.stderr[-800:]     # o bloco do primeiro run rodou
    assert not marca.exists(), marca.read_text(encoding="utf-8")


def test_nenhum_pip_install_e_nenhum_requirements_no_hook_nem_na_raiz() -> None:
    assert "pip install" not in HOOK.read_text(encoding="utf-8")
    assert not (ROOT / "requirements.txt").exists()
