"""Testes do MOC raiz e das duas checagens de estrutura que ele exigiu.

O ponto delicado: um MOC que linka tudo **destroi** a checagem de órfã. Com um grafo de
in-link so, o primeiro índice genérico deixaria todo mundo linkado e ninguém integrado —
e o instrumento cego. Dai os dois grafos (`alcancavel` x `integrado`), que estes testes
travam.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from tools.wiki_index import build_index
from tools.wiki_lint import MIN_PAGES_FOR_MOC, analyze_wiki, subtrees_without_moc
from tools.wiki_moc import MOC_NAME, area_spine, build_moc

FM = """---
type: {tipo}
created: 2026-01-01
updated: 2026-01-01
status: active
tags: [t]
---

"""


def escrever(root: Path, rel: str, corpo: str = "corpo da pagina", tipo: str = "concept") -> Path:
    path = root / "wiki" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(FM.format(tipo=tipo) + corpo, encoding="utf-8")
    return path


def vault_minimo(tmp_path: Path) -> Path:
    escrever(tmp_path, "concepts/sdd.md", "# SDD\n\nSpec-driven.")
    escrever(tmp_path, "decisions/assimilacoes.md", "# Assimilacoes\n\nO que entrou.", "decision")
    escrever(tmp_path, "projects/frente.md", "# Frente\n\nUm projeto.", "project")
    return tmp_path


# --- montagem do MOC ------------------------------------------------------


def test_moc_agrupa_por_pergunta_e_nao_por_pasta(tmp_path: Path) -> None:
    vault_minimo(tmp_path)

    moc = build_moc(tmp_path, today="2026-08-12")

    assert "## O que decidimos" in moc
    assert "## Onde está o trabalho" in moc
    assert "[[decisions/assimilacoes]]" in moc
    assert "type: index" in moc  # senao o MOC contaria como citação de conteúdo


def test_porta_vazia_diz_que_esta_vazia(tmp_path: Path) -> None:
    """Área que S4 vai preencher não pode fingir que já existe."""
    vault_minimo(tmp_path)  # sem `workflows/` nem `ops/`

    moc = build_moc(tmp_path)

    assert "Será preenchido pelos resumos de workflow." in moc
    # A porta de aprender NÃO esta vazia aqui: concepts/ já a preenche.
    assert "Será preenchido pelo compêndio." not in moc


def test_porta_se_preenche_sozinha_quando_a_area_nasce(tmp_path: Path) -> None:
    """O MOC e gerado do disco: S3 cria compendio/ e a porta para de dizer que esta vazia."""
    escrever(tmp_path, "projects/frente.md", "# F\n\nx", "project")
    assert "Será preenchido pelo compêndio." in build_moc(tmp_path)

    escrever(tmp_path, "compendio/chunking.md", "# Chunking\n\nUm vetor por secao.", "term")

    moc = build_moc(tmp_path)

    assert "Será preenchido pelo compêndio." not in moc
    assert "[[compendio/chunking]]" in moc


def test_moc_traz_dataview_e_espinha_de_wikilink(tmp_path: Path) -> None:
    """Dataview so renderiza dentro do Obsidian; metade dos leitores le markdown cru."""
    vault_minimo(tmp_path)

    moc = build_moc(tmp_path)

    assert "```dataview" in moc
    assert f'FROM "{tmp_path.name}/wiki/decisions"' in moc
    assert "- [[decisions/assimilacoes]]" in moc


def test_espinha_ordena_por_profundidade(tmp_path: Path) -> None:
    """Página rasa e a frente; funda e capítulo interno de outra frente."""
    escrever(tmp_path, "projects/frente-rasa.md", "# Rasa\n\nx", "project")
    escrever(tmp_path, "projects/outra/01 Capitulo.md", "# Cap\n\nx", "project")
    escrever(tmp_path, "projects/outra/00 MOC.md", "# MOC\n\nx", "project")

    espinha = area_spine(tmp_path, "projects")

    assert "frente-rasa" in espinha[0]
    assert "00 MOC" in espinha[1]
    assert "01 Capitulo" in espinha[2]


def test_moc_corta_area_grande_e_remete_ao_index(tmp_path: Path) -> None:
    for i in range(20):
        escrever(tmp_path, f"projects/p{i:02}.md", f"# P{i}\n\nx", "project")

    moc = build_moc(tmp_path)

    assert "ver [[index]]" in moc
    assert "p19" not in moc


def test_painel_de_saude_traz_os_numeros_do_lint(tmp_path: Path) -> None:
    vault_minimo(tmp_path)

    moc = build_moc(tmp_path)

    assert "## Saúde do vault" in moc
    assert "| Links quebrados |" in moc
    assert "wiki_lint.py" in moc  # o comando para reproduzir fica na própria página


# --- o MOC não pode cegar o lint ------------------------------------------


def test_moc_nao_conta_como_citacao_de_conteudo(tmp_path: Path) -> None:
    """O teste que justifica os dois grafos de in-link."""
    vault_minimo(tmp_path)
    (tmp_path / "wiki" / MOC_NAME).write_text(build_moc(tmp_path), encoding="utf-8")
    (tmp_path / "wiki" / "index.md").write_text(build_index(tmp_path), encoding="utf-8")

    resultado = analyze_wiki(tmp_path)
    s = resultado["summary"]

    # Alcancável pelo MOC: nenhum erro de página inalcancável.
    assert s["unreachable_pages"] == []
    # Mas não integrada: o aviso permanece, que e a informação útil.
    assert "projects/frente.md" in s["orphan_pages"]


def test_citacao_de_conteudo_integra_de_verdade(tmp_path: Path) -> None:
    vault_minimo(tmp_path)
    escrever(tmp_path, "concepts/sdd.md", "# SDD\n\nVer [[../projects/frente]].")
    (tmp_path / "wiki" / MOC_NAME).write_text(build_moc(tmp_path), encoding="utf-8")
    (tmp_path / "wiki" / "index.md").write_text(build_index(tmp_path), encoding="utf-8")

    s = analyze_wiki(tmp_path)["summary"]

    assert "projects/frente.md" not in s["orphan_pages"]


def test_moc_entra_no_index_gerado(tmp_path: Path) -> None:
    """Página na raiz de wiki/ não pertence a seção nenhuma — ficava fora e o lint acusava."""
    vault_minimo(tmp_path)
    (tmp_path / "wiki" / MOC_NAME).write_text(build_moc(tmp_path), encoding="utf-8")
    (tmp_path / "wiki" / "index.md").write_text(build_index(tmp_path), encoding="utf-8")

    s = analyze_wiki(tmp_path)["summary"]

    assert "## Entrada" in (tmp_path / "wiki" / "index.md").read_text(encoding="utf-8")
    assert s["pages_missing_from_index"] == []


# --- subarvore sem porta de entrada ---------------------------------------


def test_area_de_primeiro_nivel_nao_precisa_de_00(tmp_path: Path) -> None:
    """O MOC raiz já cobre todas — cobrar `00 ...` seria pedir duplicata."""
    for i in range(MIN_PAGES_FOR_MOC + 1):
        escrever(tmp_path, f"specs/s{i}.md", "x", "spec")

    assert subtrees_without_moc(tmp_path) == []


def test_subarvore_funda_e_grande_sem_00_e_acusada(tmp_path: Path) -> None:
    for i in range(MIN_PAGES_FOR_MOC + 1):
        escrever(tmp_path, f"projects/frente-x/c{i}.md", "x", "project")

    assert subtrees_without_moc(tmp_path) == ["projects/frente-x"]


def test_subarvore_com_00_passa(tmp_path: Path) -> None:
    for i in range(MIN_PAGES_FOR_MOC + 1):
        escrever(tmp_path, f"projects/frente-y/c{i}.md", "x", "project")
    escrever(tmp_path, "projects/frente-y/00 MOC.md", "x", "project")

    assert subtrees_without_moc(tmp_path) == []


def test_subarvore_pequena_passa(tmp_path: Path) -> None:
    """Duas páginas se leem direto; exigir porta seria burocracia."""
    for i in range(MIN_PAGES_FOR_MOC - 1):
        escrever(tmp_path, f"projects/frente-z/c{i}.md", "x", "project")

    assert subtrees_without_moc(tmp_path) == []
