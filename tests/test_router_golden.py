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
    hits, details, degradados = 0, [], []
    for case in data["positives"]:
        expect = [e for e in case["expect_any"] if e in known]
        assert expect, f"nenhum id esperado existe no indice: {case['expect_any']}"
        # Zera o disjuntor por pergunta: um embed lento nao pode derrubar as seguintes.
        sr.write_breaker({"failures": 0, "opened_at": 0.0, "last_msg": "", "last_msg_ts": 0.0})
        top = [h["id"] for h in sr.route(case["prompt"], skills, vecs)]
        # A Camada B registra falha no disjuntor quando o embed estoura o timeout
        # (skill_router.py, "layer B degraded"). Como o disjuntor foi zerado logo
        # acima, failures>0 aqui significa: ESTA pergunta rodou sem a Camada B.
        if (sr.read_breaker().get("failures") or 0) > 0:
            degradados.append(case["prompt"][:40])
        ok = any(e in top for e in expect)
        hits += ok
        details.append(f"{'OK ' if ok else 'MISS'} {case['prompt'][:50]} -> {top}")
    rate = hits / len(data["positives"])
    print("\n".join(details))

    # Medicao contaminada nao passa NEM reprova.
    #
    # Este teste chama o Ollama uma vez por pergunta com EMBED_TIMEOUT=3.0. Numa
    # suite de seis minutos com outros testes de integracao, o Ollama fica
    # disputado, o embed estoura e a Camada B degrada — o hit rate cai para o
    # valor da Camada A sozinha, sem que o router tenha piorado em nada.
    #
    # O sintoma custou tres diagnosticos errados em 2026-08-13: indice stale,
    # depois alvos dispensados (esse era real e foi corrigido), depois nada.
    # Passava isolado e falhava na suite, e reprovar nas duas situacoes treinava
    # a ler a falha como ruido — que e exatamente como um gate morre.
    #
    # Reprovar aqui afirmaria "o router piorou" sem poder distinguir isso de "a
    # maquina estava ocupada". Skip diz a verdade: nao deu para medir.
    if degradados:
        pytest.skip(
            f"medicao contaminada: a Camada B degradou em {len(degradados)} de "
            f"{len(data['positives'])} perguntas (embed estourou o timeout). "
            f"hit rate observado {rate:.0%}, mas ele mede carga da maquina, nao "
            f"acuracia do router. Rode isolado: pytest {os.path.basename(__file__)}"
        )
    assert rate >= 0.80, f"top-3 hit rate {rate:.0%} < 80%"


@pytest.mark.integration
@needs_stack
def test_golden_detecta_contaminacao_em_vez_de_reprovar(disjuntor_isolado, monkeypatch):
    """Red-green do skip: com a Camada B degradada, o teste NAO pode reprovar.

    Sem este caso, a decisao de pular seria invisivel — e a proxima vez que a
    suite ficasse pesada, alguem leria a falha como "o router piorou" e sairia
    atras do bug errado. Foi o que aconteceu tres vezes em 2026-08-13.

    Simula degradacao direto no sinal que o teste le, porque contencao real de
    Ollama nao se reproduz sob demanda: derrubar a porta faz o `needs_stack`
    pular antes, e ai o caminho novo nao roda.
    """
    real = sr.read_breaker
    monkeypatch.setattr(sr, "read_breaker",
                        lambda *a, **k: {"failures": 1, "opened_at": 0.0,
                                         "last_msg": "", "last_msg_ts": 0.0})
    # `Skipped` herda de BaseException, nao de Exception: pytest.raises(Exception)
    # nao o captura e o skip vaza, fazendo ESTE teste pular em silencio — o
    # sintoma exato que ele existe para impedir.
    with pytest.raises(pytest.skip.Exception) as exc:
        test_golden_top3_hit_rate(disjuntor_isolado)
    monkeypatch.setattr(sr, "read_breaker", real)
    assert "contaminada" in str(exc.value)
    assert "carga da maquina" in str(exc.value)


def test_golden_alvos_estao_habilitados():
    """Todo alvo de `positives` tem que ser uma skill HABILITADA.

    Guarda a invariante que faltava. Em 2026-08-12 a poda do arsenal desabilitou
    21 plugins; 4 dos 15 casos do golden so podiam ser satisfeitos por skills
    dessa lista (firecrawl x2, huggingface-skills, plugin-dev). O teto de
    acuracia virou 73% contra um piso de 80%.

    E o modo de falha foi pior que a falha: `pick` ainda deixa skill desabilitada
    aparecer quando o cosseno folga o bastante, entao os casos passavam isolados
    e falhavam na suite cheia. Duas sessoes foram gastas atras de "flakiness"
    que era, na verdade, a metrica medindo uma capacidade removida de proposito.

    Sem este teste, a proxima poda repete tudo. Com ele, quem dispensa uma
    ferramenta descobre na hora que precisa arquivar os casos correspondentes.
    """
    with open(GOLDEN, encoding="utf-8") as f:
        data = json.load(f)
    index, _ = sr.load_index()
    habilitadas = {s["id"] for s in index.get("skills", []) if s.get("enabled")}
    if not habilitadas:
        pytest.skip("indice ausente ou sem skills habilitadas")
    orfaos = [
        (c["prompt"][:60], c["expect_any"])
        for c in data["positives"]
        if not any(alvo in habilitadas for alvo in c["expect_any"])
    ]
    assert not orfaos, (
        "casos do golden sem nenhum alvo habilitado — a metrica esta medindo "
        f"capacidade que o sistema nao tem mais. Mova para positives_arquivados: {orfaos}"
    )


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
