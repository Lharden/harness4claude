"""Testes do build_wiki_index — chunking por secao e frescor do indice."""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_wiki_index as bwi

FRONTMATTER = """---
type: decision
created: 2026-01-01
updated: 2026-01-01
status: active
tags: [assimilacao, harness]
---

"""


def write_page(root: Path, rel: str, body: str, *, frontmatter: bool = True) -> Path:
    path = root / "wiki" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text((FRONTMATTER if frontmatter else "") + body, encoding="utf-8")
    return path


def chunks_of(root: Path, rel: str, **kwargs) -> list[dict]:
    return bwi.page_chunks(str(root / "wiki" / rel), str(root / "wiki"), **kwargs)


# --- helpers puros --------------------------------------------------------


def test_flatten_row_vira_prosa() -> None:
    assert bwi.flatten_row("| TLA+ | twin-execution | custo |") == "TLA+ twin-execution custo"


def test_clean_lines_mantem_tabela_e_descarta_ruido() -> None:
    bloco = ["| a | b |", "|---|---|", "```py", "> citacao", "", "prosa normal"]

    assert bwi.clean_lines(bloco) == ["a b", "prosa normal"]


def test_split_sections_quebra_por_heading_2_a_4() -> None:
    corpo = "intro\n## Um\nx\n### Dois\ny\n"

    assert [h for h, _ in bwi.split_sections(corpo)] == ["", "Um", "Dois"]


# --- chunking -------------------------------------------------------------


LONGO = "conteudo relevante o suficiente para virar um chunk proprio nesta secao. " * 3


def test_cada_secao_longa_vira_um_chunk(tmp_path: Path) -> None:
    write_page(tmp_path, "decisions/a.md", f"# Titulo\n\n## Adotado\n{LONGO}\n## Recusado\n{LONGO}")

    chunks = chunks_of(tmp_path, "decisions/a.md")

    assert [c["heading"] for c in chunks] == ["Adotado", "Recusado"]
    assert all(c["page_id"] == "decisions/a" for c in chunks)
    assert [c["id"] for c in chunks] == ["decisions/a#Adotado", "decisions/a#Recusado"]


def test_secao_curta_e_absorvida_pela_seguinte(tmp_path: Path) -> None:
    write_page(tmp_path, "decisions/a.md", f"# T\n\n## Nota\nok\n## Corpo\n{LONGO}")

    chunks = chunks_of(tmp_path, "decisions/a.md")

    assert len(chunks) == 1
    assert chunks[0]["heading"] == "Nota"
    assert "ok" in chunks[0]["description"] and "conteudo relevante" in chunks[0]["description"]


def test_aliases_so_no_primeiro_chunk(tmp_path: Path) -> None:
    write_page(tmp_path, "decisions/a.md", f"# T\n\n## Um\n{LONGO}\n## Dois\n{LONGO}")

    chunks = chunks_of(tmp_path, "decisions/a.md")

    assert chunks[0]["aliases"]
    assert chunks[1]["aliases"] == []


def test_aliases_curados_entram_no_primeiro_chunk(tmp_path: Path) -> None:
    write_page(tmp_path, "decisions/a.md", f"# T\n\n## Um\n{LONGO}")

    chunks = chunks_of(tmp_path, "decisions/a.md", aliases_map={"decisions/a": ["prior art"]})

    assert "prior art" in chunks[0]["aliases"]


# --- cabecalho como nome canonico -----------------------------------------


COMPENDIO_FM = "---\ntype: compendium\nupdated: 2026-01-01\nstatus: active\n---\n\n"


def escrever_verbete(root: Path, rel: str, heading: str) -> None:
    path = root / "wiki" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"{COMPENDIO_FM}# Colecao\n\n## {heading}\n{LONGO}", encoding="utf-8"
    )


def test_cabecalho_de_verbete_vira_alias(tmp_path: Path) -> None:
    """Quem sabe o nome do conceito merece resposta pela Camada A, sem passar por cosseno."""
    escrever_verbete(tmp_path, "compendio/02 recuperacao.md", "Embedding")

    assert "Embedding" in chunks_of(tmp_path, "compendio/02 recuperacao.md")[0]["aliases"]


def test_cabecalho_estrutural_nao_vira_alias(tmp_path: Path) -> None:
    """"Contexto"/"Objetivo" como alias disparariam em qualquer prompt que os use."""
    write_page(tmp_path, "projects/p.md", f"# P\n\n## Contexto\n{LONGO}")

    assert "Contexto" not in chunks_of(tmp_path, "projects/p.md")[0]["aliases"]


def test_alias_curto_sem_parentetico(tmp_path: Path) -> None:
    """A Camada A procura o alias DENTRO do prompt: rotulo longo nunca casa com o curto."""
    escrever_verbete(tmp_path, "compendio/03 confiabilidade.md", "Disjuntor (circuit breaker)")

    aliases = chunks_of(tmp_path, "compendio/03 confiabilidade.md")[0]["aliases"]

    assert "Disjuntor (circuit breaker)" in aliases
    assert "Disjuntor" in aliases


def test_chunk_carrega_campos_neutros_do_contrato_do_router(tmp_path: Path) -> None:
    write_page(tmp_path, "decisions/a.md", f"# T\n\n## Um\n{LONGO}")

    chunk = chunks_of(tmp_path, "decisions/a.md")[0]

    assert chunk["enabled"] is True
    assert chunk["usage_count"] == 0
    assert chunk["vec_row"] == -1
    assert chunk["type"] == "decision"
    assert chunk["tags"] == ["assimilacao", "harness"]


def test_subarvore_de_grafo_e_paginas_meta_ficam_fora(tmp_path: Path) -> None:
    write_page(tmp_path, "concepts/x.md", LONGO)
    write_page(tmp_path, "index.md", LONGO)
    write_page(tmp_path, "log.md", LONGO)
    write_page(tmp_path, "graphs/repo/no.md", LONGO)

    encontrados = [os.path.basename(p) for p in bwi.page_files(str(tmp_path))]

    assert encontrados == ["x.md"]


def test_load_aliases_ignora_chaves_de_comentario(tmp_path: Path) -> None:
    arquivo = tmp_path / "aliases.json"
    arquivo.write_text(json.dumps({"_comment": "nota", "a/b": ["x"]}), encoding="utf-8")

    assert bwi.load_aliases(str(arquivo)) == {"a/b": ["x"]}


# --- build e frescor ------------------------------------------------------


def test_build_sem_embed_grava_indice_navegavel(tmp_path: Path) -> None:
    write_page(tmp_path, "concepts/x.md", f"# X\n\n## Um\n{LONGO}")
    out = tmp_path / "out"

    total = bwi.build(str(tmp_path), str(out), no_embed=True)

    index = json.loads((out / "wiki-index.json").read_text(encoding="utf-8"))
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    assert total == 1
    assert index["dim"] == 0 and index["model"] is None
    assert meta["chunks"] == 1 and meta["pages"] == 1


def test_check_stale_detecta_ausencia_e_mudanca(tmp_path: Path) -> None:
    page = write_page(tmp_path, "concepts/x.md", f"# X\n\n## Um\n{LONGO}")
    out = tmp_path / "out"

    assert bwi.check_stale(str(tmp_path), str(out)) is True

    bwi.build(str(tmp_path), str(out), no_embed=True)
    assert bwi.check_stale(str(tmp_path), str(out)) is False

    page.write_text(page.read_text(encoding="utf-8") + "\nmais conteudo\n", encoding="utf-8")
    os.utime(page, (page.stat().st_atime, page.stat().st_mtime + 10))
    assert bwi.check_stale(str(tmp_path), str(out)) is True
