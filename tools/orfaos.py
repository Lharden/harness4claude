"""Guarda de orfao — a pergunta que este repositorio fazia sobre tudo menos si.

`wiki_lint` valida a wiki, `arsenal check` o registry, `compendium check` os
verbetes, `graph_lint` o grafo, `check_hook_liveness` os hooks. Nenhum deles
pergunta se uma funcao tem chamador. E o docstring do `check_hook_liveness` ja
nomeia por que isso importa:

    "O smoke-test prova que os hooks funcionam quando executados. Ele nao prova
    que continuam sendo chamados. [...] codigo presente != codigo rodando."

Medido em 2026-09-16 sobre este repositorio: 419 funcoes publicas de topo, 67 sem
caminho ate qualquer raiz que o host execute — 29 sao ferramentas de mao com o
como e o quando escritos em algum documento, e 38 nao sao nada. **A fronteira
entre os dois grupos e uma frase escrita, e nada mais**: `calibrate_wiki_floor` e
`calibrate_classify_guard` sao a mesma ferramenta no mesmo formato; uma tem duas
linhas num doc e e decisao, a outra nao tem nenhuma e e esquecimento.

Este scanner mede o fato; `tools/orfaos.json` guarda o julgamento. E a mesma
divisao do `arsenal.py`, pela mesma razao: *"o registry guarda apenas JULGAMENTO;
todo fato vem do disco, medido na hora."*

**Alcancabilidade, nao contagem de referencia.** Uma funcao citada num `.md` nao
roda. Uma funcao chamada por outra funcao morta nao roda. Uma funcao que so chama
a si mesma nao roda. A unica pergunta que vale e se existe caminho de uma RAIZ
EXTERNA ate ela — hook, health-check, CI, ou uma skill que mande roda-la.

**Reporta, nunca corrige**, no contrato do `wiki_lint` e do `graph_lint`: JSON no
stdout, booleano `ready`, exit 1. A excecao e `--sync`, que mexe na allowlist — e
ainda assim nunca apaga motivo escrito.

Uso:
    python tools/orfaos.py                # JSON, exit 1 se reprovar
    python tools/orfaos.py --report       # legivel
    python tools/orfaos.py --sync         # sincroniza a allowlist
    python tools/orfaos.py --raiz DIR     # outro repositorio
"""

from __future__ import annotations

import argparse
import ast
import json
import re
from collections import defaultdict, deque
from pathlib import Path

PROD_DIRS = ("scripts", "hooks", "tools", "skills")
HOST_DIRS = ("scripts", "hooks", "tools", "skills", "contract", "sync",
             ".claude-plugin", ".github")
HOST_EXT = {".sh", ".js", ".cjs", ".json", ".yml", ".yaml", ".toml", ".md"}
ALLOWLIST_REL = Path("tools") / "orfaos.json"

CATEGORIAS = {
    "FERRAMENTA_DE_MAO": "decisao",
    "RESERVA_DECLARADA": "decisao",
    "API_EXTERNA": "decisao",
    "ORFAO": "divida",
}

#: A allowlist NAO pode ser raiz de si mesma. Ela cita `scripts/x.py` em todo
#: `arquivo`, e le-la como arquivo de host faria cada modulo declarado parecer
#: invocado — o registro se autoconfirmando, que e o defeito que este scanner
#: existe para pegar.
NAO_SAO_HOST = {ALLOWLIST_REL.as_posix()}

#: Markdown so e raiz quando e uma SKILL.md, e a distincao nao e estetica.
#: Uma SKILL.md e carregada pelo harness e INSTRUI O AGENTE a rodar o comando:
#: e caminho de execucao. Um README e prosa que um humano pode ler.
#:
#: Sem essa linha, escrever `python tools/x.py` num README silenciaria o guarda
#: sem passar pela allowlist — e trataria `wiki_lint` (documentado num README)
#: diferente de `deploy_to_cache` (documentado em docs/), que sao a mesma coisa.
#: A diferenca entre ferramenta de mao e peca esquecida continuaria sendo uma
#: frase escrita, so que agora o acaso de EM QUAL ARQUIVO a frase caiu decidiria.
#: Ferramenta de mao vai para a allowlist com motivo, e e la que ela pertence.
def _md_e_raiz(rel: str) -> bool:
    return rel.startswith("skills/") and rel.endswith("/SKILL.md")


# ---------------------------------------------------------------- coleta ----
def _py_de(raiz: Path, dirs) -> list[Path]:
    out: list[Path] = []
    for d in dirs:
        base = raiz / d
        if base.is_dir():
            out += [p for p in base.rglob("*.py") if "__pycache__" not in p.parts]
    return sorted(out)


def _rel(raiz: Path, p: Path) -> str:
    return p.relative_to(raiz).as_posix()


def _alias_map(tree: ast.AST) -> dict:
    """alias -> ('func', mod, nome) | ('mod', mod) | ('star', mod)"""
    m: dict = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            mod = (n.module or "").split(".")[-1]
            for a in n.names:
                if a.name == "*":
                    m["*" + mod] = ("star", mod)
                else:
                    m[a.asname or a.name] = ("func", mod, a.name)
        elif isinstance(n, ast.Import):
            for a in n.names:
                m[a.asname or a.name.split(".")[0]] = ("mod", a.name.split(".")[-1])
        elif (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "spec_from_file_location" and n.args):
            a0 = n.args[0]
            if isinstance(a0, ast.Constant) and isinstance(a0.value, str):
                # `mod = module_from_spec(spec)` nao carrega o nome do modulo na
                # variavel; o `spec` carrega. Sem isto, `mod.mark_stale()` em
                # hooks/harness-lifecycle.py:103 nao resolve, e `mark_stale`
                # aparece orfa — o que aconteceu de verdade em 2026-09-16.
                m["*" + a0.value] = ("star", a0.value)
    return m


# ------------------------------------------------------------- varredura ----
def varrer(raiz: Path) -> dict:
    """Mede: quem existe, quem e raiz, e quem e alcancavel a partir dela."""
    raiz = Path(raiz).resolve()
    prod = _py_de(raiz, PROD_DIRS)

    arvores: dict[str, ast.Module] = {}
    nao_analisados: list[str] = []
    por_mod: dict[str, Path] = {}
    for p in prod:
        try:
            arvores[p.stem] = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
            por_mod[p.stem] = p
        except SyntaxError:
            nao_analisados.append(_rel(raiz, p))

    publicas: dict[tuple, dict] = {}
    nos: dict[tuple, ast.AST] = {}
    for mod, tree in arvores.items():
        for n in tree.body:
            if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            k = (mod, n.name)
            if k in nos:
                continue
            nos[k] = n
            if not n.name.startswith("_"):
                publicas[k] = {"modulo": mod, "nome": n.name,
                               "arquivo": _rel(raiz, por_mod[mod]),
                               "linha": n.lineno, "entrypoint": n.name == "main"}

    alias = {mod: _alias_map(t) for mod, t in arvores.items()}
    locais = {mod: {n.name for n in t.body
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
              for mod, t in arvores.items()}

    def refs(mod: str, node: ast.AST) -> set:
        am = alias.get(mod, {})
        estrelas = {v[1] for v in am.values() if v[0] == "star"}
        out: set = set()
        for n in ast.walk(node):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
                a = am.get(n.id)
                if a and a[0] == "func":
                    out.add((a[1], a[2]))
                elif n.id in locais.get(mod, ()):
                    out.add((mod, n.id))
                else:
                    out |= {(s, n.id) for s in estrelas if (s, n.id) in nos}
            elif isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name):
                a = am.get(n.value.id)
                if a and a[0] == "mod" and (a[1], n.attr) in nos:
                    out.add((a[1], n.attr))
                elif (n.value.id, n.attr) in nos:
                    out.add((n.value.id, n.attr))
                else:
                    out |= {(s, n.attr) for s in estrelas if (s, n.attr) in nos}
            elif (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                    and n.func.id == "getattr" and len(n.args) >= 2
                    and isinstance(n.args[1], ast.Constant)
                    and isinstance(n.args[1].value, str)):
                nome = n.args[1].value
                alvo = None
                if isinstance(n.args[0], ast.Name):
                    a = am.get(n.args[0].id)
                    if a and a[0] in ("mod", "star"):
                        alvo = a[1]
                if alvo and (alvo, nome) in nos:
                    out.add((alvo, nome))
                else:
                    out |= {(s, nome) for s in estrelas if (s, nome) in nos}
        return out

    arestas = {k: refs(k[0], n) for k, n in nos.items()}
    topo: dict[str, set] = {}
    for mod, tree in arvores.items():
        acc: set = set()
        for n in tree.body:
            if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                acc |= refs(mod, n)   # inclui ClassDef: metodo chama funcao de topo
        topo[mod] = acc

    importa: dict[str, set] = defaultdict(set)
    for mod, am in alias.items():
        for v in am.values():
            if v[1] in por_mod and v[1] != mod:
                importa[mod].add(v[1])

    raizes, importado_por_host = _raizes(raiz, por_mod)

    mod_vivo = dict(raizes)
    fila = deque(raizes)
    while fila:
        m = fila.popleft()
        for alvo in importa.get(m, ()):
            if alvo not in mod_vivo:
                mod_vivo[alvo] = ["import<-" + m]
                fila.append(alvo)

    vivas: set = set()
    fila = deque()

    def semear(k):
        if k in nos and k not in vivas:
            vivas.add(k)
            fila.append(k)

    for m in mod_vivo:
        for k in topo.get(m, ()):
            semear(k)
        for v in alias.get(m, {}).values():
            if v[0] == "func" and (v[1], v[2]) in nos:
                semear((v[1], v[2]))
    for m, nomes in importado_por_host.items():
        for nome in nomes:
            semear((m, nome))
    while fila:
        k = fila.popleft()
        for alvo in arestas.get(k, ()):
            if alvo != k:      # auto-referencia nao e vida: recursao nao e chamador
                semear(alvo)

    vereditos = {}
    for k, info in publicas.items():
        if k in vivas:
            vereditos[k] = "viva"
        elif info["modulo"] not in mod_vivo:
            vereditos[k] = "modulo_morto"
        else:
            vereditos[k] = "inalcancavel"

    return {"publicas": publicas, "vereditos": vereditos, "raizes": raizes,
            "modulos_vivos": sorted(mod_vivo), "nao_analisados": nao_analisados}


def _raizes(raiz: Path, por_mod: dict[str, Path]) -> tuple[dict, dict]:
    """Modulos que algum arquivo executado pelo host nomeia, e como."""
    if not por_mod:
        return {}, {}
    mods = "|".join(re.escape(m) for m in sorted(por_mod, key=len, reverse=True))
    # Fronteira a esquerda obrigatoria. Sem ela, `tests/test_harness_paths.py`
    # vira raiz do modulo `harness_paths` e `build_wiki_index.py` vira raiz de
    # `wiki_index` — os dois aconteceram em 2026-09-16, e o primeiro e
    # literalmente o teste certificando a producao dentro do detector disso.
    pat_path = re.compile(r"(?:^|[\s\"'`(/\\=])((?:[\w.-]+[/\\])*?(" + mods + r"))\.py\b",
                          re.MULTILINE)
    pat_from = re.compile(r"\bfrom\s+(" + mods + r")\s+import\s+([\w, ]+)")
    pat_import = re.compile(r"^\s*import\s+(" + mods + r")\b", re.MULTILINE)
    pat_alias = re.compile(r"^(\w+)(?:\s+as\s+\w+)?$")

    raizes: dict[str, list[str]] = {}
    importado: dict[str, set] = defaultdict(set)
    for d in (*HOST_DIRS, "."):
        base = raiz / d
        if not base.is_dir():
            continue
        alvos = base.glob("*") if d == "." else base.rglob("*")
        for p in alvos:
            if (not p.is_file() or p.suffix not in HOST_EXT
                    or "__pycache__" in p.parts or ".git" in p.parts
                    or "graphify-out" in p.parts):
                continue
            rel = _rel(raiz, p)
            if rel in NAO_SAO_HOST or (p.suffix == ".md" and not _md_e_raiz(rel)):
                continue
            try:
                txt = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for m in pat_path.finditer(txt):
                caminho = m.group(1).replace("\\", "/")
                # Um caminho sob tests/ nao e raiz de producao: seria a prova
                # se autocertificando, que mede consistencia e nao vida.
                if caminho.startswith("tests/") or "/tests/" in caminho:
                    continue
                raizes.setdefault(m.group(2), []).append(rel)
            for m in pat_from.finditer(txt):
                raizes.setdefault(m.group(1), []).append(rel)
                for bruto in m.group(2).split(","):
                    # `from harness_paths import find_repo_root as _raiz`:
                    # sem tirar o ` as `, a funcao aparece orfa. Aconteceu.
                    mm = pat_alias.match(bruto.strip())
                    if mm:
                        importado[m.group(1)].add(mm.group(1))
            for m in pat_import.finditer(txt):
                raizes.setdefault(m.group(1), []).append(rel)
    return raizes, importado


# ------------------------------------------------------------- allowlist ----
def ler_allowlist(raiz: Path) -> dict:
    p = Path(raiz) / ALLOWLIST_REL
    if not p.is_file():
        return {"versao": 1, "declaracoes": []}
    try:
        dados = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as erro:
        raise SystemExit(f"{ALLOWLIST_REL.as_posix()} nao e JSON valido: {erro}") from erro
    dados.setdefault("declaracoes", [])
    return dados


def _motivo_informa(motivo: str, nome: str) -> bool:
    """Motivo que so repete o nome nao informa — e sem isto a allowlist vira
    `# noqa` em massa e o guarda deixa de discriminar no primeiro mes."""
    limpo = re.sub(r"[^\w\s]", " ", (motivo or "")).lower().split()
    return bool(limpo) and set(limpo) - {nome.lower()} != set()


def conferir(raiz: Path, medida: dict | None = None) -> dict:
    """Confronta o que foi medido com o que foi declarado. Sem lado macio."""
    raiz = Path(raiz).resolve()
    m = medida or varrer(raiz)
    lista = ler_allowlist(raiz)

    nao_vivas = {k: m["publicas"][k] for k, v in m["vereditos"].items() if v != "viva"}
    declarado: dict[tuple, dict] = {}
    invalidas: list[dict] = []
    for d in lista["declaracoes"]:
        k = (d.get("modulo"), d.get("nome"))
        declarado[k] = d
        base = {"modulo": k[0], "nome": k[1], "arquivo": d.get("arquivo", "")}
        if d.get("categoria") not in CATEGORIAS:
            invalidas.append({**base, "por_que": "categoria_invalida"})
        elif not (d.get("motivo") or "").strip():
            invalidas.append({**base, "por_que": "motivo_vazio"})
        elif not _motivo_informa(d["motivo"], k[1] or ""):
            invalidas.append({**base, "por_que": "motivo_nao_informa"})

    orfaos = [
        {**info, "veredito": m["vereditos"][k]}
        for k, info in sorted(nao_vivas.items()) if k not in declarado
    ]
    obsoletas = []
    for k, d in sorted(declarado.items()):
        base = {"modulo": k[0], "nome": k[1], "arquivo": d.get("arquivo", ""),
                "motivo": d.get("motivo", "")}
        if k in m["vereditos"] and m["vereditos"][k] == "viva":
            obsoletas.append({**base, "por_que": "ganhou_chamador"})
        elif k not in m["publicas"]:
            obsoletas.append({**base, "por_que": "alvo_sumiu"})

    por_categoria = defaultdict(int)
    for d in lista["declaracoes"]:
        por_categoria[d.get("categoria", "?")] += 1

    ready = not (orfaos or obsoletas or invalidas or m["nao_analisados"]) and bool(m["raizes"])
    return {
        "ready": ready,
        "total": len(m["publicas"]),
        "vivas": sum(1 for v in m["vereditos"].values() if v == "viva"),
        "raizes": len(m["raizes"]),
        "declaradas": len(lista["declaracoes"]),
        "por_categoria": dict(por_categoria),
        "orfaos_nao_declarados": orfaos,
        "declaracoes_obsoletas": obsoletas,
        "declaracoes_invalidas": invalidas,
        "arquivos_nao_analisados": m["nao_analisados"],
        "comando": "python tools/orfaos.py --sync",
    }


def sincronizar(raiz: Path) -> dict:
    """Acrescenta orfao novo e remove linha que ganhou chamador.

    NUNCA apaga motivo escrito: a allowlist e o produto desta feature, e um
    `git mv` invalidaria a chave de dezenas de linhas de julgamento de uma vez.
    Alvo que sumiu vira `obsoleta: true` e fica, para recolagem manual.

    E sincronizar NAO e aprovar: o orfao novo entra com `motivo` vazio, e o
    guarda segue reprovando ate alguem escrever a frase. A frase e o produto.
    """
    raiz = Path(raiz).resolve()
    m = varrer(raiz)
    lista = ler_allowlist(raiz)
    p = raiz / ALLOWLIST_REL
    p.parent.mkdir(parents=True, exist_ok=True)

    nao_vivas = {k for k, v in m["vereditos"].items() if v != "viva"}
    saiu: list[str] = []
    mantidas: list[dict] = []
    for d in lista["declaracoes"]:
        k = (d.get("modulo"), d.get("nome"))
        if k in m["vereditos"] and m["vereditos"][k] == "viva":
            saiu.append(f"{k[0]}.{k[1]}")      # progresso: a funcao ganhou chamador
            continue
        if k not in m["publicas"]:
            d = {**d, "obsoleta": True}        # alvo sumiu: marca, nao apaga
        else:
            d.pop("obsoleta", None)
        mantidas.append(d)

    conhecidas = {(d.get("modulo"), d.get("nome")) for d in mantidas}
    novas = []
    for k in sorted(nao_vivas - conhecidas):
        info = m["publicas"][k]
        novas.append({"modulo": k[0], "nome": k[1], "arquivo": info["arquivo"],
                      "categoria": "ORFAO", "motivo": ""})

    lista["declaracoes"] = sorted(mantidas + novas,
                                  key=lambda d: (d.get("arquivo", ""), d.get("nome", "")))
    p.write_text(json.dumps(lista, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return {"removidas": saiu, "acrescentadas": [f"{d['modulo']}.{d['nome']}" for d in novas],
            "total": len(lista["declaracoes"])}


# --------------------------------------------------------------- saida ------
def mensagem_de_falha(payload: dict) -> str:
    """Diz QUAL caso e, e so promete verde no que o comando resolve sozinho.

    Uma mensagem de portao que entrega instrucao impossivel de seguir repete o
    defeito que ela veio consertar (`5ca9d4e`). Aqui ha dois casos e so um deles
    o comando resolve inteiro — prometer os dois seria a mesma mentira.
    """
    if payload["ready"]:
        return ""
    linhas: list[str] = []
    if not payload["raizes"]:
        linhas.append(
            "NENHUMA RAIZ EXTERNA encontrada. Zero raiz nao e 'nada roda aqui': e "
            "scanner cego. Reprovado de proposito — aprovar seria um relatorio "
            "vazio com cara de completo."
        )
    for a in payload["arquivos_nao_analisados"]:
        linhas.append(f"NAO PARSEIA  {a}  (as funcoes dele sumiram da conta)")
    for o in payload["orfaos_nao_declarados"]:
        linhas.append(
            f"ORFAO NAO DECLARADO  {o['arquivo']}:{o['linha']}  {o['nome']}()  [{o['veredito']}]"
        )
    for d in payload["declaracoes_obsoletas"]:
        porque = ("ganhou chamador de producao — a linha nao vale mais"
                  if d["por_que"] == "ganhou_chamador" else
                  "o alvo sumiu do codigo (rename? delecao?)")
        linhas.append(f"LINHA OBSOLETA  {d['modulo']}.{d['nome']}  {porque}")
    for d in payload["declaracoes_invalidas"]:
        porque = {"categoria_invalida": "categoria fora do vocabulario: "
                                        + ", ".join(sorted(CATEGORIAS)),
                  "motivo_vazio": "motivo vazio",
                  "motivo_nao_informa": "o motivo so repete o nome da funcao"}[d["por_que"]]
        linhas.append(f"DECLARACAO INVALIDA  {d['modulo']}.{d['nome']}  {porque}")

    rodape = [""]
    if payload["declaracoes_obsoletas"]:
        rodape.append("Linha obsoleta o comando abaixo resolve inteiro, e a suite fica verde:")
    rodape.append(f"    {payload['comando']}")
    if payload["orfaos_nao_declarados"]:
        rodape.append("")
        rodape.append(
            "Orfao novo ele NAO resolve sozinho — entra com motivo vazio e a suite "
            "segue vermelha. Falta escrever, em tools/orfaos.json, por que a funcao "
            "existe sem chamador. Essa frase e o produto: e a unica diferenca entre "
            "uma ferramenta de mao e uma peca esquecida."
        )
    return "\n".join(linhas + rodape)


def render(payload: dict) -> str:
    cab = (f"orfaos: {payload['total']} funcoes publicas de topo | "
           f"{payload['vivas']} vivas | {payload['raizes']} raizes | "
           f"{payload['declaradas']} declaradas")
    linhas = [cab, ""]
    decisao = sum(n for c, n in payload["por_categoria"].items()
                  if CATEGORIAS.get(c) == "decisao")
    divida = sum(n for c, n in payload["por_categoria"].items()
                 if CATEGORIAS.get(c) == "divida")
    linhas.append(f"  decisao declarada  {decisao:4}")
    linhas.append(f"  divida (ORFAO)     {divida:4}   <- ninguem sabe por que existe")
    for c in sorted(payload["por_categoria"]):
        linhas.append(f"      {c:20} {payload['por_categoria'][c]:4}")
    linhas.append("")
    linhas.append(mensagem_de_falha(payload) if not payload["ready"]
                  else "[OK] nenhum orfao nao declarado")
    return "\n".join(linhas)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Guarda de orfao: funcao publica sem raiz externa.")
    p.add_argument("--raiz", default=None, help="raiz do repositorio (default: a deste arquivo)")
    p.add_argument("--report", action="store_true", help="saida legivel em vez de JSON")
    p.add_argument("--sync", action="store_true", help="sincroniza tools/orfaos.json")
    args = p.parse_args(argv)

    raiz = Path(args.raiz).resolve() if args.raiz else Path(__file__).resolve().parent.parent
    if args.sync:
        r = sincronizar(raiz)
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return 0

    payload = conferir(raiz)
    print(render(payload) if args.report
          else json.dumps(payload, ensure_ascii=False, indent=1))
    return 0 if payload["ready"] else 1


if __name__ == "__main__":
    # O relatorio ecoa motivo escrito a mao, e motivo tem acento e travessao.
    # Num console cp1252 isso levanta UnicodeEncodeError e o traceback substitui
    # a resposta inteira — o defeito de 2026-08-24, que `test_console_utf8`
    # transformou num teste do CONJUNTO. Ele pegou este arquivo no mesmo dia em
    # que este arquivo nasceu para pegar o esquecimento dos outros.
    import os as _os
    import sys as _sys

    _AQUI = _os.path.dirname(_os.path.abspath(__file__))
    if _AQUI not in _sys.path:
        _sys.path.insert(0, _AQUI)

    from console import usar_utf8

    usar_utf8()
    raise SystemExit(main())
