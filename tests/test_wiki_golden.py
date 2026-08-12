"""Acuracia da operação query no golden set da wiki.

Espelha tests/test_router_golden.py: requer índice real + Ollama, pula se ausentes.
Dois gates, um por banda:
  - positives: a página alvo no top-3 em >= 80% dos casos (banda "vale mostrar")
  - negatives: zero hits **confiantes** — pergunta fora do domínio não pode fazer a
    wiki afirmar cobertura (banda "vale afirmar")
"""

import json
import os
import sys
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))
from tools import wiki_query as wq

GOLDEN = Path(__file__).parent / "data" / "golden-wiki.json"
TOP_K = 3
HIT_RATE_GATE = 0.80


def _ollama_up() -> bool:
    try:
        with urllib.request.urlopen(wq.OLLAMA_URL + "/api/tags", timeout=2) as response:
            return wq.EMBED_MODEL in response.read().decode()
    except Exception:
        return False


needs_stack = pytest.mark.skipif(
    not (os.path.isfile(wq.DEFAULT_INDEX / "wiki-index.json") and _ollama_up()),
    reason="wiki-index real ou Ollama indisponivel",
)


def _golden() -> dict:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


_chaves = wq.chaves_de_citacao


def test_golden_bem_formado() -> None:
    """O golden set e artefato versionado: não pode degradar sem alguém perceber."""
    golden = _golden()

    assert len(golden["positives"]) >= 15
    assert len(golden["negatives"]) >= 3
    for case in golden["positives"]:
        assert case["prompt"] and case["expect_any"]
    for case in golden["negatives"]:
        assert case["prompt"] and case["reason"]


@needs_stack
def test_alvos_do_golden_existem_no_indice() -> None:
    """Alvo renomeado ou apagado inutiliza o caso — falhar aqui, não no hit rate."""
    index, _ = wq.load_index()
    conhecidos = {chunk["page_id"] for chunk in index["pages"]}
    conhecidos |= {chunk["id"] for chunk in index["pages"]}

    orfaos = [
        case["expect_any"]
        for case in _golden()["positives"]
        if not any(alvo in conhecidos for alvo in case["expect_any"])
    ]

    assert not orfaos, f"casos sem nenhum alvo no indice: {orfaos}"


@needs_stack
def test_positives_top3_hit_rate() -> None:
    golden = _golden()
    detalhes, acertos = [], 0
    for case in golden["positives"]:
        hits = wq.query(case["prompt"], top_k=TOP_K)["hits"]
        top = [sorted(_chaves(hit))[-1] for hit in hits]
        alcance = set().union(*(_chaves(hit) for hit in hits)) if hits else set()
        ok = any(alvo in alcance for alvo in case["expect_any"])
        acertos += ok
        detalhes.append(f"{'OK  ' if ok else 'MISS'} {case['prompt'][:48]} -> {top}")
    taxa = acertos / len(golden["positives"])
    print("\n".join(detalhes))
    assert taxa >= HIT_RATE_GATE, f"top-{TOP_K} hit rate {taxa:.0%} < {HIT_RATE_GATE:.0%}"


@needs_stack
def test_negatives_nao_produzem_hit_confiante() -> None:
    for case in _golden()["negatives"]:
        resultado = wq.query(case["prompt"], top_k=TOP_K)
        confiantes = [hit for hit in resultado["hits"] if hit["confident"]]
        assert not confiantes, (
            f"{case['reason']}: {case['prompt'][:48]!r} devolveu hit confiante {confiantes}"
        )
