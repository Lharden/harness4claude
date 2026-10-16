"""Toda conexao SQLite aberta no caminho quente dos hooks e fechada ao sair.

`with sqlite3.connect(...)` so faz commit/rollback: nao fecha. No Windows o
handle do arquivo fica preso ate o coletor, e no Python 3.14 o descarte sem
`close()` emite `ResourceWarning`. A prova rastreia cada `sqlite3.connect`
chamado e exige que, ao voltar da operacao, todas estejam fechadas.
"""

from __future__ import annotations

import importlib.util
import io
import os
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
sys.path.insert(0, str(ROOT / "scripts"))

import harness_paths  # noqa: E402
from transactional_state import HarnessDatabase  # noqa: E402


@pytest.fixture
def abertas(monkeypatch):
    original = sqlite3.connect
    lista: list[sqlite3.Connection] = []

    def rastreia(*args, **kwargs):
        con = original(*args, **kwargs)
        lista.append(con)
        return con

    monkeypatch.setattr(sqlite3, "connect", rastreia)
    return lista


def _todas_fechadas(lista):
    assert lista, "a operacao nao abriu conexao nenhuma: o teste nao mede nada"
    abertas = []
    for con in lista:
        try:
            con.execute("SELECT 1")
            abertas.append(con)
        except sqlite3.ProgrammingError:
            pass
    assert abertas == [], f"{len(abertas)} de {len(lista)} conexoes seguem abertas"


def _carrega(nome: str, caminho: Path):
    spec = importlib.util.spec_from_file_location(nome, caminho)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_harness_database_fecha_toda_conexao(tmp_path, abertas):
    db = HarnessDatabase(tmp_path)
    task = db.start_task(scope_id="s", legacy_level="L1", tier="L1", kind="bug",
                         pipeline=["tdd"], prompt="x")
    db.record_evidence(task["task_id"], evidence_type="test", command="pytest", exit_code=0,
                       tests_collected=1, tests_passed=1, output_hash="h")
    _todas_fechadas(abertas)


def test_conta_evidencia_do_hook_fecha_a_conexao(tmp_path, abertas):
    db = HarnessDatabase(tmp_path)
    hook = _carrega("harness_transactional_sq", ROOT / "hooks" / "harness-transactional.py")
    abertas.clear()
    hook._conta_evidencia(db, "t-inexistente", 1)
    _todas_fechadas(abertas)


def test_iniciada_antes_fecha_a_conexao(tmp_path, abertas):
    from datetime import datetime, timezone

    db = HarnessDatabase(tmp_path)
    abertas.clear()
    harness_paths._iniciada_antes(tmp_path, "t-inexistente", datetime.now(timezone.utc))
    _todas_fechadas(abertas)


def test_lifecycle_fecha_a_conexao(tmp_path, abertas, monkeypatch):
    lifecycle = _carrega("harness_lifecycle_sq", ROOT / "hooks" / "harness-lifecycle.py")
    monkeypatch.setenv("HARNESS_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["harness-lifecycle.py", "--event", "PostCompact"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
    lifecycle.main()
    _todas_fechadas(abertas)
