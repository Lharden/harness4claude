"""Testes para scripts/expire_stale_pipeline.py (auditoria 2026-07-28).

O incidente que motivou o TTL: `state.json` ficou com `status="active"` de
2026-07-24 ate 2026-07-28 porque nada no sistema devolvia o state para `idle`.
Como `harness-classify.sh` emite CONTINUING e sai ANTES de classificar quando ha
pipeline ativo, e o state e global, isso bloqueou TODA classificacao nova em
TODOS os projetos da maquina por 4 dias.

Estes testes travam as tres propriedades que impedem a recorrencia:
- pipeline fresco NAO expira (nao se destroi trabalho em andamento);
- pipeline alem do TTL expira, volta para `idle` e deixa rastro em signals.json;
- state travado sem `started_at` expira (senao ficaria preso para sempre).
"""
from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
EXPIRE_PATH = ROOT / "scripts" / "expire_stale_pipeline.py"


@pytest.fixture(scope="module")
def exp():
    """Carrega o modulo pelo path (scripts/ nao e um pacote instalavel)."""
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("expire_stale_pipeline", EXPIRE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["expire_stale_pipeline"] = mod
    spec.loader.exec_module(mod)
    return mod


def _state(*, status="active", started_at=None, pipeline=("tdd",), task_id="t-20260724-170615"):
    return {
        "task_id": task_id,
        "schema_version": 3,
        "classification": "L2-feature",
        "classification_meta": {"suggested": "L2-feature", "final": None,
                                "source": "regex", "confidence": None, "agreed": None},
        "status": status,
        "pipeline": list(pipeline),
        "current_step": None,
        "artifacts_so_far": [],
        "started_at": started_at,
    }


def _write(harness_dir: Path, state: dict, count: int = 0) -> None:
    (harness_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (harness_dir / ".session-files-count").write_text(
        json.dumps({"count": count, "files": [], "task_id": state.get("task_id")}), encoding="utf-8"
    )


class TestIsExpired:
    """Regras de decisao, sem tocar em disco."""

    def test_idle_nunca_expira(self, exp):
        assert exp.is_expired(_state(status="idle"), 24) is False

    def test_gate_humano_abandonado_tambem_expira(self, exp):
        velho = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        assert exp.is_expired(_state(status="awaiting_gate", started_at=velho), 24) is True

    def test_active_sem_pipeline_nao_expira(self, exp):
        assert exp.is_expired(_state(pipeline=()), 24) is False

    def test_dentro_do_ttl_nao_expira(self, exp):
        recente = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        assert exp.is_expired(_state(started_at=recente), 24) is False

    def test_alem_do_ttl_expira(self, exp):
        velho = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        assert exp.is_expired(_state(started_at=velho), 24) is True

    def test_started_at_ausente_expira(self, exp):
        """State travado sem timestamp nao teria como se recuperar sozinho."""
        assert exp.is_expired(_state(started_at=None), 24) is True

    def test_started_at_invalido_expira(self, exp):
        assert exp.is_expired(_state(started_at="nao-e-uma-data"), 24) is True

    def test_started_at_naive_tratado_como_utc(self, exp):
        naive = (datetime.now(timezone.utc) - timedelta(hours=2)).replace(tzinfo=None).isoformat()
        assert exp.is_expired(_state(started_at=naive), 24) is False


class TestTtlConfig:
    """HARNESS_PIPELINE_TTL_H com fallback seguro."""

    def test_default_sem_env(self, exp, monkeypatch):
        monkeypatch.delenv("HARNESS_PIPELINE_TTL_H", raising=False)
        assert exp.default_ttl_hours() == 24

    def test_env_valida_vence(self, exp, monkeypatch):
        monkeypatch.setenv("HARNESS_PIPELINE_TTL_H", "6")
        assert exp.default_ttl_hours() == 6

    def test_env_lixo_cai_no_default(self, exp, monkeypatch):
        monkeypatch.setenv("HARNESS_PIPELINE_TTL_H", "abacaxi")
        assert exp.default_ttl_hours() == 24

    def test_env_zero_ou_negativa_cai_no_default(self, exp, monkeypatch):
        monkeypatch.setenv("HARNESS_PIPELINE_TTL_H", "0")
        assert exp.default_ttl_hours() == 24
        monkeypatch.setenv("HARNESS_PIPELINE_TTL_H", "-3")
        assert exp.default_ttl_hours() == 24


class TestExpire:
    """Efeito em disco: reset do state + rastro em signals.json."""

    def test_pipeline_fresco_intocado(self, exp, harness_dir):
        recente = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
        original = _state(started_at=recente)
        _write(harness_dir, original)

        assert exp.expire(harness_dir, 24) is None
        depois = json.loads((harness_dir / "state.json").read_text(encoding="utf-8"))
        assert depois == original

    def test_pipeline_velho_volta_para_idle(self, exp, harness_dir):
        velho = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        _write(harness_dir, _state(started_at=velho), count=18)

        assert exp.expire(harness_dir, 24) == "t-20260724-170615"

        depois = json.loads((harness_dir / "state.json").read_text(encoding="utf-8"))
        assert depois["status"] == "idle"
        assert depois["task_id"] is None
        assert depois["pipeline"] == []

    def test_pipeline_velho_abandona_tambem_o_estado_transacional(self, exp, harness_dir):
        velho = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        db = exp.HarnessDatabase(harness_dir)
        db.start_task(
            scope_id="legacy", legacy_level="L1-feature", tier="L1", kind="feature",
            pipeline=["write-spec-light", "tdd", "verify-against-spec"], prompt="build",
            task_id="t-20260724-170615",
        )
        with sqlite3.connect(harness_dir / "harness.db") as connection:
            connection.execute(
                "UPDATE tasks SET started_at = ? WHERE task_id = ?",
                (velho, "t-20260724-170615"),
            )
        state = _state(started_at=velho)
        state["scope_id"] = "legacy"
        _write(harness_dir, state)

        assert exp.expire(harness_dir, 24) == "t-20260724-170615"

        assert db.task("t-20260724-170615")["status"] == "abandoned"
        assert db.current_task("legacy") is None

    def test_json_atrasado_e_reparado_sem_apagar_tarefa_transacional_nova(self, exp, harness_dir):
        velho = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        db = exp.HarnessDatabase(harness_dir)
        replacement = db.start_task(
            scope_id="legacy", legacy_level="L1-feature", tier="L1", kind="feature",
            pipeline=["write-spec-light", "tdd", "verify-against-spec"], prompt="new",
            task_id="t-new",
        )
        _write(harness_dir, _state(started_at=velho, task_id="t-old"))

        assert exp.expire(harness_dir, 24) is None

        projection = json.loads((harness_dir / "state.json").read_text(encoding="utf-8"))
        assert projection["task_id"] == replacement["task_id"]
        assert projection["status"] == "active"
        assert db.current_task("legacy")["task_id"] == replacement["task_id"]
        started = datetime.fromisoformat(replacement["started_at"]).timestamp()
        db.expire_stale_task(
            "legacy", ttl_seconds=1, now=started + 2, expected_task_id=replacement["task_id"]
        )

    def test_session_start_serializa_expiracao_com_o_classificador(self):
        source = (ROOT / "hooks" / "harness-session-start.sh").read_text(encoding="utf-8")

        assert "acquire_state_lock" in source
        assert source.index("acquire_state_lock") < source.index("expire_stale_pipeline.py")

    def test_contador_zerado_no_expire(self, exp, harness_dir):
        """O contador global inflava a reclassificacao da task seguinte."""
        velho = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        _write(harness_dir, _state(started_at=velho), count=130)

        exp.expire(harness_dir, 24)

        counter = json.loads((harness_dir / ".session-files-count").read_text(encoding="utf-8"))
        assert counter["count"] == 0
        assert counter["task_id"] is None

    def test_abandono_registrado_em_signals(self, exp, harness_dir):
        velho = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        _write(harness_dir, _state(started_at=velho), count=18)

        exp.expire(harness_dir, 24)

        signals = json.loads((harness_dir / "signals.json").read_text(encoding="utf-8"))
        tasks = [t for t in signals["tasks"] if t["task_id"] == "t-20260724-170615"]
        assert len(tasks) == 1
        assert tasks[0]["pipeline_completed"] is False
        assert tasks[0]["reason"].startswith("ttl_expired")
        assert tasks[0]["files_modified"] == 18

    def test_state_ausente_nao_quebra(self, exp, harness_dir):
        assert exp.expire(harness_dir, 24) is None

    def test_state_corrompido_nao_quebra(self, exp, harness_dir):
        (harness_dir / "state.json").write_text("{ nao e json", encoding="utf-8")
        assert exp.expire(harness_dir, 24) is None


class TestCli:
    """Contrato com os hooks: exit 0 sempre, `EXPIRED <id>` no stdout."""

    def _run(self, harness_dir: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(EXPIRE_PATH), "--harness-dir", str(harness_dir), "--ttl-hours", "24"],
            capture_output=True, text=True, check=False,
        )

    def test_expirado_imprime_task_id(self, harness_dir):
        velho = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        _write(harness_dir, _state(started_at=velho))

        res = self._run(harness_dir)
        assert res.returncode == 0
        assert res.stdout.strip() == "EXPIRED t-20260724-170615"

    def test_fresco_nao_imprime_nada(self, harness_dir):
        recente = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        _write(harness_dir, _state(started_at=recente))

        res = self._run(harness_dir)
        assert res.returncode == 0
        assert res.stdout.strip() == ""


class TestVarreduraDeTodosOsBuckets:
    """O TTL nao alcancava projeto abandonado, que e onde ele mais importa.

    `expire()` roda sobre UM bucket, e so quando alguem abre sessao naquele
    diretorio. Um projeto que ninguem visita mais nunca recebe a visita — entao
    o pipeline dele fica `active` para sempre, e o TTL de 24h vira decorativo
    exatamente no caso que ele existe para cobrir.

    Medido em 2026-09-02: 20 pipelines vencidos na maquina, o mais velho de
    2026-07-28 — cinco semanas. Abrir sessao em qualquer um deles faria o
    harness tentar retomar um pipeline de julho.
    """

    def _bucket(self, raiz: Path, nome: str, *, dias: float, task="t-x",
                classificacao="L1-feature"):
        d = raiz / "projects" / nome
        d.mkdir(parents=True, exist_ok=True)
        quando = datetime.now(timezone.utc) - timedelta(days=dias)
        (d / "state.json").write_text(json.dumps({
            "task_id": f"{task}-{nome}", "schema_version": 3,
            "classification": classificacao, "status": "active",
            "pipeline": ["tdd"], "current_step": "tdd",
            "artifacts_so_far": [], "started_at": quando.isoformat(),
        }), encoding="utf-8")
        return d

    def test_acha_bucket_em_qualquer_profundidade(self, exp, tmp_path):
        """Buckets de sessao vivem em projects/<slug>/sessions/<uuid>/."""
        self._bucket(tmp_path, "raso", dias=10)
        self._bucket(tmp_path, "fundo/sessions/uuid-1", dias=10)
        achados = exp.buckets(tmp_path)
        assert len(achados) == 2

    def test_dry_run_lista_e_nao_toca(self, exp, tmp_path):
        d = self._bucket(tmp_path, "velho", dias=10)
        achados = exp.sweep(tmp_path, 24.0)
        assert len(achados) == 1
        estado = json.loads((d / "state.json").read_text(encoding="utf-8"))
        assert estado["status"] == "active", "dry_run alterou o estado"

    def test_apply_expira_de_verdade(self, exp, tmp_path):
        d = self._bucket(tmp_path, "velho", dias=10)
        achados = exp.sweep(tmp_path, 24.0, dry_run=False)
        assert len(achados) == 1 and achados[0].get("expired")
        estado = json.loads((d / "state.json").read_text(encoding="utf-8"))
        assert estado["status"] != "active"

    def test_pipeline_fresco_nao_e_tocado(self, exp, tmp_path):
        """A varredura nao pode destruir trabalho em andamento em outro projeto."""
        d = self._bucket(tmp_path, "fresco", dias=0.1)
        assert exp.sweep(tmp_path, 24.0, dry_run=False) == []
        assert json.loads((d / "state.json").read_text(encoding="utf-8"))["status"] == "active"

    def test_mistura_expira_so_o_vencido(self, exp, tmp_path):
        self._bucket(tmp_path, "velho-a", dias=40)
        self._bucket(tmp_path, "velho-b", dias=5)
        self._bucket(tmp_path, "fresco", dias=0.5)
        achados = exp.sweep(tmp_path, 24.0, dry_run=False)
        nomes = {Path(x["bucket"]).name for x in achados}
        assert nomes == {"velho-a", "velho-b"}

    def test_bucket_sem_task_e_ignorado(self, exp, tmp_path):
        d = tmp_path / "projects" / "idle"
        d.mkdir(parents=True)
        (d / "state.json").write_text(json.dumps({"task_id": None, "status": "idle"}),
                                      encoding="utf-8")
        assert exp.sweep(tmp_path, 24.0) == []

    def test_state_json_torto_nao_derruba_a_varredura(self, exp, tmp_path):
        d = tmp_path / "projects" / "torto"
        d.mkdir(parents=True)
        (d / "state.json").write_text("{nao e json}", encoding="utf-8")
        self._bucket(tmp_path, "velho", dias=10)
        assert len(exp.sweep(tmp_path, 24.0)) == 1

    def test_sem_projects_nao_quebra(self, exp, tmp_path):
        assert exp.buckets(tmp_path) == [] and exp.sweep(tmp_path, 24.0) == []
