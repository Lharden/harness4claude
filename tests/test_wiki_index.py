"""Testes do wiki_index — gerador do index.md a partir do disco.

Invariante central: o par index/lint fecha. Um indice recem-gerado nao pode deixar
nenhum erro de cobertura no wiki_lint, nos dois sentidos.
"""

from pathlib import Path

from tools.wiki_index import build_digest, build_index, summarize
from tools.wiki_lint import analyze_wiki

FRONTMATTER = """---
type: concept
created: 2026-01-01
updated: 2026-01-01
status: active
tags: [test]
---

"""


def write_page(root: Path, rel: str, body: str = "") -> Path:
    path = root / "wiki" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(FRONTMATTER + body, encoding="utf-8")
    return path


def test_summarize_pula_titulo_e_limpa_markup(tmp_path: Path) -> None:
    page = write_page(tmp_path, "concepts/x.md", "# Titulo\n\n**Padrao** de [[y|algo]].")

    assert summarize(page) == "Padrao de algo."


def test_summarize_trunca_em_palavra_inteira(tmp_path: Path) -> None:
    page = write_page(tmp_path, "concepts/x.md", "palavra " * 40)

    resumo = summarize(page, limit=30)

    assert resumo.endswith("...")
    assert len(resumo) <= 33
    assert "palavr..." not in resumo


def test_summarize_vazio_quando_so_ha_titulo(tmp_path: Path) -> None:
    page = write_page(tmp_path, "concepts/x.md", "# Só titulo\n")

    assert summarize(page) == ""


def test_index_lista_toda_pagina_e_marca_secao_vazia(tmp_path: Path) -> None:
    write_page(tmp_path, "concepts/sdd.md", "Spec-driven development.")
    write_page(tmp_path, "projects/harness/00 MOC.md", "Mapa do projeto.")

    index = build_index(tmp_path, today="2026-08-11")

    assert "- [[concepts/sdd]] — Spec-driven development." in index
    assert "- [[projects/harness/00 MOC]] — Mapa do projeto." in index
    assert "## Sources (`wiki/sources/`)" in index
    assert "- *(vazio)*" in index
    assert "updated: 2026-08-11" in index


def test_index_conta_notas_de_grafo_sem_listar_uma_a_uma(tmp_path: Path) -> None:
    write_page(tmp_path, "concepts/sdd.md", "corpo")
    for i in range(3):
        write_page(tmp_path, f"graphs/repo-x/nota-{i}.md", "no")

    index = build_index(tmp_path)

    assert "- `graphs/repo-x/` — 3 notas" in index
    assert "nota-0" not in index


def test_index_gerado_zera_os_erros_de_cobertura_do_lint(tmp_path: Path) -> None:
    write_page(tmp_path, "concepts/sdd.md", "Ver [[entities/leonardo]].")
    write_page(tmp_path, "entities/leonardo.md", "Ver [[concepts/sdd]].")
    write_page(tmp_path, "specs/spec-a.md", "Ver [[concepts/sdd]].")
    write_page(tmp_path, "graphs/repo-x/nota.md", "no")
    (tmp_path / "wiki" / "index.md").write_text(build_index(tmp_path), encoding="utf-8")

    result = analyze_wiki(tmp_path)

    assert result["summary"]["pages_missing_from_index"] == []
    assert result["summary"]["index_phantom_entries"] == []
    assert result["summary"]["error_count"] == 0


def test_digest_e_curto_e_lista_as_decisoes(tmp_path: Path) -> None:
    """O index.md passa de 10 KB — caro demais para entrar em toda sessao."""
    write_page(tmp_path, "concepts/sdd.md", "Spec-driven.")
    write_page(tmp_path, "decisions/assimilacoes.md", "O que veio de fora.")
    write_page(tmp_path, "decisions/contexto.md", "Decisoes travadas.")

    digest = build_digest(tmp_path)

    assert len(digest.encode("utf-8")) < 1024
    assert "[[decisions/assimilacoes]]" in digest
    assert "[[decisions/contexto]]" in digest
    assert "Concepts 1" in digest
    assert "wiki-query" in digest


def test_digest_omite_secao_vazia(tmp_path: Path) -> None:
    write_page(tmp_path, "concepts/sdd.md", "Spec-driven.")

    digest = build_digest(tmp_path)

    assert "Sources" not in digest
    assert "Concepts 1" in digest


def test_digest_trunca_lista_longa_de_decisoes(tmp_path: Path) -> None:
    for i in range(20):
        write_page(tmp_path, f"decisions/d{i:02}.md", "corpo")

    digest = build_digest(tmp_path, max_decisions=5)

    assert "(+15)" in digest


def test_digest_vazio_quando_nao_ha_vault(tmp_path: Path) -> None:
    assert build_digest(tmp_path / "inexistente") == ""


def test_index_e_deterministico(tmp_path: Path) -> None:
    write_page(tmp_path, "concepts/b.md", "beta")
    write_page(tmp_path, "concepts/a.md", "alfa")

    primeiro = build_index(tmp_path, today="2026-08-11")
    segundo = build_index(tmp_path, today="2026-08-11")

    assert primeiro == segundo
    assert primeiro.index("[[concepts/a]]") < primeiro.index("[[concepts/b]]")
