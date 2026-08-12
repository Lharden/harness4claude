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


@pytest.fixture
def disjuntor_isolado(tmp_path, monkeypatch):
    """Isola o disjuntor da Camada B do estado de producao.

    Sem isto o teste tinha dois defeitos, ambos observados em 2026-08-12:

    1. **Auto-envenenamento.** Tres embeds lentos seguidos abrem o disjuntor, e o
       restante das perguntas do MESMO teste pula a Camada B — o hit rate despenca
       para 53%, que e o valor da Camada A sozinha. A medicao passava a depender da
       carga da maquina, nao da acuracia do router.
    2. **Efeito colateral no usuario.** O estado ficava em
       `~/.claude/harness/router/`, entao rodar a suite silenciava o router real por
       `BREAKER_COOLDOWN_S` (15 min).

    O disjuntor tem cobertura propria em `test_router_breaker.py`. Aqui se mede
    acuracia; misturar as duas coisas nao testa nenhuma das duas direito.
    """
    monkeypatch.setattr(sr, "ROUTER_DIR", str(tmp_path))
    return tmp_path


@pytest.mark.integration
@pytest.mark.touches_real
@needs_stack
def test_golden_top3_hit_rate(disjuntor_isolado):
    """Gate de acuracia do router. REQ-F10: fora do gate hermetico.

    Depende de dois recursos externos por definicao — o indice real de skills e o
    Ollama com o modelo de embedding. `touches_real` porque `skill_router.IDX_DIR` e
    resolvido no import, apontando para o diretorio real; isolar HARNESS_DIR aqui nao
    teria efeito e apenas mascararia a dependencia. O disjuntor, esse sim, e isolado —
    ver a fixture. Ver docs/self-reform/claude/TEST_MATRIX.md.
    """
    with open(GOLDEN, encoding="utf-8") as f:
        data = json.load(f)
    index, vecs = sr.load_index()
    skills = index["skills"]
    known = {s["id"] for s in skills}
    hits, details = 0, []
    for case in data["positives"]:
        expect = [e for e in case["expect_any"] if e in known]
        assert expect, f"nenhum id esperado existe no indice: {case['expect_any']}"
        # Zera o disjuntor por pergunta: um embed lento nao pode derrubar as seguintes.
        sr.write_breaker({"failures": 0, "opened_at": 0.0, "last_msg": "", "last_msg_ts": 0.0})
        top = [h["id"] for h in sr.route(case["prompt"], skills, vecs)]
        ok = any(e in top for e in expect)
        hits += ok
        details.append(f"{'OK ' if ok else 'MISS'} {case['prompt'][:50]} -> {top}")
    rate = hits / len(data["positives"])
    print("\n".join(details))
    assert rate >= 0.80, f"top-3 hit rate {rate:.0%} < 80%"


def test_golden_negatives_no_injection():
    sem_estado = os.path.join(os.path.dirname(GOLDEN), "no-state.json")
    for case in data_negatives():
        assert sr.passes_guards(case["prompt"], state_json=sem_estado) is False, case["reason"]


def data_negatives():
    with open(GOLDEN, encoding="utf-8") as f:
        return json.load(f)["negatives"]


@pytest.mark.integration
def test_golden_nao_escreve_no_disjuntor_de_producao(disjuntor_isolado, tmp_path):
    """Regressor do efeito colateral: rodar a suite nao pode silenciar o router do usuario.

    Se a fixture parar de isolar, este teste passa a escrever em
    ~/.claude/harness/router/ e o disjuntor real abre por 15 minutos.
    """
    sr.write_breaker({"failures": 2, "opened_at": 0.0, "last_msg": "", "last_msg_ts": 0.0})

    assert (tmp_path / "layer-b-breaker.json").is_file()
    assert sr.read_breaker(str(tmp_path))["failures"] == 2
    assert str(tmp_path) == sr.ROUTER_DIR, "ROUTER_DIR nao foi isolado"
