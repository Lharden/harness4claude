"""Testes do wiki_prior_art — camada literal, filtro de decisao e silencio.

O requisito que mais importa: **silencio quando nao ha decisao registrada**. Injecao
automatica que fala em toda tarefa vira ruido e o passo acaba desligado.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_wiki_index as bwi

from tools import wiki_prior_art as pa

FRONTMATTER = "---\ntype: {tipo}\ncreated: 2026-01-01\nupdated: 2026-01-01\nstatus: active\ntags: [x]\n---\n\n"
ENCHIMENTO = "texto de apoio suficiente para o bloco virar um chunk indexavel aqui. " * 2


def montar_vault(tmp_path: Path) -> Path:
    """Vault com uma decisao (recusa de TLA+) e uma spec que so menciona CSV."""
    def escrever(rel: str, tipo: str, corpo: str) -> None:
        caminho = tmp_path / "wiki" / rel
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text(FRONTMATTER.format(tipo=tipo) + corpo, encoding="utf-8")

    escrever(
        "decisions/assimilacoes.md", "decision",
        f"# Assimilacoes\n\n## Recusas registradas\nRecusamos TLA+ em favor de "
        f"twin-execution. {ENCHIMENTO}",
    )
    escrever(
        "specs/data-provenance.md", "spec",
        f"# Proveniencia\n\n## Inventario\nO arquivo corpus.CSV fica fora do git. "
        f"{ENCHIMENTO}",
    )
    escrever(
        "projects/notas.md", "project",
        f"# Notas\n\n## Contexto\nO harness usa varias tecnicas. {ENCHIMENTO}",
    )
    out = tmp_path / "out"
    bwi.build(str(tmp_path), str(out), no_embed=True)
    return out


# --- termos salientes -----------------------------------------------------


def test_termo_distintivo_reconhece_nome_de_tecnica() -> None:
    assert pa.is_distinctive("TLA+") is True
    assert pa.is_distinctive("qwen3.5") is True
    assert pa.is_distinctive("pm4py") is True
    assert pa.is_distinctive("HNSW") is True


def test_termo_distintivo_rejeita_palavra_comum() -> None:
    assert pa.is_distinctive("para") is False
    assert pa.is_distinctive("quero") is False
    assert pa.is_distinctive("busca") is False
    assert pa.is_distinctive("ab") is False


def test_salient_terms_nao_repete_e_preserva_ordem() -> None:
    termos = pa.salient_terms("usar TLA+ e depois TLA+ junto com HNSW para tudo")

    assert termos == ["TLA+", "HNSW"]


# --- filtro de decisao ----------------------------------------------------


def test_carries_decision_aceita_pagina_de_decisao() -> None:
    assert pa.carries_decision({"type": "decision", "heading": "Qualquer"}) is True


def test_carries_decision_aceita_cabecalho_de_decisao() -> None:
    assert pa.carries_decision({"type": "spec", "heading": "Recusas registradas"}) is True
    assert pa.carries_decision({"type": "project", "heading": "Decisões estruturantes"}) is True


def test_carries_decision_rejeita_mencao_solta() -> None:
    assert pa.carries_decision({"type": "spec", "heading": "Inventario"}) is False
    assert pa.carries_decision({"type": "project", "heading": ""}) is False


# --- ponta a ponta --------------------------------------------------------


def test_encontra_a_recusa_pelo_nome_da_tecnica(tmp_path: Path) -> None:
    out = montar_vault(tmp_path)

    dados = pa.collect("quero adotar TLA+ para verificar as invariantes", index_dir=out)

    assert [h["id"] for h in dados["literal"]] == ["decisions/assimilacoes"]
    assert dados["literal"][0]["terms"] == ["TLA+"]
    assert "Recusas registradas" in pa.render(dados)


def test_mencao_fora_de_secao_de_decisao_nao_conta(tmp_path: Path) -> None:
    out = montar_vault(tmp_path)

    dados = pa.collect("criar um parser de CSV para extratos", index_dir=out)

    assert dados["literal"] == []
    assert pa.render(dados) == ""


def test_tarefa_sem_termo_distintivo_e_silenciosa(tmp_path: Path) -> None:
    out = montar_vault(tmp_path)

    dados = pa.collect("melhorar a legibilidade das mensagens de erro", index_dir=out)

    assert pa.render(dados) == ""


def test_termo_generico_demais_nao_dispara(tmp_path: Path) -> None:
    """Termo presente em fracao grande do vault nao discrimina — nao vira prior-art."""
    out = montar_vault(tmp_path)

    dados = pa.collect("ajustar o Harness inteiro", index_dir=out)

    assert dados["literal"] == []


def test_indice_ausente_nao_levanta_e_fica_silencioso(tmp_path: Path) -> None:
    dados = pa.collect("adotar TLA+", index_dir=tmp_path / "nao-existe")

    assert dados["literal"] == []
    assert dados["semantic"] == []
    assert pa.render(dados) == ""


def test_main_sempre_sai_zero(tmp_path: Path, monkeypatch, capsys) -> None:
    """Passo de contexto nunca reprova a fase, nem com o indice quebrado."""
    monkeypatch.setattr(sys, "argv", ["wiki_prior_art.py", "adotar TLA+",
                                      "--index", str(tmp_path / "vazio")])

    assert pa.main() == 0
    assert capsys.readouterr().out == ""
