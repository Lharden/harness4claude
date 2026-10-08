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
import threading
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

    @pytest.mark.parametrize("criador", sorted(_CRIADORES))
    def test_criador_sozinho_cria_signals_v3(self, tmp_path, criador):
        """O verde acima nao basta: `record` tambem cria o arquivo quando falta.

        Sem este, um criador que deixou de criar (Python ausente, caminho
        errado, `|| true` engolindo o erro) passaria pelo teste da corrida.
        """
        script, stdin = _CRIADORES[criador]
        harness = tmp_path / "h"
        harness.mkdir()
        env = os.environ.copy()
        env.update(HARNESS_DIR=str(harness), HARNESS_SKIP_DEPCHECK="1", PYTHONUTF8="1")
        subprocess.run([BASH, str(script)], env=env, input=stdin, text=True,
                       capture_output=True, timeout=_PRAZO)
        doc = json.loads((harness / "signals.json").read_text(encoding="utf-8"))
        assert doc["version"] == 3 and doc["tasks"] == []
        assert "classify" in doc["aggregates"], "modelo sem o bloco classify da migracao"


class TestCriaSignalsSobLock:
    def test_record_durante_cria_signals_espera_e_grava_a_task(self, mods, tmp_path, monkeypatch):
        """cria_signals decide criar, para antes de gravar; record tem de esperar.

        Soltura por evento, como em test_signals_escritores.py: o record
        terminou (sem lock comum) ou bateu no lockdir ocupado.
        """
        parado = threading.Event()
        segue = threading.Event()
        record_decidiu = threading.Event()
        erros: dict[str, BaseException] = {}

        grava_real = mods.ms._grava_atomico

        def grava_parado(path, data):
            parado.set()
            assert segue.wait(_PRAZO), "cria_signals nunca foi solto"
            grava_real(path, data)

        monkeypatch.setattr(mods.ms, "_grava_atomico", grava_parado)
        mkdir_real = os.mkdir

        def mkdir(path, *a, **kw):
            try:
                return mkdir_real(path, *a, **kw)
            except FileExistsError:
                if (threading.current_thread().name == "record"
                        and str(path).endswith("signals.json.lockdir")):
                    record_decidiu.set()
                raise

        monkeypatch.setattr(os, "mkdir", mkdir)

        def roda(nome, corpo, fim=None):
            def alvo():
                try:
                    corpo()
                except BaseException as exc:  # noqa: BLE001 - reportado abaixo
                    erros[nome] = exc
                finally:
                    if fim is not None:
                        fim.set()
            t = threading.Thread(target=alvo, name=nome, daemon=True)
            t.start()
            return t

        t1 = roda("cria", lambda: mods.ms.cria_signals(tmp_path))
        assert parado.wait(_PRAZO), "cria_signals nunca chegou a gravar"
        t2 = roda("record", lambda: mods.rs.record(tmp_path, _task(mods.rs)), record_decidiu)
        assert record_decidiu.wait(_PRAZO), "record nem terminou nem esperou o lock"
        segue.set()
        for t in (t1, t2):
            t.join(_PRAZO)
            assert not t.is_alive(), f"{t.name} nao terminou"
        assert not erros, f"participante levantou: {erros!r}"
        doc = json.loads((tmp_path / "signals.json").read_text(encoding="utf-8"))
        assert [t["task_id"] for t in doc["tasks"]] == ["t-criacao"], (
            f"cria_signals gravou o modelo por cima da task: {doc['tasks']!r}"
        )
