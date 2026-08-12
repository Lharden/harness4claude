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
    ("sources", "Sources", "1 pagina por fonte processada."),
    ("entities", "Entities", "pessoas, produtos, ferramentas, organizacoes."),
    ("concepts", "Concepts", "ideias, metodos, padroes, frameworks."),
    ("decisions", "Decisions", "registros de assimilacao: o que veio de fora e o que ficou."),
    ("synthesis", "Synthesis", "escritos de ordem superior puxando varias paginas."),
    ("projects", "Projects", "projetos ativos."),
    ("ops", "Ops", "arquitetura de sync, runbooks, decisoes operacionais."),
    ("specs", "Specs", "espelhadas de docs/specs pelo vault_sync."),
    ("sessions", "Sessions", "espelhadas de harness/traces pelo vault_sync."),
    ("schema", "Schema", "configuracao do vault."),
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
        "Indice mestre, **gerado do disco** por `tools/wiki_index.py` — toda pagina de",
        "`wiki/` aparece aqui e toda entrada aqui aponta para pagina existente.",
        "Regenerar apos criar ou mover paginas; `tools/wiki_lint.py` acusa divergencia",
        "nos dois sentidos.",
        "",
    ]

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
        ("*Knowledge graphs por repo, exportados pelo graphify. Geradas por maquina — "
         "fora das checagens de orfa/estagnada do lint.*"),
        "",
    ]
    if (generated / "index.md").is_file():
        lines.append(f"- [[{GENERATED_SUBTREE}/index]] — convencao de export e catalogo")
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
        "- `.remember/` — buffer de sessao; espelhado para `raw/inbox/` pelo vault_sync",
        "",
        "## Como buscar",
        "",
        "- **Por categoria**: navegue para `wiki/{tipo}/`",
        "- **Por tag**: busca do Obsidian, `tag:#nome`",
        '- **Full-text**: busca do Obsidian ou `grep -r "termo" wiki/`',
        "- **Semantica**: operacao `query` — ver `AI-Brain/CLAUDE.md`",
        "",
        "## Saude",
        "",
        "```",
        'python tools/wiki_lint.py  --root "$VAULT_PATH/AI-Brain" --report',
        'python tools/wiki_index.py --root "$VAULT_PATH/AI-Brain" --write',
        "```",
        "",
    ]
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
    return parser


def main() -> None:
    args = build_parser().parse_args()
    root = args.root or default_root()
    if args.digest:
        print(build_digest(root))
        return
    content = build_index(root)
    if args.write:
        (root / "wiki" / "index.md").write_text(content, encoding="utf-8")
        print(f"index.md regenerado em {root / 'wiki' / 'index.md'}")
    else:
        print(content)


if __name__ == "__main__":
    main()
