"""Testes do wiki_query — dedupe por pagina, bandas de confianca e degradacao.

O contrato mais importante aqui e o herdado do skill-router: **nunca levanta**. Quem
consome e um passo de pipeline que nao pode quebrar porque o Ollama caiu.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_wiki_index as bwi

from tools import wiki_query as wq

LONGO = "conteudo relevante o suficiente para virar um chunk proprio nesta secao. " * 3
FRONTMATTER = "---\ntype: decision\ncreated: 2026-01-01\nupdated: 2026-01-01\nstatus: active\ntags: [x]\n---\n\n"


def montar_indice(tmp_path: Path) -> Path:
    """Vault minimo + indice sem embeddings (Camada A apenas)."""
    page = tmp_path / "wiki" / "decisions" / "assimilacoes-2026.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        FRONTMATTER + f"# Assimilacoes\n\n## Adotado\n{LONGO}\n## Recusas registradas\n{LONGO}",
        encoding="utf-8",
    )
    out = tmp_path / "out"
    bwi.build(str(tmp_path), str(out), no_embed=True,
              pages=bwi.scan_pages(str(tmp_path),
                                   aliases_map={"decisions/assimilacoes-2026": ["recusas"]}))
    return out


def fake_hit(page_id: str, heading: str, score: float, layer: str = "B") -> dict:
    return {
        "id": f"{page_id}#{heading}",
        "score": score,
        "cos": score,
        "layer": layer,
        "skill": {"page_id": page_id, "heading": heading, "title": page_id,
                  "type": "decision", "path": "", "description": "trecho"},
    }


# --- dedupe ---------------------------------------------------------------


def test_dedupe_mantem_o_melhor_chunk_de_cada_pagina() -> None:
    hits = [fake_hit("a", "um", 0.9), fake_hit("a", "dois", 0.5), fake_hit("b", "um", 0.7)]

    resultado = wq.dedupe_by_page(hits, top_k=5)

    assert [h["skill"]["page_id"] for h in resultado] == ["a", "b"]
    assert resultado[0]["skill"]["heading"] == "um"


def test_dedupe_respeita_o_top_k() -> None:
    hits = [fake_hit(pid, "s", 0.9 - i / 10) for i, pid in enumerate("abcd")]

    assert len(wq.dedupe_by_page(hits, top_k=2)) == 2


# --- indice ausente / corrompido -----------------------------------------


def test_load_index_ausente_devolve_vazio_sem_levantar(tmp_path: Path) -> None:
    assert wq.load_index(tmp_path / "nao-existe") == ({}, [])


def test_query_sem_indice_reporta_indisponivel(tmp_path: Path) -> None:
    resultado = wq.query("qualquer coisa", index_dir=tmp_path / "vazio")

    assert resultado["available"] is False
    assert resultado["hits"] == []
    assert "wiki-index ausente" in wq.render(resultado)


def test_load_index_com_blob_truncado_degrada_para_camada_a(tmp_path: Path) -> None:
    out = montar_indice(tmp_path)
    dados = json.loads((out / "wiki-index.json").read_text(encoding="utf-8"))
    dados["dim"] = 768  # declara vetores que o blob vazio nao tem
    (out / "wiki-index.json").write_text(json.dumps(dados), encoding="utf-8")

    index, vecs = wq.load_index(out)

    assert index["pages"]
    assert vecs == []


# --- camadas e confianca --------------------------------------------------


def test_camada_a_acha_por_alias_curado_e_e_confiavel(tmp_path: Path) -> None:
    out = montar_indice(tmp_path)

    resultado = wq.query("quais recusas ja foram registradas", index_dir=out)

    assert resultado["hits"][0]["id"] == "decisions/assimilacoes-2026"
    assert resultado["hits"][0]["layer"] == "A"
    assert resultado["hits"][0]["confident"] is True
    assert resultado["confident_hits"] == 1


def test_camada_a_nao_devolve_a_mesma_pagina_por_secao(tmp_path: Path) -> None:
    out = montar_indice(tmp_path)

    resultado = wq.query("recusas", index_dir=out)

    assert [h["id"] for h in resultado["hits"]] == ["decisions/assimilacoes-2026"]


def test_score_abaixo_da_barra_marca_nao_confiavel(tmp_path: Path) -> None:
    out = montar_indice(tmp_path)
    index, _ = wq.load_index(out)
    baixo = fake_hit("decisions/assimilacoes-2026", "Adotado", wq.CONFIDENT_COS - 0.05)

    marcado = wq.dedupe_by_page([baixo], top_k=3)[0]

    assert marcado["cos"] < wq.CONFIDENT_COS
    assert index["pages"]  # indice montado, mas o hit nao alcanca a barra


def test_render_avisa_quando_nada_atinge_a_barra() -> None:
    resultado = {
        "question": "x", "available": True, "pages_indexed": 1, "chunks_indexed": 2,
        "embeddings": True, "confident_hits": 0,
        "hits": [{"id": "a", "title": "A", "section": "s", "type": "page", "layer": "B",
                  "score": 0.33, "confident": False, "wikilink": "[[a]]", "path": "",
                  "snippet": "trecho"}],
    }

    texto = wq.render(resultado)

    assert "pode nao cobrir isto" in texto
    assert "(abaixo da barra)" in texto


# --- contrato de falha ----------------------------------------------------


def test_ollama_fora_do_ar_degrada_sem_levantar(tmp_path: Path, monkeypatch) -> None:
    out = montar_indice(tmp_path)
    index, _ = wq.load_index(out)

    def explode(*_args, **_kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(wq, "embed_query", explode)
    vecs_falsos = [[0.0] * 4 for _ in index["pages"]]
    for i, chunk in enumerate(index["pages"]):
        chunk["vec_row"] = i

    hits = wq.route("pergunta sem alias que exigiria embed", index["pages"], vecs_falsos)

    assert hits == []


def test_route_restaura_os_knobs_globais_do_router(tmp_path: Path) -> None:
    import skill_router as sr

    out = montar_indice(tmp_path)
    index, vecs = wq.load_index(out)
    antes = (sr.TOP_K, sr.MIN_COS, sr.MIN_MARGIN)

    wq.route("recusas", index["pages"], vecs, top_k=2)

    assert antes == (sr.TOP_K, sr.MIN_COS, sr.MIN_MARGIN)


def chunks_fake(n: int) -> list[dict]:
    return [
        {"id": f"p{i}", "page_id": f"p{i}", "vec_row": i, "enabled": True,
         "usage_count": 0, "aliases": [], "name": f"p{i}", "heading": "",
         "title": f"p{i}", "type": "page", "path": "", "description": "x"}
        for i in range(n)
    ]


def test_corpus_pequeno_dispensa_a_regra_de_margem(monkeypatch) -> None:
    """Com 2 chunks a mediana E o topo: exigir mediana+margem descartaria o acerto."""
    import skill_router as sr

    monkeypatch.setattr(wq, "embed_query", lambda *_a, **_k: [1.0, 0.0])
    vecs = [[1.0, 0.0], [0.6, 0.8]]  # cos com a consulta: 1.00 e 0.60
    antes = sr.MIN_MARGIN

    hits = wq.route("consulta", chunks_fake(2), vecs, top_k=3)

    assert [h["skill"]["page_id"] for h in hits] == ["p0", "p1"]
    assert antes == sr.MIN_MARGIN, "knob global do router nao foi restaurado"


def test_corpus_grande_mantem_a_regra_de_margem(monkeypatch) -> None:
    """Acima do limiar, o melhor-de-um-lote-ruim continua sendo filtrado."""
    monkeypatch.setattr(wq, "embed_query", lambda *_a, **_k: [1.0, 0.0])
    n = wq.MIN_CHUNKS_FOR_MARGIN + 2
    vecs = [[1.0, 0.0] for _ in range(n)]  # todos identicos: nenhum se destaca

    hits = wq.route("consulta", chunks_fake(n), vecs, top_k=3)

    assert hits == []
