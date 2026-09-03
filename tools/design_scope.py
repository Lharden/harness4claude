"""Qual spec governa este arquivo?

A pergunta inversa da rastreabilidade, e a que faltava. O `design-doc` ancora por
`{feature-slug}`, o trace corre num sentido só (`[traces: REQ-X]` do design para a
spec) e o `verify-against-spec` roda uma vez, no fim do pipeline. Dá para ir do
requisito ao código. Não dá para ir do código ao requisito — e é essa a direção
que alguém precisa às três da tarde, seis semanas depois, prestes a editar um
arquivo cujo design doc ninguém lembra que existe.

O mecanismo veio do `design/AGENTS.md` do Magnitude (2026-08-19,
`wiki/sources/magnitude.md`): cada documento normativo declara no front matter os
caminhos que governa, e um resolvedor casa caminho contra declaração.

    ---
    applies_to:
      - src/auth/**
      - src/middleware/session.py
    ---

Três coisas que este módulo NÃO faz, cada uma escrita porque a alternativa seria
mentir com cara de resposta:

1. **Não afirma que o doc está certo.** `applies_to` é declaração do autor, não
   fato medido. Um glob largo demais casa com o repositório inteiro e devolve um
   relatório verdadeiro e inútil. O `--explain` existe para isso: mostra QUAL
   padrão casou com QUAL alvo, para o glob preguiçoso ficar visível.

2. **Não adivinha `applies_to`.** Documento sem front matter entra em
   `sem_applies_to` e é reportado como governando nada. Inferir escopo do
   conteúdo produziria roteamento plausível e errado, que é pior que ausente.

3. **Não responde "o que minha mudança afeta".** Isso é o `impact.py`, e o fato é
   de outra natureza: lá a vizinhança é MEDIDA no grafo do graphify; aqui o
   escopo é DECLARADO por quem escreveu a spec. Misturar os dois faria uma
   declaração passar por medição.

Arquivo alterado que nenhum doc governa **não é aviso**. Teste, config e script
legitimamente não têm design doc, e transformar isso em alerta faria o relatório
viver vermelho até ninguém mais ler. Ele aparece em `detalhe.sem_doc`, para quem
foi procurar. Os avisos reais são os outros dois, e os dois são órfão de spec:
**design doc sem `applies_to` governa nada**, e **`applies_to` que não casa com
arquivo nenhum do repositório** — este segundo é o pior dos dois, porque tem
aparência de saudável (o front matter está lá, o doc aparece na lista de
governantes) e mesmo assim não roteia ninguém.

Contrato de saída herdado do `wiki_lint`: JSON no stdout, booleano `ready`, exit 1
quando há erro de front matter. Exit 2 é erro de uso ou ambiente, nunca achado.

Uso:
    python tools/design_scope.py src/auth/token.py     # quem governa este caminho
    python tools/design_scope.py src/auth              # diretório inteiro
    python tools/design_scope.py --changed             # todo o diff não commitado
    python tools/design_scope.py --changed --explain
    python tools/design_scope.py --all --report
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

SPECS_DIR_PADRAO = "docs/specs"
FRONT_MATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)", re.S)


# ---------------------------------------------------------------------------
# Glob
# ---------------------------------------------------------------------------
# `fnmatch` não serve: nele `*` atravessa `/`, então `src/*.py` casaria com
# `src/a/b.py` e todo padrão viraria um padrão largo em silêncio. E
# `PurePath.full_match` só existe no 3.13. Daí a tradução explícita, com a única
# distinção que importa: `*` fica dentro de um segmento, `**` atravessa.


def glob_para_regex(pattern: str) -> re.Pattern[str]:
    """Traduz um glob estilo `applies_to` para regex ancorada."""
    saida: list[str] = []
    i, n = 0, len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            if pattern.startswith("**", i):
                i += 2
                if pattern.startswith("/", i):
                    # `**/` casa com zero ou mais diretórios: `**/x` pega `x`.
                    saida.append("(?:[^/]+/)*")
                    i += 1
                else:
                    saida.append(".*")
            else:
                saida.append("[^/]*")
                i += 1
        elif c == "?":
            saida.append("[^/]")
            i += 1
        else:
            saida.append(re.escape(c))
            i += 1
    return re.compile("".join(saida) + r"\Z")


def validar_padrao(doc: str, idx: int, pattern: object) -> str:
    """Mesmas restrições do original: relativo à raiz e com barra normal."""
    if not isinstance(pattern, str) or not pattern:
        raise ValueError(f"{doc}: applies_to[{idx}] deve ser string não vazia")
    if "\\" in pattern:
        raise ValueError(
            f"{doc}: applies_to[{idx}] usa barra invertida — use `/` mesmo no Windows")
    if pattern.startswith("/") or re.match(r"\A[A-Za-z]:", pattern):
        raise ValueError(f"{doc}: applies_to[{idx}] deve ser relativo à raiz do projeto")
    if pattern.startswith("../") or pattern == "..":
        raise ValueError(f"{doc}: applies_to[{idx}] aponta para fora do projeto")
    return pattern


# ---------------------------------------------------------------------------
# Front matter
# ---------------------------------------------------------------------------
# Um parser de lista YAML, não um parser de YAML. `applies_to` é sempre uma lista
# de strings; puxar PyYAML para dentro de uma ferramenta stdlib-only custaria uma
# dependência por um caso que cabe em doze linhas.


def extrair_applies_to(texto: str) -> list[str] | None:
    """Lista de `applies_to`, ou None se o doc não declara escopo."""
    m = FRONT_MATTER.match(texto)
    if not m:
        return None
    linhas = m.group(1).splitlines()
    dentro = False
    itens: list[str] = []
    for linha in linhas:
        if re.match(r"\Aapplies_to\s*:\s*\Z", linha):
            dentro = True
            continue
        if dentro:
            item = re.match(r"\A\s+-\s+(.*?)\s*\Z", linha)
            if item:
                itens.append(item.group(1).strip("\"'"))
                continue
            if linha.strip() and not linha.startswith((" ", "\t")):
                break
        inline = re.match(r"\Aapplies_to\s*:\s*\[(.*)\]\s*\Z", linha)
        if inline:
            itens += [p.strip().strip("\"'") for p in inline.group(1).split(",") if p.strip()]
    return itens if itens else None


def carregar_docs(raiz: Path, specs_dir: str) -> tuple[list[dict], list[str], list[str]]:
    """Devolve (governantes, sem_applies_to, erros)."""
    base = raiz / specs_dir
    if not base.is_dir():
        return [], [], []
    governantes: list[dict] = []
    sem: list[str] = []
    erros: list[str] = []
    for caminho in sorted(base.rglob("*.md")):
        rel = caminho.relative_to(raiz).as_posix()
        try:
            texto = caminho.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            erros.append(f"{rel}: ilegível ({exc})")
            continue
        bruto = extrair_applies_to(texto)
        if bruto is None:
            sem.append(rel)
            continue
        try:
            padroes = [validar_padrao(rel, i, p) for i, p in enumerate(bruto)]
        except ValueError as exc:
            erros.append(str(exc))
            continue
        governantes.append({"doc": rel, "patterns": padroes})
    return governantes, sem, erros


# ---------------------------------------------------------------------------
# Alvos
# ---------------------------------------------------------------------------


def _git(raiz: Path, args: list[str]) -> list[str]:
    r = subprocess.run(["git", *args], cwd=raiz, capture_output=True, text=True)
    if r.returncode != 0:
        return []
    return [p.replace("\\", "/") for p in r.stdout.split("\0") if p]


def padroes_mortos(raiz: Path, governantes: list[dict]) -> list[dict]:
    """Padrões declarados que não casam com arquivo nenhum do repositório.

    O órfão que este módulo já matava era o doc SEM `applies_to`. Falta o outro,
    e ele é pior porque tem aparência de saudável: doc COM `applies_to` apontando
    para um diretório renomeado ou apagado. O front matter está lá, o lint passa,
    o relatório mostra o doc na lista de governantes — e ele não governa nada.

    A validação de caminho existente veio do `validateModuleImpactManifest` do
    open-science (2026-08-19, `wiki/sources/open-science.md`), onde declarar um
    `ownerPath` inexistente reprova o manifesto. Aqui não reprova: `applies_to`
    pode legitimamente apontar para arquivo que ainda vai nascer, e transformar
    isso em erro obrigaria a escrever a spec depois do código, invertendo o SDD.
    Vira aviso, com o padrão nomeado — quem lê decide se é spec adiantada ou
    declaração podre.

    Sem git, não devolve nada: ausência de listagem não é prova de padrão morto.
    """
    arquivos = _git(raiz, ["ls-files", "-z", "--"])
    if not arquivos:
        return []
    mortos: list[dict] = []
    for g in governantes:
        for pattern in g["patterns"]:
            rx = glob_para_regex(pattern)
            if not any(rx.match(a) for a in arquivos):
                mortos.append({"doc": g["doc"], "pattern": pattern})
    return mortos


def alvos_alterados(raiz: Path) -> list[str]:
    """Não staged + staged + não rastreado. Um arquivo prestes a existir conta."""
    return sorted({
        *_git(raiz, ["diff", "--name-only", "-z", "--"]),
        *_git(raiz, ["diff", "--cached", "--name-only", "-z", "--"]),
        *_git(raiz, ["ls-files", "--others", "--exclude-standard", "-z", "--"]),
    })


def expandir(raiz: Path, entradas: list[str]) -> tuple[list[str], list[str]]:
    alvos: set[str] = set()
    erros: list[str] = []
    for entrada in entradas:
        try:
            rel = (raiz / entrada).resolve().relative_to(raiz.resolve()).as_posix()
        except ValueError:
            erros.append(f"caminho fora da raiz do projeto: {entrada}")
            continue
        absoluto = raiz / rel
        if not absoluto.is_dir():
            # Caminho inexistente ainda pode descrever arquivo planejado ou
            # apagado, e é casado direto — perder isso mataria o uso principal:
            # saber qual norma governa o arquivo ANTES de criá-lo.
            alvos.add(rel)
            continue
        alvos.add(rel if rel == "." else f"{rel}/")
        for filho in _git(raiz, ["ls-files", "-co", "--exclude-standard", "-z", "--",
                                 "." if rel == "." else rel]):
            alvos.add(filho)
    return sorted(alvos), erros


# ---------------------------------------------------------------------------
# Casamento
# ---------------------------------------------------------------------------


def casar(governantes: list[dict], alvos: list[str]) -> list[dict]:
    conjunto = set(alvos)
    resultado: list[dict] = []
    for g in governantes:
        casos: list[dict] = []
        if g["doc"] in conjunto:
            casos.append({"alvo": g["doc"], "pattern": "(o próprio documento)"})
        for pattern in g["patterns"]:
            rx = glob_para_regex(pattern)
            for alvo in alvos:
                if rx.match(alvo):
                    casos.append({"alvo": alvo, "pattern": pattern})
        if casos:
            resultado.append({"doc": g["doc"], "matches": casos})
    return sorted(resultado, key=lambda r: r["doc"])


def command_design_scope(raiz: Path, entradas: list[str], *, changed: bool = False,
                         todos: bool = False, specs_dir: str = SPECS_DIR_PADRAO,
                         estrito: bool = False) -> dict:
    governantes, sem_applies, erros = carregar_docs(raiz, specs_dir)
    avisos: list[str] = []

    if sem_applies:
        msg = (f"{len(sem_applies)} doc(s) em {specs_dir}/ sem `applies_to` — não governam "
               "caminho nenhum e nada vai rotear para eles")
        (erros if estrito else avisos).append(msg)

    mortos = padroes_mortos(raiz, governantes)
    if mortos:
        avisos.append(f"{len(mortos)} padrão(ões) `applies_to` não casam com arquivo nenhum "
                      "do repositório — spec adiantada ou declaração podre. Veja "
                      "`detalhe.padroes_mortos`")

    if todos:
        matches = [{"doc": g["doc"], "matches": []} for g in governantes]
        alvos: list[str] = []
    else:
        if changed:
            alvos = alvos_alterados(raiz)
        else:
            alvos, erros_alvo = expandir(raiz, entradas)
            erros += erros_alvo
        matches = casar(governantes, alvos)

    casados = {c["alvo"] for m in matches for c in m["matches"]}
    sem_doc = [a for a in alvos if a not in casados]

    return {
        "comando": "design-scope",
        "ready": not erros,
        "errors": erros,
        "warnings": avisos,
        "resumo": {
            "docs_governantes": len(governantes),
            "sem_applies_to": len(sem_applies),
            "alvos": len(alvos),
            "docs_aplicaveis": len(matches),
            "alvos_governados": len(casados),
            "alvos_sem_doc": len(sem_doc),
            "padroes_mortos": len(mortos),
        },
        "detalhe": {
            "matches": matches,
            "sem_applies_to": sem_applies,
            "sem_doc": sem_doc,
            "padroes_mortos": mortos,
        },
    }


def render(res: dict, explicar: bool = False) -> str:
    linhas = [f"# design-scope — {'OK' if res['ready'] else 'REPROVADO'}", ""]
    linhas += ["| campo | valor |", "|---|---|"]
    linhas += [f"| {k} | {v}" + " |" for k, v in res["resumo"].items()]
    linhas.append("")
    for titulo, chave in (("Erros", "errors"), ("Avisos", "warnings")):
        itens = res.get(chave) or []
        linhas.append(f"## {titulo} ({len(itens)})")
        linhas += [f"- {i}" for i in itens] or ["- nenhum"]
        linhas.append("")
    matches = res["detalhe"]["matches"]
    linhas.append("## Documentos aplicáveis")
    if not matches:
        linhas.append("- nenhum")
    for m in matches:
        linhas.append(f"- `{m['doc']}`")
        if explicar:
            linhas += [f"    - `{c['alvo']}`  ←  `{c['pattern']}`" for c in m["matches"]]
    orfaos = res["detalhe"]["sem_applies_to"]
    if orfaos:
        linhas += ["", "## Sem `applies_to` (governam nada)"]
        linhas += [f"- `{d}`" for d in orfaos]
    return "\n".join(linhas)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="*", help="arquivos ou diretórios do projeto")
    parser.add_argument("--root", default=".", help="raiz do projeto (default: .)")
    parser.add_argument("--specs", default=SPECS_DIR_PADRAO,
                        help=f"diretório das specs (default: {SPECS_DIR_PADRAO})")
    parser.add_argument("--changed", action="store_true",
                        help="casar contra todo o diff não commitado")
    parser.add_argument("--all", dest="todos", action="store_true",
                        help="listar todo doc que declara escopo")
    parser.add_argument("--explain", action="store_true",
                        help="mostrar qual alvo casou com qual padrão")
    parser.add_argument("--strict", action="store_true",
                        help="doc sem applies_to vira erro, não aviso")
    parser.add_argument("--report", action="store_true", help="markdown em vez de JSON")
    args = parser.parse_args()

    if not (args.paths or args.changed or args.todos):
        parser.error("informe caminhos, --changed ou --all")

    raiz = Path(args.root)
    if not raiz.is_dir():
        print(f"design-scope: raiz inexistente: {raiz}", file=sys.stderr)
        return 2

    res = command_design_scope(raiz, args.paths, changed=args.changed, todos=args.todos,
                               specs_dir=args.specs, estrito=args.strict)
    print(render(res, args.explain) if args.report
          else json.dumps(res, indent=2, ensure_ascii=False))
    return 0 if res["ready"] else 1


if __name__ == "__main__":
    # `tools/` e sys.path[0] quando o arquivo roda como script; em modo importado
    # este bloco nao executa, e o stdout do chamador fica intacto.
    # `sys.path` conta a historia certa nos DOIS modos de invocacao. Ate
    # 2026-09-03 este bloco confiava em `tools/` ser `sys.path[0]`, o que so
    # vale em `python tools/x.py`: sob `python -m tools.x` o primeiro caminho
    # e o diretorio de trabalho, e o import morria com ModuleNotFoundError.
    # `scripts/health-check.sh` invoca com `-m`, e reportava a falha como
    # "Obsidian doctor nao-ready (app fechado / REST off?)" — com o Obsidian
    # rodando e a porta 27124 respondendo 200.
    import os as _os
    import sys as _sys

    _AQUI = _os.path.dirname(_os.path.abspath(__file__))
    if _AQUI not in _sys.path:
        _sys.path.insert(0, _AQUI)

    from console import usar_utf8

    usar_utf8()
    sys.exit(main())
