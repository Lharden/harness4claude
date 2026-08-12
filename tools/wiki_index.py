"""Gera o index.md da wiki AI-Brain a partir do disco.

Contraparte do wiki_lint: o lint acusa divergencia entre disco e indice nos dois
sentidos, este script resolve. Sem um gerador, o indice volta a apodrecer — foi o que
aconteceu entre 2026-05 e 2026-08 (54 de 63 paginas ficaram fora, 5 entradas apontavam
para paginas inexistentes).

Uso:
    python tools/wiki_index.py [--root DIR] [--write]
Sem --write, imprime o indice no stdout (diff-avel contra o atual).
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import date
from pathlib import Path

META_PAGES = {"index.md", "log.md"}
GENERATED_SUBTREE = "graphs"

# Ordem de leitura do indice: da fonte crua para a sintese, depois operacao.
SECTIONS: tuple[tuple[str, str, str], ...] = (
    ("sources", "Sources", "1 página por fonte processada."),
    ("entities", "Entities", "pessoas, produtos, ferramentas, organizações."),
    ("concepts", "Concepts", "ideias, métodos, padrões, frameworks."),
    ("decisions", "Decisions", "registros de assimilação: o que veio de fora e o que ficou."),
    ("synthesis", "Synthesis", "escritos de ordem superior puxando várias páginas."),
    ("projects", "Projects", "projetos ativos."),
    ("ops", "Ops", "arquitetura de sync, runbooks, decisões operacionais."),
    ("specs", "Specs", "espelhadas de docs/specs pelo vault_sync."),
    ("sessions", "Sessions", "espelhadas de harness/traces pelo vault_sync."),
    ("schema", "Schema", "configuração do vault."),
)

_FRONTMATTER_RE = re.compile(r"\A---\s*\n.*?\n---\s*\n", re.S)
_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(\|[^\]]+)?\]\]")
_EMPHASIS_RE = re.compile(r"\*\*|__|\*|`")
_SKIP_PREFIXES = ("#", "```", "|", ">", "---", "!", "- ")

SUMMARY_MAX = 95


def summarize(path: Path, *, limit: int = SUMMARY_MAX) -> str:
    """Primeira linha de prosa da pagina, sem markup, truncada em palavra inteira."""
    body = _FRONTMATTER_RE.sub("", path.read_text(encoding="utf-8", errors="replace"))
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith(_SKIP_PREFIXES):
            continue
        line = _WIKILINK_RE.sub(lambda m: (m.group(2) or f"|{m.group(1)}")[1:], line)
        line = _EMPHASIS_RE.sub("", line).strip()
        if not line:
            continue
        return f"{line[:limit].rsplit(' ', 1)[0]}..." if len(line) > limit else line
    return ""


def section_pages(wiki: Path, folder: str) -> list[Path]:
    """Paginas de uma secao, ordenadas por subpasta e nome."""
    directory = wiki / folder
    if not directory.is_dir():
        return []
    return sorted(
        (p for p in directory.rglob("*.md") if p.name not in META_PAGES),
        key=lambda p: (str(p.parent).lower(), p.name.lower()),
    )


def build_index(root: Path, *, today: str | None = None) -> str:
    """Monta o conteudo completo do index.md."""
    wiki = root / "wiki"
    stamp = today or date.today().isoformat()
    lines = [
        "---",
        "type: index",
        "created: 2026-05-09",
        f"updated: {stamp}",
        "status: active",
        "tags:",
        "  - meta",
        "---",
        "",
        "# AI-Brain Index",
        "",
        "Índice mestre, **gerado do disco** por `tools/wiki_index.py` — toda página de",
        "`wiki/` aparece aqui e toda entrada aqui aponta para página existente.",
        "Regenerar após criar ou mover páginas; `tools/wiki_lint.py` acusa divergência",
        "nos dois sentidos.",
        "",
    ]

    # Paginas na raiz de wiki/ (o MOC, por exemplo) nao pertencem a nenhuma secao. Sem
    # esta entrada elas ficavam fora do index e o lint as acusava — corretamente.
    entrada = sorted(
        (p for p in wiki.glob("*.md") if p.name not in META_PAGES),
        key=lambda p: p.name.lower(),
    )
    if entrada:
        lines += ["## Entrada", "", "*Porta de acesso — comece por aqui.*", ""]
        for page in entrada:
            summary = summarize(page)
            lines.append(f"- [[{page.stem}]]" + (f" — {summary}" if summary else ""))
        lines.append("")

    for folder, title, note in SECTIONS:
        lines += [f"## {title} (`wiki/{folder}/`)", "", f"*{note}*", ""]
        pages = section_pages(wiki, folder)
        if not pages:
            lines += ["- *(vazio)*", ""]
            continue
        for page in pages:
            link = page.relative_to(wiki).as_posix()[:-3]
            summary = summarize(page)
            lines.append(f"- [[{link}]]" + (f" — {summary}" if summary else ""))
        lines.append("")

    generated = wiki / GENERATED_SUBTREE
    notes = sorted(generated.rglob("*.md")) if generated.is_dir() else []
    lines += [
        f"## Graphs (`wiki/{GENERATED_SUBTREE}/`)",
        "",
        ("*Knowledge graphs por repo, exportados pelo graphify. Geradas por máquina — "
         "fora das checagens de órfã/estagnada do lint.*"),
        "",
    ]
    if (generated / "index.md").is_file():
        lines.append(f"- [[{GENERATED_SUBTREE}/index]] — convenção de export e catálogo")
    for repo in sorted({p.parent.name for p in notes if p.parent != generated}):
        count = sum(1 for p in notes if p.parent.name == repo)
        lines.append(f"- `{GENERATED_SUBTREE}/{repo}/` — {count} notas")
    if not notes:
        lines.append("- *(vazio)*")

    lines += [
        "",
        "## Pointers externos",
        "",
        "- `~/.claude/CLAUDE.md` — diretrizes globais (harness v3 SDD, hooks, graphify, Obsidian)",
        "- `~/.claude/harness/state.json` — estado runtime do harness",
        "- `~/.claude/plugins/local/harness4claude/` — executores do vault:",
        "  `scripts/vault_sync.py` (sync), `tools/wiki_lint.py` (lint), `tools/wiki_index.py` (indice)",
        "- `.remember/` — buffer de sessão; espelhado para `raw/inbox/` pelo vault_sync",
        "",
        "## Como buscar",
        "",
        "- **Por categoria**: navegue para `wiki/{tipo}/`",
        "- **Por tag**: busca do Obsidian, `tag:#nome`",
        '- **Full-text**: busca do Obsidian ou `grep -r "termo" wiki/`',
        "- **Semântica**: operação `query` — ver `AI-Brain/CLAUDE.md`",
        "",
        "## Saúde",
        "",
        "```",
        'python tools/wiki_lint.py  --root "$VAULT_PATH/AI-Brain" --report',
        'python tools/wiki_index.py --root "$VAULT_PATH/AI-Brain" --write',
        "```",
        "",
    ]
    return "\n".join(lines)


_PROJECT_RE = re.compile(r"^project:\s*(\S+)\s*$", re.M)
_TITLE_RE = re.compile(r"^#\s+(.+)$", re.M)

SPECS_INDEX_NAME = "00 Índice de Specs.md"
SEM_FRENTE = "sem frente declarada"


def page_project(path: Path) -> str:
    """Frente declarada no frontmatter (`project:`), ou o rótulo de indefinido."""
    match = _FRONTMATTER_RE.match(path.read_text(encoding="utf-8", errors="replace"))
    if not match:
        return SEM_FRENTE
    found = _PROJECT_RE.search(match.group(0))
    return found.group(1).strip().strip("\"'") if found else SEM_FRENTE


def build_specs_index(root: Path, *, today: str | None = None) -> str:
    """Índice de `wiki/specs/` agrupado por frente.

    Existe por um motivo mecanico: spec espelhada chega ao vault sem in-link de lugar
    nenhum, e 16 delas viraram orfas. Linkar a mao nao escala — uma spec nova nasceria
    orfa de novo. Aqui o vinculo e derivado do carimbo `project:` que o `vault_sync`
    aplica na copia, entao acompanha o disco sozinho.
    """
    wiki = root / "wiki"
    specs = [p for p in section_pages(wiki, "specs") if p.name != SPECS_INDEX_NAME]
    stamp = today or date.today().isoformat()

    por_frente: dict[str, list[Path]] = {}
    for spec in specs:
        por_frente.setdefault(page_project(spec), []).append(spec)

    lines = [
        "---",
        "type: index",
        f"created: {stamp}",
        f"updated: {stamp}",
        "status: active",
        "tags:",
        "  - meta",
        "---",
        "",
        "# Índice de Specs",
        "",
        "Gerado por `tools/wiki_index.py --specs`, agrupado pelo carimbo `project:` que o",
        "`vault_sync` aplica ao espelhar. Não editar à mão — a próxima geração sobrescreve.",
        "",
        f"{len(specs)} specs em {len(por_frente)} frentes.",
        "",
    ]

    # "sem frente" por ultimo: e a fila de triagem, nao uma frente de verdade.
    ordem = sorted(k for k in por_frente if k != SEM_FRENTE)
    if SEM_FRENTE in por_frente:
        ordem.append(SEM_FRENTE)

    for frente in ordem:
        lines += [f"## {frente}", ""]
        if frente == SEM_FRENTE:
            lines += [
                "*Espelhadas antes de o carimbo `project:` existir. Preencher o campo no",
                "frontmatter move a spec para a frente certa na próxima geração.*",
                "",
            ]
        for page in por_frente[frente]:
            link = page.relative_to(wiki).as_posix()[:-3]
            titulo = _TITLE_RE.search(page.read_text(encoding="utf-8", errors="replace"))
            rotulo = titulo.group(1).strip() if titulo else page.stem
            lines.append(f"- [[{link}]] — {rotulo}")
        lines.append("")
    return "\n".join(lines)


def default_root() -> Path:
    """Resolve o sub-vault AI-Brain na precedencia do vault_sync.py."""
    ai_brain = os.environ.get("AI_BRAIN_PATH")
    if ai_brain:
        return Path(ai_brain)
    vault_root = os.environ.get("VAULT_PATH")
    if vault_root:
        return Path(vault_root) / "AI-Brain"
    return Path.home() / "Documents" / "Obsidian Vault" / "AI-Brain"


def _index_is_stale(root: Path) -> bool | None:
    """True/False se der para checar o wiki-index; None quando indisponivel."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        import build_wiki_index

        return bool(build_wiki_index.check_stale(str(root)))
    except Exception:
        return None


def build_digest(root: Path, *, max_decisions: int = 12) -> str:
    """Bloco curto para injetar no SessionStart.

    O index.md inteiro passa de 10 KB — caro demais para entrar em toda sessao. Este
    digest entrega o que faz diferenca no comeco: que o vault existe, o que ele cobre,
    quais decisoes ja estao registradas (a superficie de prior-art) e como consultar.
    """
    wiki = root / "wiki"
    if not wiki.is_dir():
        return ""

    contagens = [
        (title, len(section_pages(wiki, folder)))
        for folder, title, _ in SECTIONS
    ]
    povoadas = [f"{title} {count}" for title, count in contagens if count]
    if not povoadas:
        return ""

    linhas = [
        "VAULT AI-Brain disponivel (memoria de decisao entre sessoes).",
        f"Cobertura: {' · '.join(povoadas)}.",
    ]

    decisions = section_pages(wiki, "decisions")
    if decisions:
        nomes = [p.relative_to(wiki).as_posix()[:-3] for p in decisions[:max_decisions]]
        resto = len(decisions) - len(nomes)
        linhas.append(
            "Decisoes registradas: "
            + "; ".join(f"[[{n}]]" for n in nomes)
            + (f" (+{resto})" if resto > 0 else "")
        )

    stale = _index_is_stale(root)
    if stale:
        linhas.append(
            "O indice de busca esta desatualizado — rode "
             "scripts/build_wiki_index.py antes de confiar numa consulta."
        )

    linhas.append(
        "Antes de assimilar tecnica nova ou reabrir decisao, consulte com a skill "
        "wiki-query (checa prior art e evita relitigar o que ja foi recusado)."
    )
    return "\n".join(linhas)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=None, help="Raiz do sub-vault AI-Brain.")
    parser.add_argument(
        "--write", action="store_true", help="Grava wiki/index.md; sem a flag, so imprime."
    )
    parser.add_argument(
        "--digest", action="store_true",
        help="Imprime o bloco curto de SessionStart em vez do index completo.",
    )
    parser.add_argument(
        "--specs", action="store_true",
        help="Gera o indice de specs agrupado por frente em vez do index mestre.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    root = args.root or default_root()
    if args.digest:
        print(build_digest(root))
        return
    alvo = (root / "wiki" / "specs" / SPECS_INDEX_NAME) if args.specs else (
        root / "wiki" / "index.md"
    )
    content = build_specs_index(root) if args.specs else build_index(root)
    if args.write:
        alvo.parent.mkdir(parents=True, exist_ok=True)
        alvo.write_text(content, encoding="utf-8")
        print(f"gerado: {alvo}")
    else:
        print(content)


if __name__ == "__main__":
    main()
