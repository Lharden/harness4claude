"""Health check read-only da wiki AI-Brain (padrão LLM Wiki).

Reporta, nunca corrige — conforme AI-Brain/CLAUDE.md ("NUNCA corrige
automaticamente. So reporta."). Mesmo contrato de saida do vault_sync_doctor:
JSON estruturado no stdout, `ready` booleano, exit 1 quando ha erros.

Uso:
    python tools/wiki_lint.py [--root DIR] [--stale-days N] [--report]
Env: AI_BRAIN_PATH / VAULT_PATH resolvem o sub-vault quando --root e omitido,
na mesma precedência do scripts/vault_sync.py.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

# Páginas meta: existem para serem o indice/registro, não para receber in-links.
META_PAGES = {"index.md", "log.md"}

# `type: index` no frontmatter cumpre o mesmo papel em qualquer pasta: um índice existe
# para ser linkado DE, não PARA. Sem esta regra todo índice gerado nasce órfão, e a
# "correção" seria linka-lo de algum lugar artificial so para calar o lint.
_INDEX_TYPE_RE = re.compile(r"^type:\s*index\s*$", re.M)
_FRONTMATTER_BLOCK_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)

# Subarvore gerada por maquina (graphify). Vale como alvo de link, mas não entra
# nas checagens de frontmatter/orfa/estagnada — 900+ notas inundariam o relatório.
GENERATED_SUBTREE = "graphs"

# Alvos que aparecem em exemplos de schema, não são links reais.
PLACEHOLDER_BASENAMES = {"page-name", "link", "none", "slug", "..."}

_WIKILINK_RE = re.compile(r"\[\[([^\]\n]+?)\]\]")

DEFAULT_STALE_DAYS = 90

# A partir de quantas páginas uma subarvore precisa de uma porta `00 ...` de entrada.
MIN_PAGES_FOR_MOC = 5

# Campos que o Dataview consulta e que o schema do AI-Brain/CLAUDE.md exige. `type`
# classifica; `updated` ordena. Sem eles a página existe mas não aparece em consulta
# nenhuma — presente no disco, ausente na navegação.
REQUIRED_FIELDS = ("type", "updated")


@dataclass(frozen=True)
class Finding:
    """Um achado do lint."""

    level: str
    check: str
    message: str
    page: str | None = None


@dataclass
class _Report:
    """Acumulador interno de findings."""

    findings: list[Finding] = field(default_factory=list)

    def add(self, level: str, check: str, message: str, page: str | None = None) -> None:
        self.findings.append(Finding(level, check, message, page))


def _default_root() -> Path:
    """Resolve o sub-vault AI-Brain na mesma precedência do vault_sync.py."""
    ai_brain = os.environ.get("AI_BRAIN_PATH")
    if ai_brain:
        return Path(ai_brain)
    vault_root = os.environ.get("VAULT_PATH")
    if vault_root:
        return Path(vault_root) / "AI-Brain"
    return Path.home() / "Documents" / "Obsidian Vault" / "AI-Brain"


def has_frontmatter(path: Path) -> bool:
    """True se o arquivo abre com o delimitador `---` de frontmatter YAML."""
    try:
        with path.open(encoding="utf-8") as handle:
            return handle.readline().strip() == "---"
    except OSError:
        return False


def missing_fields(path: Path) -> list[str]:
    """Campos do schema ausentes no frontmatter.

    O lint checava presença de frontmatter, não seu conteúdo — então três páginas
    escritas a mao em junho passaram anos com `tipo:`/`data:` em portugues em vez de
    `type:`/`updated:`. Elas eram validas para o lint e **invisíveis para o Dataview**,
    que consulta por nome de campo. Presença sem contrato não e validação.
    """
    match = _FRONTMATTER_BLOCK_RE.match(_read(path))
    if not match:
        return list(REQUIRED_FIELDS)
    bloco = match.group(1)
    return [c for c in REQUIRED_FIELDS if not re.search(rf"^{c}:\s*\S", bloco, re.M)]


def is_index_page(path: Path) -> bool:
    """True se a página se declara `type: index` no frontmatter."""
    match = _FRONTMATTER_BLOCK_RE.match(_read(path))
    return bool(match and _INDEX_TYPE_RE.search(match.group(1)))


def parse_wikilinks(text: str) -> list[str]:
    """Extrai alvos de `[[...]]`, sem alias (`|`), sem ancora (`#`), sem placeholders."""
    targets: list[str] = []
    for raw in _WIKILINK_RE.findall(text):
        target = raw.split("|")[0].split("#")[0].strip()
        if not target or ".." in target.replace("../", ""):
            continue
        if target.rsplit("/", 1)[-1].lower() in PLACEHOLDER_BASENAMES:
            continue
        targets.append(target)
    return targets


def _read(path: Path) -> str:
    """Le um arquivo de texto tolerando encoding sujo."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _wiki_pages(root: Path) -> list[Path]:
    """Páginas de wiki/ sujeitas ao lint (exclui a subarvore gerada)."""
    wiki = root / "wiki"
    if not wiki.is_dir():
        return []
    return sorted(
        path
        for path in wiki.rglob("*.md")
        if GENERATED_SUBTREE not in path.relative_to(wiki).parts[:1]
    )


def _graph_notes(root: Path) -> list[Path]:
    """Notas geradas pelo graphify (contadas a parte)."""
    generated = root / "wiki" / GENERATED_SUBTREE
    return sorted(generated.rglob("*.md")) if generated.is_dir() else []


def _rel(path: Path, root: Path) -> str:
    """Caminho relativo a wiki/, em posix."""
    return path.relative_to(root / "wiki").as_posix()


def _link_resolver(root: Path):
    """Constroi um resolvedor de wikilink no comportamento do Obsidian.

    Tenta, nesta ordem: caminho relativo a wiki/, caminho relativo a página de
    origem (cobre `../` e saidas para output/), e por fim match de basename em
    todo o sub-vault.
    """
    by_basename = {path.stem for path in root.rglob("*.md")}

    def resolves(target: str, source: Path) -> bool:
        stem = target[:-3] if target.endswith(".md") else target
        candidates = (
            root / "wiki" / f"{stem}.md",
            source.parent / f"{stem}.md",
        )
        if any(candidate.is_file() for candidate in candidates):
            return True
        return stem.rsplit("/", 1)[-1] in by_basename

    return resolves


def inbox_redundant_pairs(root: Path) -> list[str]:
    """Notas de raw/inbox que já tem gemea `.done.md` — captura duplicada."""
    inbox = root / "raw" / "inbox"
    if not inbox.is_dir():
        return []
    return sorted(
        path.name
        for path in inbox.glob("*.md")
        if not path.name.endswith(".done.md")
        and (inbox / f"{path.name[:-3]}.done.md").is_file()
    )


def subtrees_without_moc(root: Path, *, minimo: int = MIN_PAGES_FOR_MOC) -> list[str]:
    """Subarvores de `wiki/` grandes o bastante para precisar de porta de entrada.

    Uma pasta com uma ou duas páginas se le direto; a partir de um punhado, quem chega
    sem contexto precisa de um `00 ...` dizendo por onde comecar. Aviso, não erro: e um
    julgamento editorial, e o lint não decide isso por você.

    So vale da profundidade 2 para baixo. As áreas de primeiro nível (`projects/`,
    `specs/`…) já tem porta: o MOC raiz cobre todas por construção. Cobrar um `00 ...`
    delas seria pedir duplicata do que o MOC já diz.
    """
    wiki = root / "wiki"
    if not wiki.is_dir():
        return []
    faltando = []
    for directory in sorted(wiki.rglob("*")):
        if not directory.is_dir():
            continue
        partes = directory.relative_to(wiki).parts
        if GENERATED_SUBTREE in partes or len(partes) < 2:
            continue
        paginas = [p for p in directory.glob("*.md") if p.name not in META_PAGES]
        if len(paginas) < minimo:
            continue
        if not any(p.name.startswith("00 ") or is_index_page(p) for p in paginas):
            faltando.append(directory.relative_to(wiki).as_posix())
    return faltando


def stray_wiki_tree(root: Path) -> bool:
    """True se ha uma arvore `wiki/` irma do AI-Brain — regressão do bug VAULT_PATH.

    So acusa quando a assinatura bate (log.md ou specs/ dentro), para não
    confundir com uma pasta `wiki` legítima do usuário no vault.
    """
    sibling = root.parent / "wiki"
    if not sibling.is_dir():
        return False
    return (sibling / "log.md").is_file() or (sibling / "specs").is_dir()


def analyze_wiki(root: Path, *, stale_days: int = DEFAULT_STALE_DAYS) -> dict[str, Any]:
    """Analisa a wiki e devolve o resultado estruturado. Não escreve nada."""
    root = root.resolve()
    report = _R = _Report()
    pages = _wiki_pages(root)
    graph_notes = _graph_notes(root)

    if not (root / "wiki").is_dir():
        _R.add("error", "no_wiki", f"Sem diretorio wiki/ em {root}.")

    resolves = _link_resolver(root)
    lintable = [page for page in pages if page.name not in META_PAGES]
    # Índices entram no lint de frontmatter e cobertura, mas não na checagem de in-link.
    navegacao = {page for page in pages if is_index_page(page)}

    # --- frontmatter ------------------------------------------------------
    missing_frontmatter = [_rel(page, root) for page in pages if not has_frontmatter(page)]
    for rel in missing_frontmatter:
        _R.add("error", "missing_frontmatter", f"Sem frontmatter: {rel}", rel)

    campos_faltando: dict[str, list[str]] = {}
    for page in pages:
        if not has_frontmatter(page):
            continue  # já reportado acima; não duplicar o mesmo defeito
        ausentes = missing_fields(page)
        if ausentes:
            rel = _rel(page, root)
            campos_faltando[rel] = ausentes
            _R.add(
                "error",
                "missing_frontmatter_field",
                f"Frontmatter sem {', '.join(ausentes)} — invisivel para o Dataview: {rel}",
                rel,
            )

    # --- wikilinks e os DOIS grafos de in-link ----------------------------
    # `alcancavel` conta link de qualquer página, índice inclusive: responde "da para
    # chegar la?". `integrado` so conta link vindo de página de conteúdo: responde "isto
    # esta tecido no conhecimento, ou so catalogado?".
    #
    # Separar os dois e o que permite existir um MOC que linka tudo. Com um grafo so, o
    # primeiro índice genérico zeraria a checagem de órfã para sempre — todo mundo
    # linkado, ninguém integrado, e o instrumento cego.
    alcancavel: dict[str, int] = {_rel(page, root): 0 for page in pages}
    integrado: dict[str, int] = {_rel(page, root): 0 for page in pages}
    # Chaveado pelo último segmento: `../projects/x` e `projects/x` são o mesmo alvo.
    broken: dict[str, set[str]] = {}
    index_path = root / "wiki" / "index.md"
    index_targets: list[str] = []

    for page in pages:
        targets = parse_wikilinks(_read(page))
        if page == index_path:
            index_targets = targets
        e_navegacao = page.name in META_PAGES or page in navegacao
        for target in targets:
            if not resolves(target, page):
                broken.setdefault(target.rsplit("/", 1)[-1], set()).add(_rel(page, root))
                continue
            for rel in alcancavel:
                if rel == f"{target}.md" or Path(rel).stem == target.rsplit("/", 1)[-1]:
                    alcancavel[rel] += 1
                    if not e_navegacao:
                        integrado[rel] += 1

    for target, sources in sorted(broken.items()):
        _R.add(
            "error",
            "broken_wikilink",
            f"Wikilink sem destino: [[{target}]] (em {', '.join(sorted(sources))})",
        )

    # --- cobertura do index.md -------------------------------------------
    indexed = {target.rsplit("/", 1)[-1] for target in index_targets}
    missing_from_index = [
        _rel(page, root) for page in lintable if page.stem not in indexed
    ]
    phantom_entries = [
        target for target in index_targets if not resolves(target, index_path)
    ]
    for rel in missing_from_index:
        _R.add("error", "not_in_index", f"Pagina fora do index.md: {rel}", rel)
    for target in phantom_entries:
        _R.add("error", "index_phantom", f"index.md aponta para pagina inexistente: {target}")

    # --- inalcancáveis, nao-integradas e estagnadas -----------------------
    cutoff = time.time() - stale_days * 86400
    unreachable, orphans, stale = [], [], []
    for page in lintable:
        if page in navegacao:
            continue
        rel = _rel(page, root)
        if alcancavel[rel] == 0:
            unreachable.append(rel)
        if integrado[rel] > 0:
            continue
        orphans.append(rel)
        if page.stat().st_mtime < cutoff:
            stale.append(rel)
    for rel in unreachable:
        _R.add("error", "unreachable_page", f"Nenhum link chega ate: {rel}", rel)
    for rel in orphans:
        _R.add(
            "warning",
            "orphan_page",
            f"Alcancavel so por indice, sem citacao de conteudo: {rel}",
            rel,
        )
    for rel in stale:
        _R.add(
            "warning",
            "stale_page",
            f"Sem citacao de conteudo e sem toque ha mais de {stale_days} dias: {rel}",
            rel,
        )

    # --- inbox e arvore órfã ----------------------------------------------
    redundant = inbox_redundant_pairs(root)
    for name in redundant:
        _R.add("warning", "inbox_redundant", f"raw/inbox tem par redundante .md/.done.md: {name}")

    stray = stray_wiki_tree(root)
    if stray:
        _R.add(
            "error",
            "stray_wiki_tree",
            f"Arvore wiki/ orfa na raiz do vault: {(root.parent / 'wiki').as_posix()} "
            "(regressao do bug VAULT_PATH de 2026-06-12).",
        )

    # --- subarvore grande sem porta de entrada ----------------------------
    sem_moc = subtrees_without_moc(root)
    for rel in sem_moc:
        _R.add(
            "warning",
            "subtree_without_moc",
            f"Subarvore com {MIN_PAGES_FOR_MOC}+ paginas e sem `00 ...` de entrada: {rel}",
            rel,
        )

    inbox_dir = root / "raw" / "inbox"
    inbox_total = len(list(inbox_dir.glob("*.md"))) if inbox_dir.is_dir() else 0

    errors = [f for f in report.findings if f.level == "error"]
    warnings = [f for f in report.findings if f.level == "warning"]
    return {
        "root": root.as_posix(),
        "ready": not errors,
        "summary": {
            "pages": len(pages),
            "graph_notes": len(graph_notes),
            "missing_frontmatter": missing_frontmatter,
            "missing_frontmatter_fields": campos_faltando,
            "broken_wikilinks": sorted(broken),
            "pages_missing_from_index": missing_from_index,
            "index_phantom_entries": phantom_entries,
            "unreachable_pages": unreachable,
            "orphan_pages": orphans,
            "stale_pages": stale,
            "subtrees_without_moc": sem_moc,
            "inbox_files": inbox_total,
            "inbox_redundant_pairs": redundant,
            "stray_wiki_tree": stray,
            "error_count": len(errors),
            "warning_count": len(warnings),
        },
        "findings": [finding.__dict__ for finding in report.findings],
    }


def render_report(result: dict[str, Any]) -> str:
    """Renderiza o relatório priorizado em Markdown."""
    summary = result["summary"]
    lines = [
        f"# Wiki Lint — {date.today().isoformat()}",
        "",
        f"Vault: `{result['root']}`",
        (
            f"Veredito: **{'OK' if result['ready'] else 'ERROS'}** "
            f"({summary['error_count']} erros, {summary['warning_count']} avisos) "
            f"sobre {summary['pages']} paginas (+{summary['graph_notes']} notas geradas)."
        ),
    ]
    for level, header in (("error", "Erros"), ("warning", "Avisos")):
        rows = [f for f in result["findings"] if f["level"] == level]
        lines += ["", f"## {header}", ""]
        lines += [f"- `{f['check']}` — {f['message']}" for f in rows] or ["- (nenhum)"]
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=None, help="Raiz do sub-vault AI-Brain.")
    parser.add_argument("--stale-days", type=int, default=DEFAULT_STALE_DAYS)
    parser.add_argument(
        "--report",
        action="store_true",
        help="Alem do JSON, escreve output/lint-{data}.md no vault (unica escrita do tool).",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    root = args.root or _default_root()
    result = analyze_wiki(root, stale_days=args.stale_days)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.report:
        out_dir = Path(result["root"]) / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"lint-{date.today().isoformat()}.md").write_text(
            render_report(result), encoding="utf-8"
        )
    if not result["ready"]:
        raise SystemExit(1)


if __name__ == "__main__":
    # `tools/` e sys.path[0] quando o arquivo roda como script; em modo importado
    # este bloco nao executa, e o stdout do chamador fica intacto.
    from console import usar_utf8

    usar_utf8()
    main()
