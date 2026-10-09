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

Nao apaga o que ele nao implantou. Um arquivo que existe no cache e nao no repo
pode ser trabalho de outra sessao ainda nao commitado, e apagar seria repetir o
incidente com o sinal trocado. O `--check` reporta divergencia em uma direcao
so: repo -> cache.

## Remocao propaga (2026-10-09)

Ate aqui "nao apaga nada" tambem valia para o que o proprio deploy implantou. O
contrato foi de 1.3.0 para 1.4.0 removendo `node-result.schema.json`, e o
arquivo seguiu no cache: `--apply` so copiava, e o `--publicado` acusava o
arquivo e mandava rodar o mesmo `--apply` que nao o apagava.

Agora sai o que um deploy implantou antes e o repo deixou de versionar:

- **Manifesto** (`MANIFESTO`, na raiz do cache): o que o ultimo `--apply`
  entregou. Mesmo nome e mesmo formato que `mh deploy` escreve no mesmo cache
  (`master-harness/mh/deploy.py`): `{"versao": 1, "arquivos": [...]}`.
- **Legado**, cache anterior ao manifesto: so sai se o historico git prova que o
  caminho foi versionado e removido **e** o conteudo do cache e igual a uma
  versao historica dele.

"Removido do repo" se mede contra o `git ls-files` inteiro, nao contra o
inventario filtrado por `NAO_VIAJAM`: o outro deploy entrega `.github/` e este
nao, e ausencia do inventario filtrado nao e remocao.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness_paths  # noqa: E402

#: Nao viajam para o plugin: infra de CI, worktrees aninhados, bytecode.
NAO_VIAJAM = ("__pycache__", ".github", "worktrees", ".ruff_cache", ".pytest_cache")

#: Na raiz do cache: o que um deploy implantou ali. Mesmo nome e formato que o
#: `mh deploy` usa (`master-harness/mh/deploy.py`, constante `MANIFESTO`).
MANIFESTO = ".mh-deploy-manifest.json"
_VERSAO_MANIFESTO = 1

#: Existem SO no cache e nunca no repo: estado de execucao que o host escreve.
#:
#: `.in_use/<pid>` e a trava que marca o plugin em uso por uma sessao viva —
#: sete delas apareceram na primeira medicao do drift por sobra. Nao sao lixo de
#: deploy velho: sao a maquina dizendo quem esta usando isto agora.
#:
#: A distincao importa e e a mesma que separa as duas ausencias: arquivo que o
#: repo nao tem porque foi REMOVIDO e divergencia; arquivo que o repo nao tem
#: porque NUNCA e dele nao e. Sem esta lista, o guarda acusaria toda sessao
#: aberta — e portao que reprova sempre e portao que ninguem le.
SO_DO_CACHE = (".in_use", MANIFESTO)


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


def published_ref(root: Path) -> str | None:
    """A referencia que representa o PUBLICADO, ou None se nao houver.

    O cache espelha o que foi MESCLADO, nunca o que esta num ramo aberto.
    Comparar o cache com o worktree responde "o worktree esta implantado?", e
    num ramo a resposta e nao por definicao — todo ramo tem trabalho nao
    implantado, e deveria ter. A pergunta do incidente de 2026-09-02 e outra:
    "o que roda e codigo publicado?".
    """
    for ref in ("main", "origin/main", "master", "origin/master"):
        try:
            subprocess.run(
                ["git", "rev-parse", "--verify", "--quiet", ref + "^{commit}"],
                cwd=str(root), capture_output=True, timeout=30, check=True,
            )
            return ref
        except (OSError, subprocess.SubprocessError):
            continue
    return None


def files_in_tree(raiz_extraida: Path) -> list[Path]:
    """Arquivos que viajam dentro de uma arvore ja extraida."""
    arquivos = []
    for p in sorted(raiz_extraida.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(raiz_extraida)
        if any(parte in NAO_VIAJAM for parte in rel.parts):
            continue
        arquivos.append(rel)
    return arquivos


def extract_ref(root: Path, ref: str, destino: Path) -> Path:
    """Extrai a arvore de `ref` em `destino`. Um subprocesso, nao 250."""
    proc = subprocess.run(
        ["git", "archive", "--format=tar", ref],
        cwd=str(root), capture_output=True, timeout=300, check=True,
    )
    with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as tf:
        try:
            tf.extractall(destino, filter="data")   # py3.12+
        except TypeError:
            # o tar vem do `git archive` deste proprio repo, nao de fora
            tf.extractall(destino)
    return destino


def drift_publicado(root: Path, cache: Path, ref: str) -> list[Path]:
    """Arquivos em que o plugin instalado diverge do que foi publicado em `ref`.

    A lista de arquivos vem da arvore de `ref`, nao do worktree — e essa e a
    correcao inteira. Derivar do worktree exigiria que o cache contivesse
    arquivo que so existe no ramo, e reprovaria todo ramo aberto por
    construcao. Portao que reprova sempre e portao que ninguem le: o vermelho
    permanente treina a ignorar vermelho, e o proximo drift de verdade passa.

    E O SOBRANDO CONTA TAMBEM. Sao duas ausencias diferentes e so uma era vista:

    - arquivo no WORKTREE e nao em `ref`  -> trabalho de ramo, NAO reprova
    - arquivo no CACHE e nao em `ref`     -> deploy velho com lixo, REPROVA

    Derivar a lista so de `ref` torna o segundo invisivel: ninguem pergunta por
    um arquivo que `ref` nao tem. Medido em 2026-09-17: `e4212fb` removeu um
    arquivo e mais nada, e o cache de `e4212fb~1` passou como se estivesse em
    dia — `test_deploy_velho_REPROVA` acusou com `assert []`, que e o portao
    dizendo que nao ha divergencia entre uma arvore e a anterior a ela.

    Codigo removido que fica rodando e a MESMA falha que o drift existe para
    pegar, com o sinal trocado: nao e versao velha de um arquivo vivo, e um
    arquivo morto ainda vivo. `[superado: `drift(origem, cache,
    files_in_tree(origem))` — so a lista de `ref`]`
    """
    with tempfile.TemporaryDirectory(prefix="h4c-publicado-") as tmp:
        origem = extract_ref(root, ref, Path(tmp))
        do_ref = files_in_tree(origem)
        divergentes = drift(origem, cache, do_ref)
        esperados = set(do_ref)
        ja_acusados = set(divergentes)
        sobrando = [
            rel for rel in files_in_tree(cache)
            if rel not in esperados
            and rel not in ja_acusados
            and not any(parte in SO_DO_CACHE for parte in rel.parts)
        ]
        return divergentes + sobrando


def apply(origem: Path, destino: Path, arquivos) -> list[Path]:
    copiados = []
    for rel in arquivos:
        alvo = destino / rel
        alvo.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(origem / rel, alvo)
        copiados.append(rel)
    return copiados


def ler_manifesto(destino: Path) -> set[str]:
    """O que um deploy implantou em `destino`. Ausente ou ilegivel: conjunto vazio.

    Ilegivel nao derruba o deploy: sem manifesto nada sai pelo caminho do
    manifesto, que e o lado seguro de errar.
    """
    try:
        dados = json.loads((destino / MANIFESTO).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    itens = dados.get("arquivos") if isinstance(dados, dict) else None
    if not isinstance(itens, list):
        return set()
    return {i for i in itens if isinstance(i, str)}


def gravar_manifesto(destino: Path, arquivos: set[str]) -> None:
    texto = json.dumps(
        {"versao": _VERSAO_MANIFESTO, "arquivos": sorted(arquivos)}, ensure_ascii=False, indent=1,
    ) + "\n"
    alvo = destino / MANIFESTO
    try:
        if alvo.read_bytes() == texto.encode("utf-8"):
            return
    except OSError:
        pass
    destino.mkdir(parents=True, exist_ok=True)
    tmp = destino / (MANIFESTO + ".tmp")
    tmp.write_bytes(texto.encode("utf-8"))
    os.replace(tmp, alvo)


def _git_bytes(root: Path, *args: str) -> bytes | None:
    try:
        proc = subprocess.run(
            ["git", "-c", "core.quotepath=false", *args], cwd=str(root),
            capture_output=True, timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def _rastreados(root: Path) -> set[str] | None:
    """Todo o `git ls-files`, sem o filtro de `NAO_VIAJAM`. None se o git falhar."""
    bruto = _git_bytes(root, "ls-files", "-z")
    if bruto is None:
        return None
    return {c.decode("utf-8", "replace") for c in bruto.split(b"\0") if c}


def _provados_pelo_historico(root: Path, destino: Path, candidatos: list[Path]) -> list[Path]:
    """Candidatos que o historico git prova terem sido implantados por um deploy.

    Para cache anterior ao manifesto. O caminho precisa ter sido apagado em algum
    commit **e** o conteudo do cache precisa ser igual (CRLF normalizado) a uma
    versao historica dele. So o nome nao basta: um arquivo de outra sessao pode
    reusar o nome de um que saiu do repo.
    """
    if not candidatos:
        return []
    bruto = _git_bytes(
        root, "log", "--diff-filter=D", "--no-renames", "--name-only", "-z", "--format=",
    )
    if not bruto:
        return []
    apagados = {c.decode("utf-8", "replace") for c in bruto.split(b"\0") if c}
    provados = []
    for rel in candidatos:
        nome = rel.as_posix()
        if nome not in apagados:
            continue
        try:
            atual = (destino / rel).read_bytes().replace(b"\r\n", b"\n")
        except OSError:
            continue
        commits = _git_bytes(
            root, "log", "--no-renames", "--max-count=200", "--format=%H", "--", nome,
        )
        for sha in (commits or b"").decode("ascii", "replace").split():
            versao = _git_bytes(root, "show", f"{sha}:{nome}")
            if versao is not None and versao.replace(b"\r\n", b"\n") == atual:
                provados.append(rel)
                break
    return provados


def removidos_do_repo(origem: Path, destino: Path) -> list[Path]:
    """Arquivos que um deploy anterior implantou em `destino` e o repo deixou de versionar.

    So sai candidato que **ja e extra**: existe no destino e nao esta no `git
    ls-files` de `origem`. Entrada de manifesto que aponte para fora do destino
    (`..`, caminho absoluto) nao aparece na varredura do destino, entao fica de
    fora sem tratamento especial. `.in_use`, bytecode e o proprio manifesto nunca
    sao candidatos.
    """
    rastreados = _rastreados(origem)
    if rastreados is None or not destino.is_dir():
        return []
    manifesto = ler_manifesto(destino)
    extras = [rel for rel in files_in_tree(destino)
              if rel.as_posix() not in rastreados
              and not any(parte in SO_DO_CACHE for parte in rel.parts)]
    do_manifesto = [rel for rel in extras if rel.as_posix() in manifesto]
    sem_prova = [rel for rel in extras if rel.as_posix() not in manifesto]
    return sorted(do_manifesto + _provados_pelo_historico(origem, destino, sem_prova))


def remover(destino: Path, removidos) -> list[Path]:
    """Apaga os arquivos e poda os diretorios que ficaram vazios, sem tocar a raiz."""
    apagados = []
    for rel in removidos:
        alvo = destino / rel
        try:
            try:
                alvo.unlink()
            except PermissionError:
                alvo.chmod(stat.S_IWRITE)
                alvo.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            continue
        apagados.append(rel)
        pai = alvo.parent
        while pai != destino and destino in pai.parents:
            try:
                pai.rmdir()
            except OSError:
                break
            pai = pai.parent
    return apagados


def sincronizar(origem: Path, destino: Path) -> tuple[list[Path], list[Path]]:
    """Copia o que diverge, apaga o que saiu do repo e grava o manifesto.

    Devolve (copiados, apagados). Idempotente: a segunda chamada faz as duas
    listas voltarem vazias.
    """
    arquivos = shipped_files(origem)
    a_remover = removidos_do_repo(origem, destino)
    copiados = apply(origem, destino, drift(origem, destino, arquivos))
    apagados = remover(destino, a_remover)
    rastreados = _rastreados(origem)
    if arquivos and rastreados is not None:
        pendentes = {rel.as_posix() for rel in a_remover} - {rel.as_posix() for rel in apagados}
        anterior = {m for m in ler_manifesto(destino) if m in rastreados}
        gravar_manifesto(destino, anterior | {rel.as_posix() for rel in arquivos} | pendentes)
    return copiados, apagados


def _parser() -> argparse.ArgumentParser:
    """Fora do `main` para que um teste possa validar um comando SEM executa-lo.

    `--apply` escreve no plugin instalado, entao nao da para provar que o
    comando sugerido roda executando-o. Parsear com o parser REAL e a prova
    possivel: pega a ordem errada de flag, que e o defeito de `5ca9d4e`.
    """
    ap = argparse.ArgumentParser(description="Compara e sincroniza repo -> plugin instalado.")
    ap.add_argument("--apply", action="store_true", help="copia os divergentes (default: so reporta)")
    ap.add_argument("--check", action="store_true", help="explicito; e o default")
    ap.add_argument("--publicado", action="store_true",
                    help="compara o cache com `main` (o que roda e publicado?) em vez do worktree")
    return ap


def main(argv=None) -> int:
    a = _parser().parse_args(argv)
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

    if a.publicado:
        # A pergunta do portao, disponivel a mao. Sem ela as quatro funcoes que
        # respondem "o que roda e publicado?" teriam como unico consumidor um
        # teste — e uma capacidade cujo unico consumidor e o teste dela e
        # exatamente o que `tools/orfaos.py` existe para acusar.
        ref = published_ref(raiz)
        if ref is None:
            print("nenhuma ref publicada (main/master) — nada a comparar")
            return 0
        divergentes = drift_publicado(raiz, alvo, ref)
        print(f"publicado: {ref}")
        print(f"plugin:    {alvo}")
        print(f"{len(divergentes)} divergentes")
        for rel in divergentes[:40]:
            print(f"  {rel}")
        if divergentes:
            print(f"\ncausa: deploy velho, ou cache com codigo de um ramo nao mesclado."
                  f"\nde um checkout de `{ref}` (NAO de um worktree de ramo), rode:"
                  f"\n    python scripts/deploy_to_cache.py --apply")
        return 1 if divergentes else 0

    arquivos = shipped_files(raiz)
    divergentes = drift(raiz, alvo, arquivos)
    a_remover = removidos_do_repo(raiz, alvo)
    print(f"repo:   {raiz}")
    print(f"plugin: {alvo}")
    print(f"{len(arquivos)} arquivos versionados, {len(divergentes)} divergentes, "
          f"{len(a_remover)} removidos do repo")
    for rel in divergentes[:40]:
        print(f"  {rel}")
    if len(divergentes) > 40:
        print(f"  ... e mais {len(divergentes) - 40}")
    for rel in a_remover:
        print(f"  removido do repo (o --apply apaga): {rel}")
    pendente = bool(divergentes or a_remover)
    if not a.apply:
        if not pendente:
            return 0
        print("\nrode com --apply para sincronizar")
        return 1
    # Mesmo sem nada pendente o --apply passa por aqui: e ele que grava o manifesto
    # num cache que ainda nao tem um.
    copiados, apagados = sincronizar(raiz, alvo)
    restante = drift(raiz, alvo, arquivos)
    resto_a_remover = removidos_do_repo(raiz, alvo)
    if pendente:
        print(f"\ncopiados: {len(copiados)}; removidos: {len(apagados)}; "
              f"divergentes apos copia: {len(restante) + len(resto_a_remover)}")
    return 1 if (restante or resto_a_remover) else 0


if __name__ == "__main__":
    raise SystemExit(main())
