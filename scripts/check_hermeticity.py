#!/usr/bin/env python3
"""check_hermeticity.py — integridade do conjunto protegido do harness.

Tira um snapshot dos hashes do estado do harness e verifica, depois, que nada
mudou. Serve ao gate "suite verde sem tocar o diretorio real" da task P-1.b.

Existe como script separado — e nao apenas como teste — porque uma suite
interrompida nunca executa o teardown de um teste session-scoped, e o gate
precisa de verificacao externa e confiavel.

Uso:
    python scripts/check_hermeticity.py --snapshot antes.json
    python -m pytest tests/ -q
    python scripts/check_hermeticity.py --verify antes.json

Niveis de protecao (ver docs/self-reform/claude/TEST_MATRIX.md):

  Nivel A (padrao)   state.json, signals.json, trace-current.md, traces/**
                     Escritos por hooks de prompt ou por fechamento de task.
                     Ninguem os toca durante uma execucao de suite.

  Nivel B            .session-files-count
  (--include-        Escrito pelo PostToolUse a cada Edit/Write do usuario. Sob
   volatile)         sessao ativa muda por atividade legitima. So verificavel
                     com a sessao quiescente.

  Fora do conjunto   router/, skills-index/, graphify-autosetup/
                     Cache derivado e log, escritos pela propria sessao que
                     executa a suite. Verifica-los seria falso positivo.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

LEVEL_A = ("state.json", "signals.json", "trace-current.md")
LEVEL_A_GLOBS = ("traces/*.md",)
LEVEL_B = (".session-files-count",)

EXIT_OK = 0
EXIT_DIVERGED = 1
EXIT_USAGE = 2


def default_harness_dir() -> Path:
    env = os.environ.get("HARNESS_DIR")
    if env:
        return Path(env).expanduser().resolve()
    return Path.home() / ".claude" / "harness"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def collect(harness_dir: Path, *, include_volatile: bool) -> dict[str, str]:
    """Hashes do conjunto protegido. Ausente vira o sentinela ABSENT.

    Registrar ausencia importa: um arquivo que some entre snapshot e verify e
    divergencia, nao "nada a comparar".
    """
    names = list(LEVEL_A) + (list(LEVEL_B) if include_volatile else [])
    out: dict[str, str] = {}
    for name in names:
        p = harness_dir / name
        out[name] = _digest(p) if p.is_file() else "ABSENT"
    for pattern in LEVEL_A_GLOBS:
        for p in sorted(harness_dir.glob(pattern)):
            out[f"{p.parent.name}/{p.name}"] = _digest(p)
    return out


def snapshot(harness_dir: Path, dest: Path, *, include_volatile: bool) -> int:
    payload = {
        "harness_dir": str(harness_dir),
        "level": "A+B" if include_volatile else "A",
        "files": collect(harness_dir, include_volatile=include_volatile),
    }
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(f"snapshot: {len(payload['files'])} arquivos (nivel {payload['level']}) -> {dest}")
    return EXIT_OK


def verify(harness_dir: Path, src: Path, *, include_volatile: bool) -> int:
    try:
        payload = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"ERRO: snapshot ilegivel ({src}): {exc}", file=sys.stderr)
        return EXIT_USAGE

    before: dict[str, str] = payload.get("files", {})
    after = collect(harness_dir, include_volatile=include_volatile)

    diverged: list[str] = []
    for name in sorted(set(before) | set(after)):
        b = before.get(name, "AUSENTE-NO-SNAPSHOT")
        a = after.get(name, "AUSENTE-AGORA")
        if b != a:
            diverged.append(f"  {name}\n      antes:  {b[:16]}\n      depois: {a[:16]}")

    level = "A+B" if include_volatile else "A"
    if diverged:
        print(f"DIVERGENCIA no conjunto protegido (nivel {level}), {len(diverged)} arquivo(s):")
        print("\n".join(diverged))
        print(f"\nharness_dir inspecionado: {harness_dir}")
        if not include_volatile:
            print("Nota: o nivel B (.session-files-count) nao foi verificado.")
        return EXIT_DIVERGED

    print(f"OK: conjunto protegido intacto (nivel {level}, {len(after)} arquivos)")
    return EXIT_OK


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verifica integridade do conjunto protegido do harness.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--snapshot", type=Path, metavar="ARQUIVO")
    group.add_argument("--verify", type=Path, metavar="ARQUIVO")
    parser.add_argument("--harness-dir", type=Path, default=None)
    parser.add_argument(
        "--include-volatile",
        action="store_true",
        help="inclui o nivel B (.session-files-count); so use com a sessao quiescente",
    )
    args = parser.parse_args()

    harness_dir = (
        args.harness_dir.expanduser().resolve()
        if args.harness_dir is not None
        else default_harness_dir()
    )
    if not harness_dir.is_dir():
        print(f"ERRO: diretorio inexistente: {harness_dir}", file=sys.stderr)
        return EXIT_USAGE

    if args.snapshot is not None:
        return snapshot(harness_dir, args.snapshot, include_volatile=args.include_volatile)
    return verify(harness_dir, args.verify, include_volatile=args.include_volatile)


if __name__ == "__main__":
    raise SystemExit(main())
