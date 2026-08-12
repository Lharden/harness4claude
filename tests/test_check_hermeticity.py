"""Verificacao de integridade do conjunto protegido — Fase 4 da task P-1.b.

Cobre US-4 (AC-1, AC-2) e REQ-F7.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
SCRIPT = ROOT / "scripts" / "check_hermeticity.py"


def _run(*args: str, harness_dir: Path | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if harness_dir is not None:
        env["HARNESS_DIR"] = str(harness_dir)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, timeout=60, env=env,
    )


@pytest.fixture
def populated(tmp_path):
    """Um diretorio de harness com o conjunto protegido preenchido."""
    d = tmp_path / "h"
    (d / "traces").mkdir(parents=True)
    (d / "state.json").write_text('{"task_id": "t-1"}', encoding="utf-8")
    (d / "signals.json").write_text('{"version": 3, "tasks": []}', encoding="utf-8")
    (d / "trace-current.md").write_text("# trace\n", encoding="utf-8")
    (d / ".session-files-count").write_text('{"count": 0}', encoding="utf-8")
    (d / "traces" / "2026-07-25-0900.md").write_text("hist\n", encoding="utf-8")
    return d


class TestSnapshotAndVerify:
    def test_snapshot_then_verify_unchanged_passes(self, populated, tmp_path):
        """AC-1: nada mudou -> exit 0."""
        snap = tmp_path / "snap.json"
        assert _run("--snapshot", str(snap), harness_dir=populated).returncode == 0
        assert snap.exists()
        proc = _run("--verify", str(snap), harness_dir=populated)
        assert proc.returncode == 0, proc.stdout + proc.stderr

    def test_verify_detects_modification(self, populated, tmp_path):
        """AC-2: arquivo alterado -> exit 1 e nome do arquivo no relatorio."""
        snap = tmp_path / "snap.json"
        _run("--snapshot", str(snap), harness_dir=populated)
        (populated / "state.json").write_text('{"task_id": "OUTRA"}', encoding="utf-8")

        proc = _run("--verify", str(snap), harness_dir=populated)
        assert proc.returncode == 1, "modificacao no conjunto protegido deve falhar"
        assert "state.json" in proc.stdout, (
            f"o relatorio deve nomear o arquivo alterado.\n{proc.stdout}"
        )

    def test_verify_detects_deletion(self, populated, tmp_path):
        snap = tmp_path / "snap.json"
        _run("--snapshot", str(snap), harness_dir=populated)
        (populated / "trace-current.md").unlink()

        proc = _run("--verify", str(snap), harness_dir=populated)
        assert proc.returncode == 1
        assert "trace-current.md" in proc.stdout

    def test_verify_detects_new_trace(self, populated, tmp_path):
        """traces/** entra no nivel A: um arquivo novo tambem e divergencia."""
        snap = tmp_path / "snap.json"
        _run("--snapshot", str(snap), harness_dir=populated)
        (populated / "traces" / "2026-07-25-1000.md").write_text("novo\n", encoding="utf-8")

        proc = _run("--verify", str(snap), harness_dir=populated)
        assert proc.returncode == 1
        assert "2026-07-25-1000" in proc.stdout


class TestProtectionLevels:
    def test_level_a_excludes_volatile_by_default(self, populated, tmp_path):
        """.session-files-count e nivel B: mudanca nao falha o gate padrao.

        E escrito pelo PostToolUse a cada Edit/Write do usuario; sob sessao
        ativa muda por atividade legitima, sem relacao com os testes.
        """
        snap = tmp_path / "snap.json"
        _run("--snapshot", str(snap), harness_dir=populated)
        (populated / ".session-files-count").write_text('{"count": 99}', encoding="utf-8")

        proc = _run("--verify", str(snap), harness_dir=populated)
        assert proc.returncode == 0, (
            "nivel B nao deveria derrubar o gate padrao.\n" + proc.stdout
        )

    def test_include_volatile_catches_it(self, populated, tmp_path):
        """Com --include-volatile (sessao quiescente), o nivel B e verificado."""
        snap = tmp_path / "snap.json"
        _run("--snapshot", str(snap), "--include-volatile", harness_dir=populated)
        (populated / ".session-files-count").write_text('{"count": 99}', encoding="utf-8")

        proc = _run("--verify", str(snap), "--include-volatile", harness_dir=populated)
        assert proc.returncode == 1
        assert "session-files-count" in proc.stdout

    def test_derived_cache_is_never_checked(self, populated, tmp_path):
        """router/ e skills-index/ ficam fora do conjunto em qualquer nivel."""
        (populated / "router").mkdir()
        (populated / "router" / "session-x.json").write_text("{}", encoding="utf-8")
        snap = tmp_path / "snap.json"
        _run("--snapshot", str(snap), "--include-volatile", harness_dir=populated)
        (populated / "router" / "session-x.json").write_text('{"mudou": 1}', encoding="utf-8")

        proc = _run("--verify", str(snap), "--include-volatile", harness_dir=populated)
        assert proc.returncode == 0, "cache derivado nao pertence ao conjunto protegido"


class TestSnapshotFormat:
    def test_snapshot_is_readable_json_with_provenance(self, populated, tmp_path):
        snap = tmp_path / "snap.json"
        _run("--snapshot", str(snap), harness_dir=populated)
        data = json.loads(snap.read_text(encoding="utf-8"))
        assert "harness_dir" in data, "o snapshot deve registrar o dir inspecionado"
        assert data.get("files"), "deve conter os hashes"
        assert "level" in data, "deve registrar o nivel (A ou A+B)"
