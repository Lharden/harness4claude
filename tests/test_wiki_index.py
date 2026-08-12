"""Testes do wiki_index — gerador do index.md a partir do disco.

Invariante central: o par index/lint fecha. Um indice recem-gerado nao pode deixar
nenhum erro de cobertura no wiki_lint, nos dois sentidos.
"""

from pathlib import Path

from tools.wiki_index import (
    SECTIONS,
    SEM_FRENTE,
    build_digest,
    build_index,
    build_specs_index,
    page_project,
    summarize,
)
from tools.wiki_lint import analyze_wiki, is_index_page
from tools.wiki_moc import PORTAS

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


def test_toda_porta_do_moc_tem_secao_no_indice() -> None:
    """Area citada pelo MOC e ausente do SECTIONS some do catalogo sem ninguem notar.

    Foi o que aconteceu com `workflows/`: o MOC ja abria a porta "Como o sistema funciona"
    apontando para ela enquanto o indice a ignorava.
    """
    pastas = {pasta for pasta, _, _ in SECTIONS}
    do_moc = {area for porta in PORTAS for area in porta["areas"]}

    assert do_moc <= pastas, f"porta do MOC sem secao no indice: {sorted(do_moc - pastas)}"


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


SPEC_COM_FRENTE = """---
type: spec
created: 2026-01-01
updated: 2026-01-01
status: active
project: {frente}
---

# {titulo}

corpo
"""


def write_spec(root: Path, nome: str, *, frente: str | None, titulo: str = "Uma spec") -> Path:
    path = root / "wiki" / "specs" / nome
    path.parent.mkdir(parents=True, exist_ok=True)
    if frente is None:
        path.write_text(FRONTMATTER + f"# {titulo}\n\ncorpo\n", encoding="utf-8")
    else:
        path.write_text(SPEC_COM_FRENTE.format(frente=frente, titulo=titulo), encoding="utf-8")
    return path


# --- indice de specs ------------------------------------------------------


def test_page_project_le_o_carimbo(tmp_path: Path) -> None:
    com = write_spec(tmp_path, "a.md", frente="harness4claude")
    sem = write_spec(tmp_path, "b.md", frente=None)

    assert page_project(com) == "harness4claude"
    assert page_project(sem) == SEM_FRENTE


def test_specs_index_agrupa_por_frente(tmp_path: Path) -> None:
    write_spec(tmp_path, "a.md", frente="harness4claude", titulo="Router")
    write_spec(tmp_path, "b.md", frente="harness4claude", titulo="Graphify")
    write_spec(tmp_path, "c.md", frente="fastslr", titulo="Auditoria")

    indice = build_specs_index(tmp_path, today="2026-08-12")

    assert "## fastslr" in indice
    assert "## harness4claude" in indice
    assert "- [[specs/a]] — Router" in indice
    assert "3 specs em 2 frentes." in indice
    assert indice.index("## fastslr") < indice.index("## harness4claude")


def test_specs_sem_frente_vao_para_o_fim(tmp_path: Path) -> None:
    """A fila de triagem nao pode se passar por uma frente de verdade."""
    write_spec(tmp_path, "a.md", frente=None)
    write_spec(tmp_path, "b.md", frente="zzz-ultima-alfabeticamente")

    indice = build_specs_index(tmp_path)

    assert indice.index("## zzz-ultima-alfabeticamente") < indice.index(f"## {SEM_FRENTE}")


def test_specs_index_nao_lista_a_si_mesmo(tmp_path: Path) -> None:
    write_spec(tmp_path, "a.md", frente="x")
    (tmp_path / "wiki" / "specs" / "00 Índice de Specs.md").write_text(
        build_specs_index(tmp_path), encoding="utf-8"
    )

    indice = build_specs_index(tmp_path)

    assert "1 specs em 1 frentes." in indice
    assert "Índice de Specs]]" not in indice


def test_specs_index_torna_alcancavel_mas_nao_integra(tmp_path: Path) -> None:
    """O gerador resolve alcance, nao integracao — e a distincao importa.

    16 specs espelhadas nasceram sem in-link nenhum. O indice conserta o alcance: da para
    chegar la. Nao conserta o tecido: nenhuma pagina de conteudo as cita ainda, e e por
    isso que continuam aparecendo como aviso. Fingir que o catalogo resolve as duas coisas
    apagaria justamente o sinal de que falta escrever sobre elas.
    """
    write_page(tmp_path, "concepts/x.md", "corpo")
    for i in range(3):
        write_spec(tmp_path, f"s{i}.md", frente="harness4claude")
    (tmp_path / "wiki" / "specs" / "00 Índice de Specs.md").write_text(
        build_specs_index(tmp_path), encoding="utf-8"
    )
    (tmp_path / "wiki" / "index.md").write_text(build_index(tmp_path), encoding="utf-8")

    s = analyze_wiki(tmp_path)["summary"]

    assert s["unreachable_pages"] == []
    assert [o for o in s["orphan_pages"] if o.startswith("specs/")] == [
        "specs/s0.md", "specs/s1.md", "specs/s2.md",
    ]


def test_spec_citada_por_conteudo_sai_do_aviso(tmp_path: Path) -> None:
    write_page(tmp_path, "concepts/x.md", "Ver [[../specs/s0]].")
    write_spec(tmp_path, "s0.md", frente="harness4claude")
    (tmp_path / "wiki" / "specs" / "00 Índice de Specs.md").write_text(
        build_specs_index(tmp_path), encoding="utf-8"
    )
    (tmp_path / "wiki" / "index.md").write_text(build_index(tmp_path), encoding="utf-8")

    s = analyze_wiki(tmp_path)["summary"]

    assert "specs/s0.md" not in s["orphan_pages"]


# --- pagina-indice nao e orfa ---------------------------------------------


def test_is_index_page_reconhece_o_tipo(tmp_path: Path) -> None:
    indice = tmp_path / "i.md"
    indice.write_text("---\ntype: index\nstatus: active\n---\n\n# I\n", encoding="utf-8")
    comum = write_page(tmp_path, "concepts/c.md", "corpo")

    assert is_index_page(indice) is True
    assert is_index_page(comum) is False


def test_indice_gerado_nao_conta_como_orfao(tmp_path: Path) -> None:
    """Um indice existe para ser linkado DE, nao PARA — linka-lo de algum lugar
    artificial so para calar o lint seria pior que a regra."""
    write_page(tmp_path, "concepts/x.md", "corpo")
    write_spec(tmp_path, "s.md", frente="f")
    (tmp_path / "wiki" / "specs" / "00 Índice de Specs.md").write_text(
        build_specs_index(tmp_path), encoding="utf-8"
    )
    (tmp_path / "wiki" / "index.md").write_text(build_index(tmp_path), encoding="utf-8")

    orfas = analyze_wiki(tmp_path)["summary"]["orphan_pages"]

    assert not any("Índice de Specs" in o for o in orfas)


def test_index_e_deterministico(tmp_path: Path) -> None:
    write_page(tmp_path, "concepts/b.md", "beta")
    write_page(tmp_path, "concepts/a.md", "alfa")

    primeiro = build_index(tmp_path, today="2026-08-11")
    segundo = build_index(tmp_path, today="2026-08-11")

    assert primeiro == segundo
    assert primeiro.index("[[concepts/a]]") < primeiro.index("[[concepts/b]]")
