"""Testes de comportamento para o roteamento por tabela do wf-verify-multimodel.js.

Complementa test_workflow_returns.py (que so verifica sintaxe/estrutura) com
execucao real do Workflow script via `tests/wf_run_harness.cjs`: o corpo do
script roda de verdade, com `agent`/`parallel`/`phase`/`log`/`args` mockados,
para provar comportamento (agentType usado, quem foi adjudicado, o `pass`
resultante) em vez de so procurar texto no arquivo.

Regra de origem (CLAUDE.md global do usuario, "Roteamento de modelo e
esforco"): "Fan-out (Workflow): cada `agent()` com o tipo da tabela; revisao
ampla adjudica so achados criticos e altos."
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
WF_DIR = ROOT / "scripts" / "workflows"
WF_FILE = WF_DIR / "wf-verify-multimodel.js"
TESTS_DIR = Path(__file__).parent
HARNESS = TESTS_DIR / "wf_run_harness.cjs"

# Tabela esperada (docs/specs/verify-por-tabela-spec-light.md)
EXPECTED_AGENT_TYPES = {
    "spec-coverage": "analise-complexa",
    "correctness": "analise-complexa",
    "security": "juiz-alto-risco",
    "edge-cases": "analise-complexa",
    "regressions": "analise-complexa",
}

NODE = shutil.which("node")


def _run(wf_file: Path, scenario: dict, tmp_path: Path) -> dict:
    scenario_file = tmp_path / "scenario.json"
    scenario_file.write_text(json.dumps(scenario), encoding="utf-8")
    result = subprocess.run(
        [NODE, str(HARNESS), str(wf_file), str(scenario_file)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, f"harness falhou:\n{result.stdout}\n{result.stderr}"
    return json.loads(result.stdout)


@pytest.fixture(autouse=True)
def _skip_sem_node():
    if not NODE:
        pytest.skip("node nao disponivel no PATH")


SOMENTE_MEDIUM_LOW = {
    "reviews": {
        "spec-coverage": {"findings": []},
        "correctness": {
            "findings": [
                {
                    "title": "nome de variavel poderia ser melhor",
                    "severity": "low",
                    "file": "a.py",
                    "line": 10,
                    "rationale": "estilo",
                }
            ]
        },
        "security": {"findings": []},
        "edge-cases": {
            "findings": [
                {
                    "title": "lista vazia nao testada explicitamente",
                    "severity": "medium",
                    "file": "b.py",
                    "line": 20,
                    "rationale": "cobertura parcial",
                }
            ]
        },
        "regressions": {"findings": []},
    }
}


def test_cada_dimensao_de_review_usa_o_agentType_da_tabela(tmp_path):
    out = _run(WF_FILE, {"reviews": {}}, tmp_path)
    by_key = {}
    for call in out["calls"]["review"]:
        key = call["label"].split("review:", 1)[1]
        by_key[key] = call["agentType"]
    assert by_key == EXPECTED_AGENT_TYPES


def test_adjudicador_usa_juiz_alto_risco(tmp_path):
    scenario = {
        "reviews": {
            "spec-coverage": {"findings": []},
            "correctness": {
                "findings": [
                    {
                        "title": "bug real",
                        "severity": "critical",
                        "file": "a.py",
                        "line": 1,
                        "rationale": "racional do produtor",
                    }
                ]
            },
            "security": {"findings": []},
            "edge-cases": {"findings": []},
            "regressions": {"findings": []},
        },
        "verdicts": {"a.py": {"is_real": True, "confidence": 0.9, "reason": "ok"}},
    }
    out = _run(WF_FILE, scenario, tmp_path)
    assert len(out["calls"]["adjudicate"]) == 1
    assert out["calls"]["adjudicate"][0]["agentType"] == "juiz-alto-risco"


def test_medium_low_nunca_geram_agent_de_adjudicacao(tmp_path):
    out = _run(WF_FILE, SOMENTE_MEDIUM_LOW, tmp_path)
    assert out["calls"]["adjudicate"] == [], (
        "finding medium/low nao pode abrir agent() de adjudicacao"
    )
    assert out["result"]["nao_adjudicados"] == 2
    adjudicados = {f["title"]: f["adjudicado"] for f in out["result"]["findings"]}
    assert adjudicados == {
        "nome de variavel poderia ser melhor": False,
        "lista vazia nao testada explicitamente": False,
    }


def test_pass_invariante_lote_so_medium_low(tmp_path):
    """Um lote so com medium/low (sem critico/alto, sem no morto) aprova,
    exatamente como aprovava antes desta mudanca (0 findings bloqueantes)."""
    out = _run(WF_FILE, SOMENTE_MEDIUM_LOW, tmp_path)
    assert out["result"]["pass"] is True
    assert out["result"]["critical_count"] == 0


def test_no_morto_ainda_forca_pass_false(tmp_path):
    scenario = {
        "reviews": {
            "spec-coverage": {"findings": []},
            "correctness": {"findings": []},
            "security": None,  # no morto: agent() nao retornou nada
            "edge-cases": {
                "findings": [
                    {
                        "title": "so um finding baixo, sem nada bloqueante",
                        "severity": "low",
                        "file": "c.py",
                        "line": 5,
                        "rationale": "x",
                    }
                ]
            },
            "regressions": {"findings": []},
        }
    }
    out = _run(WF_FILE, scenario, tmp_path)
    assert out["result"]["pass"] is False
    assert "security" in out["result"]["nos_mortos"]


# ---------------------------------------------------------------------------
# Duas metades: o mesmo cenario roda contra o codigo ANTIGO (antes desta
# mudanca) via git show, e o teste tem que reprovar la para provar que nao e
# tautologia.
# ---------------------------------------------------------------------------

# Pai de d017a6b (o commit que introduziu o roteamento por tabela). Fixado por
# SHA, nunca por branch: `main` passou a conter o codigo novo no merge c175fd9 e
# o controle reprovava por construcao.
OLD_REF = "9487f48b2689247d9c8e86f756b864d281c86dca"


def _old_wf_source() -> str:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "show", f"{OLD_REF}:scripts/workflows/wf-verify-multimodel.js"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, f"git show falhou:\n{result.stderr}"
    return result.stdout


def test_CONTROLE_codigo_antigo_adjudicava_medium_low(tmp_path):
    """Metade 1: no codigo de OLD_REF (antes desta tarefa), medium/low SEMPRE
    iam para adjudicacao — prova que o teste acima nao e tautologia."""
    old_file = tmp_path / "wf-verify-multimodel-old.js"
    old_file.write_text(_old_wf_source(), encoding="utf-8")
    out = _run(old_file, SOMENTE_MEDIUM_LOW, tmp_path)
    assert len(out["calls"]["adjudicate"]) == 2, (
        "esperava que o codigo antigo adjudicasse os 2 findings medium/low "
        "(comportamento que esta tarefa remove)"
    )
    # o codigo antigo nao tem `agentType` nem `nao_adjudicados`
    assert "nao_adjudicados" not in out["result"]
    for call in out["calls"]["review"]:
        assert call.get("agentType") is None, (
            "codigo antigo nao deveria passar agentType — se passar, o "
            "arquivo em OLD_REF nao e o anterior a d017a6b e este controle "
            "precisa ser revisto"
        )
