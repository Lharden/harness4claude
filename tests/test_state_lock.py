"""Testes de concorrencia para state-lock.sh.

Garante:
- acquire/release basico
- 2 acquires concorrentes: um espera, sem corrupcao
- timeout retorna 1
- stale lock e auto-removido
- N=10 processos concorrentes nao corrompem state.json
- Lock e re-entrante apenas via re-acquire (release explicito)
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
LOCK_SH = ROOT / "scripts" / "state-lock.sh"

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


@pytest.fixture
def harness_dir(tmp_path):
    """Use tmp dir como HARNESS_DIR para isolar de instancia real."""
    d = tmp_path / "harness"
    d.mkdir()
    return d


def _env(harness_dir: Path, **extra: str) -> dict[str, str]:
    env = os.environ.copy()
    env["HARNESS_DIR"] = str(harness_dir)
    env.update(extra)
    return env


def _run_lock(args: list[str], harness_dir: Path, **extra) -> subprocess.CompletedProcess:
    return subprocess.run(
        [BASH, str(LOCK_SH), *args],
        env=_env(harness_dir, **extra),
        capture_output=True,
        text=True,
        timeout=15,
    )


class TestBasicLifecycle:
    def test_acquire_then_release(self, harness_dir):
        # Acquire em sub-shell so locks dentro daquele processo. Para testar
        # de fora, precisamos checar via filesystem.
        # Usamos um script inline que adquire, segura, e libera.
        script = f"""
            source "{LOCK_SH}"
            acquire_state_lock || exit 99
            test -d "$STATE_LOCK_DIR" || exit 98
            release_state_lock
            test ! -d "$STATE_LOCK_DIR" || exit 97
            exit 0
        """
        result = subprocess.run(
            [BASH, "-c", script],
            env=_env(harness_dir),
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"

    def test_age_secs_when_no_lock(self, harness_dir):
        result = _run_lock(["age-secs"], harness_dir)
        assert result.returncode == 0
        assert result.stdout.strip() == "-1"

    def test_is_locked_false_initially(self, harness_dir):
        result = _run_lock(["is-locked"], harness_dir)
        assert result.returncode == 1


class TestConcurrency:
    def test_two_concurrent_acquires_serialize(self, harness_dir):
        """Dois processos concorrentes: ambos acquire, mas serializados."""
        # O que se afirma e ORDEM, medida dentro da secao critica com
        # $EPOCHREALTIME (builtin, microssegundos) — nao um teto de tempo de
        # parede. `date +%s` tem resolucao de 1s e cada fork custa 0,1-0,3s no
        # Git Bash: um teto de 3s reprovava por carga da maquina, sem defeito
        # de exclusao mutua (medido: elapsed=4 sob `-n 8`).
        marks = harness_dir / "marks"
        marks.mkdir()
        script_a = f"""
            source "{LOCK_SH}"
            acquire_state_lock || exit 1
            echo "$EPOCHREALTIME" > "{marks.as_posix()}/a_in"
            : > "{marks.as_posix()}/a_holds"
            sleep 1
            echo "$EPOCHREALTIME" > "{marks.as_posix()}/a_out"
            release_state_lock
        """
        script_b = f"""
            source "{LOCK_SH}"
            acquire_state_lock || exit 1
            echo "$EPOCHREALTIME" > "{marks.as_posix()}/b_in"
            release_state_lock
        """
        # STATE_LOCK_TIMEOUT_SECS so guarda contra deadlock; timeout tem teste proprio.
        env = _env(harness_dir, STATE_LOCK_TIMEOUT_SECS="60")

        proc_a = subprocess.Popen(
            [BASH, "-c", script_a],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        # B so nasce depois que A COMPROVADAMENTE segura o lock (sem sleep cego).
        deadline = time.monotonic() + 60
        while not (marks / "a_holds").exists():
            assert time.monotonic() < deadline, "A nunca pegou o lock"
            assert proc_a.poll() is None, "A morreu antes de pegar o lock"
            time.sleep(0.02)
        proc_b = subprocess.Popen(
            [BASH, "-c", script_b],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        proc_a.wait(timeout=90)
        proc_b.wait(timeout=90)
        assert proc_a.returncode == 0
        assert proc_b.returncode == 0

        def mark(name: str) -> float:
            return float((marks / name).read_text(encoding="utf-8").strip().replace(",", "."))

        a_in, a_out, b_in = mark("a_in"), mark("a_out"), mark("b_in")
        assert a_out - a_in >= 1.0, "A nao segurou o lock pelo tempo do sleep"
        # Serializacao: B so entrou depois que A saiu da secao critica.
        assert b_in >= a_out, f"secoes criticas se sobrepuseram: b_in={b_in} < a_out={a_out}"

    def test_timeout_returns_failure(self, harness_dir):
        """Lock segurado por outro processo + timeout curto = falha."""
        # Hold script segura por 3s
        hold_script = f"""
            source "{LOCK_SH}"
            acquire_state_lock || exit 1
            sleep 3
            release_state_lock
        """
        proc_hold = subprocess.Popen(
            [BASH, "-c", hold_script],
            env=_env(harness_dir), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        time.sleep(0.3)
        # Tenta com timeout 1s — deve falhar
        result = _run_lock(
            ["acquire"], harness_dir,
            STATE_LOCK_TIMEOUT_SECS="1",
        )
        assert result.returncode == 1
        assert "timeout" in result.stderr.lower()
        proc_hold.wait(timeout=10)


class TestStaleHandling:
    def test_stale_lock_auto_removed(self, harness_dir):
        """Lockdir antigo (mtime > stale threshold) e removido na proxima aquisicao."""
        lockdir = harness_dir / "state.json.lockdir"
        lockdir.mkdir()
        # Backdate mtime 60s para o passado
        old = time.time() - 60
        os.utime(lockdir, (old, old))

        # Stale threshold = 10s, lockdir tem 60s -> deve ser removido
        result = _run_lock(
            ["acquire"], harness_dir,
            STATE_LOCK_STALE_SECS="10",
            STATE_LOCK_TIMEOUT_SECS="2",
        )
        assert result.returncode == 0
        assert lockdir.exists()  # foi recriado pelo acquire
        # Cleanup
        _run_lock(["release"], harness_dir)

    def test_fresh_lock_not_removed(self, harness_dir):
        """Lockdir novo (mtime recente) nao e removido por stale-detection."""
        lockdir = harness_dir / "state.json.lockdir"
        lockdir.mkdir()
        result = _run_lock(
            ["acquire"], harness_dir,
            STATE_LOCK_STALE_SECS="100",
            STATE_LOCK_TIMEOUT_SECS="1",
        )
        assert result.returncode == 1


class TestWriteRaceProtection:
    """Cenario realistico: N processos escrevem state.json sob lock.
    Sem lock, alguns escreveriam JSON corrompido. Com lock, todos preservam.
    """

    @pytest.mark.parametrize("n_workers", [5, 10])
    def test_concurrent_writes_no_corruption(self, harness_dir, n_workers):
        counter_file = harness_dir / "counter"
        writers_file = harness_dir / "writers"
        # newline="\n": no Windows `write_text` viraria "0\r\n" e o `$((n + 1))` do bash quebraria.
        counter_file.write_text("0\n", encoding="utf-8", newline="\n")
        writers_file.write_text("", encoding="utf-8", newline="\n")

        # Cada worker: acquire lock, le counter, ESPERA (abre a janela do
        # lost-update: sem lock todos leem o mesmo valor), incrementa, anota o id,
        # release. A secao critica e propria do teste e nao paga o startup de um
        # `python` (~1-1,5s neste host): com 10 workers em serie isso ja comia o
        # timeout de 20s inteiro, e o teste reprovava por carga, nao por defeito.
        worker_template = f"""
            source "{LOCK_SH}"
            acquire_state_lock || exit 1
            read -r n < "$HARNESS_DIR/counter"
            sleep 0.2
            echo $((n + 1)) > "$HARNESS_DIR/counter"
            echo "$WORKER_ID" >> "$HARNESS_DIR/writers"
            release_state_lock
        """

        # STATE_LOCK_TIMEOUT_SECS so guarda contra deadlock; timeout tem teste proprio.
        env = _env(harness_dir, STATE_LOCK_TIMEOUT_SECS="120")

        procs = []
        for i in range(n_workers):
            worker_env = env.copy()
            worker_env["WORKER_ID"] = str(i)
            p = subprocess.Popen(
                [BASH, "-c", worker_template],
                env=worker_env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            procs.append(p)

        for p in procs:
            p.wait(timeout=150)

        failures = [p.returncode for p in procs if p.returncode != 0]
        assert not failures, f"workers falharam: {failures}"

        # counter == n_workers (nenhum lost-update); writers = n_workers ids unicos
        counter = int(counter_file.read_text(encoding="utf-8").strip())
        assert counter == n_workers, (
            f"counter perdeu writes: esperado {n_workers}, achou {counter}"
        )
        writers = [int(w) for w in writers_file.read_text(encoding="utf-8").split()]
        assert sorted(writers) == list(range(n_workers)), (
            f"writers ids inconsistentes: {writers}"
        )


class TestCustoDoLock:
    """O lock nao pode gastar processos por volta do laco.

    No Git Bash do Windows cada fork custa 0,1-0,3s (medido: `$(date)` 0,28s,
    `$(:)` 0,13s, builtin ~0). Com o caminho de aquisicao forkando ~9 vezes por
    tentativa, N waiters entopem a maquina, atrasam quem segura o lock e o
    handoff, e a suite inteira sob carga estoura o timeout de 20s. O oraculo
    aqui e a CONTAGEM de execs de `date`/`awk`/`stat` (shims no PATH), que nao
    depende de relogio nem de carga.
    """

    @staticmethod
    def _shims(tmp_path: Path) -> Path:
        shim_dir = tmp_path / "shims"
        shim_dir.mkdir()
        for name in ("date", "awk", "stat"):
            shim = shim_dir / name
            shim.write_text(
                f'#!/bin/bash\necho {name} >> "$SHIM_LOG"\nexec /usr/bin/{name} "$@"\n',
                encoding="utf-8", newline="\n",
            )
            shim.chmod(0o755)
        return shim_dir

    @staticmethod
    def _counts(log: Path) -> dict[str, int]:
        counts: dict[str, int] = {}
        if log.exists():
            for line in log.read_text(encoding="utf-8").split():
                counts[line] = counts.get(line, 0) + 1
        return counts

    def _env_com_shims(self, tmp_path, harness_dir, **extra):
        log = tmp_path / "shim.log"
        shim_dir = self._shims(tmp_path)
        env = _env(harness_dir, SHIM_LOG=str(log), **extra)
        env["PATH"] = f"{shim_dir}{os.pathsep}{env['PATH']}"
        return env, log

    def test_acquire_release_sem_fork_de_relogio_ou_awk(self, harness_dir, tmp_path):
        env, log = self._env_com_shims(tmp_path, harness_dir)
        script = f"""
            source "{LOCK_SH}"
            acquire_state_lock || exit 1
            release_state_lock
        """
        result = subprocess.run(
            [BASH, "-c", script], env=env, capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert self._counts(log) == {}, f"acquire+release forkaram: {self._counts(log)}"

    def test_espera_nao_forka_a_cada_volta(self, harness_dir, tmp_path):
        """Lock fresco segurado por outro: o waiter gira ~20 voltas/s. Relogio e
        awk nao forkam nunca; `stat` (checagem de stale) no maximo 1 por segundo.
        """
        (harness_dir / "state.json.lockdir").mkdir()
        env, log = self._env_com_shims(
            tmp_path, harness_dir,
            STATE_LOCK_TIMEOUT_SECS="2", STATE_LOCK_STALE_SECS="100",
        )
        t0 = time.monotonic()
        result = subprocess.run(
            [BASH, str(LOCK_SH), "acquire"],
            env=env, capture_output=True, text=True, timeout=30,
        )
        elapsed = time.monotonic() - t0
        assert result.returncode == 1
        counts = self._counts(log)
        assert counts.get("date", 0) == 0, counts
        assert counts.get("awk", 0) == 0, counts
        assert counts.get("stat", 0) <= int(elapsed) + 2, (counts, elapsed)


class TestReentrancySemantics:
    def test_release_only_removes_own_lock(self, harness_dir):
        """release_state_lock so remove se owner_pid bate com $$."""
        # Cria lockdir manualmente com owner = PID falso
        lockdir = harness_dir / "state.json.lockdir"
        lockdir.mkdir()
        (lockdir / "owner").write_text("999999 12345\n", encoding="utf-8")

        # release_state_lock deve recusar (owner != $$)
        script = f"""
            source "{LOCK_SH}"
            release_state_lock
            test -d "$STATE_LOCK_DIR" && exit 0 || exit 1
        """
        result = subprocess.run(
            [BASH, "-c", script],
            env=_env(harness_dir),
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0, "lockdir foi removido indevidamente"
