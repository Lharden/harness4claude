#!/usr/bin/env python3
"""deploy_to_cache.py — leva o repo para o plugin instalado, e prova que chegou.

## Por que existe

Ate 2026-09-02 o deploy era `cp` digitado na hora. Duas sessoes trabalhando em
checkouts diferentes deployaram no mesmo cache em janelas proximas, cada uma
sobrescrevendo pedacos da outra, e o que rodava deixou de existir em qualquer
repo. O defeito so apareceu horas depois, como um teste vermelho que parecia
nao ter relacao nenhuma com deploy.

`cp` nao e o problema; a ausencia de uma resposta barata para "o que roda e o
que esta versionado?" e. Este script da essa resposta em um comando, e
`tests/test_deploy_drift.py` a transforma em portao.

## O inventario e derivado, nunca escrito

A lista de arquivos vem de `git ls-files`. Uma lista mantida a mao envelhece em
silencio: o arquivo novo nao entra nela, nao viaja, e o cache fica velho sem
nada acusar — que e exatamente o modo de falha que este script existe para
fechar.

## O que ele NAO faz

Nao apaga nada no destino. Um arquivo que existe no cache e nao no repo pode
ser trabalho de outra sessao ainda nao commitado, e apagar seria repetir o
incidente com o sinal trocado. O `--check` reporta divergencia em uma direcao
so: repo -> cache.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness_paths  # noqa: E402

#: Nao viajam para o plugin: infra de CI, worktrees aninhados, bytecode.
NAO_VIAJAM = ("__pycache__", ".github", "worktrees", ".ruff_cache", ".pytest_cache")


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def installed_root() -> Path | None:
    """Onde o plugin esta instalado nesta maquina, ou None se nao houver.

    Le o marcador primeiro porque e o que os hooks leem; cai no registro do
    host so quando o marcador falta. Devolve None quando o alvo resolvido e o
    proprio repo — comparar uma arvore consigo mesma nao prova nada.
    """
    candidatos = []
    # INV-4: o diretorio de ESTADO se resolve por `default_root()`, que honra
    # HARNESS_DIR. Compor `~/.claude/harness` aqui a mao faria este script ler
    # um marcador diferente do que os hooks escrevem quando a variavel esta
    # setada — e o script existe justamente para dizer o que roda de verdade.
    marcador = harness_paths.default_root() / "plugin-root"
    try:
        candidatos.append(Path(marcador.read_text(encoding="utf-8").strip()))
    except OSError:
        pass
    registro = Path.home() / ".claude" / "plugins" / "installed_plugins.json"
    try:
        dados = json.loads(registro.read_text(encoding="utf-8"))
        for chave, entradas in (dados.get("plugins") or {}).items():
            if "harness4claude" not in str(chave):
                continue
            for entrada in entradas if isinstance(entradas, list) else [entradas]:
                caminho = (entrada or {}).get("installPath")
                if caminho:
                    candidatos.append(Path(caminho))
    except (OSError, ValueError, AttributeError):
        pass

    raiz = repo_root().resolve()
    for alvo in candidatos:
        try:
            resolvido = alvo.resolve()
        except OSError:
            continue
        if resolvido == raiz or not (resolvido / "scripts").is_dir():
            continue
        return resolvido
    return None


def shipped_files(root: Path) -> list[Path]:
    """Arquivos versionados que viajam, em caminhos relativos a `root`."""
    try:
        saida = subprocess.run(
            ["git", "ls-files", "-z"], cwd=str(root),
            capture_output=True, text=True, encoding="utf-8", timeout=60, check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    arquivos = []
    for bruto in saida.split("\0"):
        if not bruto.strip():
            continue
        rel = Path(bruto)
        if any(parte in NAO_VIAJAM for parte in rel.parts):
            continue
        arquivos.append(rel)
    return arquivos


def same_content(a: Path, b: Path) -> bool:
    """Igualdade de conteudo, indiferente a fim de linha.

    O cache recebe copias feitas por caminhos diferentes (git, cp, editor) e
    CRLF aparece sem que uma linha de codigo tenha mudado. Tratar isso como
    divergencia encheria o portao de ruido e ele deixaria de ser lido.
    """
    try:
        return a.read_bytes().replace(b"\r\n", b"\n") == b.read_bytes().replace(b"\r\n", b"\n")
    except OSError:
        return False


def drift(origem: Path, destino: Path, arquivos) -> list[Path]:
    """Arquivos do repo que faltam no destino ou diferem dele."""
    divergentes = []
    for rel in arquivos:
        alvo = destino / rel
        if not alvo.is_file() or not same_content(origem / rel, alvo):
            divergentes.append(rel)
    return divergentes


def apply(origem: Path, destino: Path, arquivos) -> list[Path]:
    copiados = []
    for rel in arquivos:
        alvo = destino / rel
        alvo.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(origem / rel, alvo)
        copiados.append(rel)
    return copiados


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Compara e sincroniza repo -> plugin instalado.")
    ap.add_argument("--apply", action="store_true", help="copia os divergentes (default: so reporta)")
    ap.add_argument("--check", action="store_true", help="explicito; e o default")
    a = ap.parse_args(argv)
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    raiz = repo_root()
    alvo = installed_root()
    if alvo is None:
        print("nenhum plugin harness4claude instalado nesta maquina")
        return 0

    arquivos = shipped_files(raiz)
    divergentes = drift(raiz, alvo, arquivos)
    print(f"repo:   {raiz}")
    print(f"plugin: {alvo}")
    print(f"{len(arquivos)} arquivos versionados, {len(divergentes)} divergentes")
    for rel in divergentes[:40]:
        print(f"  {rel}")
    if len(divergentes) > 40:
        print(f"  ... e mais {len(divergentes) - 40}")
    if not divergentes:
        return 0
    if not a.apply:
        print("\nrode com --apply para sincronizar")
        return 1
    copiados = apply(raiz, alvo, divergentes)
    restante = drift(raiz, alvo, arquivos)
    print(f"\ncopiados: {len(copiados)}; divergentes apos copia: {len(restante)}")
    return 1 if restante else 0


if __name__ == "__main__":
    raise SystemExit(main())
