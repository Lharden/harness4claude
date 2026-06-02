"""Testes para os Workflow scripts do Harness (Bloco D).

Garante:
- os Workflow scripts existem
- todos passam pelo validador de sintaxe + meta (validate_workflows.cjs via node)
- cada um declara `export const meta` com name/description/phases
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
WF_DIR = ROOT / "scripts" / "workflows"
VALIDATOR = WF_DIR / "validate_workflows.cjs"


def test_workflows_exist():
    assert (WF_DIR / "wf-verify-multimodel.js").is_file()
    assert (WF_DIR / "wf-context-scan.js").is_file()


def test_validator_passes():
    node = shutil.which("node")
    if not node:
        pytest.skip("node nao disponivel no PATH")
    result = subprocess.run(
        [node, str(VALIDATOR)], capture_output=True, text=True, encoding="utf-8"
    )
    assert result.returncode == 0, f"validador falhou:\n{result.stdout}\n{result.stderr}"
    assert "ERRO" not in result.stdout


def test_each_workflow_has_meta():
    workflows = list(WF_DIR.glob("*.js"))
    assert workflows, "nenhum Workflow .js encontrado"
    for wf in workflows:
        content = wf.read_text(encoding="utf-8")
        assert "export const meta" in content, f"{wf.name} sem 'export const meta'"
        for field in ("name:", "description:", "phases:"):
            assert field in content, f"{wf.name} meta sem campo '{field}'"
