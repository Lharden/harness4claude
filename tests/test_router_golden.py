"""Avaliacao de acuracia no golden set. Requer indice real + Ollama; pula se ausentes."""
import json
import os
import sys
import urllib.request

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hooks"))
import skill_router as sr

GOLDEN = os.path.join(os.path.dirname(__file__), "data", "golden-prompts.json")


def _ollama_up():
    try:
        with urllib.request.urlopen(sr.OLLAMA_URL + "/api/tags", timeout=2) as r:
            return sr.EMBED_MODEL in r.read().decode()
    except Exception:
        return False


needs_stack = pytest.mark.skipif(
    not (os.path.isfile(os.path.join(sr.IDX_DIR, "skills-index.json")) and _ollama_up()),
    reason="indice real ou Ollama indisponivel")


@needs_stack
def test_golden_top3_hit_rate():
    data = json.load(open(GOLDEN, encoding="utf-8"))
    index, vecs = sr.load_index()
    skills = index["skills"]
    known = {s["id"] for s in skills}
    hits, details = 0, []
    for case in data["positives"]:
        expect = [e for e in case["expect_any"] if e in known]
        assert expect, f"nenhum id esperado existe no indice: {case['expect_any']}"
        a = sr.layer_a(case["prompt"].lower(), skills)
        b = sr.layer_b(sr.embed_query(case["prompt"]), skills, vecs)
        top = [h["id"] for h in sr.pick(a, b)]
        ok = any(e in top for e in expect)
        hits += ok
        details.append(f"{'OK ' if ok else 'MISS'} {case['prompt'][:50]} -> {top}")
    rate = hits / len(data["positives"])
    print("\n".join(details))
    assert rate >= 0.80, f"top-3 hit rate {rate:.0%} < 80%"


@needs_stack
def test_golden_negatives_no_injection():
    for case in data_negatives():
        assert sr.passes_guards(case["prompt"],
                                state_json=os.path.join(os.path.dirname(GOLDEN), "no-state.json")) is False, case["reason"]


def data_negatives():
    return json.load(open(GOLDEN, encoding="utf-8"))["negatives"]
