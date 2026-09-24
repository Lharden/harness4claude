#!/usr/bin/env python
"""Espelha artefatos vivos do Harness para o vault Obsidian (AI-Brain).

Mirrors (idempotente, decidido por hash de conteudo):
  <balde>/traces/*.md             -> <vault>/wiki/sessions/
  <cwd>/docs/specs/*.md           -> <vault>/wiki/specs/          (carimba frontmatter)
  <cwd>/docs/CONTEXT.md           -> <vault>/wiki/decisions/<repo>-context.md  (carimba)
  <raiz>/projects/<slug>/branches/*.seed.md -> <vault>/wiki/branches/  (carimba)
  <repo>/.remember/today-X.md     -> <vault>/raw/inbox/<repo>--today-X.md
  C:/.remember/today-X.md         -> <vault>/raw/inbox/maquina--today-X.md

As notas diarias levam o rotulo do repositorio no nome: todo repo tem um
`today-2026-08-06.done.md`, e com o nome da fonte so o de um repo sobrevivia no vault
(medido em 2026-09-24: 33 versoes de 7 repos fora). O repo e o checkout DONO do `cwd`,
o mesmo que da identidade ao balde: sessao em worktree espelha as notas do principal.

Nota diaria tirada do inbox nao volta: conta como consumida se o manifesto registra a
escrita e a pagina sumiu, ou se `raw/inbox/_processed/` tem copia de mesmo conteudo
(com o nome novo ou o antigo). A segunda regra vive no vault e vale em qualquer maquina.
Fonte com conteudo novo volta sempre.

O CONTEXT.md gerado pela skill `discuss` ja e um registro de assimilacao em tres tiers
(Locked/Deferred/Discretion). Espelha-lo para wiki/decisions/ faz a camada de decisao do
vault se popular do trabalho que o pipeline ja produz, sem passo manual novo.

**Pagina editada no vault nunca e sobrescrita.** Um manifesto FORA do vault
(`vault-sync-manifest.json`, na raiz do harness) guarda, por pagina, o hash dos bytes
que o sync escreveu e o hash da fonte de onde eles vieram. A pagina so e reescrita
quando a fonte mudou E a pagina continua com os bytes da ultima escrita; se alguem a
editou, o sync recusa e relata. Ate 2026-09-24 a decisao era por mtime, e a primeira
mudanca da fonte depois de uma edicao no Obsidian apagava a edicao, sem backup.

Pagina que ja existe e nao esta no manifesto (vault anterior ao manifesto, manifesto
perdido) e ADOTADA quando e igual ao que o sync escreveria hoje, a menos das datas
carimbadas, do slug do projeto e do fim de linha. Diferente disso, e recusada com aviso
quando a fonte e mais nova que ela (o caso em que o espelho por mtime a sobrescreveria);
com a fonte mais velha, fica como esta e sem aviso, porque nada se perderia.

Duas fontes de mesmo nome ainda podem cair na mesma pagina: specs de projetos diferentes
(1 nome em 2 repos, medido em 2026-09-24) e sementes de ramo. Vence a mais nova, como no
espelho por mtime, e a mais velha nao a retoma. O mtime so arbitra entre fontes; a
protecao da edicao humana e sempre por hash.

Uma falha de E/S num arquivo vira evento e o lote segue. Os eventos (recusas, falhas,
manifesto ilegivel) saem como WARNING no stderr, que o hook do PreCompact grava em
`<raiz do harness>/logs/vault-sync.log`.

Degradacao graceful: se o vault nao existir, sai 0 sem erro. Usado pelo
harness-precompact.sh (auto-sync no handoff) e pela skill vault-bridge.

Uso:
    python vault_sync.py [--vault DIR] [--raiz DIR] [--harness-dir BALDE] [--cwd DIR]
                         [--manifesto ARQ] [--quiet]
Env: AI_BRAIN_PATH sobrescreve o default. NAO usar VAULT_PATH aqui: desde a
migracao MCP (2026-06-12), VAULT_PATH aponta para a RAIZ do vault Obsidian
(consumida pelo NODE_EXTRA_CA_CERTS/MCP), e o alvo deste sync e o sub-vault
AI-Brain — usar VAULT_PATH duplicaria a arvore wiki/ na raiz (bug 2026-06-12).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import shutil
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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

MANIFESTO = "vault-sync-manifest.json"

# chave = caminho da pagina -> {destino: sha escrito, fonte: sha da fonte, origem: caminho
# da fonte, mtime: mtime da fonte na escrita}
Registro = dict[str, dict[str, Any]]

# As paginas que este sync escreve, relativas ao AI-Brain. Quem mexe no vault por outro
# caminho (tools/vault_maintenance.py, tools/wiki_accents.py) pergunta aqui antes de
# editar: pagina espelhada editada e recusada na proxima mudanca da fonte, e o espelho
# dela para. Destino exato, nao pasta: `wiki/decisions` tambem tem decisoes escritas no
# vault, e `raw/inbox` tambem recebe notas humanas. A paridade com o que `sync()` escreve
# de fato e travada em tests/test_vault_sync.py.
PAGINAS_ESPELHADAS = tuple(
    re.compile(padrao)
    for padrao in (
        r"wiki/sessions/[^/]+\.md",
        r"wiki/specs/[^/]+\.md",
        r"wiki/decisions/[^/]+-context\.md",
        r"wiki/branches/[^/]+\.seed\.md",
        r"raw/inbox/[^/]+--today-[^/]+\.md",
        # Nome sem rotulo: as paginas escritas antes de 2026-09-24. O sync nao as escreve
        # mais, mas continuam sendo copia de fonte, e editar copia nao muda a fonte.
        r"raw/inbox/today-[^/]+\.md",
    )
)


def is_mirrored(relativo: str) -> bool:
    """True se a pagina (caminho relativo ao AI-Brain, com `/`) e escrita por este sync."""
    return any(padrao.fullmatch(relativo) for padrao in PAGINAS_ESPELHADAS)


# BOM e espaco a esquerda toleram arquivo salvo pelo Obsidian no Windows.
_FRONTMATTER_RE = re.compile(r"\A﻿?[ \t\r\n]*---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n?", re.S)


def stamp_frontmatter(
    text: str, page_type: str, *, source: str, today: str, project: str | None = None
) -> str:
    """Completa o frontmatter Obsidian do texto — corpo e campos existentes intactos.

    Artefatos crus do harness (specs, CONTEXT) nascem sem frontmatter; sem carimbo
    eles chegam ao vault como paginas invalidas pelo schema do AI-Brain/CLAUDE.md.
    Carimbar na copia (e nao na origem) mantem o repo de trabalho limpo.

    `project` e o slug da frente de onde o artefato veio. Sem ele, uma spec espelhada
    chega ao vault sem nenhuma pista de a que projeto pertence — e foi por isso que 16
    delas ficaram orfas, sem in-link de lugar nenhum. Quem sabe a origem e o sync, no
    momento da copia; depois a informacao se perde.

    **Bloco parcial e completado, nao pulado.** A versao anterior desistia inteira ao ver
    um `---` na primeira linha, tratando "tem bloco" como "tem contrato". Em 2026-08-19 as
    specs ganharam `applies_to` na origem (roteamento do design_scope), o bloco passou a
    existir, e quatro paginas chegaram ao vault sem `type` nem `updated`: invisiveis para o
    Dataview e erro no `wiki_lint`. O mesmo defeito que o `missing_fields` do lint existe
    para pegar, do outro lado do cano.

    Campo ja declarado na origem **manda** — o carimbo so acrescenta o que falta.
    """
    campos: list[tuple[str, str]] = [
        ("type", page_type),
        ("created", today),
        ("updated", today),
        ("status", "active"),
        ("tags", f"[{page_type}, harness]"),
    ]
    if project:
        campos.append(("project", project))
    campos.append(("source", source))

    bloco = _FRONTMATTER_RE.match(text)
    if not bloco:
        linhas = "".join(f"{k}: {v}\n" for k, v in campos)
        return f"---\n{linhas}---\n\n" + text

    existente = bloco.group(1)
    faltando = [
        (k, v)
        for k, v in campos
        if not re.search(rf"^{re.escape(k)}:\s*\S", existente, re.M)
    ]
    if not faltando:
        return text
    adicao = "".join(f"{k}: {v}\n" for k, v in faltando)
    return f"---\n{existente}\n{adicao}---\n" + text[bloco.end() :]


def _sha(dados: bytes) -> str:
    return hashlib.sha256(dados).hexdigest()


def _chave(dst: Path) -> str:
    """Chave da pagina no manifesto: caminho absoluto, com a caixa normalizada no Windows."""
    return os.path.normcase(str(dst.resolve()))


# Campos que o carimbo varia sem que o conteudo mude: a data do dia e o slug. Desde
# 2026-09-24 o slug e o do repositorio, mas paginas escritas antes carregam o do
# worktree de onde o sync rodou, e continuam sendo adotaveis.
_CARIMBO_VOLATIL = re.compile(r"^(?:created|updated|project):.*(?:\n|\Z)", re.M)


def _sem_carimbo_volatil(texto: str) -> str:
    bloco = _FRONTMATTER_RE.match(texto)
    if not bloco:
        return texto
    return _CARIMBO_VOLATIL.sub("", bloco.group(1) + "\n") + "\x00" + texto[bloco.end() :]


def _equivalente(esperado: str, atual: str, *, carimbada: bool) -> bool:
    """A pagina e o que o sync escreveria hoje, a menos do fim de linha (e do carimbo volatil)."""
    esperado = esperado.replace("\r\n", "\n")
    atual = atual.replace("\r\n", "\n")
    if carimbada:
        return _sem_carimbo_volatil(esperado) == _sem_carimbo_volatil(atual)
    return esperado == atual


PASTA_PROCESSADAS = "_processed"


def _foi_consumida(
    src: Path,
    dst: Path,
    conteudo: str,
    anterior: dict[str, Any] | None,
    *,
    origem: str,
    fonte_sha: str,
    fonte_mtime: float,
    carimbada: bool,
) -> bool:
    """A pagina AUSENTE ja cumpriu o papel e nao deve ser recriada?

    Duas provas, e basta uma:

    - `<pasta>/_processed/` tem copia de mesmo conteudo, com o nome da pagina ou o da
      fonte (as processadas antes de 2026-09-24 tem o nome sem rotulo). Vive no vault:
      vale em qualquer maquina e sobrevive a perda do manifesto.
    - O manifesto registra que o sync escreveu esta pagina a partir desta mesma fonte,
      com este mesmo conteudo, e ela sumiu: alguem a tirou dali.

    Fonte com conteudo novo nunca e consumida: e trabalho novo. Registro de OUTRA fonte
    no mesmo destino segue a regra de sempre: so a mais nova escreve.
    """
    processadas = dst.parent / PASTA_PROCESSADAS
    for nome in dict.fromkeys((dst.name, src.name)):
        copia = processadas / nome
        if copia.is_file():
            atual = copia.read_bytes().decode("utf-8", errors="replace")
            if _equivalente(conteudo, atual, carimbada=carimbada):
                return True
    if anterior is None:
        return False
    if anterior.get("origem") == origem:
        return anterior.get("fonte") == fonte_sha
    return fonte_mtime <= float(anterior.get("mtime") or 0)


def _espelhar(
    src: Path,
    dst: Path,
    *,
    page_type: str | None,
    today: str,
    project: str | None,
    registro: Registro,
    eventos: list[str],
    consumo: bool = False,
) -> bool:
    """Decide e executa a copia de UMA pagina. True se escreveu.

    `consumo` liga a regra de pagina consumida (`_foi_consumida`) para destinos que sao
    fila de trabalho, como `raw/inbox`: la, pagina que sumiu foi processada.
    """
    dados_fonte = src.read_bytes()
    fonte_sha = _sha(dados_fonte)
    # read_text faria a traducao universal de fim de linha; decodificar uma vez so
    # garante que o hash e o texto sao da mesma leitura.
    texto = dados_fonte.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    conteudo = (
        stamp_frontmatter(texto, page_type, source=src.name, today=today, project=project)
        if page_type
        else texto
    )
    chave = _chave(dst)
    anterior = registro.get(chave)
    origem = _chave(src)
    fonte_mtime = src.stat().st_mtime

    def registrar(dados_destino: bytes) -> None:
        registro[chave] = {
            "destino": _sha(dados_destino), "fonte": fonte_sha, "origem": origem, "mtime": fonte_mtime,
        }

    if consumo and not dst.exists() and _foi_consumida(
        src, dst, conteudo, anterior, origem=origem, fonte_sha=fonte_sha,
        fonte_mtime=fonte_mtime, carimbada=bool(page_type),
    ):
        return False

    if dst.exists():
        dados_destino = dst.read_bytes()
        if anterior is None:
            atual = dados_destino.decode("utf-8", errors="replace")
            if _equivalente(conteudo, atual, carimbada=bool(page_type)):
                registrar(dados_destino)
                return False
            if fonte_mtime <= dst.stat().st_mtime:
                # Pagina mais nova que a fonte (editada no vault, ou de outra fonte de mesmo
                # nome): o espelho por mtime tambem nao a tocaria. Nada se perde, e um aviso
                # aqui se repetiria a cada PreCompact sem acao possivel.
                return False
            eventos.append(
                f"recusada: {dst} difere do que o sync escreveria e nao ha registro de "
                f"escrita anterior; a fonte {src} nao foi copiada. Para aceitar a fonte, "
                "apague a pagina; para manter a edicao, leve-a para a fonte."
            )
            return False
        if anterior.get("fonte") == fonte_sha:
            return False
        # Dois arquivos de mesmo nome em projetos (ou worktrees) diferentes caem na mesma
        # pagina. Vence a fonte mais nova, como no espelho por mtime que este substituiu;
        # sem esta regra, cada projeto reescreveria a pagina do outro a cada PreCompact.
        outra = anterior.get("origem")
        if outra and outra != origem and fonte_mtime <= float(anterior.get("mtime") or 0):
            return False
        if _sha(dados_destino) != anterior.get("destino"):
            eventos.append(
                f"recusada: {dst} foi editada fora do sync desde a ultima escrita; a fonte "
                f"{src} mudou e nao foi copiada. Para aceitar a fonte, apague a pagina; para "
                "manter a edicao, leve-a para a fonte."
            )
            return False

    if page_type:
        dst.write_text(conteudo, encoding="utf-8")
        shutil.copystat(src, dst)
    else:
        shutil.copy2(src, dst)
    registrar(dst.read_bytes())
    return True


def mirror(
    sources: list[Path],
    dst_dir: Path,
    *,
    page_type: str | None = None,
    rename: Callable[[Path], str] | None = None,
    project: str | None = None,
    registro: Registro | None = None,
    eventos: list[str] | None = None,
    consumo: bool = False,
) -> int:
    """Espelha cada source em dst_dir, pagina a pagina. Retorna nº de escritas feitas.

    page_type carimba frontmatter na copia quando a origem nao tem; rename define
    o nome de destino (CONTEXT.md vira {projeto}-context.md, senao colidiria).
    `registro` e o manifesto (atualizado no lugar); `eventos` recebe recusas e falhas.
    Uma falha de E/S numa pagina vira evento e as demais seguem.
    """
    if not sources:
        return 0
    registro = {} if registro is None else registro
    eventos = [] if eventos is None else eventos
    try:
        dst_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        eventos.append(f"falha: nao foi possivel criar {dst_dir}: {exc}")
        return 0
    today = datetime.now().strftime("%Y-%m-%d")
    copied = 0
    for src in sources:
        dst = dst_dir / (rename(src) if rename else src.name)
        try:
            if _espelhar(
                src, dst, page_type=page_type, today=today, project=project,
                registro=registro, eventos=eventos, consumo=consumo,
            ):
                copied += 1
        except OSError as exc:
            eventos.append(f"falha: {src} -> {dst}: {exc}")
    return copied


def carregar_registro(caminho: Path, eventos: list[str]) -> Registro | None:
    """Le o manifesto. Ausente = vazio; ilegivel = None com evento (as paginas passam pela adocao)."""
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        eventos.append(f"manifesto ilegivel em {caminho}: {exc}; tratado como vazio")
        return None
    paginas = dados.get("paginas") if isinstance(dados, dict) else None
    if not isinstance(paginas, dict):
        eventos.append(f"manifesto sem o campo 'paginas' em {caminho}; tratado como vazio")
        return None
    return {k: v for k, v in paginas.items() if isinstance(k, str) and isinstance(v, dict)}


def salvar_registro(caminho: Path, paginas: Registro, eventos: list[str]) -> None:
    """Grava o manifesto de forma atomica (temporario + replace)."""
    temporario = caminho.with_name(f"{caminho.name}.{os.getpid()}.tmp")
    try:
        caminho.parent.mkdir(parents=True, exist_ok=True)
        temporario.write_text(
            json.dumps({"versao": 1, "paginas": paginas}, ensure_ascii=False, sort_keys=True, indent=1),
            encoding="utf-8",
        )
        os.replace(temporario, caminho)
    except OSError as exc:
        eventos.append(f"manifesto nao gravado em {caminho}: {exc}")
        try:
            temporario.unlink()
        except OSError:
            pass


def glob_md(directory: Path, pattern: str = "*.md") -> list[Path]:
    """Lista arquivos que casam o pattern num diretório (vazio se ausente)."""
    return sorted(directory.glob(pattern)) if directory.is_dir() else []


REMEMBER_GLOBAL = Path("C:/.remember")
# Rotulo fixo do `.remember` global. Com o rotulo do `cwd`, cada repo copiaria a mesma
# nota global para o inbox com o proprio prefixo.
ROTULO_REMEMBER_GLOBAL = "maquina"


def _raiz_do_repo(cwd: Path) -> Path | None:
    """Checkout DONO do `cwd` (num worktree, o principal), pela mesma regra do balde."""
    try:
        import sys as _sys

        _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import _escopo

        raiz = _escopo.raiz_do_repo(cwd)
    except Exception:
        return None
    return Path(raiz) if raiz else None


def fontes_do_inbox(cwd: Path, remember_global: Path = REMEMBER_GLOBAL) -> dict[Path, str]:
    """Notas `.remember/today-*.md` -> nome da pagina em `raw/inbox/`.

    Fontes: o `.remember` do checkout dono do `cwd`, o do proprio `cwd` quando for outro
    diretorio, e o global. O nome e `<rotulo>--<nome da fonte>`, com o rotulo da pasta
    que contem o `.remember`: todo repo tem `today-2026-08-06.done.md`, e sem rotulo so
    um deles sobrevivia no vault. O plugin remember escreve no checkout principal, e por
    isso o dono entra: pelo `cwd` de um worktree, nenhuma nota era achada.
    """
    bases: list[tuple[Path, str]] = []
    dono = _raiz_do_repo(cwd)
    for projeto in ([dono] if dono else []) + [cwd]:
        # Rotulo da PASTA, nao do repositorio: com `project_slug`, a nota do `.remember`
        # de um worktree cairia na mesma pagina que a de mesmo nome do principal.
        bases.append((projeto / ".remember", _slug_do_nome(projeto)))
    bases.append((remember_global, ROTULO_REMEMBER_GLOBAL))
    nomes: dict[Path, str] = {}
    vistas: set[str] = set()
    for base, rotulo in bases:
        chave = os.path.normcase(os.path.realpath(base))
        if chave in vistas:
            continue
        vistas.add(chave)
        for fonte in glob_md(base, "today-*.md"):
            nomes[fonte] = f"{rotulo}--{fonte.name}"
    return nomes


LOG_HEADER = """---
type: log
created: {hoje}
updated: {hoje}
status: active
tags:
  - meta
---

# Operations Log

Append-only. Cada ingest/inbox/lint/sync fica registrado aqui. Nunca reescreva — só
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


def _slug_do_nome(pasta: Path) -> str:
    """Slug kebab-case do nome de uma pasta."""
    return re.sub(r"[^a-z0-9]+", "-", pasta.name.lower()).strip("-") or "projeto"


def project_slug(cwd: Path) -> str:
    """Slug kebab-case do REPOSITORIO dono do `cwd`: nomeia a decisao e o campo `project:`.

    O repositorio e o checkout principal, pela mesma regra do balde (`_raiz_do_repo`):
    worktree colapsa no principal, submodulo nao. Fora do git, vale o nome da pasta.

    Ate 2026-09-24 era sempre o nome da pasta, e sessao em worktree nomeava a pagina
    pelo worktree: no AI-Brain, 8 paginas `*-context.md` com o mesmo CONTEXT.md do
    harness4claude, uma por worktree.

    Consequencia declarada: worktrees do mesmo repositorio, cada um no seu ramo, escrevem
    a MESMA pagina de decisao. Com CONTEXT.md diferentes, vale a regra de colisao do
    manifesto (`_espelhar`): a fonte mais nova assume a pagina, e a mais velha nao a
    retoma enquanto nao ficar mais nova. A pagina mostra o CONTEXT.md do ramo em que se
    trabalhou por ultimo, e pode continuar com o de um worktree ja removido ate o
    principal mudar o dele.
    """
    return _slug_do_nome(_raiz_do_repo(cwd) or cwd)


def context_docs(cwd: Path) -> list[Path]:
    """docs/CONTEXT.md do projeto atual, se existir."""
    context = cwd / "docs" / "CONTEXT.md"
    return [context] if context.is_file() else []


def branch_seeds(raiz: Path, cwd: Path) -> list[Path]:
    """Sementes de ramo deste projeto (vazio se nao houver nenhuma).

    A semente e o unico registro legivel de por que um ramo existe. Ela vive no
    bucket do harness, que e volatil por natureza; espelhar no vault e o que
    permite reencontrar um ramo meses depois pela busca, sem depender de a
    sessao ainda existir.

    `raiz` e a RAIZ do harness, nunca o balde da sessao. O escritor
    (`branch_state.branches_dir`) resolve `state_dir(cwd)` a partir da raiz e sem
    sessao; este leitor resolve igual. Ate 2026-09-24 o hook passava o balde aqui,
    o caminho virava `<balde>/projects/<slug>/branches`, e nenhuma semente chegava.
    """
    try:
        import sys as _sys

        _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import harness_paths

        d = harness_paths.state_dir(root=raiz, cwd=cwd) / "branches"
    except Exception:
        return []
    return glob_md(d, "*.seed.md")


def sync(
    vault: Path,
    harness_dir: Path,
    cwd: Path,
    *,
    raiz: Path,
    manifesto: Path | None = None,
    eventos: list[str] | None = None,
    remember_global: Path = REMEMBER_GLOBAL,
) -> dict[str, int]:
    """Executa o espelhamento. Retorna contagens de escritas por destino.

    Duas entradas de estado, cada uma com um papel, e nenhuma deduzida da outra:

    - `harness_dir` e o BALDE da sessao, a convencao de `record_signal.py` e
      `confirm_classification.py`: dele saem os traces (`<balde>/traces`).
    - `raiz` e a RAIZ do harness: dela saem as sementes de ramo e o manifesto.

    Ate 2026-09-24 um argumento so fazia os dois papeis. O hook passava o balde, e as
    sementes eram procuradas num caminho que nao existe.

    `manifesto` default e `<raiz>/vault-sync-manifest.json`: no balde ele recomecaria
    vazio a cada sessao. `eventos` recebe recusas e falhas.
    """
    eventos = [] if eventos is None else eventos
    caminho_manifesto = manifesto or raiz / MANIFESTO
    lido = carregar_registro(caminho_manifesto, eventos)
    # Manifesto ilegivel e regravado mesmo sem mudanca: senao o aviso se repetiria para sempre.
    antes = None if lido is None else json.dumps(lido, sort_keys=True)
    registro: Registro = {} if lido is None else lido
    slug = project_slug(cwd)
    notas = fontes_do_inbox(cwd, remember_global)
    comum = {"registro": registro, "eventos": eventos}
    counts = {
        "sessions": mirror(glob_md(harness_dir / "traces"), vault / "wiki" / "sessions", **comum),
        "specs": mirror(
            glob_md(cwd / "docs" / "specs"),
            vault / "wiki" / "specs",
            page_type="spec",
            project=slug,
            **comum,
        ),
        "decisions": mirror(
            context_docs(cwd),
            vault / "wiki" / "decisions",
            page_type="decision",
            rename=lambda _src: f"{slug}-context.md",
            project=slug,
            **comum,
        ),
        "inbox": mirror(
            list(notas),
            vault / "raw" / "inbox",
            rename=notas.__getitem__,
            consumo=True,
            **comum,
        ),
        "branches": mirror(
            branch_seeds(raiz, cwd),
            vault / "wiki" / "branches",
            page_type="branch",
            project=slug,
            **comum,
        ),
    }
    if json.dumps(registro, sort_keys=True) != antes:
        salvar_registro(caminho_manifesto, registro, eventos)
    if any(counts.values()):
        append_log(
            vault,
            f"autosync: sessions:{counts['sessions']} specs:{counts['specs']} "
            f"decisions:{counts['decisions']} inbox:{counts['inbox']} "
            f"branches:{counts['branches']}",
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
    parser.add_argument(
        "--raiz", type=Path, default=_default_harness_dir(),
        help="raiz do harness: sementes de ramo e manifesto (default: HARNESS_DIR ou ~/.claude/harness)",
    )
    parser.add_argument(
        "--harness-dir", type=Path, default=None,
        help="balde da sessao, de onde saem os traces (default: a raiz)",
    )
    parser.add_argument(
        "--cwd", type=Path, default=None,
        help="projeto de onde saem specs, CONTEXT, notas e sementes (default: o diretorio atual)",
    )
    parser.add_argument(
        "--manifesto", type=Path, default=None,
        help=f"manifesto de hashes (default: <raiz>/{MANIFESTO}); fica fora do vault",
    )
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s vault-sync %(levelname)s %(message)s",
    )

    if not args.vault.is_dir():
        logger.info("vault inexistente em %s — sync ignorado", args.vault)
        return 0

    eventos: list[str] = []
    counts = sync(
        args.vault, args.harness_dir or args.raiz, args.cwd or Path.cwd(),
        raiz=args.raiz, manifesto=args.manifesto, eventos=eventos,
    )
    for evento in eventos:
        logger.warning("%s", evento)
    logger.info("sessions:%s specs:%s decisions:%s inbox:%s branches:%s",
                counts["sessions"], counts["specs"], counts["decisions"], counts["inbox"],
                counts["branches"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
