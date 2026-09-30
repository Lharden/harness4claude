"""O `pass` do wf-verify-multimodel so sai verde sem achado aberto.

Spec: docs/specs/verify-adjudica-tudo-spec.md (US-1, US-3). Medido em
docs/specs/verify-adjudica-tudo-medicao.md: tres rodadas com `pass: true` e 49
achados medium/low abertos, 25 deles defeito real. O `pass` contava so
critico/alto confirmado, e ninguem rotulou nada de critico/alto.

Roda o corpo real do Workflow via `tests/wf_run_harness.cjs`, com os
primitivos do host mockados.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
WF_FILE = ROOT / "scripts" / "workflows" / "wf-verify-multimodel.js"
TESTS_DIR = Path(__file__).parent
HARNESS = TESTS_DIR / "wf_run_harness.cjs"

NODE = shutil.which("node")


def _run(wf_file: Path, scenario: dict, tmp_path: Path) -> dict:
    assert NODE is not None  # a fixture abaixo pula o teste antes, sem node
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


def _achado(titulo: str, severidade: str, arquivo: str = "a.py", linha: int = 1) -> dict:
    return {"title": titulo, "severity": severidade, "file": arquivo, "line": linha, "rationale": "racional"}


def _cenario(**por_dimensao) -> dict:
    """Cenario com as cinco dimensoes; as nao citadas voltam sem achado."""
    reviews = {d: {"findings": []} for d in ("spec-coverage", "correctness", "security", "edge-cases", "regressions")}
    for dim, achados in por_dimensao.items():
        reviews[dim.replace("_", "-")] = {"findings": achados}
    return {"reviews": reviews}


# ---------------------------------------------------------------------------
# T1: o harness grava o prompt, senao nenhum teste de prompt tem o que ler
# ---------------------------------------------------------------------------


def test_harness_grava_o_prompt_de_revisao_e_de_adjudicacao(tmp_path):
    out = _run(WF_FILE, _cenario(correctness=[_achado("bug real", "critical")]), tmp_path)
    assert len(out["calls"]["review"]) == 5
    for call in out["calls"]["review"]:
        assert isinstance(call.get("prompt"), str) and call["prompt"], call["label"]
    assert len(out["calls"]["adjudicate"]) == 1
    assert "REFUT" in out["calls"]["adjudicate"][0]["prompt"]


# ---------------------------------------------------------------------------
# US-1: `pass` honesto
# ---------------------------------------------------------------------------

SO_MEDIUM_LOW = _cenario(
    correctness=[_achado("nome de variavel poderia ser melhor", "low", "a.py", 10)],
    edge_cases=[_achado("lista vazia nao testada explicitamente", "medium", "b.py", 20)],
)


def test_lote_so_medium_low_nao_aprova(tmp_path):
    """AC-1.1. As tres rodadas medidas tinham 11-23 achados assim, e `pass: true`."""
    out = _run(WF_FILE, SO_MEDIUM_LOW, tmp_path)
    assert out["result"]["pass"] is False
    assert out["result"]["critical_count"] == 0
    assert out["result"]["nao_adjudicados"] == 2


def test_sem_achado_aprova(tmp_path):
    """AC-1.3."""
    out = _run(WF_FILE, _cenario(), tmp_path)
    assert out["result"]["pass"] is True


def test_high_refutado_aprova(tmp_path):
    """AC-1.4. Refutado nao bloqueia nem vira pendente: o `pass` ainda sai verde."""
    cenario = _cenario(security=[_achado("injecao alegada", "high", "c.py", 3)])
    cenario["verdicts"] = {"c.py": {"is_real": False, "confidence": 0.9, "reason": "refutado"}}
    out = _run(WF_FILE, cenario, tmp_path)
    assert out["result"]["pass"] is True
    assert out["result"]["nao_adjudicados"] == 0


def test_cada_achado_traz_n_da_posicao(tmp_path):
    """AC-1.6. A coluna `#` da tabela de destino aponta esse numero."""
    cenario = _cenario(
        correctness=[_achado("bug confirmado", "high", "d.py", 1)],
        edge_cases=[_achado("borda um", "medium", "e.py", 2), _achado("borda dois", "low", "f.py", 3)],
    )
    out = _run(WF_FILE, cenario, tmp_path)
    assert [f["n"] for f in out["result"]["findings"]] == [1, 2, 3]


def test_lote_misto_bloqueia_pelos_dois_motivos(tmp_path):
    """Edge case de US-1 (achado #2 do verify da task): high confirmado junto com
    medium/low. Bloqueio e triagem aparecem os dois no retorno."""
    cenario = _cenario(
        correctness=[_achado("bug confirmado", "high", "d.py", 1)],
        edge_cases=[_achado("borda um", "medium", "e.py", 2), _achado("borda dois", "low", "f.py", 3)],
    )
    out = _run(WF_FILE, cenario, tmp_path)
    assert out["result"]["pass"] is False
    assert out["result"]["critical_count"] == 1
    assert out["result"]["nao_adjudicados"] == 2


def test_rodada_sem_achado_traz_nao_adjudicados_zero(tmp_path):
    """Achado #16: o retorno antecipado nao tinha a chave que o contrato documenta."""
    out = _run(WF_FILE, _cenario(), tmp_path)
    assert out["result"]["nao_adjudicados"] == 0


def test_repo_root_sem_lista_de_arquivos_nao_contradiz_o_git_diff(tmp_path):
    """Achado #14: "o escopo sao os arquivos listados" sem lista nenhuma, ao lado
    de "use git diff <base>", era instrucao contraditoria."""
    cenario = _cenario()
    cenario["args"] = {"repo_root": "C:/wt/x", "base_ref": "abc123"}
    out = _run(WF_FILE, cenario, tmp_path)
    for c in out["calls"]["review"]:
        assert "Repositorio: C:/wt/x" in c["prompt"]
        assert "arquivos listados" not in c["prompt"]
        assert "git diff abc123" in c["prompt"]


def test_adjudicador_morto_bloqueia(tmp_path):
    """Rodada 2 do verify da task, achados #1/#2 (critical/high, confirmados): o
    `.then` embrulhava o veredito null num objeto sempre verdadeiro, o censo nao
    via o no morto, o achado sumia e o `pass` saia verde."""
    cenario = _cenario(correctness=[_achado("bug critico nao julgado", "critical", "a.py", 1)])
    cenario["verdicts"] = {"a.py": None}
    out = _run(WF_FILE, cenario, tmp_path)
    assert out["result"]["pass"] is False
    assert "bug critico nao julgado" in out["result"]["nos_mortos"]


def test_dedupe_nao_junta_titulos_diferentes_com_o_mesmo_prefixo(tmp_path):
    """Rodada 2, achado #7: a chave usava os 40 primeiros caracteres do titulo, e o
    segundo achado do mesmo arquivo sumia sem `n` e sem destino."""
    prefixo = "AC-2.1 sem teste correspondente em checar_triagem"
    cenario = _cenario(
        spec_coverage=[
            _achado(f"{prefixo} pelo lado A", "low", "a.py", 1),
            _achado(f"{prefixo} pelo lado B", "low", "a.py", 2),
        ]
    )
    out = _run(WF_FILE, cenario, tmp_path)
    assert out["result"]["nao_adjudicados"] == 2


def test_dedupe_de_duplicata_exata_fica_com_a_maior_severidade(tmp_path):
    """Mesmo achado de duas dimensoes com rotulos diferentes: o mais grave vai ao adjudicador."""
    cenario = _cenario(
        correctness=[_achado("mesmo defeito visto duas vezes", "medium", "a.py", 5)],
        security=[_achado("mesmo defeito visto duas vezes", "high", "a.py", 5)],
    )
    out = _run(WF_FILE, cenario, tmp_path)
    assert len(out["calls"]["adjudicate"]) == 1
    assert len(out["result"]["findings"]) == 1
    assert out["result"]["findings"][0]["severity"] == "high"


def test_repo_root_so_com_espacos_e_ignorado(tmp_path):
    """Rodada 2, achado #11: '   ' virava 'Repositorio:  .' em todo prompt."""
    cenario = _cenario(correctness=[_achado("bug real", "critical")])
    cenario["args"] = {"changed_files": ["a.py"], "repo_root": "   "}
    out = _run(WF_FILE, cenario, tmp_path)
    prompts = [c["prompt"] for c in out["calls"]["review"]] + [c["prompt"] for c in out["calls"]["adjudicate"]]
    assert all("Repositorio:" not in p for p in prompts)


def test_summary_nomeia_o_checador_e_a_secao(tmp_path):
    """AC-1.7. Sessao com o texto antigo da skill le no retorno o que fazer."""
    summary = _run(WF_FILE, SO_MEDIUM_LOW, tmp_path)["result"]["summary"]
    assert "2 aguardam triagem" in summary
    assert "checar_triagem.py" in summary
    assert "harness-workflow" in summary


def test_severidade_fora_do_conjunto_conta_como_nao_adjudicada(tmp_path):
    """AC-1.8. Erra bloqueando: severidade estranha nao some do `pass`."""
    out = _run(WF_FILE, _cenario(regressions=[_achado("rotulo estranho", "info")]), tmp_path)
    assert out["calls"]["adjudicate"] == []
    assert out["result"]["nao_adjudicados"] == 1
    assert out["result"]["pass"] is False


def test_adjudicador_nao_refuta_por_severidade(tmp_path):
    """AC-1.9 / REQ-F6. Refutado sai do retorno: um refutador que derrubasse um
    defeito real por "nao e high" o faria sumir — nem bloqueia nem vai a
    triagem (grill #24, #33)."""
    out = _run(WF_FILE, _cenario(correctness=[_achado("bug real", "high")]), tmp_path)
    assert "nao e motivo para refutar" in out["calls"]["adjudicate"][0]["prompt"]


def test_repo_root_chega_a_todo_prompt(tmp_path):
    """REQ-F5. Sem isto os agentes leem o cwd da sessao, que na medicao e o
    worktree da mae com os consertos ja aplicados (ASSUMPTION-004)."""
    cenario = _cenario(correctness=[_achado("bug real", "critical")])
    cenario["args"] = {"changed_files": ["scripts/x.py"], "repo_root": "C:/wt/medicao"}
    out = _run(WF_FILE, cenario, tmp_path)
    prompts = [c["prompt"] for c in out["calls"]["review"]] + [c["prompt"] for c in out["calls"]["adjudicate"]]
    assert len(prompts) == 6
    for p in prompts:
        assert "Repositorio: C:/wt/medicao" in p
        assert "nao compare com outro ramo" in p


def test_sem_repo_root_o_prompt_nao_fala_de_repositorio(tmp_path):
    """REQ-F5, metade sem o argumento."""
    out = _run(WF_FILE, _cenario(correctness=[_achado("bug real", "critical")]), tmp_path)
    prompts = [c["prompt"] for c in out["calls"]["review"]] + [c["prompt"] for c in out["calls"]["adjudicate"]]
    assert all("Repositorio:" not in p for p in prompts)


# ---------------------------------------------------------------------------
# US-3 (rubrica de severidade) NAO entrou: a medicao de US-4 deu 1 de 7 defeitos
# conhecidos re-achados em high/critical, abaixo da metade de L-06 (secao 7 de
# docs/specs/verify-adjudica-tudo-medicao.md). A constante e os testes de
# AC-3.1/3.2/3.4 sairam no mesmo commit (REQ-F12); AC-3.3 fica.
# ---------------------------------------------------------------------------


def test_adjudicador_nao_recebe_o_racional(tmp_path):
    """AC-3.3 / REQ-NF2, pelo prompt que o harness gravou (o teste estatico de
    test_workflow_returns le o texto do arquivo). A aresta do refutador carrega
    alegacao, local e criterio."""
    out = _run(WF_FILE, _cenario(correctness=[_achado("bug real", "critical")]), tmp_path)
    assert "racional" not in out["calls"]["adjudicate"][0]["prompt"]


# ---------------------------------------------------------------------------
# Metade-controle: o mesmo cenario no codigo de antes desta task
# ---------------------------------------------------------------------------

# Blob de scripts/workflows/wf-verify-multimodel.js em 8cd6884 (base do ramo),
# fixado por hash de blob do git pelo mesmo motivo de test_verify_por_tabela.py:
# `git show main:` reprova por construcao depois do merge, e o CI faz checkout raso.
OLD_FILE = TESTS_DIR / "data" / "wf-verify-multimodel-antes-de-verify-adjudica-tudo.js"
OLD_BLOB = "248189ecd315a5e1110e4b1705aa6376c526c931"


def _git_blob_sha1(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()


def _old_wf_file() -> Path:
    blob = _git_blob_sha1(OLD_FILE.read_bytes())
    assert blob == OLD_BLOB, (
        f"{OLD_FILE.name} virou o blob {blob}, nao e mais o {OLD_BLOB} de 8cd6884. Restaure (Git Bash, na raiz):\n"
        f"  git cat-file blob {OLD_BLOB} > tests/data/{OLD_FILE.name}"
    )
    return OLD_FILE


def test_CONTROLE_codigo_de_antes_aprovava_lote_so_medium_low(tmp_path):
    """AC-1.2. Sem esta metade, `test_lote_so_medium_low_nao_aprova` nao
    distingue a mudanca de um cenario que ja reprovava."""
    out = _run(_old_wf_file(), SO_MEDIUM_LOW, tmp_path)
    assert out["result"]["pass"] is True
    assert out["result"]["nao_adjudicados"] == 2


def test_CONTROLE_codigo_de_antes_apagava_o_critico_com_adjudicador_morto(tmp_path):
    """Metade de `test_adjudicador_morto_bloqueia`: o defeito e anterior ao ramo."""
    cenario = _cenario(correctness=[_achado("bug critico nao julgado", "critical", "a.py", 1)])
    cenario["verdicts"] = {"a.py": None}
    out = _run(_old_wf_file(), cenario, tmp_path)
    assert out["result"]["pass"] is True
    assert out["result"]["nos_mortos"] == []

