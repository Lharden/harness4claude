#!/usr/bin/env python
"""Espelha artefatos vivos do Harness para o vault Obsidian (AI-Brain).

Mirrors (idempotente, baseado em mtime):
  ~/.claude/harness/traces/*.md   -> <vault>/wiki/sessions/
  <cwd>/docs/specs/*.md           -> <vault>/wiki/specs/
  <cwd>/.remember/today-*.md      -> <vault>/raw/inbox/   (e C:/.remember tambem)

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
import shutil
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


def mirror(sources: list[Path], dst_dir: Path) -> int:
    """Copia cada source para dst_dir se mais novo. Retorna nº de cópias feitas."""
    if not sources:
        return 0
    dst_dir.mkdir(parents=True, exist_ok=True)
    copied = 0
    for src in sources:
        dst = dst_dir / src.name
        if newer(src, dst):
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


def append_log(vault: Path, message: str) -> None:
    """Append append-only em wiki/log.md (não falha se indisponível)."""
    log_file = vault / "wiki" / "log.md"
    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    try:
        with log_file.open("a", encoding="utf-8") as fh:
            fh.write(f"\n{stamp} — {message}\n")
    except OSError as exc:
        logger.warning("nao foi possivel escrever log.md: %s", exc)


def sync(vault: Path, harness_dir: Path, cwd: Path) -> dict[str, int]:
    """Executa o espelhamento. Retorna contagens por destino."""
    counts = {
        "sessions": mirror(glob_md(harness_dir / "traces"), vault / "wiki" / "sessions"),
        "specs": mirror(glob_md(cwd / "docs" / "specs"), vault / "wiki" / "specs"),
        "inbox": mirror(remember_today(cwd), vault / "raw" / "inbox"),
    }
    if any(counts.values()):
        append_log(vault, f"autosync: sessions:{counts['sessions']} specs:{counts['specs']} inbox:{counts['inbox']}")
    return counts


def main() -> int:
    """Ponto de entrada CLI."""
    parser = argparse.ArgumentParser(description="Espelha artefatos do Harness para o vault.")
    # DEFAULT_VAULT ja resolve AI_BRAIN_PATH / VAULT_PATH / ~ de forma portavel.
    parser.add_argument("--vault", type=Path, default=DEFAULT_VAULT)
    parser.add_argument("--harness-dir", type=Path, default=Path.home() / ".claude" / "harness")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING if args.quiet else logging.INFO, format="%(message)s")

    if not args.vault.is_dir():
        logger.info("vault inexistente em %s — sync ignorado", args.vault)
        return 0

    counts = sync(args.vault, args.harness_dir, Path.cwd())
    logger.info("vault-sync: sessions:%s specs:%s inbox:%s",
                counts["sessions"], counts["specs"], counts["inbox"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
