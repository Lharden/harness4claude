"""Uma task L2 tem de sobreviver a varios turnos.

Ramo `ciclo-de-vida-da-task`, 2026-09-23. Relatorio de causa raiz:
master-harness `docs/specs/ciclo-de-vida-da-task-causa-raiz.md`.

Dois incidentes medidos no mesmo dia, na mesma sessao:

- **HC-00h (R1).** Task L2 na fase 2 de 11 virou `superseded` com o prompt
  seguinte. `record_evidence` punha `status='verified'`, e "task viva" estava
  definida duas vezes: o banco (`ACTIVE_STATUSES`) contava `verified` como viva
  e a fechava; o classify (`CONTINUABLE_STATUSES`) nao contava e nao a
  continuava. Reproduzido 1 de 1, deterministico.
- **Incidente 2 (R2).** Task L2 ativa virou `abandoned` quando uma mensagem entre
  sessoes disparou o UserPromptSubmit. O classify decidia pela PROJECAO
  (`state.json`), a leitura falhava aberto (`except Exception: pass`), e o
  PostToolUse regravava a projecao sem lock. Medido no Windows: 5,5% das
  leituras falham com 1 escritor, 12,3% com 4 — todas viravam "sem pipeline".
  A mesma leitura contra o BANCO (WAL, busy_timeout=5000): 0 falhas em 4 137.

Decisoes do usuario (D1-D3) no relatorio, secao 6 e 8.4.
"""
from __future__ import annotations

import importlib.util
import json
import multiprocessing as mp
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
sys.path.insert(0, str(ROOT / "scripts"))

import continuation_policy  # noqa: E402
import harness_paths  # noqa: E402
from transactional_state import (  # noqa: E402
    ACTIVE_STATUSES,
    TERMINAL_STATUSES,
    HarnessDatabase,
)

PIPELINE_L2 = ["discuss", "brainstorming", "graph-context", "write-spec", "tdd"]
PROMPT_L2 = "planeje a arquitetura completa de um novo sistema de pipeline com spec e design"


def _bash() -> str:
    achado = shutil.which("bash")
    if achado:
        return achado
    for c in (
        Path(r"C:\Program Files\Git\bin\bash.exe"),
        Path(r"C:\Program Files\Git\usr\bin\bash.exe"),
    ):
        if c.exists():
            return str(c)
    return "bash"


BASH = _bash()


def _evidencia_valida(db: HarnessDatabase, task_id: str) -> dict:
    return db.record_evidence(
        task_id, evidence_type="test", command="python -m pytest -q", exit_code=0,
        tests_collected=10, tests_passed=10, output_hash="h",
    )


# ============================================================================
# D1 — "tem evidencia fresca" vive so na coluna `verified`
# ============================================================================


class TestEvidenciaNaoMexeNoStatus:
    def test_evidencia_valida_mantem_active(self, tmp_path):
        db = HarnessDatabase(tmp_path)
        task = db.start_task(scope_id="s", legacy_level="L2-bug", tier="L2", kind="bug",
                             pipeline=PIPELINE_L2, prompt="x")
        depois = _evidencia_valida(db, task["task_id"])
        assert depois["verified"] is True
        assert depois["status"] == "active", "evidencia no meio do pipeline nao e desfecho"

    def test_evidencia_valida_mantem_awaiting_gate(self, tmp_path):
        """O gate pendente e o que o humano precisa ver; evidencia nao o apaga do status."""
        db = HarnessDatabase(tmp_path)
        task = db.start_task(scope_id="s", legacy_level="L2-bug", tier="L2", kind="bug",
                             pipeline=PIPELINE_L2, prompt="x")
        db.open_gate(task["task_id"], "escalation")
        with sqlite3.connect(db.path) as raw:
            raw.execute("UPDATE tasks SET status = 'awaiting_gate' WHERE task_id = ?",
                        (task["task_id"],))
        depois = _evidencia_valida(db, task["task_id"])
        assert depois["verified"] is True
        assert depois["status"] == "awaiting_gate"

    def test_evidencia_falha_zera_a_coluna_e_mantem_status(self, tmp_path):
        """Metade de falsificacao: a coluna continua sendo invalidada."""
        db = HarnessDatabase(tmp_path)
        task = db.start_task(scope_id="s", legacy_level="L2-bug", tier="L2", kind="bug",
                             pipeline=PIPELINE_L2, prompt="x")
        _evidencia_valida(db, task["task_id"])
        depois = db.record_evidence(
            task["task_id"], evidence_type="test", command="python -m pytest -q", exit_code=1,
            tests_collected=10, tests_passed=9, output_hash="h2",
        )
        assert depois["verified"] is False
        assert depois["status"] == "active"

    def test_arquivo_tocado_zera_a_coluna(self, tmp_path):
        db = HarnessDatabase(tmp_path)
        task = db.start_task(scope_id="s", legacy_level="L2-bug", tier="L2", kind="bug",
                             pipeline=PIPELINE_L2, prompt="x")
        _evidencia_valida(db, task["task_id"])
        depois = db.touch_files(task["task_id"], ["a.py"], origem="edit")
        assert depois["verified"] is False
        assert depois["status"] == "active"


class TestLinhaVelhaVerifiedMigra:
    """Bancos gravados antes de D1 tem `status='verified'` em task viva.

    Pendencia declarada (relatorio 8.4): `verified` continua tolerado em
    `ACTIVE_STATUSES` e no indice enquanto houver sessao aberta com hook antigo,
    que ainda grava o status. Remover a tolerancia depois do reload de todas.
    """

    def test_reabrir_o_banco_converte_verified_em_active(self, tmp_path):
        db = HarnessDatabase(tmp_path)
        task = db.start_task(scope_id="s", legacy_level="L2-bug", tier="L2", kind="bug",
                             pipeline=PIPELINE_L2, prompt="x")
        with sqlite3.connect(db.path) as raw:
            raw.execute("UPDATE tasks SET status = 'verified', verified = 1 WHERE task_id = ?",
                        (task["task_id"],))

        reaberta = HarnessDatabase(tmp_path).task(task["task_id"])
        assert reaberta["status"] == "active"
        assert reaberta["verified"] is True, "a verificacao que aconteceu nao pode sumir"

    def test_migracao_nao_toca_status_terminal(self, tmp_path):
        db = HarnessDatabase(tmp_path)
        task = db.start_task(scope_id="s", legacy_level="L2-bug", tier="L2", kind="bug",
                             pipeline=PIPELINE_L2, prompt="x")
        with sqlite3.connect(db.path) as raw:
            raw.execute("UPDATE tasks SET status = 'superseded', verified = 1 WHERE task_id = ?",
                        (task["task_id"],))
        assert HarnessDatabase(tmp_path).task(task["task_id"])["status"] == "superseded"

    def test_migracao_deixa_evento_por_task_convertida(self, tmp_path):
        """O criterio de fechamento da pendencia (F9): linha 'verified' nova so
        aparece se ainda ha hook antigo rodando. Zero eventos depois do deploy e
        do reload = a tolerancia pode sair, com prova e nao com palpite."""
        db = HarnessDatabase(tmp_path)
        task = db.start_task(scope_id="s", legacy_level="L2-bug", tier="L2", kind="bug",
                             pipeline=PIPELINE_L2, prompt="x")
        with sqlite3.connect(db.path) as raw:
            raw.execute("UPDATE tasks SET status = 'verified', verified = 1 WHERE task_id = ?",
                        (task["task_id"],))
        HarnessDatabase(tmp_path)
        with sqlite3.connect(f"file:{db.path}?mode=ro", uri=True) as raw:
            eventos = raw.execute(
                "SELECT task_id FROM events WHERE event_type = 'migracao-verified'").fetchall()
        assert eventos == [(task["task_id"],)]

    def test_sem_linha_velha_nao_ha_evento(self, tmp_path):
        """Falsificacao: evento em toda abertura tornaria o criterio inutil."""
        db = HarnessDatabase(tmp_path)
        db.start_task(scope_id="s", legacy_level="L2-bug", tier="L2", kind="bug",
                      pipeline=PIPELINE_L2, prompt="x")
        HarnessDatabase(tmp_path)
        with sqlite3.connect(f"file:{db.path}?mode=ro", uri=True) as raw:
            n = raw.execute("SELECT COUNT(*) FROM events WHERE event_type = 'migracao-verified'").fetchone()[0]
        assert n == 0

    def test_tolerancia_continua_no_conjunto_vivo(self):
        """Tirar `verified` daqui antes do reload abriria duas tasks vivas por escopo."""
        assert "verified" in ACTIVE_STATUSES


# ============================================================================
# R1 + R2 — uma unica pergunta "ha task viva?", respondida pelo banco
# ============================================================================


class TestPerguntaUnica:
    def test_banco_ausente_e_nenhuma(self, tmp_path):
        r = continuation_policy.task_viva(str(tmp_path))
        assert r.resposta == continuation_policy.NENHUMA
        assert not (tmp_path / "harness.db").exists(), "perguntar nao cria banco"

    def test_task_com_pipeline_e_viva(self, tmp_path):
        db = HarnessDatabase(tmp_path)
        task = db.start_task(scope_id=str(tmp_path), legacy_level="L2-bug", tier="L2",
                             kind="bug", pipeline=PIPELINE_L2, prompt="x")
        r = continuation_policy.task_viva(str(tmp_path))
        assert r.resposta == continuation_policy.VIVA
        assert r.task["task_id"] == task["task_id"]

    def test_task_com_evidencia_continua_viva(self, tmp_path):
        """R1: o conjunto do banco e o conjunto da continuacao sao o mesmo."""
        db = HarnessDatabase(tmp_path)
        task = db.start_task(scope_id=str(tmp_path), legacy_level="L2-bug", tier="L2",
                             kind="bug", pipeline=PIPELINE_L2, prompt="x")
        _evidencia_valida(db, task["task_id"])
        assert continuation_policy.task_viva(str(tmp_path)).resposta == continuation_policy.VIVA

    def test_linha_velha_verified_continua_viva(self, tmp_path):
        """Sessao com hook antigo ainda grava o status; a pergunta nao pode mata-la."""
        db = HarnessDatabase(tmp_path)
        task = db.start_task(scope_id=str(tmp_path), legacy_level="L2-bug", tier="L2",
                             kind="bug", pipeline=PIPELINE_L2, prompt="x")
        with sqlite3.connect(db.path) as raw:
            raw.execute("UPDATE tasks SET status = 'verified', verified = 1 WHERE task_id = ?",
                        (task["task_id"],))
        assert continuation_policy.task_viva(str(tmp_path)).resposta == continuation_policy.VIVA

    def test_task_l0_concluida_nao_e_viva(self, tmp_path):
        db = HarnessDatabase(tmp_path)
        db.start_task(scope_id=str(tmp_path), legacy_level="L0-question", tier="L0",
                      kind="question", pipeline=[], prompt="x")
        assert continuation_policy.task_viva(str(tmp_path)).resposta == continuation_policy.NENHUMA

    def test_banco_corrompido_e_desconhecida(self, tmp_path):
        (tmp_path / "harness.db").write_bytes(b"isto nao e sqlite" * 64)
        r = continuation_policy.task_viva(str(tmp_path))
        assert r.resposta == continuation_policy.DESCONHECIDA
        assert r.erro, "desconhecida sem causa escrita repete o incidente 2"

    def test_a_projecao_nao_e_consultada(self, tmp_path):
        """A projecao diz idle, o banco diz viva: manda o banco."""
        db = HarnessDatabase(tmp_path)
        db.start_task(scope_id=str(tmp_path), legacy_level="L2-bug", tier="L2",
                      kind="bug", pipeline=PIPELINE_L2, prompt="x")
        (tmp_path / "state.json").write_text('{"status": "idle", "pipeline": []}', encoding="utf-8")
        assert continuation_policy.task_viva(str(tmp_path)).resposta == continuation_policy.VIVA


def _env(tmp_path: Path) -> dict:
    return {
        **os.environ,
        "PYTHONUTF8": "1",
        "MASTER_HARNESS_HOME": str(tmp_path / "mh"),
        "HARNESS_ROUTER": "0",
        "HARNESS_BRANCH": "0",
    }


def _repo(base: Path, nome: str) -> Path:
    d = base / nome
    (d / ".git").mkdir(parents=True)
    return d


def _balde(cwd: Path, sessao: str) -> Path:
    return Path(harness_paths.ensure_state_dir(os.environ["HARNESS_DIR"], str(cwd), session_id=sessao))


def _prompt(tmp_path: Path, cwd: Path, sessao: str, texto: str) -> subprocess.CompletedProcess:
    payload = {"session_id": sessao, "cwd": str(cwd), "prompt": texto}
    return subprocess.run(
        [BASH, str(ROOT / "hooks" / "harness-classify.sh")],
        input=json.dumps(payload), capture_output=True, text=True, timeout=60,
        env=_env(tmp_path), encoding="utf-8",
    )


def _tasks(balde: Path) -> dict[str, str]:
    with sqlite3.connect(f"file:{balde / 'harness.db'}?mode=ro", uri=True) as c:
        return dict(c.execute("SELECT task_id, status FROM tasks").fetchall())


def _abrir_l2(tmp_path: Path, cwd: Path, sessao: str) -> str:
    res = _prompt(tmp_path, cwd, sessao, PROMPT_L2)
    assert "HARNESS v3 CLASSIFIED" in res.stdout, res.stdout + res.stderr
    return json.loads((_balde(cwd, sessao) / "state.json").read_text(encoding="utf-8"))["task_id"]


class TestClassifyPerguntaAoBanco:
    def test_hc00h_evidencia_no_meio_do_pipeline_nao_mata_a_task(self, tmp_path):
        """T1. Reproducao literal do HC-00h."""
        cwd = _repo(tmp_path, "r1")
        tid = _abrir_l2(tmp_path, cwd, "s-t1")
        balde = _balde(cwd, "s-t1")
        _evidencia_valida(HarnessDatabase(balde), tid)
        # A projecao acompanha, como o PostToolUse real faz.
        estado = json.loads((balde / "state.json").read_text(encoding="utf-8"))
        estado.update(HarnessDatabase(balde).task(tid))
        (balde / "state.json").write_text(json.dumps(estado), encoding="utf-8")

        res = _prompt(tmp_path, cwd, "s-t1", "qual o proximo passo?")

        assert "HARNESS v3 CONTINUING" in res.stdout, res.stdout + res.stderr
        assert tid in res.stdout
        assert _tasks(balde) == {tid: "active"}, "nenhuma task nova, nenhuma morta"

    def test_projecao_atrasada_nao_mata_a_task(self, tmp_path):
        """T2a. Incidente 2: o banco diz viva, a projecao diz o contrario."""
        cwd = _repo(tmp_path, "r2a")
        tid = _abrir_l2(tmp_path, cwd, "s-t2a")
        balde = _balde(cwd, "s-t2a")
        (balde / "state.json").write_text('{"task_id": null, "status": "idle", "pipeline": []}',
                                          encoding="utf-8")

        res = _prompt(tmp_path, cwd, "s-t2a", "mensagem de outra sessao: terminei o censo")

        assert "HARNESS v3 CONTINUING" in res.stdout, res.stdout + res.stderr
        assert _tasks(balde) == {tid: "active"}

    def test_continuacao_repara_a_projecao(self, tmp_path):
        """O PostToolUse acha a task pelo `task_id` da projecao; sem reparo, a
        evidencia seguinte nao teria para onde ir."""
        cwd = _repo(tmp_path, "r2r")
        tid = _abrir_l2(tmp_path, cwd, "s-t2r")
        balde = _balde(cwd, "s-t2r")
        (balde / "state.json").write_text('{"task_id": null, "status": "idle", "pipeline": []}',
                                          encoding="utf-8")

        _prompt(tmp_path, cwd, "s-t2r", "segue")

        estado = json.loads((balde / "state.json").read_text(encoding="utf-8"))
        assert estado["task_id"] == tid
        assert estado["status"] == "active"

    def test_sem_task_viva_abre_task_nova(self, tmp_path):
        """T2b. Falsificacao: o conserto nao pode travar a abertura."""
        cwd = _repo(tmp_path, "r2b")
        res = _prompt(tmp_path, cwd, "s-t2b", PROMPT_L2)
        assert "HARNESS v3 CLASSIFIED" in res.stdout, res.stdout + res.stderr
        assert list(_tasks(_balde(cwd, "s-t2b")).values()) == ["active"]

    def test_troca_explicita_encerra_e_abre(self, tmp_path):
        """T2c. Falsificacao: a unica porta de saida continua aberta."""
        cwd = _repo(tmp_path, "r2c")
        tid = _abrir_l2(tmp_path, cwd, "s-t2c")
        res = _prompt(tmp_path, cwd, "s-t2c", "esquece isso, nova tarefa: " + PROMPT_L2)
        assert "HARNESS v3 CONTINUING" not in res.stdout, res.stdout
        estados = _tasks(_balde(cwd, "s-t2c"))
        assert estados[tid] == "abandoned"
        assert sorted(estados.values()) == ["abandoned", "active"]

    def test_banco_ilegivel_avisa_e_nao_abre(self, tmp_path):
        """T2d. Fail-closed com causa escrita (decisao do usuario, 8.4)."""
        cwd = _repo(tmp_path, "r2d")
        balde = _balde(cwd, "s-t2d")
        (balde / "harness.db").write_bytes(b"isto nao e sqlite" * 64)
        antes = (balde / "state.json").read_text(encoding="utf-8") if (balde / "state.json").exists() else None

        res = _prompt(tmp_path, cwd, "s-t2d", PROMPT_L2)

        assert "HARNESS v3 CLASSIFIED" not in res.stdout, res.stdout
        assert "estado ilegivel" in res.stdout.lower(), res.stdout + res.stderr
        depois = (balde / "state.json").read_text(encoding="utf-8") if (balde / "state.json").exists() else None
        assert depois == antes, "nao pode anunciar nem projetar task que nao abriu"
        log = (balde / "debug-classify.log")
        assert log.exists() and "harness.db" in log.read_text(encoding="utf-8")


# ============================================================================
# Os outros leitores da mesma pergunta (escopo ampliado, 8.4)
# ============================================================================


def _carregar_router():
    spec = importlib.util.spec_from_file_location("skill_router_ciclo", ROOT / "hooks" / "skill_router.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


class TestRouterPerguntaAoBanco:
    PROMPT = "preciso de ajuda para depurar um teste que falha de vez em quando no windows"

    def test_task_viva_no_banco_cala_o_router(self, tmp_path):
        router = _carregar_router()
        db = HarnessDatabase(tmp_path)
        db.start_task(scope_id=str(tmp_path), legacy_level="L2-bug", tier="L2",
                      kind="bug", pipeline=PIPELINE_L2, prompt="x")
        (tmp_path / "state.json").write_text('{"status": "idle", "pipeline": []}', encoding="utf-8")
        assert router.passes_guards(self.PROMPT, str(tmp_path / "state.json")) is False

    def test_sem_task_viva_o_router_fala(self, tmp_path):
        """Falsificacao: o guarda nao pode calar sempre."""
        router = _carregar_router()
        assert router.passes_guards(self.PROMPT, str(tmp_path / "state.json")) is True


class TestSessionStartPerguntaAoBanco:
    def _session_start(self, tmp_path, cwd, sessao):
        payload = {"session_id": sessao, "cwd": str(cwd), "source": "startup"}
        env = {**_env(tmp_path), "HARNESS_SKIP_DEPCHECK": "1"}
        return subprocess.run(
            [BASH, str(ROOT / "hooks" / "harness-session-start.sh")],
            input=json.dumps(payload), capture_output=True, text=True, timeout=120,
            env=env, encoding="utf-8",
        )

    def test_projecao_atrasada_ainda_retoma(self, tmp_path):
        cwd = _repo(tmp_path, "ss")
        tid = _abrir_l2(tmp_path, cwd, "s-ss")
        balde = _balde(cwd, "s-ss")
        (balde / "state.json").write_text(
            '{"task_id": null, "schema_version": 3, "status": "idle", "pipeline": []}',
            encoding="utf-8",
        )
        res = self._session_start(tmp_path, cwd, "s-ss")
        assert "HARNESS v3 RESUMING" in res.stdout, res.stdout + res.stderr
        assert tid in res.stdout

    def test_sem_task_viva_nao_retoma(self, tmp_path):
        cwd = _repo(tmp_path, "ss0")
        _balde(cwd, "s-ss0")
        (_balde(cwd, "s-ss0") / "state.json").write_text(
            '{"task_id": null, "schema_version": 3, "status": "idle", "pipeline": []}',
            encoding="utf-8",
        )
        res = self._session_start(tmp_path, cwd, "s-ss0")
        assert "RESUMING" not in res.stdout, res.stdout


# ============================================================================
# Escritores da projecao
# ============================================================================


def _carregar_transacional():
    spec = importlib.util.spec_from_file_location(
        "harness_transactional_ciclo", ROOT / "hooks" / "harness-transactional.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


TASK_PROJETADA = {
    "task_id": "t-x", "status": "active", "pipeline": PIPELINE_L2, "phase": "tdd",
    "revision": 1, "code_revision": 0, "verified": False, "stop_continuations": 0,
    "pending_gate": None, "scope_id": "s", "artifacts": [],
}


def _escritor(balde: str, n: int, fila) -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    sys.path.insert(0, str(ROOT / "hooks"))
    tx = _carregar_transacional()
    erros = 0
    for _ in range(n):
        try:
            tx._sync_projection(Path(balde), tx._projection(Path(balde)), TASK_PROJETADA)
        except Exception:
            erros += 1
    fila.put(erros)


class TestEscritorDaProjecao:
    def test_escritores_concorrentes_nao_levantam(self, tmp_path):
        """T3. Medido antes: 56% (1 escritor) a 86% (4) das escritas levantavam.

        Criterio decidido no grill (8.1, pergunta 35): o escritor nunca derruba o
        hook e a projecao final e JSON valido da task. A ordem entre escritores
        nao e prometida — quem decide e o banco, nao a projecao.
        """
        ctx = mp.get_context("spawn")
        fila = ctx.Queue()
        ps = [ctx.Process(target=_escritor, args=(str(tmp_path), 150, fila)) for _ in range(4)]
        for p in ps:
            p.start()
        erros = [fila.get(timeout=120) for _ in ps]
        for p in ps:
            p.join()
        assert erros == [0, 0, 0, 0]
        final = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
        assert final["task_id"] == "t-x"

    def test_falha_esgotada_fica_registrada(self, tmp_path, monkeypatch):
        """Falha engolida sem registro e o defeito que o incidente 2 tinha."""
        tx = _carregar_transacional()

        def sempre_falha(origem, destino):
            raise PermissionError(13, "simulado")

        monkeypatch.setattr(tx.os, "replace", sempre_falha)
        monkeypatch.setattr(tx.time, "sleep", lambda s: None)
        tx._sync_projection(tmp_path, {}, TASK_PROJETADA)  # nao levanta
        log = tmp_path / "projection-errors.log"
        assert log.exists() and "PermissionError" in log.read_text(encoding="utf-8")

    def test_reclassify_grava_a_projecao_de_forma_atomica(self):
        """`open(state_file, 'w')` trunca antes de escrever: um leitor no meio le vazio."""
        hook = (ROOT / "hooks" / "harness-reclassify.sh").read_text(encoding="utf-8")
        assert "open(state_file, 'w'" not in hook


# ============================================================================
# Achados do /code-review (2026-09-23) sobre o proprio conserto
# ============================================================================


def _l1_na_fase_final(db: HarnessDatabase, balde: str) -> dict:
    task = db.start_task(scope_id=balde, legacy_level="L1-bug", tier="L1", kind="bug",
                         pipeline=["tdd", "verify"], prompt="x")
    return db.transition(task["task_id"], "verify", expected_revision=task["revision"])


class TestRevisaoFechamento:
    def test_f2_task_verificada_na_fase_final_nao_continua(self, tmp_path):
        """Antes do conserto o prompt seguinte a fechava como `superseded`; o
        conserto de R1 nao pode transformar "trabalho entregue sem `complete`"
        em CONTINUING por 24 h."""
        db = HarnessDatabase(tmp_path)
        task = _l1_na_fase_final(db, str(tmp_path))
        _evidencia_valida(db, task["task_id"])
        assert continuation_policy.task_viva(str(tmp_path)).resposta == continuation_policy.NENHUMA

    def test_f2_task_na_fase_final_sem_evidencia_continua(self, tmp_path):
        """Falsificacao: so a evidencia fecha; fase final sem prova segue viva."""
        db = HarnessDatabase(tmp_path)
        _l1_na_fase_final(db, str(tmp_path))
        assert continuation_policy.task_viva(str(tmp_path)).resposta == continuation_policy.VIVA

    def test_f1_record_signal_abandoned_fecha_a_task_no_banco(self, tmp_path):
        """O protocolo documentado de abandono tem de encerrar a task na autoridade."""
        db = HarnessDatabase(tmp_path)
        task = db.start_task(scope_id=str(tmp_path), legacy_level="L2-bug", tier="L2",
                             kind="bug", pipeline=PIPELINE_L2, prompt="x")
        (tmp_path / "state.json").write_text(json.dumps(
            {"task_id": task["task_id"], "classification": "L2-bug", "status": "active",
             "pipeline": PIPELINE_L2}), encoding="utf-8")
        res = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "record_signal.py"), "--abandoned",
             "--reason", "user_switch", "--expect-task", task["task_id"],
             "--harness-dir", str(tmp_path), "--signals-dir", str(tmp_path)],
            capture_output=True, text=True, timeout=60,
        )
        assert res.returncode == 0, res.stderr
        assert HarnessDatabase(tmp_path).task(task["task_id"])["status"] == "abandoned"
        assert continuation_policy.task_viva(str(tmp_path)).resposta == continuation_policy.NENHUMA


class TestRevisaoPerguntaSoLe:
    def test_f5_pergunta_responde_com_escrita_em_andamento(self, tmp_path):
        """Leitura nao pode esperar trava de escrita (WAL deixa ler)."""
        import time as _t

        db = HarnessDatabase(tmp_path)
        task = db.start_task(scope_id=str(tmp_path), legacy_level="L2-bug", tier="L2",
                             kind="bug", pipeline=PIPELINE_L2, prompt="x")
        escritor = sqlite3.connect(db.path, timeout=0.1)
        # Linha de hook antigo: a migracao de `_ensure_schema` quereria escreve-la.
        escritor.execute("UPDATE tasks SET status = 'verified' WHERE task_id = ?", (task["task_id"],))
        escritor.commit()
        escritor.execute("BEGIN IMMEDIATE")
        try:
            t0 = _t.perf_counter()
            r = continuation_policy.task_viva(str(tmp_path))
            gasto = _t.perf_counter() - t0
        finally:
            escritor.rollback()
            escritor.close()
        assert r.resposta == continuation_policy.VIVA, r.erro
        assert gasto < 2.0, f"a pergunta esperou a trava de escrita: {gasto:.1f}s"

    def test_f5_pergunta_nao_escreve(self, tmp_path):
        db = HarnessDatabase(tmp_path)
        task = db.start_task(scope_id=str(tmp_path), legacy_level="L2-bug", tier="L2",
                             kind="bug", pipeline=PIPELINE_L2, prompt="x")
        with sqlite3.connect(db.path) as raw:
            raw.execute("UPDATE tasks SET status = 'verified' WHERE task_id = ?", (task["task_id"],))
        continuation_policy.task_viva(str(tmp_path))
        with sqlite3.connect(f"file:{db.path}?mode=ro", uri=True) as raw:
            status = raw.execute("SELECT status FROM tasks").fetchone()[0]
        assert status == "verified", "a pergunta migrou o banco — leitura nao escreve"


def _carregar_projecao():
    spec = importlib.util.spec_from_file_location("projecao_ciclo", ROOT / "scripts" / "projecao.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


class TestRevisaoEscritaUnica:
    def test_f6_retentativa_supera_falha_transitoria(self, tmp_path, monkeypatch):
        projecao = _carregar_projecao()
        real = os.replace
        falhas = {"n": 2}

        def instavel(origem, destino):
            if falhas["n"]:
                falhas["n"] -= 1
                raise PermissionError(13, "leitor segurando o destino")
            return real(origem, destino)

        monkeypatch.setattr(projecao.os, "replace", instavel)
        monkeypatch.setattr(projecao.time, "sleep", lambda s: None)
        erro = projecao.gravar_json_atomico(tmp_path / "state.json", {"task_id": "t"})
        assert erro is None
        assert json.loads((tmp_path / "state.json").read_text(encoding="utf-8")) == {"task_id": "t"}

    def test_f6_falha_esgotada_devolve_erro_sem_levantar_e_sem_lixo(self, tmp_path, monkeypatch):
        projecao = _carregar_projecao()

        def sempre(origem, destino):
            raise PermissionError(13, "simulado")

        monkeypatch.setattr(projecao.os, "replace", sempre)
        monkeypatch.setattr(projecao.time, "sleep", lambda s: None)
        erro = projecao.gravar_json_atomico(tmp_path / "state.json", {"task_id": "t"})
        assert isinstance(erro, OSError)
        assert list(tmp_path.iterdir()) == [], "tmp orfao ficou no balde"

    @pytest.mark.parametrize("arquivo", [
        "hooks/harness-classify.sh",
        "hooks/harness-transactional.py",
        "hooks/harness-reclassify.sh",
        "scripts/confirm_classification.py",
        "scripts/expire_stale_pipeline.py",
    ])
    def test_f6_todo_escritor_da_projecao_usa_o_helper(self, arquivo):
        """Tres copias do laco eram o defeito; uma so e o conserto."""
        texto = (ROOT / arquivo).read_text(encoding="utf-8")
        assert "gravar_json_atomico" in texto, arquivo
        assert "os.replace(" not in texto and ".replace(bucket" not in texto, arquivo


class TestRevisaoProjecao:
    def test_f7_escritor_nao_sobrescreve_projecao_de_outra_task(self, tmp_path):
        tx = _carregar_transacional()
        (tmp_path / "state.json").write_text(
            json.dumps({"task_id": "t-nova", "status": "active"}), encoding="utf-8")
        velha = {"task_id": "t-velha", "status": "active"}
        tx._sync_projection(tmp_path, dict(velha), TASK_PROJETADA | {"task_id": "t-velha"})
        atual = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
        assert atual["task_id"] == "t-nova"

    def test_f7_escritor_atualiza_a_propria_task(self, tmp_path):
        """Falsificacao: a guarda nao pode calar o escritor legitimo."""
        tx = _carregar_transacional()
        (tmp_path / "state.json").write_text(
            json.dumps({"task_id": "t-x", "status": "active", "revision": 0}), encoding="utf-8")
        tx._sync_projection(tmp_path, {"task_id": "t-x"}, TASK_PROJETADA | {"revision": 9})
        assert json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))["revision"] == 9

    def test_f8_ttl_decide_pelo_banco_com_projecao_ilegivel(self, tmp_path):
        spec = importlib.util.spec_from_file_location(
            "expire_ciclo", ROOT / "scripts" / "expire_stale_pipeline.py")
        exp = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(exp)

        db = HarnessDatabase(tmp_path)
        task = db.start_task(scope_id=str(tmp_path), legacy_level="L2-bug", tier="L2",
                             kind="bug", pipeline=PIPELINE_L2, prompt="x")
        with sqlite3.connect(db.path) as raw:
            raw.execute("UPDATE tasks SET started_at = '2026-01-01T00:00:00+00:00'")
        (tmp_path / "state.json").write_text("{{{ilegivel", encoding="utf-8")

        assert exp.expire(tmp_path, 24, signals_dir=tmp_path / "sinais") == task["task_id"]
        assert HarnessDatabase(tmp_path).task(task["task_id"])["status"] == "abandoned"


class TestRevisaoReparoGuardaMeta:
    def test_f3_reparo_traz_classification_meta_do_banco(self, tmp_path):
        cwd = _repo(tmp_path, "rf3")
        tid = _abrir_l2(tmp_path, cwd, "s-f3")
        balde = _balde(cwd, "s-f3")
        (balde / "state.json").write_text('{"task_id": null, "status": "idle", "pipeline": []}',
                                          encoding="utf-8")

        _prompt(tmp_path, cwd, "s-f3", "segue")

        estado = json.loads((balde / "state.json").read_text(encoding="utf-8"))
        assert estado["task_id"] == tid
        meta = estado.get("classification_meta") or {}
        assert meta.get("suggested") == HarnessDatabase(balde).classification(tid)["suggested"]


# ============================================================================
# Contrato
# ============================================================================


class TestContratoConheceTodoStatus:
    def test_todo_status_gravado_esta_no_enum(self):
        """`superseded` e gravado desde 2026-09-03 e faltava no contrato."""
        schema = json.loads((ROOT / "contract" / "schemas" / "task-state.schema.json")
                            .read_text(encoding="utf-8"))
        enum = set(schema["properties"]["status"]["enum"])
        faltando = (set(ACTIVE_STATUSES) | set(TERMINAL_STATUSES)) - enum
        assert not faltando, f"status gravado pelo banco e ausente do contrato: {sorted(faltando)}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
