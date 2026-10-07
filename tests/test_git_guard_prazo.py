"""O git-guard decide dentro do prazo sob carga (AC-1 de
`docs/specs/git-guard-prazo-sob-carga-spec-light.md`).

Medido em 2026-10-07 (`docs/specs/git-guard-prazo-sob-carga-diagnostico.md`):
o guarda criava ~15 processos por chamada — 4 interpretadores Python e ~11
processos do bash do Windows — e custava 2,1 s ocioso, 3,8 s com 4 lacos nos
mesmos 2 nucleos, contra 0,22 s / 0,30 s de um Python vazio. Em producao, 531
chamadas passaram de 10 s entre 20/09 e 07/10; o host mata o hook e a
ferramenta segue, ou seja, a checagem nao acontece.

O oraculo e uma RAZAO, nao um tempo absoluto: guarda e Python vazio medidos
intercalados, sob a mesma carga. Tempo absoluto depende da maquina e do que as
outras sessoes estao rodando; a razao mede quantos processos o guarda custa.
Codigo antigo: ~12x. Teto: 2,5x.

Carga focada (padrao da maquina compartilhada): o processo do teste e 4 lacos
presos aos mesmos 2 nucleos logicos, nunca a maquina inteira. Os lacos tem
homem-morto (sentinela + prazo maximo) e sao encerrados pelo handle do Popen.
A afinidade do worker do pytest e restaurada no fim.
"""
from __future__ import annotations

import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
GUARD = ROOT / "hooks" / "harness-git-guard.sh"

BASH = "bash"
if sys.platform == "win32":
    for candidate in (
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
        "bash",
    ):
        if Path(candidate).exists() or candidate == "bash":
            BASH = candidate
            break

RODADAS = 6
LACOS = 4
RAZAO_MAXIMA = 2.5
PRAZO_DO_HOST_S = 10  # hooks/hooks.json, "timeout" do git-guard

# Laco ocupado com homem-morto: sai quando a sentinela some ou o prazo vence.
_LACO = (
    "import os, sys, time\n"
    "sentinela, fim = sys.argv[1], time.monotonic() + float(sys.argv[2])\n"
    "proximo = 0.0\n"
    "while True:\n"
    "    agora = time.monotonic()\n"
    "    if agora >= proximo:\n"
    "        if agora >= fim or not os.path.exists(sentinela):\n"
    "            os._exit(0)\n"
    "        proximo = agora + 0.25\n"
)


def _interpretador_do_guarda() -> str:
    """O mesmo interpretador que o guarda escolhe: o nomeado, ou `python`."""
    marca = Path(os.environ.get("MASTER_HARNESS_HOME") or Path.home() / ".master-harness") / "interpretador"
    try:
        candidato = marca.read_text(encoding="utf-8").strip()
    except OSError:
        candidato = ""
    if candidato and os.access(candidato, os.X_OK):
        return candidato
    return shutil.which("python") or sys.executable


def _dois_nucleos() -> list[int]:
    total = os.cpu_count() or 1
    if total < 2:
        pytest.skip("carga focada exige 2 nucleos logicos")
    return [total - 2, total - 1]


@contextmanager
def _afinidade(cpus: list[int]):
    """Prende ESTE processo (e os filhos que ele criar) a `cpus`; restaura no fim."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.GetProcessAffinityMask.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(ctypes.c_size_t)]
        k32.SetProcessAffinityMask.argtypes = [wintypes.HANDLE, ctypes.c_size_t]
        proc = k32.GetCurrentProcess()
        original, sistema = ctypes.c_size_t(), ctypes.c_size_t()
        if not k32.GetProcessAffinityMask(proc, ctypes.byref(original), ctypes.byref(sistema)):
            pytest.skip("GetProcessAffinityMask falhou")
        mascara = sum(1 << c for c in cpus)
        if not k32.SetProcessAffinityMask(proc, mascara):
            pytest.skip("SetProcessAffinityMask falhou")
        try:
            yield
        finally:
            k32.SetProcessAffinityMask(proc, original.value)
    elif hasattr(os, "sched_setaffinity"):
        original = os.sched_getaffinity(0)
        os.sched_setaffinity(0, set(cpus))
        try:
            yield
        finally:
            os.sched_setaffinity(0, original)
    else:
        pytest.skip("sem API de afinidade nesta plataforma")


@contextmanager
def _carga_focada(cpus: list[int]):
    """Processo do teste + LACOS lacos presos aos mesmos 2 nucleos."""
    with _afinidade(cpus), tempfile.TemporaryDirectory() as tmp:
        sentinela = Path(tmp) / "carga-viva"
        sentinela.write_text("1", encoding="utf-8")
        lacos = [subprocess.Popen([sys.executable, "-c", _LACO, str(sentinela), "180"])
                 for _ in range(LACOS)]
        try:
            time.sleep(0.5)
            yield
        finally:
            sentinela.unlink(missing_ok=True)
            for laco in lacos:
                try:
                    laco.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    laco.kill()
                    laco.wait(timeout=5)


def _tempo(cmd: list[str], entrada: str | None, env: dict) -> tuple[float, int]:
    inicio = time.perf_counter()
    res = subprocess.run(cmd, input=entrada, capture_output=True, text=True, env=env,
                         timeout=PRAZO_DO_HOST_S * 6)
    return time.perf_counter() - inicio, res.returncode


def test_guarda_custa_pouco_mais_que_um_python_vazio_sob_carga(tmp_path):
    py = _interpretador_do_guarda()
    env = os.environ.copy()
    env["HARNESS_DIR"] = str(tmp_path)
    env["PYTHONUTF8"] = "1"
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "git status"}})

    guarda, vazio = [], []
    with _carga_focada(_dois_nucleos()):
        for _ in range(RODADAS):
            t, rc = _tempo([BASH, str(GUARD)], payload, env)
            assert rc == 0, "git status nao pode bloquear"
            guarda.append(t)
            t, _ = _tempo([py, "-c", "pass"], None, env)
            vazio.append(t)

    razao = statistics.median(guarda) / statistics.median(vazio)
    detalhe = (f"guarda p50={statistics.median(guarda):.2f}s max={max(guarda):.2f}s | "
               f"python vazio p50={statistics.median(vazio):.2f}s | razao={razao:.1f}x")
    assert razao <= RAZAO_MAXIMA, f"guarda caro demais sob carga: {detalhe}"
    assert max(guarda) < PRAZO_DO_HOST_S / 2, f"guarda perto do prazo do host: {detalhe}"
