"""Pre-check de alcancabilidade do Ollama (issue #13).

O router tinha um unico relogio para dois casos opostos: "porta morta" (quero desistir
na hora) e "modelo ocupado" (quero esperar e acertar). O `EMBED_TIMEOUT` de 1.2s deixava
73ms de margem sobre o embed real (p95 1049ms), e ao estourar a Camada B devolvia vazio
— a Camada A sozinha vale 47% no golden set.

Separar os dois relogios permite o teto generoso sem encarecer o caso em que nao ha
nada do outro lado. Testes puros: nao exigem Ollama.
"""

import socket
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))
import skill_router as sr


def porta_livre() -> int:
    """Porta que ninguem esta escutando — fechada no momento em que o teste roda."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def ouvinte():
    """Socket escutando de verdade, para o caso 'porta viva'."""
    servidor = socket.socket()
    servidor.bind(("127.0.0.1", 0))
    servidor.listen(1)
    yield servidor.getsockname()[1]
    servidor.close()


# --- os dois relogios -----------------------------------------------------


def test_os_dois_relogios_sao_distintos() -> None:
    """Se voltarem a ser um so, o defeito da issue #13 volta junto."""
    assert sr.CONNECT_TIMEOUT < sr.EMBED_TIMEOUT
    assert sr.CONNECT_TIMEOUT <= 0.5, "pre-check precisa ser barato o bastante"
    assert sr.EMBED_TIMEOUT >= 5.0, (
        "o teto tem de cobrir a carga do modelo na VRAM, nao so o embed quente: "
        "medido em 2026-09-03, frio 4.68-4.87s contra quente p50 190ms"
    )


# --- alcancabilidade ------------------------------------------------------


def test_porta_fechada_e_inalcancavel() -> None:
    assert sr.ollama_reachable(f"http://127.0.0.1:{porta_livre()}") is False


def test_porta_aberta_e_alcancavel(ouvinte: int) -> None:
    assert sr.ollama_reachable(f"http://127.0.0.1:{ouvinte}") is True


def test_porta_fechada_falha_rapido() -> None:
    """O ganho principal: porta morta custava ~1700ms no Windows, agora ~150ms."""
    inicio = time.perf_counter()
    sr.ollama_reachable(f"http://127.0.0.1:{porta_livre()}")
    decorrido = time.perf_counter() - inicio

    assert decorrido < 1.0, f"pre-check levou {decorrido * 1000:.0f}ms"


def test_url_sem_host_nao_levanta() -> None:
    assert sr.ollama_reachable("nao-e-uma-url") is False


def test_porta_default_quando_url_omite() -> None:
    """Sem porta explicita, cai em 11434 — e nao em None, que explodiria no connect."""
    assert sr.ollama_reachable("http://127.0.0.1", timeout=0.05) in (True, False)


# --- integracao com o disjuntor -------------------------------------------


def _skills_com_vetor():
    return [{
        "id": "p:1", "name": "alguma-skill-bem-especifica", "aliases": [],
        "enabled": True, "usage_count": 0, "vec_row": 0,
    }]


def test_ollama_fora_nao_chama_embed_e_conta_no_disjuntor(monkeypatch, tmp_path) -> None:
    """Sem o pre-check, este caminho pagaria EMBED_TIMEOUT inteiro para nada."""
    monkeypatch.setattr(sr, "ROUTER_DIR", str(tmp_path))
    monkeypatch.setattr(sr, "ollama_reachable", lambda *a, **k: False)

    def nao_deveria_rodar(_prompt):
        raise AssertionError("embed_query rodou com o Ollama inalcancavel")

    monkeypatch.setattr(sr, "embed_query", nao_deveria_rodar)

    assert sr.route("prompt sem alias algum aqui", _skills_com_vetor(), [[1.0]]) == []
    assert sr.read_breaker(str(tmp_path))["failures"] == 1


def test_ollama_alcancavel_ainda_tenta_o_embed(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(sr, "ROUTER_DIR", str(tmp_path))
    monkeypatch.setattr(sr, "ollama_reachable", lambda *a, **k: True)
    chamou = []

    def embed(_prompt):
        chamou.append(True)
        return [1.0]

    monkeypatch.setattr(sr, "embed_query", embed)

    sr.route("prompt sem alias algum aqui", _skills_com_vetor(), [[1.0]])

    assert chamou == [True]
    assert sr.read_breaker(str(tmp_path))["failures"] == 0


def test_disjuntor_aberto_pula_ate_o_pre_check(monkeypatch, tmp_path) -> None:
    """Ollama desligado de vez: apos o limiar, nem os 150ms sao pagos."""
    monkeypatch.setattr(sr, "ROUTER_DIR", str(tmp_path))
    tocou = []
    monkeypatch.setattr(sr, "ollama_reachable", lambda *a, **k: tocou.append(True) or False)
    monkeypatch.setattr(sr, "embed_query", lambda _p: pytest.fail("nao deveria embedar"))

    for _ in range(sr.BREAKER_THRESHOLD + 3):
        sr.route("prompt sem alias algum aqui", _skills_com_vetor(), [[1.0]])

    assert len(tocou) == sr.BREAKER_THRESHOLD, (
        f"pre-check rodou {len(tocou)}x; deveria parar no limiar do disjuntor"
    )
