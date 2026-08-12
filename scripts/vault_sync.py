#!/usr/bin/env python
"""Espelha artefatos vivos do Harness para o vault Obsidian (AI-Brain).

Mirrors (idempotente, baseado em mtime):
  ~/.claude/harness/traces/*.md   -> <vault>/wiki/sessions/
  <cwd>/docs/specs/*.md           -> <vault>/wiki/specs/          (carimba frontmatter)
  <cwd>/docs/CONTEXT.md           -> <vault>/wiki/decisions/      (carimba frontmatter)
  <cwd>/.remember/today-*.md      -> <vault>/raw/inbox/   (e C:/.remember tambem)

O CONTEXT.md gerado pela skill `discuss` ja e um registro de assimilacao em tres tiers
(Locked/Deferred/Discretion). Espelha-lo para wiki/decisions/ faz a camada de decisao do
vault se popular do trabalho que o pipeline ja produz, sem passo manual novo.

Degradacao graceful: se o vault nao existir, sai 0 sem erro. Usado pelo
harness-precompact.sh (auto-sync no handoff) e pela skill vault-bridge.

Uso:
    python vault_sync.py [--vault DIR] [--quiet]
Env: AI_BRAIN_PATH sobrescreve o default. NAO usar VAULT_PATH aqui: desde a
migracao MCP (2026-06-12), VAULT_PATH aponta para a RAIZ do vault Obsidian
(consumida pelo NODE_EXTRA_CA_CERTS/MCP), e o alvo deste sync e o sub-vault
AI-Brain — usar VAULT_PATH duplicaria a arvore wiki/ na raiz (bug 2026-06-12).
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import shutil
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("harness.vault_sync")


def _default_vault() -> Path:
    """Resolve o sub-vault AI-Brain de forma portavel (cross-OS, sem hardcode).

    Precedencia:
      1. AI_BRAIN_PATH  -> aponta direto para o sub-vault AI-Brain (uso preferido).
      2. VAULT_PATH     -> RAIZ do vault Obsidian; o alvo deste sync e a subpasta
         "AI-Brain" (nunca a raiz — usar a raiz duplicaria a arvore wiki/, bug
         2026-06-12). Por isso anexamos "/AI-Brain" em vez de usar VAULT_PATH cru.
      3. Fallback        -> ~/Documents/Obsidian Vault/AI-Brain (Windows/macOS/Linux
         via Path.home(), sem caminho de usuario hardcoded).
    """
    ai_brain = os.environ.get("AI_BRAIN_PATH")
    if ai_brain:
        return Path(ai_brain)
    vault_root = os.environ.get("VAULT_PATH")
    if vault_root:
        return Path(vault_root) / "AI-Brain"
    return Path.home() / "Documents" / "Obsidian Vault" / "AI-Brain"


DEFAULT_VAULT = _default_vault()


def newer(src: Path, dst: Path) -> bool:
    """True se src deve ser copiado (dst ausente ou mais antigo que src)."""
    return not dst.exists() or src.stat().st_mtime > dst.stat().st_mtime


def stamp_frontmatter(text: str, page_type: str, *, source: str, today: str) -> str:
    """Prefixa frontmatter Obsidian quando o texto nao tem — corpo intacto.

    Artefatos crus do harness (specs, CONTEXT) nascem sem frontmatter; sem carimbo
    eles chegam ao vault como paginas invalidas pelo schema do AI-Brain/CLAUDE.md.
    Carimbar na copia (e nao na origem) mantem o repo de trabalho limpo.
    """
    if text.lstrip("﻿ \t\r\n").startswith("---"):
        return text
    return (
        "---\n"
        f"type: {page_type}\n"
        f"created: {today}\n"
        f"updated: {today}\n"
        "status: active\n"
        f"tags: [{page_type}, harness]\n"
        f"source: {source}\n"
        "---\n\n"
    ) + text


def mirror(
    sources: list[Path],
    dst_dir: Path,
    *,
    page_type: str | None = None,
    rename: Callable[[Path], str] | None = None,
) -> int:
    """Copia cada source para dst_dir se mais novo. Retorna nº de cópias feitas.

    page_type carimba frontmatter na copia quando a origem nao tem; rename define
    o nome de destino (CONTEXT.md vira {projeto}-context.md, senao colidiria).
    """
    if not sources:
        return 0
    dst_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    copied = 0
    for src in sources:
        dst = dst_dir / (rename(src) if rename else src.name)
        if not newer(src, dst):
            continue
        if page_type:
            text = src.read_text(encoding="utf-8", errors="replace")
            dst.write_text(
                stamp_frontmatter(text, page_type, source=src.name, today=today),
                encoding="utf-8",
            )
            shutil.copystat(src, dst)  # preserva mtime: mantem o sync idempotente
        else:
            shutil.copy2(src, dst)
        copied += 1
    return copied


def glob_md(directory: Path, pattern: str = "*.md") -> list[Path]:
    """Lista arquivos que casam o pattern num diretório (vazio se ausente)."""
    return sorted(directory.glob(pattern)) if directory.is_dir() else []


def remember_today(cwd: Path) -> list[Path]:
    """Encontra notas .remember/today-*.md no cwd e em C:/.remember."""
    found: list[Path] = []
    for base in (cwd / ".remember", Path("C:/.remember")):
        found.extend(glob_md(base, "today-*.md"))
    return found


LOG_HEADER = """---
type: log
created: {hoje}
updated: {hoje}
status: active
tags:
  - meta
---

# Operations Log

Append-only. Cada ingest/inbox/lint/sync fica registrado aqui. Nunca reescreva — so
prune trimestral com flag `[lint]`.

Formato: `YYYY-MM-DD HH:MM — operation: short description`

---
"""


def append_log(vault: Path, message: str) -> None:
    """Append append-only em wiki/log.md (não falha se indisponível).

    Cria o arquivo com frontmatter quando ele ainda nao existe: sem isso, o primeiro
    sync de um vault novo ja nascia com um erro de lint (`missing_frontmatter`), porque
    o schema do AI-Brain exige frontmatter em toda pagina de wiki/.
    """
    log_file = vault / "wiki" / "log.md"
    agora = datetime.now(timezone.utc).astimezone()
    try:
        if not log_file.exists():
            log_file.parent.mkdir(parents=True, exist_ok=True)
            log_file.write_text(
                LOG_HEADER.format(hoje=agora.strftime("%Y-%m-%d")), encoding="utf-8"
            )
        with log_file.open("a", encoding="utf-8") as fh:
            fh.write(f"\n{agora.strftime('%Y-%m-%d %H:%M')} — {message}\n")
    except OSError as exc:
        logger.warning("nao foi possivel escrever log.md: %s", exc)


def project_slug(cwd: Path) -> str:
    """Slug kebab-case do projeto, usado para nomear a decisao no vault."""
    return re.sub(r"[^a-z0-9]+", "-", cwd.name.lower()).strip("-") or "projeto"


def context_docs(cwd: Path) -> list[Path]:
    """docs/CONTEXT.md do projeto atual, se existir."""
    context = cwd / "docs" / "CONTEXT.md"
    return [context] if context.is_file() else []


def sync(vault: Path, harness_dir: Path, cwd: Path) -> dict[str, int]:
    """Executa o espelhamento. Retorna contagens por destino."""
    slug = project_slug(cwd)
    counts = {
        "sessions": mirror(glob_md(harness_dir / "traces"), vault / "wiki" / "sessions"),
        "specs": mirror(
            glob_md(cwd / "docs" / "specs"), vault / "wiki" / "specs", page_type="spec"
        ),
        "decisions": mirror(
            context_docs(cwd),
            vault / "wiki" / "decisions",
            page_type="decision",
            rename=lambda _src: f"{slug}-context.md",
        ),
        "inbox": mirror(remember_today(cwd), vault / "raw" / "inbox"),
    }
    if any(counts.values()):
        append_log(
            vault,
            f"autosync: sessions:{counts['sessions']} specs:{counts['specs']} "
            f"decisions:{counts['decisions']} inbox:{counts['inbox']}",
        )
    return counts


def _default_harness_dir() -> Path:
    """Diretorio de estado: HARNESS_DIR se definida, senao ~/.claude/harness."""
    env = os.environ.get("HARNESS_DIR")
    return Path(env).expanduser().resolve() if env else Path.home() / ".claude" / "harness"


def main() -> int:
    """Ponto de entrada CLI."""
    parser = argparse.ArgumentParser(description="Espelha artefatos do Harness para o vault.")
    # DEFAULT_VAULT ja resolve AI_BRAIN_PATH / VAULT_PATH / ~ de forma portavel.
    parser.add_argument("--vault", type=Path, default=DEFAULT_VAULT)
    parser.add_argument("--harness-dir", type=Path, default=_default_harness_dir())
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING if args.quiet else logging.INFO, format="%(message)s")

    if not args.vault.is_dir():
        logger.info("vault inexistente em %s — sync ignorado", args.vault)
        return 0

    counts = sync(args.vault, args.harness_dir, Path.cwd())
    logger.info("vault-sync: sessions:%s specs:%s decisions:%s inbox:%s",
                counts["sessions"], counts["specs"], counts["decisions"], counts["inbox"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
