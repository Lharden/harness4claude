"""Migracao unica: notas diarias legadas de `raw/inbox/` para o nome com rotulo do repo.

Ate 2026-09-24 o vault_sync gravava `<repo>/.remember/today-X.md` como
`raw/inbox/today-X.md`, e so o repo de nota mais nova sobrevivia no vault. Depois do
conserto ele grava `raw/inbox/<repo>--today-X.md`. Sem esta migracao, cada pagina antiga
ficaria ao lado da nova do mesmo dono: no AI-Brain real, 41 duplicatas.

O que a migracao faz com cada `raw/inbox/today-*.md` (so o nivel de cima; `_processed/`
nao e tocado):

- **bytes**: a pagina e igual (a menos do fim de linha) a nota de UM repo -> renomeia.
- **nome-unico**: nao e igual a nenhuma, mas so um repo tem aquele nome -> e copia velha
  daquele dono; renomeia, e o proximo sync a atualiza.
- **ambigua**: igual a notas de dois repos, ou diferente de todas com dois donos -> fica.
- **sem fonte**: nenhum repo tem mais aquele nome -> fica.
- **conflito**: o nome novo ja existe -> fica.

Cada renome entra no manifesto do vault_sync com a pagina renomeada, para o sync
reconhecer a pagina como dele e nao escrever outra ao lado.

**Escreve no vault real**, que tem Obsidian Sync: o padrao e o ensaio, que so le.
`--aplicar` exige `--backup` fora do vault e vazio; o backup guarda a copia de cada
pagina, as entradas antigas do manifesto e o `mapa.json` (antigo -> novo, com sha).
`--restaurar` desfaz a partir dele, e nao desfaz pagina editada depois da migracao.

Ordem: rodar DEPOIS de o plugin com o vault_sync novo estar instalado, em cada maquina
que roda o harness. Com o plugin antigo, o proximo PreCompact recriaria o nome sem rotulo.

Uso:
    python tools/migrar_inbox_rotulado.py --repo DIR [--repo DIR ...]            # ensaio
    python tools/migrar_inbox_rotulado.py --repo DIR ... --aplicar --backup DIR
    python tools/migrar_inbox_rotulado.py --restaurar DIR
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import vault_sync as vs  # noqa: E402

MAPA = "mapa.json"


@dataclass
class Renome:
    antigo: str
    novo: str
    prova: str  # "bytes" | "nome-unico"
    fonte: Path
    processada: bool


@dataclass
class Plano:
    renomear: list[Renome] = field(default_factory=list)
    ambiguas: list[str] = field(default_factory=list)
    sem_fonte: list[str] = field(default_factory=list)
    conflitos: list[str] = field(default_factory=list)
    novas: list[str] = field(default_factory=list)


def _sha(dados: bytes) -> str:
    return hashlib.sha256(dados).hexdigest()


def _texto(caminho: Path) -> str:
    return caminho.read_bytes().decode("utf-8", errors="replace").replace("\r\n", "\n")


def _inbox(vault: Path) -> Path:
    return vault / "raw" / "inbox"


def _processada(inbox: Path, nomes: tuple[str, ...], texto: str) -> bool:
    """Ha copia de mesmo conteudo em `_processed/`, com algum dos nomes dados?"""
    for nome in nomes:
        copia = inbox / vs.PASTA_PROCESSADAS / nome
        if copia.is_file() and _texto(copia) == texto:
            return True
    return False


def planejar(
    vault: Path, repos: list[Path], *, remember_global: Path = vs.REMEMBER_GLOBAL
) -> Plano:
    """So le. Os nomes novos saem de `vs.fontes_do_inbox`, a mesma funcao do sync."""
    fontes: dict[Path, str] = {}
    for repo in repos:
        fontes.update(vs.fontes_do_inbox(repo, remember_global))
    por_nome: dict[str, list[tuple[Path, str]]] = {}
    for fonte, novo in fontes.items():
        por_nome.setdefault(fonte.name, []).append((fonte, novo))

    inbox = _inbox(vault)
    plano = Plano()
    alvos: set[str] = set()
    legados = sorted(p for p in inbox.glob("today-*.md") if p.is_file()) if inbox.is_dir() else []
    for pagina in legados:
        candidatas = por_nome.get(pagina.name, [])
        if not candidatas:
            plano.sem_fonte.append(pagina.name)
            continue
        texto = _texto(pagina)
        iguais = [(f, n) for f, n in candidatas if _texto(f) == texto]
        if len({n for _f, n in iguais}) == 1:
            (fonte, novo), prova = iguais[0], "bytes"
        elif not iguais and len({n for _f, n in candidatas}) == 1:
            (fonte, novo), prova = candidatas[0], "nome-unico"
        else:
            plano.ambiguas.append(pagina.name)
            continue
        if (inbox / novo).exists():
            plano.conflitos.append(pagina.name)
            continue
        alvos.add(novo)
        plano.renomear.append(Renome(
            antigo=pagina.name, novo=novo, prova=prova, fonte=fonte,
            processada=_processada(inbox, (pagina.name, novo), texto),
        ))

    for fonte, novo in sorted(fontes.items(), key=lambda item: item[1]):
        if novo in alvos or (inbox / novo).exists():
            continue
        if _processada(inbox, (novo, fonte.name), _texto(fonte)):
            continue
        plano.novas.append(novo)
    return plano


def _validar_backup(backup: Path, vault: Path) -> None:
    alvo, raiz = backup.resolve(), vault.resolve()
    if alvo == raiz or alvo.is_relative_to(raiz):
        raise ValueError(f"o backup tem de ficar fora do vault: {backup}")
    if alvo.exists() and any(alvo.iterdir()):
        raise ValueError(f"o backup tem de ser um diretorio novo ou vazio: {backup}")


def _gravar_mapa(backup: Path, mapa: dict[str, Any]) -> None:
    (backup / MAPA).write_text(json.dumps(mapa, ensure_ascii=False, indent=1), encoding="utf-8")


def aplicar(plano: Plano, vault: Path, *, manifesto: Path, backup: Path) -> dict[str, Any]:
    """Renomeia o que o plano mandou e registra cada pagina no manifesto do vault_sync."""
    _validar_backup(backup, vault)
    eventos: list[str] = []
    registro = vs.carregar_registro(manifesto, eventos)
    if registro is None:
        raise ValueError(f"manifesto ilegivel, nada foi feito: {'; '.join(eventos)}")
    if not plano.renomear:
        return {"renomeadas": 0, "eventos": eventos}

    inbox = _inbox(vault)
    (backup / "paginas").mkdir(parents=True)
    mapa: dict[str, Any] = {"manifesto": str(manifesto), "renomes": []}
    feitas = 0
    for renome in plano.renomear:
        antigo, novo = inbox / renome.antigo, inbox / renome.novo
        if not antigo.is_file() or novo.exists():
            eventos.append(f"pulada: {renome.antigo} (mudou desde o ensaio)")
            continue
        dados = antigo.read_bytes()
        shutil.copy2(antigo, backup / "paginas" / renome.antigo)
        chave_antiga, chave_nova = vs._chave(antigo), vs._chave(novo)
        mapa["renomes"].append({
            "antigo": renome.antigo, "novo": renome.novo, "sha": _sha(dados),
            "prova": renome.prova, "fonte": str(renome.fonte),
            "registro_antigo": registro.get(chave_antiga), "chave_nova": chave_nova,
            "chave_antiga": chave_antiga,
        })
        _gravar_mapa(backup, mapa)  # antes do renome: um crash no meio ainda desfaz
        antigo.rename(novo)
        registro.pop(chave_antiga, None)
        # Copia velha ("nome-unico") entra sem o sha da fonte: o sync ve a fonte como
        # mudada, ve a pagina intocada, e a atualiza.
        registro[chave_nova] = {
            "destino": _sha(dados),
            "fonte": _sha(renome.fonte.read_bytes()) if renome.prova == "bytes" else "",
            "origem": vs._chave(renome.fonte),
            "mtime": renome.fonte.stat().st_mtime,
        }
        feitas += 1
    vs.salvar_registro(manifesto, registro, eventos)
    if feitas:
        vs.append_log(vault, f"migracao-inbox: {feitas} notas diarias renomeadas para <repo>--today-*.md")
    return {"renomeadas": feitas, "eventos": eventos}


def restaurar(backup: Path, vault: Path, *, manifesto: Path) -> dict[str, Any]:
    """Desfaz os renomes do backup. Pagina editada depois da migracao fica como esta."""
    mapa = json.loads((backup / MAPA).read_text(encoding="utf-8"))
    eventos: list[str] = []
    registro = vs.carregar_registro(manifesto, eventos)
    if registro is None:
        raise ValueError(f"manifesto ilegivel, nada foi feito: {'; '.join(eventos)}")
    inbox = _inbox(vault)
    restauradas: list[str] = []
    mantidas: list[str] = []
    for item in mapa["renomes"]:
        antigo, novo = inbox / item["antigo"], inbox / item["novo"]
        if antigo.exists() or not novo.is_file() or _sha(novo.read_bytes()) != item["sha"]:
            mantidas.append(item["novo"])
            continue
        novo.rename(antigo)
        registro.pop(item["chave_nova"], None)
        if item["registro_antigo"] is not None:
            registro[item["chave_antiga"]] = item["registro_antigo"]
        restauradas.append(item["antigo"])
    vs.salvar_registro(manifesto, registro, eventos)
    return {"restauradas": restauradas, "mantidas": mantidas, "eventos": eventos}


def _imprimir(plano: Plano) -> None:
    grupos = [
        ("renomear (dono provado pelos bytes)", [f"{r.antigo} -> {r.novo}" + (
            "   [tambem em _processed/]" if r.processada else "")
            for r in plano.renomear if r.prova == "bytes"]),
        ("renomear (copia velha do unico dono; o sync atualiza)", [
            f"{r.antigo} -> {r.novo}" + ("   [tambem em _processed/]" if r.processada else "")
            for r in plano.renomear if r.prova == "nome-unico"]),
        ("ficam: ambiguas", plano.ambiguas),
        ("ficam: sem fonte viva", plano.sem_fonte),
        ("ficam: nome novo ja ocupado", plano.conflitos),
        ("entram no proximo sync (nunca chegaram ao vault)", plano.novas),
    ]
    for titulo, itens in grupos:
        print(f"\n{titulo}: {len(itens)}")
        for item in itens:
            print(f"  {item}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migra as notas diarias legadas de raw/inbox para <repo>--today-*.md."
    )
    parser.add_argument("--vault", type=Path, default=vs.DEFAULT_VAULT)
    parser.add_argument("--repo", type=Path, action="append", default=[],
                        help="repositorio cujo .remember alimenta o inbox (repetivel)")
    parser.add_argument("--remember-global", type=Path, default=vs.REMEMBER_GLOBAL)
    parser.add_argument("--manifesto", type=Path, default=None,
                        help=f"default: <raiz do harness>/{vs.MANIFESTO}")
    parser.add_argument("--aplicar", action="store_true", help="sem isto, so o ensaio")
    parser.add_argument("--backup", type=Path, default=None)
    parser.add_argument("--restaurar", type=Path, default=None, metavar="BACKUP")
    args = parser.parse_args()
    manifesto = args.manifesto or vs._default_harness_dir() / vs.MANIFESTO

    if args.restaurar:
        relatorio = restaurar(args.restaurar, args.vault, manifesto=manifesto)
        print(json.dumps(relatorio, ensure_ascii=False, indent=1))
        return 0
    if not args.repo:
        parser.error("informe ao menos um --repo")
    plano = planejar(args.vault, args.repo, remember_global=args.remember_global)
    _imprimir(plano)
    if not args.aplicar:
        print("\nensaio: nada foi escrito. Para aplicar: --aplicar --backup <dir fora do vault>")
        return 0
    if args.backup is None:
        parser.error("--aplicar exige --backup")
    relatorio = aplicar(plano, args.vault, manifesto=manifesto, backup=args.backup)
    print(json.dumps(relatorio, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    # Mesmo bloco de `vault_maintenance.py`: `tools/` no `sys.path` vale nos dois modos
    # de invocacao (`python tools/x.py` e `python -m tools.x`).
    import os as _os

    _AQUI = _os.path.dirname(_os.path.abspath(__file__))
    if _AQUI not in sys.path:
        sys.path.insert(0, _AQUI)

    from console import usar_utf8

    usar_utf8()
    raise SystemExit(main())
