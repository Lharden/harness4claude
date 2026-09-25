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


# --- R5: o rastro da invalidacao sobrevive a escrita -------------------------
#
# `files` usa INSERT OR IGNORE: tocar de novo um caminho ja visto nao cria
# linha, mas sobe `code_revision` do mesmo jeito. Somando o `code_revision`
# final de seis tasks reais: 2 594 subidas, das quais so 366 (14,1%) deixaram
# rastro. As outras 2 228 eram invisiveis — nenhuma tabela dizia o que as
# causou, porque a informacao era descartada na escrita.
#
# `touches` e uma linha por toque. Nada que le `files` muda.


def test_mesmo_caminho_tres_vezes_da_uma_linha_em_files_e_tres_em_touches(tmp_path: Path):
    db = state.HarnessDatabase(tmp_path)
    task = db.start_task(scope_id="s|r|w", legacy_level="L1-bug", tier="L1",
                         kind="bug", pipeline=["tdd"], prompt="um")
    for _ in range(3):
        db.touch_files(task["task_id"], ["src/x.py"], origem="shell")

    assert db.files(task["task_id"]) == [str(Path("src/x.py"))]
    toques = db.touches(task["task_id"])
    assert len(toques) == 3
    assert [t["code_revision"] for t in toques] == [1, 2, 3]
    assert {t["origem"] for t in toques} == {"shell"}


def test_toques_reconstroem_code_revision_sem_buraco(tmp_path: Path):
    """A identidade aritmetica: nenhuma subida sem rastro.

    `code_revision` so e incrementado em `touch_files` (unico site em
    `transactional_state.py`), e cada chamada grava >= 1 linha em `touches` com
    a revisao nova. Logo, numa base criada ja com a tabela:

        COUNT(DISTINCT code_revision) em `touches` == `tasks.code_revision`

    A soma CRUA de linhas NAO serve, e isso e o instrumento e nao a regra: uma
    chamada com tres caminhos e UMA invalidacao e tres linhas. Contar linhas
    daria 3 para uma subida e o desvio pareceria defeito do sistema quando seria
    defeito da conta.
    """
    db = state.HarnessDatabase(tmp_path)
    task = db.start_task(scope_id="s|r|w", legacy_level="L1-bug", tier="L1",
                         kind="bug", pipeline=["tdd"], prompt="um")
    db.touch_files(task["task_id"], ["a.py"], origem="shell")
    db.touch_files(task["task_id"], ["a.py", "b.py", "c.py"], origem="shell")
    db.touch_files(task["task_id"], ["shell-command"], origem="shell-placeholder")
    final = db.touch_files(task["task_id"], ["a.py"], origem="edit")

    toques = db.touches(task["task_id"])
    assert len(toques) == 6                                    # linhas != subidas
    assert len({t["code_revision"] for t in toques}) == 4       # subidas
    assert final["code_revision"] == 4
    assert len({t["code_revision"] for t in toques}) == final["code_revision"]


def test_touches_nomeia_a_origem_de_cada_toque(tmp_path: Path):
    """Sem `origem`, "o numero caiu" e indistinguivel de "caiu pelo motivo errado"."""
    db = state.HarnessDatabase(tmp_path)
    task = db.start_task(scope_id="s|r|w", legacy_level="L1-bug", tier="L1",
                         kind="bug", pipeline=["tdd"], prompt="um")
    db.touch_files(task["task_id"], ["src/x.py"], origem="shell")
    db.touch_file(task["task_id"], "docs/y.md", origem="edit")
    db.touch_files(task["task_id"], ["shell-command"], origem="shell-placeholder")

    assert [t["origem"] for t in db.touches(task["task_id"])] == [
        "shell", "edit", "shell-placeholder",
    ]
    assert db.touches(task["task_id"], limite=2)[0]["path"] == str(Path("docs/y.md"))


def test_task_fechada_nao_grava_toque(tmp_path: Path):
    """A protecao de task terminal vale para as DUAS tabelas.

    `touches` sem esta guarda cresceria numa task 'done' mesmo com `files` e
    `code_revision` parados — e o rastro de uma entrega fechada passaria a
    contradizer o contador que ele existe para explicar.
    """
    db = state.HarnessDatabase(tmp_path)
    task = db.start_task(scope_id="s|r|w", legacy_level="L1-bug", tier="L1",
                         kind="bug", pipeline=["tdd"], prompt="um")
    db.touch_files(task["task_id"], ["src/x.py"], origem="shell")
    task = db.record_evidence(task["task_id"], evidence_type="test", command="pytest",
                              exit_code=0, tests_collected=5, tests_passed=5, output_hash="h")
    concluida = db.complete(task["task_id"], expected_revision=task["revision"])
    antes = db.touches(concluida["task_id"])

    db.touch_files(concluida["task_id"], ["src/depois.py"], origem="shell")

    assert db.touches(concluida["task_id"]) == antes


def test_banco_antigo_ganha_touches_sem_perder_files(tmp_path: Path):
    """Migracao: base criada antes da tabela continua legivel e passa a rastrear."""
    db = state.HarnessDatabase(tmp_path)
    task = db.start_task(scope_id="s|r|w", legacy_level="L1-bug", tier="L1",
                         kind="bug", pipeline=["tdd"], prompt="um")
    db.touch_files(task["task_id"], ["src/antigo.py"], origem="shell")
    import sqlite3

    with sqlite3.connect(db.path) as cru:
        cru.execute("DROP TABLE touches")

    reaberto = state.HarnessDatabase(tmp_path)
    assert reaberto.files(task["task_id"]) == [str(Path("src/antigo.py"))]
    assert reaberto.touches(task["task_id"]) == []
    reaberto.touch_files(task["task_id"], ["src/novo.py"], origem="shell")
    assert len(reaberto.touches(task["task_id"])) == 1


def test_artifacts_voltam_do_banco_e_chegam_ao_state_json(tmp_path: Path):
    """`record_artifact` sempre gravou; nada lia de volta.

    O sintoma era o pior tipo: `state_cli artifact` saia com 0, a linha entrava
    na tabela, e `artifacts_so_far` no `state.json` continuava `[]`. Quem lia o
    state — incluindo `harness-lifecycle.py` — via uma task sem artefato nenhum,
    e a conclusao natural era perda de dado. O dado estava la; faltava a volta.

    Medido em 2026-09-03: tres artefatos no SQLite, `artifacts_so_far: []`.
    """
    db = state.HarnessDatabase(tmp_path)
    task = db.start_task(
        scope_id="s|r|w", legacy_level="L1-bug", tier="L1", kind="bug",
        pipeline=["systematic-debugging", "tdd", "verify"], prompt="fix",
    )
    tid = task["task_id"]

    assert db.task(tid)["artifacts"] == [], "task sem artefato comeca vazia"

    db.record_artifact(tid, "analysis", "dossie.md", None)
    db.record_artifact(tid, "registry", "terms.toml", "deadbeef")

    artifacts = db.task(tid)["artifacts"]
    assert [a["path"] for a in artifacts] == ["dossie.md", "terms.toml"]
    assert [a["type"] for a in artifacts] == ["analysis", "registry"]
    assert all(a["phase"] for a in artifacts), "a fase em que o artefato nasceu se perde sem isto"

    # O upsert de `record_artifact` nao pode duplicar a linha na volta.
    db.record_artifact(tid, "analysis", "dossie.md", "novohash")
    assert len(db.task(tid)["artifacts"]) == 2


def test_state_cli_artifact_projeta_no_state_json(tmp_path: Path):
    """A ponta que o usuario ve: o CLI grava e o `state.json` mostra."""
    db = state.HarnessDatabase(tmp_path)
    task = db.start_task(
        scope_id="s|r|w", legacy_level="L1-bug", tier="L1", kind="bug",
        pipeline=["systematic-debugging", "tdd", "verify"], prompt="fix",
    )
    cli = ROOT / "scripts" / "state_cli.py"
    res = subprocess.run(
        [sys.executable, str(cli), "--home", str(tmp_path), "artifact",
         "--task", task["task_id"], "--type", "analysis", "--path", "dossie.md"],
        capture_output=True, text=True, check=False,
    )
    assert res.returncode == 0, res.stderr

    projecao = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert projecao["artifacts_so_far"] == ["dossie.md"], (
        "exit 0 com a lista vazia e sucesso silencioso — parece perda de dado"
    )


# --- Achado 4: `skip` nao e falha -------------------------------------------
# A regra de aceite era `tests_passed == tests_collected`. Uma suite com
# qualquer teste pulado nunca satisfaz isso, e as duas configuracoes verdes de
# um projeto inteiro ficam estruturalmente incapazes de serem declaradas
# verificadas. Medido em 2026-09-16: `1821 passed, 28 skipped` e
# `1848 passed, 1 skipped`, ambas exit 0, ambas recusadas pelo caminho manual.
#
# `skip` e ausencia de juizo, nao juizo de exclusao. O que gateia e falha.


def test_suite_com_pulados_verifica(tmp_path: Path):
    """N coletados, P passando, S pulados, P + S == N, exit 0 -> verificado."""
    db = state.HarnessDatabase(tmp_path)
    task = db.start_task(
        scope_id="s", legacy_level="L1-bug", tier="L1", kind="bug",
        pipeline=["verify"], prompt="fix",
    )
    task = db.record_evidence(
        task["task_id"], evidence_type="test", command="python -m pytest -q",
        exit_code=0, tests_collected=1849, tests_passed=1821, tests_skipped=28,
        output_hash="verde-com-skip",
    )

    assert task["verified"] is True
    # D1 (2026-09-23): evidencia mexe so na coluna; o status fica onde estava.
    # Ver tests/test_ciclo_de_vida_da_task.py::TestEvidenciaNaoMexeNoStatus.
    assert task["status"] == "active"


def test_suite_inteira_pulada_nao_verifica(tmp_path: Path):
    """Zero veredito nenhum nao e suite verde: exit 0 sozinho nao basta."""
    db = state.HarnessDatabase(tmp_path)
    task = db.start_task(
        scope_id="s", legacy_level="L1-bug", tier="L1", kind="bug",
        pipeline=["verify"], prompt="fix",
    )
    task = db.record_evidence(
        task["task_id"], evidence_type="test", command="python -m pytest -q",
        exit_code=0, tests_collected=1849, tests_passed=0, tests_skipped=1849,
        output_hash="tudo-pulado",
    )

    assert task["verified"] is False
    with pytest.raises(state.StateTransitionError, match="fresh verification"):
        db.complete(task["task_id"], expected_revision=task["revision"])


def test_falha_com_pulados_nao_verifica(tmp_path: Path):
    """Pulado nao cobre falha: 1800 + 28 != 1849 porque 21 falharam."""
    db = state.HarnessDatabase(tmp_path)
    task = db.start_task(
        scope_id="s", legacy_level="L1-bug", tier="L1", kind="bug",
        pipeline=["verify"], prompt="fix",
    )
    task = db.record_evidence(
        task["task_id"], evidence_type="test", command="python -m pytest -q",
        exit_code=1, tests_collected=1849, tests_passed=1800, tests_skipped=28,
        output_hash="com-falha",
    )

    assert task["verified"] is False


# --- Achado 5: a regua escrita duas vezes -----------------------------------
# A condicao existia em `record_evidence` (Python) e em
# `_has_fresh_test_evidence` (SQL, hoje `_has_fresh_evidence`), independentes. E a assinatura que este
# portao existe para detectar, dentro dele mesmo: duas leituras do mesmo fato,
# e nada obriga as duas a concordarem. O teste exercita o PAR — grava e le de
# volta — porque testar so o lado da escrita e o que deixou as duas divergirem.


@pytest.mark.parametrize(
    "exit_code, coletados, passando, pulados, aceita",
    [
        (0, 1849, 1821, 28, True),     # verde com skip
        (0, 1849, 1849, 0, True),      # verde puro
        (0, 1, 1, 0, True),            # suite minima
        (0, 1849, 0, 1849, False),     # tudo pulado: nenhum veredito
        (1, 1849, 1800, 28, False),    # falha
        (0, 0, 0, 0, False),           # nada coletado
        (0, 1849, 1820, 28, False),    # 1820 + 28 != 1849: sumiu um veredito
    ],
)
def test_escrita_e_leitura_concordam(tmp_path: Path, exit_code, coletados, passando, pulados, aceita):
    db = state.HarnessDatabase(tmp_path)
    task = db.start_task(
        scope_id="s", legacy_level="L1-bug", tier="L1", kind="bug",
        pipeline=["verify"], prompt="fix",
    )
    task = db.record_evidence(
        task["task_id"], evidence_type="test", command="python -m pytest -q",
        exit_code=exit_code, tests_collected=coletados, tests_passed=passando,
        tests_skipped=pulados, output_hash=f"h-{coletados}-{passando}-{pulados}",
    )

    # Lado da escrita.
    assert task["verified"] is aceita

    # Lado da leitura: `complete` consulta `_has_fresh_evidence`, em SQL.
    # As duas pontas tem de dar o mesmo veredito sobre a mesma linha.
    if aceita:
        assert db.complete(task["task_id"], expected_revision=task["revision"])["status"] == "done"
    else:
        with pytest.raises(state.StateTransitionError, match="fresh verification"):
            db.complete(task["task_id"], expected_revision=task["revision"])


def test_banco_antigo_sem_coluna_migra_e_preserva_veredito(tmp_path: Path):
    """Linha gravada antes da coluna existir tem `tests_skipped` NULL.

    `COALESCE(tests_skipped, 0)` faz a regua nova coincidir exatamente com a
    antiga nessas linhas — `passed + 0 == collected` e o que elas ja diziam.
    Evidencia verde de antes do conserto continua verde depois dele.
    """
    import sqlite3

    db = state.HarnessDatabase(tmp_path)
    task = db.start_task(
        scope_id="s", legacy_level="L1-bug", tier="L1", kind="bug",
        pipeline=["verify"], prompt="fix",
    )

    # Simula o esquema anterior: derruba a coluna e grava como se gravava antes.
    with sqlite3.connect(db.path) as raw:
        raw.execute("ALTER TABLE evidence DROP COLUMN tests_skipped")
        raw.execute(
            "INSERT INTO evidence(task_id, code_revision, evidence_type, command, "
            "exit_code, tests_collected, tests_passed, output_hash, created_at) "
            "VALUES (?, 0, 'test', 'pytest', 0, 7, 7, 'antigo', '2026-09-01T00:00:00+00:00')",
            (task["task_id"],),
        )
        raw.execute("UPDATE tasks SET verified = 1, status = 'verified' WHERE task_id = ?",
                    (task["task_id"],))

    # Reabrir roda `_ensure_schema`, que re-adiciona a coluna como NULL.
    db = state.HarnessDatabase(tmp_path)
    task = db.task(task["task_id"])
    assert task["verified"] is True

    # E o lado da leitura aceita a linha antiga.
    assert db.complete(task["task_id"], expected_revision=task["revision"])["status"] == "done"
