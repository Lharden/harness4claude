"""Gera o MOC raiz do AI-Brain — a porta de entrada humana do vault.

Por que existe, sendo que `index.md` ja lista tudo: sao artefatos para leitores
diferentes. O `index.md` e um **catalogo** — plano, alfabetico, uma linha por pagina,
otimo para grep e para o agente. O MOC e um **mapa de intencao**: agrupa por pergunta
("como o sistema funciona", "o que decidimos") em vez de por pasta, e responde primeiro
a quem chega sem saber o que procurar.

Cada porta traz duas visoes da mesma coisa:
  - uma **consulta Dataview**, que vive sozinha e ordena pelo que mudou mais recente;
  - uma **espinha de wikilinks** gerada, que funciona sem plugin, entra no grafo do
    Obsidian e e legivel por grep.

O bloco Dataview nao substitui a espinha: Dataview so renderiza dentro do Obsidian, e
metade dos leitores deste vault e um agente lendo markdown cru.

Uso:
    python tools/wiki_moc.py [--root DIR] [--write]
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wiki_index import SPECS_INDEX_NAME, default_root, section_pages, summarize
from wiki_lint import analyze_wiki

MOC_NAME = "00 MOC AI-Brain.md"

# Cada porta e uma pergunta, nao uma pasta. `areas` sao as pastas que a respondem.
PORTAS: tuple[dict, ...] = (
    {
        "titulo": "Como o sistema funciona",
        "pergunta": "Os fluxos de trabalho e a operação — o que ler para entender ou explicar.",
        "areas": ("workflows", "ops"),
        "vazio": "Será preenchido pelos resumos de workflow.",
    },
    {
        "titulo": "O que decidimos",
        "pergunta": "Técnica que entrou, técnica que foi recusada, e o motivo de cada uma.",
        "areas": ("decisions",),
        "extra": [(f"specs/{SPECS_INDEX_NAME[:-3]}", "todas as specs, agrupadas por frente")],
        "vazio": "Nenhuma decisão registrada ainda.",
    },
    {
        "titulo": "O que eu quero aprender",
        "pergunta": "Conceitos, técnicas e métodos — o compêndio e os padrões que adotamos.",
        "areas": ("compendio", "concepts"),
        "vazio": "Será preenchido pelo compêndio.",
    },
    {
        "titulo": "Onde está o trabalho",
        "pergunta": "Os projetos ativos, sua cronologia e as pessoas envolvidas.",
        "areas": ("projects", "synthesis", "entities"),
        "vazio": "Nenhum projeto registrado.",
    },
)

# Uma pagina por area no MOC seria ilegivel em `projects/` (39 paginas). O corte mostra
# as principais e remete ao index.md, que e o catalogo completo.
MAX_POR_AREA = 12


def dataview_block(root: Path, areas: tuple[str, ...]) -> list[str]:
    """Consulta Dataview cobrindo as areas da porta, ordenada pelo que mudou por ultimo."""
    fontes = " or ".join(f'"{root.name}/wiki/{area}"' for area in areas)
    return [
        "```dataview",
        "TABLE WITHOUT ID file.link AS Pagina, type AS Tipo, updated AS Atualizado",
        f"FROM {fontes}",
        'WHERE type != "index"',
        "SORT updated DESC",
        "```",
        "",
    ]


def area_spine(root: Path, area: str) -> list[str]:
    """Wikilinks da area. MOCs de subprojeto vem primeiro: sao a entrada natural."""
    wiki = root / "wiki"
    paginas = [p for p in section_pages(wiki, area) if p.name != SPECS_INDEX_NAME]
    if not paginas:
        return []
    # Profundidade primeiro: em `projects/` as paginas rasas sao as frentes em si, e as
    # fundas sao capitulos internos (`plans/`, `projects/`). Ordenar so por nome enterrava
    # as frentes sob os capitulos de outra frente.
    paginas.sort(
        key=lambda p: (
            len(p.relative_to(wiki).parts),
            not p.name.startswith("00 "),
            p.name.lower(),
        )
    )
    linhas = []
    for page in paginas[:MAX_POR_AREA]:
        link = page.relative_to(wiki).as_posix()[:-3]
        resumo = summarize(page, limit=70)
        linhas.append(f"- [[{link}]]" + (f" — {resumo}" if resumo else ""))
    resto = len(paginas) - MAX_POR_AREA
    if resto > 0:
        linhas.append(f"- *(+{resto} em `wiki/{area}/` — ver [[index]])*")
    return linhas


def health_panel(root: Path) -> list[str]:
    """Numeros do lint no proprio vault: deriva visivel sem abrir terminal."""
    try:
        resultado = analyze_wiki(root)
    except Exception:
        return []
    s = resultado["summary"]
    veredito = "sem erros" if resultado["ready"] else f"**{s['error_count']} erros**"
    return [
        "## Saúde do vault",
        "",
        (
            f"Última verificação: {date.today().isoformat()} — {veredito}, "
            f"{s['warning_count']} avisos, {s['pages']} páginas."
        ),
        "",
        "| Checagem | Valor |",
        "|---|---|",
        f"| Links quebrados | {len(s['broken_wikilinks'])} |",
        f"| Páginas inalcançáveis | {len(s['unreachable_pages'])} |",
        f"| Só catalogadas, sem citação de conteúdo | {len(s['orphan_pages'])} |",
        f"| Sem frontmatter | {len(s['missing_frontmatter'])} |",
        f"| Notas represadas em `raw/inbox` | {s['inbox_files']} |",
        "",
        "Reproduzir: `python tools/wiki_lint.py --root \"$VAULT_PATH/AI-Brain\" --report`.",
        "",
    ]


def build_moc(root: Path, *, today: str | None = None) -> str:
    """Monta o conteudo do MOC raiz."""
    stamp = today or date.today().isoformat()
    linhas = [
        "---",
        "type: index",
        "created: 2026-08-12",
        f"updated: {stamp}",
        "status: active",
        "tags:",
        "  - meta",
        "  - moc",
        "---",
        "",
        "# AI-Brain",
        "",
        "Memória de decisão e de aprendizado do sistema. Gerado por `tools/wiki_moc.py` —",
        "não editar à mão.",
        "",
        "Quatro portas, por pergunta e não por pasta. O catálogo completo e plano está em",
        "[[index]]; aqui é por onde começar.",
        "",
    ]

    for porta in PORTAS:
        linhas += [f"## {porta['titulo']}", "", f"*{porta['pergunta']}*", ""]
        espinha: list[str] = []
        for area in porta["areas"]:
            espinha += area_spine(root, area)
        for alvo, nota in porta.get("extra", []):
            espinha.append(f"- [[{alvo}]] — {nota}")
        if espinha:
            linhas += [*espinha, ""]
            linhas += dataview_block(root, porta["areas"])
        else:
            linhas += [f"*{porta['vazio']}*", ""]

    linhas += health_panel(root)
    return "\n".join(linhas)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--write", action="store_true", help="Grava wiki/00 MOC AI-Brain.md.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    root = args.root or default_root()
    conteudo = build_moc(root)
    if args.write:
        alvo = root / "wiki" / MOC_NAME
        alvo.write_text(conteudo, encoding="utf-8")
        print(f"gerado: {alvo}")
    else:
        print(conteudo)


if __name__ == "__main__":
    main()
