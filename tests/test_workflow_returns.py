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


def test_todo_fan_out_tem_censo_de_nos():
    """Agente morto nao pode sumir em silencio.

    `filter(Boolean)` descarta no morto sem avisar: num fan-out o relatorio sai
    com cara de completo. Todo script que abre `parallel(` precisa contar os
    retornos contra o esperado. Absorvido de graph-engineering-claude (2026-08-19).
    """
    for wf in WF_DIR.glob("*.js"):
        content = wf.read_text(encoding="utf-8")
        if "parallel(" not in content:
            continue
        assert "censoNos(" in content, f"{wf.name} abre fan-out sem censo de nos"


def _prompt_de_refutacao(content: str) -> list[str]:
    """Devolve o texto do argumento-prompt de cada `agent()` que manda refutar.

    O prompt e uma concatenacao de template literals entre `agent(` e o objeto de
    opcoes que comeca em `{ label:`. Recortar ate ali isola o que o refutador le,
    sem varrer o resto do arquivo (onde `rationale` aparece legitimamente, no
    retorno para o humano).
    """
    trechos = []
    inicio = 0
    while True:
        i = content.find("REFUT", inicio)
        if i == -1:
            return trechos
        abre = content.rfind("agent(", 0, i)
        fecha = content.find("{ label:", i)
        if abre == -1:
            abre = max(0, i - 400)
        if fecha == -1:
            fecha = min(len(content), i + 1200)
        trechos.append(content[abre:fecha])
        inicio = i + 5


def test_adjudicador_nao_recebe_rationale():
    """A aresta do refutador carrega a alegacao, nunca o raciocinio de quem a fez.

    O no adversarial so vale porque a janela dele e nova. Injetar a justificativa
    do produtor recorrela as duas pontas e devolve o vies de confirmacao que o
    fan-out existe para eliminar. Absorvido de @mstockton (2026-08-26); mesma
    familia do censo de nos — defeito que o fan-out introduz e cujo retorno
    continua parecendo completo.
    """
    for wf in WF_DIR.glob("*.js"):
        content = wf.read_text(encoding="utf-8")
        for trecho in _prompt_de_refutacao(content):
            assert "rationale" not in trecho, (
                f"{wf.name}: prompt de refutacao interpola o rationale do produtor"
            )
            assert "ustificativa" not in trecho, (
                f"{wf.name}: prompt de refutacao entrega a justificativa do produtor"
            )


def test_verify_nao_aprova_com_cobertura_incompleta():
    """`pass: true` nunca sai de um review em que uma dimensao morreu."""
    content = (WF_DIR / "wf-verify-multimodel.js").read_text(encoding="utf-8")
    assert "nos_mortos" in content
    assert "nosMortos.length === 0" in content, "pass nao esta amarrado ao censo"
    assert "pass: true," not in content, "ainda existe pass:true incondicional"
