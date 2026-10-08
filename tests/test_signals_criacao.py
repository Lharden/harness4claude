"""A criacao de `signals.json` no bootstrap nao apaga o que outro escritor gravou.

`harness-session-start.sh` e `scripts/init-state.sh` criavam o arquivo com

    if [ ! -f "$HARNESS_DIR/signals.json" ]; then
        cat > "$HARNESS_DIR/signals.json" << 'EOF'

sem lock: checa, e so depois abre com truncamento e escreve. Os escritores em
Python (`record_signal.record`, `branch_state.signal`, `migrate_state.run`) se
excluem pelo `_Lock` de `signals.json` desde 2026-10-07, mas o bash nao o toma.
No intervalo entre a abertura e a escrita do `cat` o arquivo existe e esta
VAZIO: um `record` que roda ali le JSON vazio e levanta, e a task do DONE nao
chega a `signals.json`. Se o `record` ganhar a corrida inteira, o `cat` grava o
modelo por cima e a task some do mesmo jeito.

A janela so existe na primeira criacao numa maquina nova, mas existe: duas
sessoes abrindo juntas numa instalacao nova, ou `sync-machine.sh` rodando
`init-state.sh` com uma sessao viva.

O ponto de sincronizacao e um `cat` falso no PATH: o bash ja abriu o arquivo (a
redirecao e feita antes do exec) e o `cat` para antes de escrever. O teste so
solta o bash depois de o `record` terminar. Sem `cat` na criacao, o bash termina
sozinho e o `record` roda depois; o teste espera o que acontecer primeiro.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
SCRIPTS = ROOT / "scripts"
HOOKS = ROOT / "hooks"

_PRAZO = 60.0

BASH = "bash"
if sys.platform == "win32":
    for _candidato in (
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
    ):
        if Path(_candidato).exists():
            BASH = _candidato
            break

#: Para so na escrita de signals.json: o arquivo ja existe e ainda esta vazio.
#: O `cat` de state.json, do plugin-root e de qualquer outro passa direto.
_CAT_FALSO = """#!/usr/bin/env bash
if [ -n "${SHIM_PAUSA:-}" ] && [ -e "$SHIM_ALVO" ] && [ ! -s "$SHIM_ALVO" ] \\
   && [ ! -e "$SHIM_PAUSA/parado" ]; then
    : > "$SHIM_PAUSA/parado"
    while [ ! -e "$SHIM_PAUSA/segue" ]; do /usr/bin/sleep 0.05; done
fi
exec /usr/bin/cat "$@"
"""


def _barra(p: Path) -> str:
    return str(p).replace("\\", "/")


def _carrega(nome: str):
    spec = importlib.util.spec_from_file_location(nome, SCRIPTS / f"{nome}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[nome] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def mods(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    for nome in ("branch_state", "migrate_state", "record_signal"):
        monkeypatch.delitem(sys.modules, nome, raising=False)
    bs = _carrega("branch_state")
    ms = _carrega("migrate_state")
    rs = _carrega("record_signal")
    monkeypatch.setattr(bs, "LOCK_TIMEOUT_S", _PRAZO)
    return types.SimpleNamespace(bs=bs, ms=ms, rs=rs)


def _task(rs) -> dict:
    return rs.build_task(
        {"task_id": "t-criacao", "classification": "L1-bug"},
        {"count": 2},
        completed=True,
        steps=["tdd"],
        reason=None,
        timestamp="2026-10-07T00:00:00+00:00",
    )


def _espera(cond, o_que: str) -> None:
    limite = time.monotonic() + _PRAZO
    while not cond():
        assert time.monotonic() < limite, f"prazo esgotado esperando {o_que}"
        time.sleep(0.02)


_CRIADORES = {
    "session-start": (HOOKS / "harness-session-start.sh", "{}"),
    "init-state": (SCRIPTS / "init-state.sh", ""),
}


class TestCriacaoBashNaoApagaTask:
    @pytest.mark.parametrize("criador", sorted(_CRIADORES))
    def test_record_durante_a_criacao_registra_a_task(self, mods, tmp_path, criador):
        script, stdin = _CRIADORES[criador]
        harness = tmp_path / "h"
        harness.mkdir()
        alvo = harness / "signals.json"
        pausa = tmp_path / "pausa"
        pausa.mkdir()
        shim = tmp_path / "shim"
        shim.mkdir()
        (shim / "cat").write_bytes(_CAT_FALSO.encode("utf-8"))
        os.chmod(shim / "cat", 0o755)

        env = os.environ.copy()
        env.update(
            HARNESS_DIR=str(harness),
            HARNESS_SKIP_DEPCHECK="1",
            PYTHONUTF8="1",
            SHIM_PAUSA=_barra(pausa),
            SHIM_ALVO=_barra(alvo),
            PATH=str(shim) + os.pathsep + env.get("PATH", ""),
        )
        proc = subprocess.Popen(
            [BASH, str(script)], env=env, stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            assert proc.stdin is not None
            proc.stdin.write(stdin.encode("utf-8"))
            proc.stdin.close()
            # O bash parou no meio da escrita de signals.json, ou terminou.
            _espera(lambda: (pausa / "parado").exists() or proc.poll() is not None,
                    f"{criador} parar no cat ou terminar")

            erro: list[BaseException] = []
            try:
                mods.rs.record(harness, _task(mods.rs))
            except BaseException as exc:  # noqa: BLE001 - reportado abaixo
                erro.append(exc)
        finally:
            (pausa / "segue").touch()
            proc.wait(_PRAZO)

        assert not erro, f"record durante a criacao por {criador} levantou: {erro[0]!r}"
        doc = json.loads(alvo.read_text(encoding="utf-8"))
        assert [t["task_id"] for t in doc.get("tasks", [])] == ["t-criacao"], (
            f"a task gravada durante a criacao por {criador} sumiu: {doc.get('tasks')!r}"
        )
