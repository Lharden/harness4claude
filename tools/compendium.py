"""Compêndio de IA e tecnologia — registry versionado que vira páginas do vault.

Porte do desenho do motor de terminologia do mestrado
(`slb-mestrado-projeto/tools/project_hygiene/terminology*.py`), parametrizado e reduzido
ao que este corpus precisa. Não é cópia: aquele motor tem ~4.400 linhas e ~12 constantes
acopladas ao HCE, e mexer nele arriscaria a dissertação.

A ideia que se mantém inteira: **a fonte da verdade é um registry, não as páginas**. Nota
escrita à mão apodrece — foi o que aconteceu com este vault entre 2026-05 e 2026-08. Um
registry com contrato validado e render determinístico não apodrece: ou o `check` passa,
ou o defeito tem nome e linha.

Três campos além do schema original, e são eles que separam consultar de aprender:

    intuição         a analogia que faz entender, não a definição formal
    onde_no_codigo   `arquivo:símbolo` real de um dos projetos — verificável
    quando_nao_usar  o limite. É onde mora o entendimento de verdade: saber o nome
                     de uma técnica é diferente de saber quando ela não serve.

Uso:
    python tools/compendium.py check                    # valida o contrato
    python tools/compendium.py build [--write]          # gera as páginas
    python tools/compendium.py candidates [--gate]     # o que apareceu e não esta aqui
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from collections import defaultdict
from datetime import date
from pathlib import Path

import tomllib

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wiki_index import default_root

SCHEMA_VERSION = 1
REGISTRY_REL = Path("compendio") / "terms.toml"
OUTPUT_REL = Path("wiki") / "compendio"
MAPS_DIRNAME = "mapas"
HUB_NAME = "00 Compendio.md"
MAPS_INDEX_NAME = "00 Mapas.md"

STATUSES = ("confirmado", "provisorio", "revisar")

# Campos sem os quais o verbete não cumpre o que promete. `intuicao` e
# `quando_nao_usar` são obrigatórios de propósito: sem eles a entrada vira um
# dicionário comum, e dicionário comum já existe na internet.
REQUIRED_TERM_FIELDS = (
    "id", "label", "category", "kind", "status",
    "definition", "intuicao", "quando_nao_usar", "reviewed",
)

# Caracteres que quebram um heading do Obsidian — o wikilink `[[pagina#termo]]` deixa
# de resolver e o verbete fica inalcançável por âncora.
FORBIDDEN_IN_HEADING = set("#^|[]\r\n")

_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_CODE_REF_RE = re.compile(r"^[\w./\\-]+\.[a-z]+:[\w.]+$")


# --------------------------------------------------------------------------
# Carga e validação
# --------------------------------------------------------------------------


def load_registry(path: Path) -> dict:
    """Lê o registry TOML. Erro de sintaxe sobe — registry quebrado não tem fallback."""
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _normalized(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold().strip()


def _taxonomy_ids(registry: dict, chave: str, erros: list[str]) -> set[str]:
    itens = registry.get(chave)
    if not isinstance(itens, list) or not itens:
        erros.append(f"registry: '{chave}' deve ser uma lista não vazia")
        return set()
    vistos: set[str] = set()
    for i, item in enumerate(itens):
        if not isinstance(item, dict):
            erros.append(f"{chave}[{i}]: deve ser tabela")
            continue
        ident = item.get("id")
        if not isinstance(ident, str) or not _ID_RE.match(ident or ""):
            erros.append(f"{chave}[{i}]: id inválido '{ident}' (esperado kebab-case)")
            continue
        if ident in vistos:
            erros.append(f"{chave}[{i}]: id duplicado '{ident}'")
        vistos.add(ident)
        if not str(item.get("label", "")).strip():
            erros.append(f"{chave}[{i}]: label obrigatório")
    return vistos


def validate_registry(registry: dict) -> list[str]:
    """Contrato do registry. Lista de erros legíveis — vazia quando válido."""
    erros: list[str] = []

    if registry.get("schema_version") != SCHEMA_VERSION:
        erros.append(f"registry: schema_version deve ser {SCHEMA_VERSION}")
    if not str(registry.get("title", "")).strip():
        erros.append("registry: title obrigatório")
    try:
        date.fromisoformat(str(registry.get("updated", "")))
    except ValueError:
        erros.append("registry: updated deve ser data ISO (YYYY-MM-DD)")

    categorias = _taxonomy_ids(registry, "categories", erros)
    kinds = _taxonomy_ids(registry, "kinds", erros)

    termos = registry.get("terms")
    if not isinstance(termos, list) or not termos:
        erros.append("registry: 'terms' deve ser uma lista não vazia")
        return erros

    ids: set[str] = set()
    headings: defaultdict[tuple[str, str], list[str]] = defaultdict(list)

    for i, termo in enumerate(termos):
        prefixo = f"terms[{i}]"
        if not isinstance(termo, dict):
            erros.append(f"{prefixo}: deve ser tabela")
            continue

        for campo in REQUIRED_TERM_FIELDS:
            if not str(termo.get(campo, "")).strip():
                erros.append(f"{prefixo}: campo obrigatório ausente ou vazio: {campo}")

        ident = termo.get("id")
        if isinstance(ident, str) and ident:
            if not _ID_RE.match(ident):
                erros.append(f"{prefixo}: id '{ident}' deve ser kebab-case")
            if ident in ids:
                erros.append(f"{prefixo}: id duplicado '{ident}'")
            ids.add(ident)

        rotulo = str(termo.get("label", ""))
        if set(rotulo) & FORBIDDEN_IN_HEADING:
            erros.append(f"{prefixo}: label tem caractere que quebra heading do Obsidian")
        categoria = termo.get("category")
        if categoria and categoria not in categorias:
            erros.append(f"{prefixo}: categoria desconhecida '{categoria}'")
        elif categoria and rotulo:
            headings[(categoria, _normalized(rotulo))].append(str(ident))

        if termo.get("kind") and termo.get("kind") not in kinds:
            erros.append(f"{prefixo}: kind desconhecido '{termo.get('kind')}'")
        if termo.get("status") and termo.get("status") not in STATUSES:
            erros.append(
                f"{prefixo}: status inválido '{termo.get('status')}' "
                f"(esperado um de {', '.join(STATUSES)})"
            )
        try:
            date.fromisoformat(str(termo.get("reviewed", "")))
        except ValueError:
            erros.append(f"{prefixo}: reviewed deve ser data ISO")

        referencia = termo.get("onde_no_codigo")
        if referencia and not _CODE_REF_RE.match(str(referencia)):
            erros.append(
                f"{prefixo}: onde_no_codigo '{referencia}' fora do formato "
                "'caminho/arquivo.ext:simbolo'"
            )

    for (categoria, _), donos in headings.items():
        if len(donos) > 1:
            erros.append(
                f"categoria '{categoria}': label repetido entre {', '.join(donos)} — "
                "duas seções com o mesmo heading tornam a âncora ambígua"
            )

    for i, termo in enumerate(termos):
        if not isinstance(termo, dict):
            continue
        for j, relacao in enumerate(termo.get("relacoes", []) or []):
            if not isinstance(relacao, dict):
                erros.append(f"terms[{i}].relacoes[{j}]: deve ser tabela")
                continue
            alvo, tipo = relacao.get("target"), relacao.get("relation")
            if not str(tipo or "").strip():
                erros.append(f"terms[{i}].relacoes[{j}]: relation obrigatório")
            if alvo not in ids:
                erros.append(
                    f"terms[{i}].relacoes[{j}]: alvo '{alvo}' não existe no registry"
                )

    return erros


# --------------------------------------------------------------------------
# Verificação de `onde_no_codigo`
# --------------------------------------------------------------------------

# Prefixo do campo -> raiz real em disco. Sem isto o campo seria decorativo: nada
# impediria um verbete de apontar para função renomeada três refatorações atrás.
def code_roots() -> dict[str, Path]:
    """Raízes conhecidas. `harness4claude` sai da localização deste próprio arquivo."""
    plugin = Path(__file__).resolve().parents[1]
    return {"harness4claude": plugin}


def verify_code_refs(registry: dict, raizes: dict[str, Path] | None = None) -> list[str]:
    """Confere que cada `onde_no_codigo` aponta para arquivo e símbolo existentes.

    Degrada em silêncio quando a raiz não existe nesta máquina — o vault é
    multi-máquina, e um repo ausente localmente não torna o verbete errado. O que se
    verifica aqui é a referência que **dá** para verificar.
    """
    raizes = raizes if raizes is not None else code_roots()
    problemas: list[str] = []
    for termo in registry.get("terms", []):
        referencia = termo.get("onde_no_codigo")
        if not referencia:
            continue
        caminho, _, simbolo = str(referencia).partition(":")
        prefixo, _, resto = caminho.partition("/")
        raiz = raizes.get(prefixo)
        if raiz is None or not raiz.is_dir():
            continue  # repo ausente nesta máquina: silêncio, não falso alarme
        alvo = raiz / resto
        if not alvo.is_file():
            problemas.append(f"{termo['id']}: arquivo inexistente — {caminho}")
            continue
        if simbolo and simbolo not in alvo.read_text(encoding="utf-8", errors="replace"):
            problemas.append(f"{termo['id']}: símbolo '{simbolo}' não aparece em {caminho}")
    return problemas


# --------------------------------------------------------------------------
# Render
# --------------------------------------------------------------------------


def _por_categoria(registry: dict) -> dict[str, list[dict]]:
    agrupado: defaultdict[str, list[dict]] = defaultdict(list)
    for termo in registry["terms"]:
        agrupado[termo["category"]].append(termo)
    for termos in agrupado.values():
        termos.sort(key=lambda t: _normalized(t["label"]))
    return dict(agrupado)


def _categorias_ordenadas(registry: dict) -> list[dict]:
    return sorted(registry["categories"], key=lambda c: (c.get("order", 999), c["id"]))


def page_name(categoria: dict) -> str:
    """Nome do arquivo da coleção — ASCII, prefixado pela ordem de leitura."""
    slug = categoria["id"]
    return f"{categoria.get('order', 99):02d} {slug}.md"


def _rotulo_por_id(registry: dict) -> dict[str, str]:
    return {t["id"]: t["label"] for t in registry["terms"]}


def _link_para(registry: dict, termo_id: str, categorias: dict[str, dict]) -> str:
    """Wikilink com âncora para o verbete, cruzando categorias quando preciso."""
    termo = next(t for t in registry["terms"] if t["id"] == termo_id)
    arquivo = page_name(categorias[termo["category"]])[:-3]
    return f"[[{arquivo}#{termo['label']}|{termo['label']}]]"


def render_collection(registry: dict, categoria: dict, hoje: str) -> str:
    """Uma página por categoria; cada verbete é uma seção `##`."""
    termos = _por_categoria(registry).get(categoria["id"], [])
    categorias = {c["id"]: c for c in registry["categories"]}
    # O `kind` é um id kebab-case (`padrao`, `metodo`); quem lê a página quer o rótulo
    # ("Padrão de projeto"). Imprimir o id deixava a linha de metadados sem acento e
    # com vocabulário de máquina, no meio de uma coleção escrita para ser lida.
    kinds = {k["id"]: k.get("label", k["id"]) for k in registry.get("kinds", [])}
    linhas = [
        "---",
        "type: compendium",
        f"created: {hoje}",
        f"updated: {hoje}",
        "status: active",
        f"category: {categoria['id']}",
        "tags: [compendio, referencia]",
        "---",
        "",
        f"# {categoria['label']}",
        "",
        f"[[{HUB_NAME[:-3]}|← Compêndio]]",
        "",
    ]
    if categoria.get("note"):
        linhas += [f"*{categoria['note']}*", ""]
    if any(t.get("relacoes") for t in termos):
        linhas += [f"[[{MAPS_DIRNAME}/{page_name(categoria)[:-3]}|Mapa das relações]]", ""]
    linhas += [
        "Gerado por `tools/compendium.py` a partir de `compendio/terms.toml`. Editar aqui",
        "não adianta — a próxima geração sobrescreve; edite o registry.",
        "",
    ]

    for termo in termos:
        linhas += [f"## {termo['label']}", ""]
        if termo.get("aliases"):
            linhas += [f"*Também: {', '.join(termo['aliases'])}*", ""]
        linhas += [termo["definition"], ""]
        linhas += ["**Intuição.** " + termo["intuicao"], ""]
        linhas += ["**Quando NÃO usar.** " + termo["quando_nao_usar"], ""]
        if termo.get("onde_no_codigo"):
            linhas += [f"**Onde aparece no nosso código.** `{termo['onde_no_codigo']}`", ""]
        if termo.get("relacoes"):
            partes = [
                f"{r['relation']} {_link_para(registry, r['target'], categorias)}"
                for r in termo["relacoes"]
            ]
            linhas += ["**Relações.** " + " · ".join(partes), ""]
        if termo.get("sources"):
            linhas += ["> [!info]- Fontes"]
            linhas += [f"> - {fonte}" for fonte in termo["sources"]]
            linhas.append("")
        linhas += [
            (f"*{kinds.get(termo['kind'], termo['kind'])} · {termo['status']}"
             f" · revisado em {termo['reviewed']}*"),
            "",
        ]

    if not termos:
        linhas += ["*Categoria ainda sem verbetes.*", ""]
    return "\n".join(linhas)


def _mermaid_id(termo_id: str) -> str:
    return "T_" + re.sub(r"[^A-Za-z0-9_]", "_", termo_id)


def render_map(registry: dict, categoria: dict, hoje: str) -> str | None:
    """Mapa mermaid das relações **tipadas** da categoria.

    É o que o graph view nativo do Obsidian não faz: ele mostra que duas notas se ligam,
    não **como**. Aqui a aresta carrega o verbo — "é medido por", "substitui" — e é isso
    que transforma o mapa em explicação em vez de emaranhado.
    """
    termos = _por_categoria(registry).get(categoria["id"], [])
    ids_categoria = {t["id"] for t in termos}
    rotulos = _rotulo_por_id(registry)

    arestas = [
        (t["id"], r["target"], r["relation"])
        for t in termos
        for r in (t.get("relacoes") or [])
    ]
    if not arestas:
        return None

    envolvidos = ids_categoria | {alvo for _, alvo, _ in arestas}
    linhas = [
        "---",
        "type: compendium-map",
        f"created: {hoje}",
        f"updated: {hoje}",
        "status: active",
        "tags: [compendio, mapa]",
        "---",
        "",
        f"# Mapa — {categoria['label']}",
        "",
        f"[[{page_name(categoria)[:-3]}|← {categoria['label']}]]",
        "",
        "```mermaid",
        "flowchart LR",
    ]
    for termo_id in sorted(envolvidos):
        rotulo = str(rotulos.get(termo_id) or termo_id).replace('"', "'")
        externo = "" if termo_id in ids_categoria else ":::externo"
        linhas.append(f'  {_mermaid_id(termo_id)}["{rotulo}"]{externo}')
    for origem, alvo, relacao in sorted(arestas):
        linhas.append(f"  {_mermaid_id(origem)} -->|{relacao}| {_mermaid_id(alvo)}")
    linhas += [
        "  classDef externo stroke-dasharray: 4 3",
        "```",
        "",
        "*Traço pontilhado: verbete de outra categoria.*",
        "",
    ]
    return "\n".join(linhas)


def render_maps_index(categorias: list[dict], hoje: str) -> str:
    """Porta de entrada da pasta de mapas — sem ela a subárvore fica sem começo."""
    linhas = [
        "---",
        "type: index",
        f"created: {hoje}",
        f"updated: {hoje}",
        "status: active",
        "tags: [compendio, mapa, meta]",
        "---",
        "",
        "# Mapas do compêndio",
        "",
        f"[[../{HUB_NAME[:-3]}|← Compêndio]]",
        "",
        "Cada mapa mostra as relações **tipadas** entre verbetes de uma categoria — a",
        "aresta carrega o verbo. É o que o graph view do Obsidian não faz: ele mostra que",
        "duas notas se ligam, não como.",
        "",
    ]
    for categoria in categorias:
        linhas.append(f"- [[{page_name(categoria)[:-3]}|{categoria['label']}]]")
    linhas.append("")
    return "\n".join(linhas)


def render_hub(registry: dict, hoje: str) -> str:
    """Índice do compêndio: categorias, contagem e o que cada uma cobre."""
    agrupado = _por_categoria(registry)
    total = len(registry["terms"])
    linhas = [
        "---",
        "type: index",
        f"created: {hoje}",
        f"updated: {hoje}",
        "status: active",
        "tags: [compendio, meta]",
        "---",
        "",
        f"# {registry['title']}",
        "",
        f"{total} verbetes em {len(registry['categories'])} categorias. Cada um responde",
        "quatro coisas: o que é, a intuição por trás, onde aparece no nosso código e",
        "**quando não usar** — o último é o que separa saber o nome de saber a técnica.",
        "",
        "Fonte da verdade: `compendio/terms.toml`. Estas páginas são geradas.",
        "",
    ]
    for categoria in _categorias_ordenadas(registry):
        termos = agrupado.get(categoria["id"], [])
        arquivo = page_name(categoria)[:-3]
        linhas.append(f"- [[{arquivo}|{categoria['label']}]] — {len(termos)} verbetes")
        if categoria.get("note"):
            linhas.append(f"  *{categoria['note']}*")
    linhas += [
        "",
        "## Manutenção",
        "",
        "```",
        "python tools/compendium.py check       # contrato do registry",
        "python tools/compendium.py build --write",
        "python tools/compendium.py candidates  # o que apareceu no código e não está aqui",
        "```",
        "",
    ]
    return "\n".join(linhas)


def build(root: Path, *, write: bool = False, hoje: str | None = None) -> dict[str, str]:
    """Renderiza todas as páginas. Devolve {caminho relativo: conteúdo}."""
    registry = load_registry(root / REGISTRY_REL)
    stamp = hoje or date.today().isoformat()
    saida: dict[str, str] = {HUB_NAME: render_hub(registry, stamp)}

    com_mapa = []
    for categoria in _categorias_ordenadas(registry):
        saida[page_name(categoria)] = render_collection(registry, categoria, stamp)
        mapa = render_map(registry, categoria, stamp)
        if mapa:
            saida[f"{MAPS_DIRNAME}/{page_name(categoria)}"] = mapa
            com_mapa.append(categoria)
    if com_mapa:
        saida[f"{MAPS_DIRNAME}/{MAPS_INDEX_NAME}"] = render_maps_index(com_mapa, stamp)

    if write:
        destino = root / OUTPUT_REL
        for rel, conteudo in saida.items():
            alvo = destino / rel
            alvo.parent.mkdir(parents=True, exist_ok=True)
            alvo.write_text(conteudo, encoding="utf-8")
    return saida


# --------------------------------------------------------------------------
# Crescimento: candidatos
# --------------------------------------------------------------------------

IGNORES_REL = Path("compendio") / "candidate_ignores.toml"

# A varredura por token acha SIGLA, não conceito multi-palavra — este é o limite da
# rede, e é por isso que existe a segunda (proposta do agente no fim do pipeline).
#
# A primeira versão deste filtro devolveu 1594 candidatos: `tmp_path`, `os.path.join`,
# `state.json`, `SKILL.md`. Quase tudo era identificador de código e caminho de arquivo,
# não nome de técnica. Uma fila de triagem com 1594 itens nunca é triada.
_TOKEN_RE = re.compile(r"[A-Za-z][\w.+#-]{2,}")

# Extensões e sufixos que denunciam caminho/arquivo em vez de conceito.
_PARECE_ARQUIVO = re.compile(r"\.(py|md|sh|json|toml|yaml|yml|txt|js|mjs|tex|csv|bin)$", re.I)
# snake_case todo minúsculo é identificador de código, não nome de técnica.
_PARECE_IDENTIFICADOR = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)+$")
# Sequências tipo `P-1.b`, `a-1`, `t-20260728` — id de tarefa, não conceito.
_PARECE_ID = re.compile(r"^[a-z]?-?\d|^[a-z]-\d", re.I)
# Referência interna de documento: `US-2`, `AC-1`, `p.1`, `top-3`. O separador é o que a
# distingue de nome de técnica — `BM25`, `HNSW`, `p50` e `LoRA` colam letra e dígito, e
# uma regra sem o separador mataria os quatro.
_PARECE_REFERENCIA = re.compile(r"^[a-z]{1,4}[-.]\d+$", re.I)

# Duas ocorrências ainda era ruído: 368 tokens apareciam exatamente duas vezes e quase
# nenhum era conceito. Três é o ponto onde a repetição começa a significar alguma coisa.
MIN_OCORRENCIAS = 3


# Marcação que precede uma palavra sem ser texto: célula de tabela, item de lista,
# heading, citação, negrito, código. Descascada antes de julgar a maiúscula.
_MARCACAO = re.compile(r"[-*>|#`~\[\](){}\"'\s]+$")
_FIM_DE_FRASE = ".!?:;"


def _forma_valida(token: str) -> bool:
    """Descarta o que nunca é nome de técnica: arquivo, identificador, id de tarefa."""
    if len(token) < 3 or _PARECE_ARQUIVO.search(token):
        return False
    if _PARECE_IDENTIFICADOR.match(token) or _PARECE_ID.match(token):
        return False
    if _PARECE_REFERENCIA.match(token):
        return False
    return not ("/" in token or "\\" in token)


def _sigla_ou_camelcase(token: str) -> bool:
    """TLA+, HNSW, LoRA, BM25: maiúscula interna, tudo-maiúsculo, dígito ou símbolo."""
    corpo = token[1:]
    return (
        any(c.isupper() for c in corpo)
        or token.isupper()
        or any(c.isdigit() for c in token)
        or any(c in "+#" for c in token)
    )


def _capitalizado_no_meio(texto: str, inicio: int) -> bool:
    """True se a maiúscula não se explica por início de frase.

    Sem esta checagem a fila enchia de "Step", "Quando", "Sem", "Rodar" — palavra comum
    que só está maiúscula porque abre a sentença. "Obsidian" e "Ollama" aparecem
    maiúsculos no meio do texto, e é isso que os torna nome próprio.

    Num vault escrito em markdown, "início de frase" precisa incluir início de célula de
    tabela, de item de lista e de rótulo em negrito: `| Evidência |` e `**Rollback**` são
    a mesma maiúscula estrutural que `. Quando`, e enchiam metade da fila.
    """
    linha = texto[:inicio].rsplit("\n", 1)[-1]
    antes = _MARCACAO.sub("", linha)
    return bool(antes) and antes[-1] not in _FIM_DE_FRASE


def known_tokens(registry: dict) -> set[str]:
    """Tudo que o registry já cobre: ids, labels e aliases, normalizados."""
    conhecidos: set[str] = set()
    for termo in registry.get("terms", []):
        conhecidos.add(_normalized(str(termo.get("id", ""))))
        conhecidos.add(_normalized(str(termo.get("label", ""))))
        for alias in termo.get("aliases", []) or []:
            conhecidos.add(_normalized(str(alias)))
    return {c for c in conhecidos if c}


def load_ignores(root: Path) -> set[str]:
    """Ruído triado uma vez e dispensado para sempre."""
    caminho = root / IGNORES_REL
    if not caminho.is_file():
        return set()
    with caminho.open("rb") as handle:
        dados = tomllib.load(handle)
    return {_normalized(t) for t in dados.get("ignore", [])}


def find_candidates(fontes: list[Path], conhecidos: set[str], ignorados: set[str]) -> list[dict]:
    """Tokens distintivos que aparecem nas fontes e não estão no registry."""
    from wiki_accents import _protegido, protected_spans

    ocorrencias: defaultdict[str, list[Path]] = defaultdict(list)
    exemplos: dict[str, str] = {}
    for caminho in fontes:
        try:
            texto = caminho.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # Mesmas fronteiras do corretor de acentos: bloco de código, código inline,
        # wikilink, URL e frontmatter não são prosa — e é de lá que vinha o ruído.
        spans = protected_spans(texto)
        for match in _TOKEN_RE.finditer(texto):
            if _protegido(match.start(), spans):
                continue
            token = match.group(0).strip(".-_")
            chave = _normalized(token)
            if not _forma_valida(token) or chave in conhecidos or chave in ignorados:
                continue
            # Sigla/CamelCase vale sozinha; palavra capitalizada comum só vale se
            # aparecer maiúscula no meio de uma frase ao menos uma vez.
            if not (
                _sigla_ou_camelcase(token)
                or (token[0].isupper() and _capitalizado_no_meio(texto, match.start()))
            ):
                continue
            ocorrencias[token].append(caminho)
            exemplos.setdefault(token, texto[max(0, match.start() - 40):match.end() + 40])

    candidatos = [
        {
            "token": token,
            "count": len(caminhos),
            "files": sorted({c.name for c in caminhos}),
            "example": " ".join(exemplos[token].split())[:90],
        }
        for token, caminhos in ocorrencias.items()
        if len(caminhos) >= MIN_OCORRENCIAS
    ]
    return sorted(candidatos, key=lambda c: (-c["count"], c["token"]))


def candidate_sources(root: Path) -> list[Path]:
    """Onde procurar candidatos: páginas do vault e docs de operação do harness.

    Fora, de propósito:
      - `wiki/graphs/` — 949 notas geradas por máquina, nome de nó não é conceito;
      - `wiki/compendio/` — é a **saída** deste próprio motor. Varrer a própria
        renderização era circular: "Fontes", "Intuição", "Relações" subiram ao topo da
        fila só porque são os rótulos dos campos que o gerador escreve.
    """
    excluidas = {"graphs", "compendio"}
    fontes = [
        p for p in (root / "wiki").rglob("*.md")
        if not (excluidas & set(p.relative_to(root / "wiki").parts))
    ]
    docs = Path(__file__).resolve().parents[1] / "docs"
    if docs.is_dir():
        fontes += sorted(docs.rglob("*.md"))
    return fontes


def command_candidates(root: Path, gate: bool) -> int:
    registry = load_registry(root / REGISTRY_REL)
    candidatos = find_candidates(
        candidate_sources(root), known_tokens(registry), load_ignores(root)
    )
    if not candidatos:
        print("nenhum candidato pendente de triagem.")
        return 0
    print(f"{len(candidatos)} candidato(s) — decida: vira verbete ou entra em ignores.\n")
    print(f"{'TOKEN':<28} {'OCORR':>5}  EXEMPLO")
    for candidato in candidatos[:40]:
        print(f"{candidato['token']:<28} {candidato['count']:>5}  {candidato['example'][:70]}")
    if len(candidatos) > 40:
        print(f"... mais {len(candidatos) - 40}")
    return 1 if gate else 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def command_check(root: Path) -> int:
    caminho = root / REGISTRY_REL
    if not caminho.is_file():
        print(f"registry ausente: {caminho}")
        return 1
    registry = load_registry(caminho)
    erros = validate_registry(registry)
    if erros:
        print(f"{len(erros)} erro(s) de contrato:")
        for erro in erros:
            print(f"  - {erro}")
        return 1
    quebradas = verify_code_refs(registry)
    if quebradas:
        print(f"{len(quebradas)} referência(s) de código quebrada(s):")
        for problema in quebradas:
            print(f"  - {problema}")
        return 1
    com_ref = sum(1 for t in registry["terms"] if t.get("onde_no_codigo"))
    print(
        f"ok: {len(registry['terms'])} verbetes, {len(registry['categories'])} categorias, "
        f"{com_ref} com referência de código verificada"
    )
    return 0


def command_build(root: Path, write: bool) -> int:
    erros = validate_registry(load_registry(root / REGISTRY_REL))
    if erros:
        print("registry inválido — corrija antes de gerar (`compendium.py check`)")
        return 1
    paginas = build(root, write=write)
    destino = root / OUTPUT_REL
    print(f"{'gerado' if write else 'renderizado (dry-run)'}: {len(paginas)} páginas em {destino}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=None)
    sub = parser.add_subparsers(dest="comando", required=True)
    sub.add_parser("check", help="Valida o contrato do registry e as referências de código.")
    construir = sub.add_parser("build", help="Gera as páginas do compêndio.")
    construir.add_argument("--write", action="store_true")
    candidatos = sub.add_parser(
        "candidates", help="Tokens distintivos ainda fora do registry."
    )
    candidatos.add_argument(
        "--gate", action="store_true",
        help=(
            "Sai 1 se houver pendência. Fora do padrão de proposito: com a fila ainda "
            "na casa das centenas, um gate assim nasceria vermelho permanente — e gate "
            "que nao passa e gate que ninguem roda. Ligue quando a triagem alcancar."
        ),
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    root = args.root or default_root()
    if args.comando == "check":
        return command_check(root)
    if args.comando == "candidates":
        return command_candidates(root, args.gate)
    return command_build(root, args.write)


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
