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


def _espera_marca(marca: Path, proc: subprocess.Popen, quem: str, prazo: float = 60) -> None:
    """Espera `marca` existir, prova de que `quem` chegou ali. Sem sleep cego."""
    deadline = time.monotonic() + prazo
    while not marca.exists():
        if proc.poll() is not None:
            assert marca.exists(), f"{quem} saiu com rc={proc.returncode} antes de {marca.name}"
            return
        assert time.monotonic() < deadline, f"{quem} nunca chegou em {marca.name}"
        time.sleep(0.02)


class TestConcurrency:
    def test_two_concurrent_acquires_serialize(self, harness_dir, tmp_path):
        """Dois processos concorrentes: B so entra depois que A sai da secao critica."""
        # O que se afirma e ORDEM, medida dentro da secao critica com
        # $EPOCHREALTIME (builtin, microssegundos) — nao um teto de tempo de
        # parede. `date +%s` tem resolucao de 1s e cada fork custa 0,1-0,3s no
        # Git Bash: um teto de 3s reprovava por carga da maquina, sem defeito
        # de exclusao mutua (medido: elapsed=4 sob `-n 8`).
        #
        # A DISPUTA tambem e garantida por construcao, nao por relogio: um shim
        # de `mkdir` so no PATH de B anota o codigo de saida de cada tentativa,
        # e A segura o lock ate B ser RECUSADO duas vezes (B esperou uma volta
        # inteira) ou ate B entrar (lock quebrado). Com A segurando por `sleep
        # 1`, sob carga B nascia depois que A ja tinha soltado: nao havia
        # disputa, e o teste passava com um lock que rouba a vez sob disputa.
        marks = harness_dir / "marks"
        marks.mkdir()
        m = marks.as_posix()
        tentativas_b = marks / "b_mkdir"

        shim_dir = tmp_path / "shim-b"
        shim_dir.mkdir()
        shim = shim_dir / "mkdir"
        # Tira o proprio diretorio do PATH e chama o mkdir de verdade: sem
        # caminho absoluto, que muda entre Git Bash, Linux e macOS.
        shim.write_text(
            '#!/bin/bash\n'
            'PATH="${PATH#*:}"\n'
            'mkdir "$@"; rc=$?\n'
            'echo "$rc" >> "$SHIM_LOG"\n'
            'exit "$rc"\n',
            encoding="utf-8", newline="\n",
        )
        shim.chmod(0o755)

        script_a = f"""
            source "{LOCK_SH}"
            acquire_state_lock || exit 1
            echo "$EPOCHREALTIME" > "{m}/a_in"
            : > "{m}/a_holds"
            fim=$(( ${{EPOCHREALTIME%[.,]*}} + 60 ))
            while (( ${{EPOCHREALTIME%[.,]*}} < fim )); do
              [[ -e "{m}/b_in" ]] && break
              recusas=0
              if [[ -f "{m}/b_mkdir" ]]; then
                while read -r rc; do
                  [[ "$rc" == 0 ]] || recusas=$((recusas + 1))
                done < "{m}/b_mkdir"
              fi
              (( recusas >= 2 )) && break
              sleep 0.05
            done
            echo "$EPOCHREALTIME" > "{m}/a_out"
            release_state_lock
        """
        script_b = f"""
            source "{LOCK_SH}"
            acquire_state_lock || exit 1
            echo "$EPOCHREALTIME" > "{m}/b_in"
            release_state_lock
        """
        # STATE_LOCK_TIMEOUT_SECS so guarda contra deadlock; timeout tem teste proprio.
        env = _env(harness_dir, STATE_LOCK_TIMEOUT_SECS="60")
        env_b = dict(env, SHIM_LOG=tentativas_b.as_posix())
        env_b["PATH"] = f"{shim_dir}{os.pathsep}{env['PATH']}"

        proc_a = subprocess.Popen(
            [BASH, "-c", script_a],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        # B so nasce depois que A COMPROVADAMENTE segura o lock (sem sleep cego).
        _espera_marca(marks / "a_holds", proc_a, "A")
        proc_b = subprocess.Popen(
            [BASH, "-c", script_b],
            env=env_b, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        _, err_a = proc_a.communicate(timeout=90)
        _, err_b = proc_b.communicate(timeout=90)
        assert proc_a.returncode == 0, err_a
        assert proc_b.returncode == 0, err_b

        def mark(name: str) -> float:
            return float((marks / name).read_text(encoding="utf-8").strip().replace(",", "."))

        a_out, b_in = mark("a_out"), mark("b_in")
        recusas = [
            rc for rc in (tentativas_b.read_text(encoding="utf-8").split() if tentativas_b.exists() else [])
            if rc != "0"
        ]
        # Serializacao: B so entrou depois que A saiu da secao critica.
        assert b_in >= a_out, f"secoes criticas se sobrepuseram: b_in={b_in} < a_out={a_out}"
        # E houve disputa de fato: o mkdir de B foi recusado enquanto A segurava.
        assert len(recusas) >= 2, f"B nao disputou o lock com A: {len(recusas)} tentativa(s) recusada(s)"

    def test_timeout_returns_failure(self, harness_dir):
        """Lock segurado por outro processo + timeout curto = falha."""
        # Sem sleep cego dos dois lados. O acquire so e tentado depois que o
        # holder COMPROVADAMENTE segura o lock, e o holder so solta depois que a
        # tentativa terminou. Com `sleep(0.3)` antes do acquire e `sleep 3` no
        # holder, sob carga o acquire vencia a corrida (rc 0: "assert 0 == 1",
        # medido sob saturacao) ou nascia depois de o holder soltar.
        marks = harness_dir / "marks"
        marks.mkdir()
        m = marks.as_posix()
        hold_script = f"""
            source "{LOCK_SH}"
            acquire_state_lock || exit 1
            : > "{m}/holds"
            while [[ ! -e "{m}/solta" ]]; do sleep 0.05; done
            release_state_lock
        """
        proc_hold = subprocess.Popen(
            [BASH, "-c", hold_script],
            env=_env(harness_dir), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            _espera_marca(marks / "holds", proc_hold, "holder")
            # Tenta com timeout 1s — deve falhar
            result = _run_lock(
                ["acquire"], harness_dir,
                STATE_LOCK_TIMEOUT_SECS="1",
            )
        finally:
            (marks / "solta").touch()
            _, err_hold = proc_hold.communicate(timeout=60)
        assert result.returncode == 1
        assert "timeout" in result.stderr.lower()
        assert proc_hold.returncode == 0, err_hold


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
        # Timeout de acquire sai com 75 e diz o prazo: "workers falharam: [1, 1]"
        # nao distinguia timeout de erro na secao critica.
        worker_template = f"""
            source "{LOCK_SH}"
            acquire_state_lock || {{ echo "timeout de lock apos ${{STATE_LOCK_TIMEOUT_SECS}}s" >&2; exit 75; }}
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
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            procs.append(p)

        falhas = {}
        for i, p in enumerate(procs):
            _, err = p.communicate(timeout=150)
            if p.returncode != 0:
                falhas[i] = (p.returncode, err.strip()[-200:])
        assert not falhas, f"workers falharam (id: rc, stderr): {falhas}"

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
        """release_state_lock so remove o lock que o proprio shell adquiriu."""
        # Cria lockdir manualmente com owner = PID falso
        lockdir = harness_dir / "state.json.lockdir"
        lockdir.mkdir()
        (lockdir / "owner").write_text("999999 12345\n", encoding="utf-8")

        # release_state_lock deve recusar (este shell nao adquiriu nada)
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


class TestIntervaloDeEspera:
    """O intervalo de retry e o declarado em STATE_LOCK_POLL_MS, em milissegundos.

    Antes, `poll_secs="0.${STATE_LOCK_POLL_MS}"` fazia 50 virar `sleep 0.50`
    (meio segundo, nao 50 ms) e 5 virar `0.5`. Cada passagem do lock custava
    ate 10x o documentado, e dez workers em fila estouravam os 20 s do teste de
    escrita concorrente sob carga (2026-09-30). Prova sem relogio: um `sleep`
    de mentira registra o argumento que o lock realmente usa.
    """

    @pytest.mark.parametrize("poll_ms,esperado", [("50", "0.050"), ("5", "0.005"), ("1500", "1.500")])
    def test_sleep_recebe_o_intervalo_em_segundos(self, harness_dir, poll_ms, esperado):
        (harness_dir / "state.json.lockdir").mkdir()  # ocupado: forca uma espera
        script = f"""
            source "{LOCK_SH}"
            sleep() {{ echo "$1"; exit 0; }}
            STATE_LOCK_STALE_SECS=3600
            acquire_state_lock
            exit 9
        """
        result = subprocess.run(
            [BASH, "-c", script],
            env=_env(harness_dir, STATE_LOCK_POLL_MS=poll_ms, STATE_LOCK_TIMEOUT_SECS="30"),
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == esperado


class TestCaminhoSemDisputaNaoLancaProcessos:
    """acquire + release sem disputa lancam so o que o lock precisa: mkdir e rm.

    No Git Bash do Windows cada processo externo custa ~200 ms. O lock lancava
    `date` (2x no acquire), `awk` e `mkdir -p` a toda passagem: um acquire sem
    disputa levava 570 ms, e dez workers em fila (10 x ~1,5 s) chegavam a 15 s
    contra os 20 s do teste de escrita concorrente, que estourava com qualquer
    carga (2026-09-30). Prova sem relogio: cada externo vigiado registra a
    chamada antes de executar.
    """

    def test_acquire_e_release_lancam_so_mkdir_e_rm(self, harness_dir):
        spy = harness_dir / "spy.log"
        vigiados = "date stat awk sleep mkdir rm"
        script = f"""
            source "{LOCK_SH}"
            for c in {vigiados}; do
                eval "$c() {{ echo $c >> \\"\\$SPY\\"; command $c \\"\\$@\\"; }}"
            done
            acquire_state_lock || exit 1
            release_state_lock
        """
        result = subprocess.run(
            [BASH, "-c", script],
            env=_env(harness_dir, SPY=spy.as_posix()),
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert spy.read_text(encoding="utf-8").split() == ["mkdir", "rm"]


# ---------------------------------------------------------------------------
# Corrida de verificar-e-remover na quebra de lock stale
# ---------------------------------------------------------------------------
#
# A checagem de idade e a remocao nao sao uma operacao so. Entre as duas, o
# lockdir velho pode ter sido quebrado por outro waiter e pego por um terceiro:
# quem remove pelo NOME, com a decisao tomada sobre o lockdir anterior, apaga o
# lock do terceiro. Os testes abrem essa janela por construcao, sem relogio:
# shims no PATH de UM processo param a primeira chamada destrutiva dele (`rm`,
# `rmdir` ou `mv`, o que a implementacao usar) ou a volta do primeiro `mkdir`
# que deu certo, e so soltam quando o teste cria o arquivo de "segue". O shim
# fotografa o lockdir logo depois do comando destrutivo.

_SHIM_DESTRUTIVO = """#!/bin/bash
# Primeira chamada destrutiva deste processo: avisa, espera a vez, executa e
# fotografa o lockdir logo depois. As demais passam direto.
PATH="${PATH#*:}"
if mkdir "$SHIM_MARKS/destrutivo" 2>/dev/null; then
  echo "@CMD@ $*" > "$SHIM_MARKS/pausado.tmp"
  mv "$SHIM_MARKS/pausado.tmp" "$SHIM_MARKS/pausado"
  while [[ ! -e "$SHIM_MARKS/segue" ]]; do sleep 0.02; done
  @CMD@ "$@"; rc=$?
  if [[ -d "$LOCKDIR" ]]; then foto=presente; else foto=ausente; fi
  echo "$foto" > "$SHIM_MARKS/depois.tmp"
  mv "$SHIM_MARKS/depois.tmp" "$SHIM_MARKS/depois"
  echo "destrutivo $rc" >> "$SHIM_LOG"
  exit "$rc"
fi
exec @CMD@ "$@"
"""

_SHIM_MKDIR = """#!/bin/bash
# Registra cada tentativa ("mkdir <rc>"). Com SHIM_PAUSA_MKDIR, a primeira que
# deu certo avisa e espera a vez antes de voltar: o lockdir existe e o dono
# ainda nao gravou nada nele.
PATH="${PATH#*:}"
mkdir "$@"; rc=$?
echo "mkdir $rc" >> "$SHIM_LOG"
if [[ $rc == 0 && -n "${SHIM_PAUSA_MKDIR:-}" ]] && mkdir "$SHIM_MARKS/mkdir-pausou" 2>/dev/null; then
  : > "$SHIM_MARKS/criou"
  while [[ ! -e "$SHIM_MARKS/segue-mkdir" ]]; do sleep 0.02; done
fi
exit "$rc"
"""

_SHIM_STAT = """#!/bin/bash
# Primeira chamada: executa, avisa e espera a vez antes de devolver a resposta.
# Quem chamou fica com a idade do lockdir na mao e ainda nao agiu.
PATH="${PATH#*:}"
saida=$(stat "$@"); rc=$?
if mkdir "$SHIM_MARKS/stat-pausou" 2>/dev/null; then
  : > "$SHIM_MARKS/leu-idade"
  while [[ ! -e "$SHIM_MARKS/segue-stat" ]]; do sleep 0.02; done
fi
if [[ -n "$saida" ]]; then printf '%s\\n' "$saida"; fi
exit "$rc"
"""


def _com_shims(
    env: dict[str, str], tmp_path: Path, nome: str, lockdir: Path,
    *, destrutivo: bool = True, pausa_mkdir: bool = False, pausa_stat: bool = False,
) -> tuple[dict[str, str], Path, Path]:
    """Ambiente de UM processo com os shims de sincronizacao no inicio do PATH.

    Devolve (env, marcas, log). `marcas/pausado` aparece quando a primeira
    chamada destrutiva parou, `marcas/segue` a solta e `marcas/depois` diz se o
    lockdir existia logo depois dela. `log` tem, em ordem, uma linha por
    `mkdir` ("mkdir <rc>") e uma pela chamada destrutiva ("destrutivo <rc>").
    Com `pausa_stat`, `marcas/leu-idade` aparece quando o primeiro `stat`
    terminou e `marcas/segue-stat` devolve a resposta a quem chamou.
    """
    shim_dir = tmp_path / f"shims-{nome}"
    marcas = tmp_path / f"marcas-{nome}"
    shim_dir.mkdir()
    marcas.mkdir()
    corpos = {"mkdir": _SHIM_MKDIR}
    if pausa_stat:
        corpos["stat"] = _SHIM_STAT
    if destrutivo:
        corpos.update({cmd: _SHIM_DESTRUTIVO.replace("@CMD@", cmd) for cmd in ("rm", "rmdir", "mv")})
    for cmd, corpo in corpos.items():
        shim = shim_dir / cmd
        shim.write_text(corpo, encoding="utf-8", newline="\n")
        shim.chmod(0o755)
    log = marcas / "eventos.log"
    env = dict(env, SHIM_MARKS=marcas.as_posix(), SHIM_LOG=log.as_posix(), LOCKDIR=lockdir.as_posix())
    if pausa_mkdir:
        env["SHIM_PAUSA_MKDIR"] = "1"
    env["PATH"] = f"{shim_dir}{os.pathsep}{env['PATH']}"
    return env, marcas, log


def _eventos(log: Path) -> list[str]:
    """Linhas completas do log de eventos (a ultima pode estar sendo escrita)."""
    if not log.exists():
        return []
    return log.read_text(encoding="utf-8").split("\n")[:-1]


def _mkdirs(log: Path) -> list[str]:
    """rc de cada `mkdir` do processo, em ordem."""
    return [ev.split()[1] for ev in _eventos(log) if ev.startswith("mkdir ")]


def _mkdir_depois_do_destrutivo(log: Path) -> str | None:
    """rc do primeiro `mkdir` tentado depois da chamada destrutiva, ou None."""
    eventos = _eventos(log)
    for i, ev in enumerate(eventos):
        if ev.startswith("destrutivo "):
            return next((d.split()[1] for d in eventos[i + 1:] if d.startswith("mkdir ")), None)
    return None


def _espera(cond, proc: subprocess.Popen, quem: str, o_que: str, prazo: float = 60) -> None:
    """Espera `cond()` valer. O sleep so espaca a consulta; nada depende dele."""
    deadline = time.monotonic() + prazo
    while not cond():
        if proc.poll() is not None:
            assert cond(), f"{quem} saiu com rc={proc.returncode} antes de {o_que}"
            return
        assert time.monotonic() < deadline, f"{quem} nunca chegou a {o_que}"
        time.sleep(0.02)


def _dono_morto(harness_dir: Path, preambulo: str = "") -> Path:
    """Lockdir de um dono que pegou o lock e morreu sem soltar, ha uma hora.

    O dono e o proprio lock (acquire num processo que sai sem release), entao o
    lockdir fica no formato que a implementacao grava, qualquer que seja ele.
    """
    script = f"""
        {preambulo}
        source "{LOCK_SH}"
        acquire_state_lock || exit 1
        exit 0
    """
    r = subprocess.run(
        [BASH, "-c", script], env=_env(harness_dir), capture_output=True, text=True, timeout=60,
    )
    assert r.returncode == 0, f"dono morto nao pegou o lock: {r.stderr}"
    lockdir = harness_dir / "state.json.lockdir"
    assert lockdir.is_dir(), "o dono morto nao deixou o lockdir"
    velho = time.time() - 3600
    os.utime(lockdir, (velho, velho))
    return lockdir


def _script_entra(m: str, nome: str) -> str:
    """Waiter no molde dos hooks: adquire, marca que entrou e solta no EXIT."""
    return f"""
        set -euo pipefail
        source "{LOCK_SH}"
        acquire_state_lock || exit 1
        trap release_state_lock EXIT
        : > "{m}/{nome}_entrou"
    """


def _script_segura(m: str, nome: str) -> str:
    """Dono no molde dos hooks: adquire, avisa e so solta quando o teste mandar."""
    return f"""
        set -euo pipefail
        source "{LOCK_SH}"
        acquire_state_lock || exit 1
        trap release_state_lock EXIT
        : > "{m}/{nome}_segura"
        while [[ ! -e "{m}/{nome}_solta" ]]; do sleep 0.05; done
    """


def _popen(script: str, env: dict[str, str]) -> subprocess.Popen:
    return subprocess.Popen(
        [BASH, "-c", script], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


def _colhe(*procs: subprocess.Popen | None) -> list[tuple[int, str]]:
    """(rc, stderr) de cada processo; quem nao sai em 60 s e morto."""
    saida = []
    for p in procs:
        if p is None:
            continue
        try:
            _, err = p.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            p.kill()
            _, err = p.communicate()
        saida.append((p.returncode, err))
    return saida


class TestCorridaDaQuebraDeStale:
    """Quem quebra um lock stale so remove o lock que julgou stale.

    O defeito existia no main antes do conserto de carga: dois waiters diante
    do mesmo lockdir velho concluem os dois "stale"; o primeiro quebra e o lock
    passa para um terceiro; o segundo, com a decisao tomada sobre o lockdir
    anterior, remove o lockdir do terceiro. A exclusao mutua acaba: o segundo
    entra com o terceiro ainda dentro.

    STATE_LOCK_STALE_SECS=300 com o dono morto ha uma hora: o lock velho e
    stale, e um lock novo so ficaria stale depois de cinco minutos, muito alem
    da duracao do teste sob qualquer carga. O timeout so guarda contra deadlock.
    """

    ENV = {"STATE_LOCK_STALE_SECS": "300", "STATE_LOCK_TIMEOUT_SECS": "60"}

    def test_waiter_atrasado_nao_remove_o_lock_que_um_terceiro_pegou(self, harness_dir, tmp_path):
        lockdir = _dono_morto(harness_dir)
        marcas = harness_dir / "marcas"
        marcas.mkdir()
        m = marcas.as_posix()
        env = _env(harness_dir, **self.ENV)
        env_w2, marcas_w2, log_w2 = _com_shims(env, tmp_path, "w2", lockdir)

        w2 = _popen(_script_entra(m, "w2"), env_w2)
        w3 = None
        try:
            # W2 julgou o lockdir velho stale e parou antes de remover.
            _espera_marca(marcas_w2 / "pausado", w2, "W2")
            # W1 tambem julga stale, quebra o lock velho, entra e sai.
            w1 = subprocess.run(
                [BASH, "-c", _script_entra(m, "w1")], env=env, capture_output=True, text=True, timeout=90,
            )
            assert w1.returncode == 0, w1.stderr
            # Um terceiro pega o lock e fica com ele.
            w3 = _popen(_script_segura(m, "w3"), env)
            _espera_marca(marcas / "w3_segura", w3, "W3")
            # W2 segue com a remocao que decidiu antes.
            (marcas_w2 / "segue").touch()
            _espera_marca(marcas_w2 / "depois", w2, "W2")
            _espera(
                lambda: _mkdir_depois_do_destrutivo(log_w2) is not None,
                w2, "W2", "tentar o lock de novo",
            )

            comando = (marcas_w2 / "pausado").read_text(encoding="utf-8").strip()
            foto = (marcas_w2 / "depois").read_text(encoding="utf-8").strip()
            assert foto == "presente", (
                f"o lock de W3 sumiu: W2 removeu o lockdir que W3 acabou de pegar ({comando})"
            )
            assert _mkdir_depois_do_destrutivo(log_w2) != "0", "W2 pegou o lock com W3 ainda dentro"
        finally:
            (marcas_w2 / "segue").touch()
            (marcas / "w3_solta").touch()
            resultados = _colhe(w2, w3)
        for rc, err in resultados:
            assert rc == 0, err

    def test_waiter_parado_depois_de_ler_a_idade_nao_remove_o_lock_novo(self, harness_dir, tmp_path):
        """A troca acontece entre a leitura da idade e o resto da quebra.

        Quem le a idade e so DEPOIS lista o lockdir junta a idade do lock velho
        com o dono do novo, e remove o dono do novo pelo nome certo.
        """
        lockdir = _dono_morto(harness_dir)
        marcas = harness_dir / "marcas"
        marcas.mkdir()
        m = marcas.as_posix()
        env = _env(harness_dir, **self.ENV)
        env_w2, marcas_w2, log_w2 = _com_shims(env, tmp_path, "w2", lockdir, pausa_stat=True)
        (marcas_w2 / "segue").touch()  # a chamada destrutiva so fotografa, sem parar

        w2 = _popen(_script_entra(m, "w2"), env_w2)
        w3 = None
        try:
            # W2 leu a idade do lockdir velho e parou antes de agir.
            _espera_marca(marcas_w2 / "leu-idade", w2, "W2")
            w1 = subprocess.run(
                [BASH, "-c", _script_entra(m, "w1")], env=env, capture_output=True, text=True, timeout=90,
            )
            assert w1.returncode == 0, w1.stderr
            w3 = _popen(_script_segura(m, "w3"), env)
            _espera_marca(marcas / "w3_segura", w3, "W3")
            (marcas_w2 / "segue-stat").touch()
            _espera_marca(marcas_w2 / "depois", w2, "W2")
            _espera(
                lambda: _mkdir_depois_do_destrutivo(log_w2) is not None,
                w2, "W2", "tentar o lock de novo",
            )

            comando = (marcas_w2 / "pausado").read_text(encoding="utf-8").strip()
            foto = (marcas_w2 / "depois").read_text(encoding="utf-8").strip()
            assert foto == "presente", (
                f"o lock de W3 sumiu: W2 juntou a idade do lock velho com o lock de W3 ({comando})"
            )
            assert _mkdir_depois_do_destrutivo(log_w2) != "0", "W2 pegou o lock com W3 ainda dentro"
        finally:
            (marcas_w2 / "segue-stat").touch()
            (marcas / "w3_solta").touch()
            resultados = _colhe(w2, w3)
        for rc, err in resultados:
            assert rc == 0, err

    def test_lock_que_nasce_durante_a_quebra_nao_fica_com_dois_donos(self, harness_dir, tmp_path):
        """O terceiro criou o lockdir e ainda nao gravou o dono quando a remocao atrasada chega."""
        lockdir = _dono_morto(harness_dir)
        marcas = harness_dir / "marcas"
        marcas.mkdir()
        m = marcas.as_posix()
        env = _env(harness_dir, **self.ENV)
        env_w2, marcas_w2, _ = _com_shims(env, tmp_path, "w2", lockdir)
        env_a, marcas_a, log_a = _com_shims(env, tmp_path, "a", lockdir, destrutivo=False, pausa_mkdir=True)

        w2 = _popen(_script_entra(m, "w2"), env_w2)
        a = b = None
        try:
            _espera_marca(marcas_w2 / "pausado", w2, "W2")
            w1 = subprocess.run(
                [BASH, "-c", _script_entra(m, "w1")], env=env, capture_output=True, text=True, timeout=90,
            )
            assert w1.returncode == 0, w1.stderr
            # A cria o lockdir e para antes de gravar o dono.
            a = _popen(_script_entra(m, "a"), env_a)
            _espera_marca(marcas_a / "criou", a, "A")
            # A remocao atrasada de W2 chega no lockdir recem-nascido de A.
            (marcas_w2 / "segue").touch()
            _espera_marca(marcas_w2 / "depois", w2, "W2")
            # B pega o lock e fica com ele.
            b = _popen(_script_segura(m, "b"), env)
            _espera_marca(marcas / "b_segura", b, "B")
            # A volta e grava o dono: no lockdir de B.
            (marcas_a / "segue-mkdir").touch()
            _espera(
                lambda: (marcas / "a_entrou").exists() or len(_mkdirs(log_a)) >= 2,
                a, "A", "entrar ou tentar o lock de novo",
            )

            assert not (marcas / "a_entrou").exists(), "A e B ficaram os dois com o lock"
            assert _mkdirs(log_a)[1] != "0", "A pegou o lock com B ainda dentro"
        finally:
            (marcas_w2 / "segue").touch()
            (marcas_a / "segue-mkdir").touch()
            (marcas / "b_solta").touch()
            resultados = _colhe(w2, a, b)
        for rc, err in resultados:
            assert rc == 0, err

    def test_dono_que_perdeu_o_lock_por_prazo_nao_remove_o_do_sucessor(self, harness_dir, tmp_path):
        """O dono passou do prazo e outro quebrou o lock; o release atrasado do primeiro nao apaga o do segundo."""
        lockdir = harness_dir / "state.json.lockdir"
        marcas = harness_dir / "marcas"
        marcas.mkdir()
        m = marcas.as_posix()
        env = _env(harness_dir, **self.ENV)
        env_h, marcas_h, _ = _com_shims(env, tmp_path, "h", lockdir)

        h = _popen(_script_segura(m, "h"), env_h)
        w = None
        try:
            _espera_marca(marcas / "h_segura", h, "H")
            # H passou do prazo: o lock dele agora e stale para quem espera.
            velho = time.time() - 3600
            os.utime(lockdir, (velho, velho))
            # H comeca a soltar e para antes de remover.
            (marcas / "h_solta").touch()
            _espera_marca(marcas_h / "pausado", h, "H")
            # W quebra o lock vencido de H e fica com ele.
            w = _popen(_script_segura(m, "w"), env)
            _espera_marca(marcas / "w_segura", w, "W")
            (marcas_h / "segue").touch()
            _espera_marca(marcas_h / "depois", h, "H")

            comando = (marcas_h / "pausado").read_text(encoding="utf-8").strip()
            foto = (marcas_h / "depois").read_text(encoding="utf-8").strip()
            assert foto == "presente", f"o release atrasado de H apagou o lock de W ({comando})"
        finally:
            (marcas_h / "segue").touch()
            (marcas / "h_solta").touch()
            (marcas / "w_solta").touch()
            resultados = _colhe(h, w)
        for rc, err in resultados:
            assert rc == 0, err

    def test_lockdir_abandonado_pela_versao_anterior_e_quebrado(self, harness_dir):
        """Lockdir stale no formato antigo (`owner` com "pid epoch"): trocar de versao nao trava o lock."""
        lockdir = harness_dir / "state.json.lockdir"
        lockdir.mkdir()
        (lockdir / "owner").write_text("999999 12345\n", encoding="utf-8", newline="\n")
        velho = time.time() - 3600
        os.utime(lockdir, (velho, velho))
        result = _run_lock(
            ["acquire"], harness_dir, STATE_LOCK_STALE_SECS="300", STATE_LOCK_TIMEOUT_SECS="10",
        )
        assert result.returncode == 0, result.stderr


class TestOpcoesDeGlobDeQuemFazSource:
    """O lock funciona com as opcoes de glob de quem faz `source` dele.

    Os hooks fazem `source` do lock no proprio shell, com as opcoes que tiverem.
    Um lock que dependesse do glob ligado travaria calado sob `set -f`.
    """

    @pytest.mark.parametrize("opcao", ["set -f", "shopt -s failglob"])
    def test_quebra_stale_e_ciclo_completo(self, harness_dir, opcao):
        preambulo = f"set -euo pipefail\n{opcao}"
        _dono_morto(harness_dir, preambulo)
        script = f"""
            {preambulo}
            source "{LOCK_SH}"
            acquire_state_lock || exit 1
            test -d "$STATE_LOCK_DIR"
            release_state_lock
            test ! -d "$STATE_LOCK_DIR"
        """
        result = subprocess.run(
            [BASH, "-c", script],
            env=_env(harness_dir, STATE_LOCK_STALE_SECS="300", STATE_LOCK_TIMEOUT_SECS="10"),
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, result.stderr
