"""Testes para scripts/record_signal.py (Harness v3 — Bloco C).

Garante:
- actual_level deriva corretamente do nº de arquivos
- build_task monta o registro (completed/abandoned) com classification_meta
- record é idempotente por task_id (atualiza, nao duplica)
- record recalcula o bloco classify (avg_classify_accuracy)
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
RECORD_PATH = ROOT / "scripts" / "record_signal.py"


@pytest.fixture(scope="module")
def rec():
    # garante que scripts/ esteja importavel (record_signal importa migrate_state)
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("record_signal", RECORD_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["record_signal"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_actual_level(rec):
    assert rec.actual_level(1) == "L0"
    assert rec.actual_level(2) == "L1"
    assert rec.actual_level(3) == "L1"
    assert rec.actual_level(4) == "L2"


def test_build_task_completed(rec):
    state = {"task_id": "t-x", "classification": "L1-feature",
             "classification_meta": {"suggested": "L2-feature", "final": "L1-feature",
                                     "source": "semantic", "agreed": False}}
    counter = {"count": 2}
    task = rec.build_task(state, counter, completed=True, steps=["tdd"],
                          reason=None, timestamp="2026-06-02T00:00:00+00:00")
    assert task["task_id"] == "t-x"
    assert task["actual_level"] == "L1"
    assert task["pipeline_completed"] is True
    assert task["completed_at"] == "2026-06-02T00:00:00+00:00"
    assert task["classification_meta"]["agreed"] is False


def test_build_task_abandoned(rec):
    task = rec.build_task({"task_id": "t-y"}, {"count": 0}, completed=False,
                          steps=[], reason="switch", timestamp="2026-06-02T00:00:00+00:00")
    assert task["pipeline_completed"] is False
    assert task["abandoned_at"] == "2026-06-02T00:00:00+00:00"
    assert task["reason"] == "switch"


def test_record_idempotent(rec, tmp_path):
    signals = {"version": 3, "harness_version": "v3", "tasks": [], "aggregates": {}}
    (tmp_path / "signals.json").write_text(json.dumps(signals), encoding="utf-8")
    task = {"task_id": "t-dup", "classification": "L1-feature", "actual_level": "L1",
            "pipeline_completed": True, "steps_executed": [], "files_modified": 2,
            "classification_meta": {"agreed": True}}
    rec.record(tmp_path, task)
    rec.record(tmp_path, dict(task))  # mesma task_id de novo
    out = json.loads((tmp_path / "signals.json").read_text(encoding="utf-8"))
    assert len(out["tasks"]) == 1  # nao duplicou


def test_record_distinct_tasks_coexist(rec, tmp_path):
    """P1.2: task_ids distintos coexistem (microssegundo evita colisao/sobrescrita)."""
    (tmp_path / "signals.json").write_text(
        json.dumps({"version": 3, "harness_version": "v3", "tasks": [], "aggregates": {}}),
        encoding="utf-8")
    base = {"classification": "L1-feature", "actual_level": "L1",
            "pipeline_completed": True, "steps_executed": [], "files_modified": 2,
            "classification_meta": {"agreed": True}}
    rec.record(tmp_path, {**base, "task_id": "t-20260616-2147381234"})
    rec.record(tmp_path, {**base, "task_id": "t-20260616-2147389876"})
    out = json.loads((tmp_path / "signals.json").read_text(encoding="utf-8"))
    assert {t["task_id"] for t in out["tasks"]} == {
        "t-20260616-2147381234", "t-20260616-2147389876"}  # ambas preservadas


def test_record_atomic_leaves_no_tmp(rec, tmp_path):
    """P1.1: escrita atomica nao deixa arquivo .tmp e produz JSON valido."""
    (tmp_path / "signals.json").write_text(
        json.dumps({"version": 3, "harness_version": "v3", "tasks": [], "aggregates": {}}),
        encoding="utf-8")
    rec.record(tmp_path, {"task_id": "t-atomic", "classification": "L1-feature",
                          "actual_level": "L1", "pipeline_completed": True,
                          "steps_executed": [], "files_modified": 1,
                          "classification_meta": {"agreed": True}})
    assert list(tmp_path.glob("signals.json.tmp-*")) == []  # nenhum temporario
    json.loads((tmp_path / "signals.json").read_text(encoding="utf-8"))  # parseavel


def test_record_updates_accuracy(rec, tmp_path):
    (tmp_path / "signals.json").write_text(
        json.dumps({"version": 3, "tasks": [], "aggregates": {}}), encoding="utf-8")
    task = {"task_id": "t-acc", "classification": "L1-feature", "actual_level": "L1",
            "pipeline_completed": True, "steps_executed": [], "files_modified": 2,
            "classification_meta": {"agreed": True, "source": "regex"}}
    out = rec.record(tmp_path, task)
    assert out["aggregates"]["classify"]["avg_classify_accuracy"] == 1.0


# ---------------------------------------------------------------------------
# --expect-task: proteção contra state.json sobrescrito por sessão paralela.
# Incidente 2026-06-12: o workflow fechou a task t-20260612-033900, mas o
# state global já tinha sido trocado pela task fantasma t-20260612-034438
# (criada pela sessão headless do remember) — e o signal foi gravado errado.
# ---------------------------------------------------------------------------

def _setup_harness_dir(tmp_path, task_id: str) -> None:
    (tmp_path / "state.json").write_text(json.dumps({
        "task_id": task_id,
        "classification": "L1-feature",
        "classification_meta": {"suggested": "L1-feature", "final": "L1-feature",
                                "source": "semantic", "agreed": True},
    }), encoding="utf-8")
    (tmp_path / ".session-files-count").write_text(
        json.dumps({"count": 2, "files": ["a.py", "b.py"], "task_id": task_id}),
        encoding="utf-8")


def test_main_expect_task_match_records(rec, tmp_path, monkeypatch):
    _setup_harness_dir(tmp_path, "t-real")
    monkeypatch.setattr(sys, "argv", [
        "record_signal.py", "--harness-dir", str(tmp_path),
        "--completed", "--steps", "tdd", "--expect-task", "t-real",
    ])
    assert rec.main() == 0
    out = json.loads((tmp_path / "signals.json").read_text(encoding="utf-8"))
    assert [t["task_id"] for t in out["tasks"]] == ["t-real"]


def test_main_expect_task_mismatch_aborts(rec, tmp_path, monkeypatch):
    """State com task diferente da esperada → não grava nada, exit 2."""
    _setup_harness_dir(tmp_path, "t-fantasma")
    monkeypatch.setattr(sys, "argv", [
        "record_signal.py", "--harness-dir", str(tmp_path),
        "--completed", "--expect-task", "t-real",
    ])
    assert rec.main() == 2
    assert not (tmp_path / "signals.json").exists(), \
        "mismatch de task_id não pode gravar signal (task fantasma)"


# ---------------------------------------------------------------------------
# Canario da classificacao (B5) — o numero que nao depende de ninguem lembrar
# ---------------------------------------------------------------------------


def _agg(rec, tasks):
    import migrate_state
    return migrate_state.recompute_aggregates(tasks, {})["classify"]


def _task(tid, classificacao, observado, agreed=None, abandonada=False):
    t = {
        "task_id": tid,
        "classification": classificacao,
        "actual_level": observado,
        "classification_meta": {"suggested": classificacao, "agreed": agreed},
    }
    if abandonada:
        t["abandoned_at"] = "2026-08-13T00:00:00Z"
    return t


def test_proxy_existe_sem_nenhuma_confirmacao(rec):
    """O ponto inteiro do canario. `agreed` exige que alguem rode
    confirm_classification.py, e foi por isso que a metrica ficou zerada: a
    instrucao existe na skill desde a auditoria de 2026-07-28 e mesmo assim 27
    tasks novas acumularam agreed=null. Metrica que depende de passo lembravel
    deriva para zero."""
    c = _agg(rec, [_task("t-1", "L2-feature", "L2"), _task("t-2", "L1-bug", "L0")])
    assert c["avg_classify_accuracy"] is None      # semantica segue null, corretamente
    assert c["proxy_regex_vs_observado"] == 0.5    # canario responde mesmo assim
    assert c["proxy_amostras"] == 2


def test_proxy_conta_tasks_abandonadas(rec):
    """26 das 27 tasks reais estavam abandonadas por TTL. Exclui-las deixaria o
    canario com 1 amostra — inutil justamente quando ele precisa servir."""
    c = _agg(rec, [_task("t-1", "L2-feature", "L2", abandonada=True),
                   _task("t-2", "L2-feature", "L2", abandonada=True)])
    assert c["proxy_amostras"] == 2 and c["proxy_regex_vs_observado"] == 1.0


def test_proxy_e_none_sem_dado_em_vez_de_zero(rec):
    """Zero significaria "o regex erra sempre". Ausencia de dado nao e erro."""
    c = _agg(rec, [])
    assert c["proxy_regex_vs_observado"] is None and c["proxy_amostras"] == 0


def test_task_sem_actual_level_nao_entra_no_denominador(rec):
    c = _agg(rec, [_task("t-1", "L2-feature", None), _task("t-2", "L1-bug", "L1")])
    assert c["proxy_amostras"] == 1 and c["proxy_regex_vs_observado"] == 1.0


def test_sem_confirmacao_torna_a_lacuna_visivel(rec):
    """accuracy=null sozinho nao diz se ninguem confirmou ou se nao ha tasks."""
    c = _agg(rec, [_task("t-1", "L2-feature", "L2"),
                   _task("t-2", "L1-bug", "L1", agreed=True)])
    assert c["sem_confirmacao"] == 1
    assert c["total_classified"] == 1
    assert c["avg_classify_accuracy"] == 1.0


def test_proxy_carrega_o_proprio_limite(rec):
    """O numero e o limite dele viajam juntos. Separar e como o proxy vira
    "acuracia do classificador" na primeira vez que alguem cita so o valor."""
    c = _agg(rec, [_task("t-1", "L2-feature", "L2")])
    assert "proxy" in c["proxy_nota"] or "arquivos" in c["proxy_nota"]
    assert "acuracia" in c["proxy_nota"].lower()


def test_semantica_e_proxy_nao_se_misturam(rec):
    """Sao numeros diferentes medindo coisas diferentes, e podem discordar."""
    tasks = [_task("t-1", "L2-feature", "L0", agreed=True),   # semantica concorda
             _task("t-2", "L2-feature", "L0", agreed=True)]   # observado discorda
    c = _agg(rec, tasks)
    assert c["avg_classify_accuracy"] == 1.0
    assert c["proxy_regex_vs_observado"] == 0.0


# --- Uniao das duas contagens (incidente 2026-09-03) --------------------------
#
# `.session-files-count` so cresce por Edit/Write: `harness-reclassify.sh` esta
# registrado em PostToolUse com matcher `Edit|Write`. O hook transacional, que
# ve os comandos de shell, esta em outro matcher (`Bash|PowerShell`) e escreve
# na tabela `files` do harness.db.
#
# As duas fontes sao DISJUNTAS, e ler so uma delas subconta. Medido: uma task
# que alterou 2 arquivos por heredoc registrou `files=0` e virou `actual_level`
# L0, que e a entrada de `proxy_regex_vs_observado`.


def _db_com_arquivos(tmp_path, task_id: str, caminhos, placeholder=False):
    sys.path.insert(0, str(ROOT / "scripts"))
    import importlib.util as iu
    spec = iu.spec_from_file_location("ts_para_record", ROOT / "scripts" / "transactional_state.py")
    ts = iu.module_from_spec(spec)
    spec.loader.exec_module(ts)
    db = ts.HarnessDatabase(tmp_path)
    task = db.start_task(scope_id="s|r|w", legacy_level="L1-bug", tier="L1",
                         kind="bug", pipeline=["tdd"], prompt="p")
    todos = list(caminhos) + (["shell-command"] if placeholder else [])
    if todos:
        db.touch_files(task["task_id"], todos)
    return task["task_id"]


def test_files_modified_une_counter_e_banco(rec, tmp_path):
    real = _db_com_arquivos(tmp_path, "ignorado", ["hooks/x.py", "scripts/y.py"])
    counter = {"count": 1, "files": ["docs/z.md"], "task_id": real}
    assert rec.files_modified(tmp_path, counter, real) == 3


def test_files_modified_nao_conta_duas_vezes_o_mesmo_arquivo(rec, tmp_path):
    real = _db_com_arquivos(tmp_path, "ignorado", ["hooks/x.py"])
    counter = {"count": 1, "files": ["hooks/x.py"], "task_id": real}
    assert rec.files_modified(tmp_path, counter, real) == 1


def test_files_modified_ignora_o_placeholder(rec, tmp_path):
    """'shell-command' nao e arquivo; conta-lo inflaria o nivel."""
    real = _db_com_arquivos(tmp_path, "ignorado", [], placeholder=True)
    counter = {"count": 0, "files": [], "task_id": real}
    assert rec.files_modified(tmp_path, counter, real) == 0


def test_files_modified_cai_no_counter_quando_nao_ha_banco(rec, tmp_path):
    """Sem harness.db (bucket antigo, ou projeto sem pipeline transacional)."""
    counter = {"count": 4, "files": ["a", "b", "c", "d"], "task_id": "t-x"}
    assert rec.files_modified(tmp_path, counter, "t-x") == 4


def test_build_task_marca_atribuicao_incompleta(rec, tmp_path):
    """Placeholder presente = houve shell que pode ter escrito sem dar para ver.

    Sem esta marca, 'nenhum arquivo atribuido' e 'nenhum arquivo alterado' ficam
    indistinguiveis no signals.json — e a segunda leitura e a que vira L0.
    """
    real = _db_com_arquivos(tmp_path, "ignorado", ["a.py"], placeholder=True)
    state = {"task_id": real, "classification": "L1-bug", "classification_meta": {}}
    counter = {"count": 0, "files": [], "task_id": real}
    task = rec.build_task(state, counter, completed=True, steps=["tdd"],
                          reason=None, timestamp="2026-09-03T00:00:00+00:00",
                          harness_dir=tmp_path)
    assert task["files_modified"] == 1
    assert task["atribuicao_incompleta"] is True
