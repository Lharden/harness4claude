import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
spec = importlib.util.spec_from_file_location("transactional_state", ROOT / "scripts" / "transactional_state.py")
state = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(state)


def test_revision_evidence_and_scope_invariants(tmp_path: Path):
    db = state.HarnessDatabase(tmp_path)
    task = db.start_task(
        scope_id="session|repo|worktree", legacy_level="L1-feature", tier="L1", kind="feature",
        pipeline=["write-spec-light", "tdd", "verify-against-spec"], prompt="build",
    )
    db.record_artifact(task["task_id"], "spec-light", "spec.md", "abc")
    task = db.task(task["task_id"])
    task = db.transition(task["task_id"], "tdd", expected_revision=task["revision"])
    task = db.transition(task["task_id"], "verify-against-spec", expected_revision=task["revision"])
    task = db.record_evidence(
        task["task_id"], evidence_type="test", command="pytest", exit_code=0,
        tests_collected=3, tests_passed=3, output_hash="out",
    )
    done = db.complete(task["task_id"], expected_revision=task["revision"])

    assert done["status"] == "done"
    with pytest.raises(state.StateTransitionError, match="revision"):
        db.complete(task["task_id"], expected_revision=0)


def test_starting_new_task_cancels_pending_gates_on_abandoned_task(tmp_path: Path):
    db = state.HarnessDatabase(tmp_path)
    first = db.start_task(
        scope_id="s", legacy_level="L1-bug", tier="L1", kind="bug",
        pipeline=["systematic-debugging", "tdd", "verify"], prompt="fix",
    )
    first = db.open_gate(first["task_id"], "escalation")

    db.start_task(
        scope_id="s", legacy_level="L1-feature", tier="L1", kind="feature",
        pipeline=["write-spec-light", "tdd", "verify-against-spec"], prompt="build",
    )

    abandoned = db.task(first["task_id"])
    assert abandoned["status"] == "abandoned"
    assert abandoned["pending_gate"] is None


def test_zero_tests_and_stale_owner_do_not_verify(tmp_path: Path):
    db = state.HarnessDatabase(tmp_path)
    task = db.start_task(
        scope_id="s", legacy_level="L1-bug", tier="L1", kind="bug",
        pipeline=["verify"], prompt="fix",
    )
    task = db.record_evidence(
        task["task_id"], evidence_type="test", command="pytest", exit_code=0,
        tests_collected=0, tests_passed=0, output_hash="out",
    )
    assert task["verified"] is False
    lease = db.acquire_lease("s", "owner-a", ttl_seconds=1, now=10)
    takeover = db.acquire_lease("s", "owner-b", ttl_seconds=1, now=12)
    with pytest.raises(state.StateTransitionError, match="owner epoch"):
        db.transition(
            task["task_id"],
            "missing",
            expected_revision=db.task(task["task_id"])["revision"],
            owner_epoch=lease["owner_epoch"],
        )
    assert takeover["owner_epoch"] == 2


def test_latest_failing_test_revokes_passing_evidence_for_same_revision(tmp_path: Path):
    db = state.HarnessDatabase(tmp_path)
    task = db.start_task(
        scope_id="s", legacy_level="L1-bug", tier="L1", kind="bug",
        pipeline=["verify"], prompt="fix",
    )
    task = db.record_evidence(
        task["task_id"], evidence_type="test", command="pytest", exit_code=0,
        tests_collected=3, tests_passed=3, output_hash="passing",
    )

    task = db.record_evidence(
        task["task_id"], evidence_type="test", command="pytest", exit_code=1,
        tests_collected=3, tests_passed=2, output_hash="failing",
    )

    assert task["verified"] is False
    assert task["status"] == "active"
    with pytest.raises(state.StateTransitionError, match="fresh verification"):
        db.complete(task["task_id"], expected_revision=task["revision"])


def test_non_test_evidence_does_not_revoke_latest_passing_test(tmp_path: Path):
    db = state.HarnessDatabase(tmp_path)
    task = db.start_task(
        scope_id="s", legacy_level="L1-bug", tier="L1", kind="bug",
        pipeline=["verify"], prompt="fix",
    )
    task = db.record_evidence(
        task["task_id"], evidence_type="test", command="pytest", exit_code=0,
        tests_collected=2, tests_passed=2, output_hash="passing",
    )

    task = db.record_evidence(
        task["task_id"], evidence_type="review", command=None, exit_code=None,
        tests_collected=None, tests_passed=None, output_hash="review",
    )

    assert task["verified"] is True


def test_state_cli_creates_database_and_projection(tmp_path: Path):
    result = subprocess.run(
        [
            sys.executable, str(ROOT / "scripts" / "state_cli.py"),
            "--home", str(tmp_path), "init", "--scope", "session|repo|worktree",
            "--task", "t-cli", "--classification", "L1-feature", "--prompt", "build",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    projection = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert projection["task_id"] == "t-cli"
    assert projection["current_step"] == "write-spec-light"
    assert (tmp_path / "harness.db").exists()


def test_stale_task_ttl_abandons_pipeline_and_releases_scope(tmp_path: Path):
    db = state.HarnessDatabase(tmp_path)
    task = db.start_task(
        scope_id="session|repo|worktree", legacy_level="L1-feature", tier="L1", kind="feature",
        pipeline=["write-spec-light", "tdd", "verify-against-spec"], prompt="build",
    )
    started = datetime.fromisoformat(task["started_at"]).timestamp()

    assert db.expire_stale_task("session|repo|worktree", ttl_seconds=3600, now=started + 3599) is None
    expired = db.expire_stale_task("session|repo|worktree", ttl_seconds=3600, now=started + 3601)

    assert expired is not None
    assert expired["status"] == "abandoned"
    assert db.current_task("session|repo|worktree") is None


def test_stale_task_ttl_cancels_a_pending_human_gate(tmp_path: Path):
    db = state.HarnessDatabase(tmp_path)
    task = db.start_task(
        scope_id="s", legacy_level="L1-bug", tier="L1", kind="bug",
        pipeline=["systematic-debugging", "tdd", "verify"], prompt="fix",
    )
    task = db.open_gate(task["task_id"], "escalation")
    started = datetime.fromisoformat(task["started_at"]).timestamp()

    expired = db.expire_stale_task("s", ttl_seconds=1, now=started + 2)

    assert expired is not None
    assert expired["status"] == "abandoned"
    assert expired["pending_gate"] is None


def test_ttl_compare_and_set_does_not_expire_a_replacement_task(tmp_path: Path):
    db = state.HarnessDatabase(tmp_path)
    old = db.start_task(
        scope_id="s", legacy_level="L1-bug", tier="L1", kind="bug",
        pipeline=["systematic-debugging", "tdd", "verify"], prompt="fix",
    )
    replacement = db.start_task(
        scope_id="s", legacy_level="L1-feature", tier="L1", kind="feature",
        pipeline=["write-spec-light", "tdd", "verify-against-spec"], prompt="build",
    )
    started = datetime.fromisoformat(replacement["started_at"]).timestamp()

    expired = db.expire_stale_task(
        "s", ttl_seconds=1, now=started + 2, expected_task_id=old["task_id"]
    )

    assert expired is None
    assert db.current_task("s")["task_id"] == replacement["task_id"]


def test_state_cli_touch_invalidates_fresh_verification(tmp_path: Path):
    cli = str(ROOT / "scripts" / "state_cli.py")
    base = [sys.executable, cli, "--home", str(tmp_path)]
    init = subprocess.run(
        [*base, "init", "--scope", "s", "--task", "t-touch", "--classification", "L1-bug"],
        capture_output=True, text=True, check=False,
    )
    assert init.returncode == 0, init.stderr
    evidence = subprocess.run(
        [*base,
            "evidence", "--task", "t-touch", "--type", "test", "--command-text", "pytest",
            "--exit-code", "0", "--tests-collected", "2", "--tests-passed", "2",
        ],
        capture_output=True, text=True, check=False,
    )
    assert evidence.returncode == 0, evidence.stderr

    touched = subprocess.run(
        [*base, "touch", "--task", "t-touch", "--path", "src/app.py"],
        capture_output=True, text=True, check=False,
    )

    assert touched.returncode == 0, touched.stderr
    projection = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert projection["verified"] is False
    assert projection["status"] == "active"
    assert projection["code_revision"] == 1


# --- Correcao semantica tem que propagar (incidente 2026-09-03) ---------------


def test_confirm_classification_propaga_legacy_level(tmp_path):
    """`legacy_level` e a forma legivel de `tier-kind`; nao pode ficar para tras.

    Medido em 2026-09-03: apos `confirm_classification.py --final L1-bug`, a
    linha ficou com `tier='L1'`, `kind='bug'` e `legacy_level='L1-feature'` —
    o rotulo antigo do regex. Quem le a coluna (relatorio, auditoria, migracao)
    recebe a classificacao que a correcao semantica descartou.

    O irmao `reclassify` ja atualizava a coluna. A divergencia entre dois
    caminhos que fazem a mesma coisa e o defeito.
    """
    database = state.HarnessDatabase(tmp_path)
    task = database.start_task(
        scope_id="s|repo|wt", legacy_level="L1-feature", tier="L1", kind="feature",
        pipeline=["write-spec-light", "tdd"], prompt="p",
    )
    depois = database.confirm_classification(
        task["task_id"], tier="L1", kind="bug",
        pipeline=["systematic-debugging", "tdd", "verify"],
        source="semantic", confidence=1.0,
    )
    assert depois["legacy_level"] == "L1-bug"
    assert depois["legacy_level"] == f"{depois['tier']}-{depois['kind']}"


def test_confirm_classification_concordante_tambem_mantem_coerencia(tmp_path):
    database = state.HarnessDatabase(tmp_path)
    task = database.start_task(
        scope_id="s|repo|wt", legacy_level="L1-bug", tier="L1", kind="bug",
        pipeline=["systematic-debugging"], prompt="p",
    )
    depois = database.confirm_classification(
        task["task_id"], tier="L1", kind="bug", pipeline=["systematic-debugging"],
        source="semantic", confidence=1.0,
    )
    assert depois["legacy_level"] == "L1-bug"


# --- Task verificada nao e task abandonada (incidente 2026-09-03) -------------
#
# `start_task` movia para 'abandoned' TODA task nas quatro situacoes vivas,
# incluindo 'verified'. Uma task que fez o trabalho, rodou a suite e passou
# ficava registrada igual a uma largada no meio — porque o prompt seguinte do
# usuario chega antes de dar tempo de fechar.
#
# Medido em 2026-09-03 nos harness.db da maquina: 6 abandonadas, 3 delas com
# verified=1. Metade do "abandono" era trabalho concluido.
#
# 'superseded' fica FORA de `one_active_task_per_scope`, entao a task sai do
# caminho da nova sem mentir sobre o que aconteceu com ela.


def test_task_verificada_vira_superseded_nao_abandonada(tmp_path):
    db = state.HarnessDatabase(tmp_path)
    primeira = db.start_task(scope_id="s|r|w", legacy_level="L1-bug", tier="L1",
                             kind="bug", pipeline=["tdd"], prompt="um")
    db.record_evidence(primeira["task_id"], evidence_type="test", command="python -m pytest -q",
                       exit_code=0, tests_collected=10, tests_passed=10, output_hash="h")
    assert db.task(primeira["task_id"])["verified"] is True

    db.start_task(scope_id="s|r|w", legacy_level="L1-bug", tier="L1",
                  kind="bug", pipeline=["tdd"], prompt="dois")

    anterior = db.task(primeira["task_id"])
    assert anterior["status"] == "superseded"
    assert anterior["verified"] is True, "a verificacao que aconteceu nao pode sumir"


def test_task_nao_verificada_continua_abandonada(tmp_path):
    """Largar no meio sem evidencia continua sendo abandono. A distincao e o ponto."""
    db = state.HarnessDatabase(tmp_path)
    primeira = db.start_task(scope_id="s|r|w", legacy_level="L1-bug", tier="L1",
                             kind="bug", pipeline=["tdd"], prompt="um")
    db.start_task(scope_id="s|r|w", legacy_level="L1-bug", tier="L1",
                  kind="bug", pipeline=["tdd"], prompt="dois")
    assert db.task(primeira["task_id"])["status"] == "abandoned"


def test_superseded_libera_o_indice_de_task_unica(tmp_path):
    """`one_active_task_per_scope` so admite um vivo; 'superseded' tem de sair dele."""
    db = state.HarnessDatabase(tmp_path)
    primeira = db.start_task(scope_id="s|r|w", legacy_level="L1-bug", tier="L1",
                             kind="bug", pipeline=["tdd"], prompt="um")
    db.record_evidence(primeira["task_id"], evidence_type="test", command="python -m pytest -q",
                       exit_code=0, tests_collected=10, tests_passed=10, output_hash="h")
    segunda = db.start_task(scope_id="s|r|w", legacy_level="L1-bug", tier="L1",
                            kind="bug", pipeline=["tdd"], prompt="dois")
    assert db.task(segunda["task_id"])["status"] == "active"
    terceira = db.start_task(scope_id="s|r|w", legacy_level="L1-bug", tier="L1",
                             kind="bug", pipeline=["tdd"], prompt="tres")
    assert db.task(terceira["task_id"])["status"] == "active"


def test_gate_pendente_de_task_verificada_tambem_e_cancelado(tmp_path):
    """A troca de task nao pode deixar gate orfao esperando decisao humana."""
    db = state.HarnessDatabase(tmp_path)
    primeira = db.start_task(scope_id="s|r|w", legacy_level="L2-feature", tier="L2",
                             kind="feature", pipeline=["write-spec", "approve-spec"], prompt="um")
    db.record_evidence(primeira["task_id"], evidence_type="test", command="python -m pytest -q",
                       exit_code=0, tests_collected=1, tests_passed=1, output_hash="h")
    db.start_task(scope_id="s|r|w", legacy_level="L1-bug", tier="L1",
                  kind="bug", pipeline=["tdd"], prompt="dois")
    assert db.task(primeira["task_id"])["pending_gate"] is None


# --- Task concluida e terminal (incidente 2026-09-03) ------------------------
#
# `touch_files` ja protege 'done': o CASE so rebaixa quem esta em 'verified'.
# `record_evidence` escrevia 'verified' por cima de QUALQUER status. Entao uma
# suite rodada depois do `complete` — o caso normal, porque a validacao final
# vem depois de fechar — devolvia a task para 'verified', e o primeiro comando
# de shell seguinte a derrubava para 'active'. Task concluida ressuscitada por
# evidencia posterior.
#
# Pior: 'verified' esta DENTRO de `one_active_task_per_scope`. Com uma task nova
# ja aberta no mesmo escopo, a ressurreicao viola o indice unico e o hook
# estoura com IntegrityError em vez de gravar a evidencia.


def test_evidencia_posterior_nao_ressuscita_task_concluida(tmp_path):
    db = state.HarnessDatabase(tmp_path)
    task = db.start_task(scope_id="s|r|w", legacy_level="L1-bug", tier="L1",
                         kind="bug", pipeline=["tdd"], prompt="um")
    task = db.record_evidence(task["task_id"], evidence_type="test", command="pytest",
                              exit_code=0, tests_collected=5, tests_passed=5, output_hash="h")
    concluida = db.complete(task["task_id"], expected_revision=task["revision"])
    assert concluida["status"] == "done"

    depois = db.record_evidence(concluida["task_id"], evidence_type="test", command="pytest",
                                exit_code=0, tests_collected=9, tests_passed=9, output_hash="h2")
    assert depois["status"] == "done", "task concluida nao volta a ficar viva"
    assert depois["verified"] is True


def test_evidencia_falha_posterior_nao_derruba_task_concluida(tmp_path):
    """Suite vermelha depois do fecho tambem nao reabre: 'done' e terminal nos dois sentidos."""
    db = state.HarnessDatabase(tmp_path)
    task = db.start_task(scope_id="s|r|w", legacy_level="L1-bug", tier="L1",
                         kind="bug", pipeline=["tdd"], prompt="um")
    task = db.record_evidence(task["task_id"], evidence_type="test", command="pytest",
                              exit_code=0, tests_collected=5, tests_passed=5, output_hash="h")
    concluida = db.complete(task["task_id"], expected_revision=task["revision"])

    depois = db.record_evidence(concluida["task_id"], evidence_type="test", command="pytest",
                                exit_code=1, tests_collected=9, tests_passed=8, output_hash="h3")
    assert depois["status"] == "done"
    assert depois["verified"] is True


def test_evidencia_em_task_concluida_nao_colide_com_a_task_viva_do_escopo(tmp_path):
    """Ressuscitar para 'verified' com outra task aberta violaria o indice unico."""
    db = state.HarnessDatabase(tmp_path)
    primeira = db.start_task(scope_id="s|r|w", legacy_level="L1-bug", tier="L1",
                             kind="bug", pipeline=["tdd"], prompt="um")
    primeira = db.record_evidence(primeira["task_id"], evidence_type="test", command="pytest",
                                  exit_code=0, tests_collected=5, tests_passed=5, output_hash="h")
    db.complete(primeira["task_id"], expected_revision=primeira["revision"])
    db.start_task(scope_id="s|r|w", legacy_level="L1-bug", tier="L1",
                  kind="bug", pipeline=["tdd"], prompt="dois")

    depois = db.record_evidence(primeira["task_id"], evidence_type="test", command="pytest",
                                exit_code=0, tests_collected=9, tests_passed=9, output_hash="h4")
    assert depois["status"] == "done"


def test_evidencia_em_task_abandonada_nao_a_reabre(tmp_path):
    """'abandoned' e 'superseded' tambem sao terminais — sem excecao para um deles."""
    db = state.HarnessDatabase(tmp_path)
    primeira = db.start_task(scope_id="s|r|w", legacy_level="L1-bug", tier="L1",
                             kind="bug", pipeline=["tdd"], prompt="um")
    db.start_task(scope_id="s|r|w", legacy_level="L1-bug", tier="L1",
                  kind="bug", pipeline=["tdd"], prompt="dois")
    assert db.task(primeira["task_id"])["status"] == "abandoned"

    depois = db.record_evidence(primeira["task_id"], evidence_type="test", command="pytest",
                                exit_code=0, tests_collected=9, tests_passed=9, output_hash="h5")
    assert depois["status"] == "abandoned"
    assert depois["verified"] is False


def test_arquivo_tocado_depois_do_fecho_nao_mexe_na_task(tmp_path):
    """A outra metade do desfecho terminal: os contadores tambem param.

    `record_evidence` ja nao ressuscita, mas `touch_files` seguia subindo
    `code_revision` e `revision` numa task fechada — medido em producao em
    2026-09-03: uma task 'done' chegou a code_revision 53 depois do fecho.
    Nao e ressurreicao, e contabilidade de uma task que acabou, e ela ainda
    atribuia arquivos novos a um trabalho que ja tinha sido entregue.
    """
    db = state.HarnessDatabase(tmp_path)
    task = db.start_task(scope_id="s|r|w", legacy_level="L1-bug", tier="L1",
                         kind="bug", pipeline=["tdd"], prompt="um")
    task = db.record_evidence(task["task_id"], evidence_type="test", command="pytest",
                              exit_code=0, tests_collected=5, tests_passed=5, output_hash="h")
    concluida = db.complete(task["task_id"], expected_revision=task["revision"])
    arquivos_antes = db.files(concluida["task_id"])

    depois = db.touch_files(concluida["task_id"], ["src/novo.py"])

    assert depois["status"] == "done"
    assert depois["code_revision"] == concluida["code_revision"]
    assert depois["revision"] == concluida["revision"]
    assert db.files(concluida["task_id"]) == arquivos_antes


def test_task_viva_continua_contando_arquivo(tmp_path):
    """Contraste: a protecao e so para desfecho registrado, nao afrouxou o geral."""
    db = state.HarnessDatabase(tmp_path)
    task = db.start_task(scope_id="s|r|w", legacy_level="L1-bug", tier="L1",
                         kind="bug", pipeline=["tdd"], prompt="um")
    depois = db.touch_files(task["task_id"], ["src/novo.py"])
    assert depois["code_revision"] == task["code_revision"] + 1
    assert db.files(task["task_id"]) == [str(Path("src/novo.py"))]
