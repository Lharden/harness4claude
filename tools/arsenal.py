"""Arsenal — registry das ferramentas ATIVAS, com orçamento e reconciliação.

Irmão do `compendium.py`, e a diferença entre os dois é o eixo do desenho:

    compêndio   conceito, técnica, método. **Inerte** — custa zero token por
                sessão. Errar significa guardar informação errada.
    arsenal     plugin, skill, MCP, CLI. **Ativo** — a descrição de cada skill
                entra no prompt de TODA sessão e muda como o agente decide.
                Errar degrada tudo que vier depois sem quebrar nada.

Por isso o compêndio não tem teto e este tem.

Medido em 2026-08-12, antes deste módulo existir: 203 skills no roster,
~19.800 tokens por sessão, 17 skills já invocadas alguma vez (8%), e ~11.940
tokens (63%) em skills com uso confirmado zero.

A regra que estrutura tudo:

    **O registry guarda apenas JULGAMENTO. Todo FATO vem do disco, medido na hora.**

Nenhum campo mensurável entra no TOML — nem versão, nem custo, nem contagem, nem
se está instalado. Guardar um fato que o disco já sabe cria uma segunda verdade
que deriva em silêncio, e por isso `check` REPROVA ao encontrar um desses campos.
Não é zelo teórico: em 2026-08-12 o vault tinha três contagens de skills
diferentes em três lugares — 299 (`wiki/specs/skill-router-design.md`), 276
(`wiki/decisions/assimilacoes-2026.md`) e 246 (o índice real). Nenhuma estava
errada no dia em que foi escrita.

`dispensado` **não** é um valor de `decisao`: ferramenta recusada sai para
`arsenal/dispensados.toml` e serve só como chave de deduplicação do funil.
Manter "rejeitado" como status ativo do índice mantém a coisa recusada no centro
e transforma uma não-questão em tema recorrente. Mesmo desenho do
`compendio/candidate_ignores.toml`, que já provou funcionar.

Contrato de saída, herdado do `wiki_lint.py`: JSON estruturado no stdout,
booleano `ready`, exit 1 quando há erros. `--report` troca por markdown legível.
Exit 2 é erro de uso/ambiente, nunca achado.

Uso:
    python tools/arsenal.py check                  # contrato do registry
    python tools/arsenal.py reconcile              # registry x disco x uso
    python tools/arsenal.py budget [--teto N]      # custo do roster vs teto
    python tools/arsenal.py collisions [--min C]   # gatilhos que se atropelam
Todos aceitam `--root DIR` (raiz do vault) e `--report`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import struct
import sys
from datetime import date
from pathlib import Path
from typing import Any

import tomllib

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wiki_index import default_root  # noqa: E402

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "scripts"))
import build_skills_index as bsi  # noqa: E402

SCHEMA_VERSION = 1
REGISTRY_REL = Path("arsenal") / "tools.toml"
DISPENSADOS_REL = Path("arsenal") / "dispensados.toml"

# `dispensado` está ausente de propósito — vive em dispensados.toml. Ver o docstring.
#
# `absorvido` é o caminho que importa e o que quase todo catálogo esquece.
# Apresentar uma ferramenta nova não é o mesmo que instalá-la: às vezes já
# existe coisa melhor aqui; às vezes existe algo parecido, mas a nova tem uma
# nuance melhor — e é a nuance, não a ferramenta, que vale.
#
# `absorvido` diz: a peça entrou, o pacote não. Solve et coagula.
#
# Não é desenho novo — é o que este vault já fazia sem nome de campo. O registro
# `wiki/decisions/assimilacoes-2026.md` lista quatro casos sob "adapted":
# caveman -> compress-memory, TLA+ -> twin-execution, pm4py -> alignments SQL,
# HNSW -> retrieval exato. Em nenhum deles a ferramenta foi instalada, e em
# todos algo mudou aqui dentro.
DECISOES = ("candidato", "prova", "adotado", "absorvido")
KINDS = ("plugin", "skill", "mcp", "cli", "tecnica")

CAMPOS_OBRIGATORIOS = ("id", "kind", "decisao", "decidido_em", "por_que", "rollback")

# Absorver exige dizer O QUE veio e ONDE encaixou. Sem os dois, "absorvido" vira
# um jeito educado de dizer "não usei" — e a nuance que justificou a decisão
# some, que é justamente a parte que não se recupera depois.
CAMPOS_DE_ABSORCAO = ("o_que_veio", "absorvido_em")

# Fato que o disco já sabe. Guardar aqui cria a segunda verdade que deriva.
# A mensagem de erro diz onde ler cada um, para a recusa ser acionável.
CAMPOS_MEDIVEIS = {
    "versao": "installed_plugins.json",
    "version": "installed_plugins.json",
    "instalado": "settings.json -> enabledPlugins",
    "enabled": "settings.json -> enabledPlugins",
    "habilitado": "settings.json -> enabledPlugins",
    "custo_tokens": "arsenal budget",
    "tokens": "arsenal budget",
    "n_skills": "arsenal budget",
    "usage_count": "~/.claude.json -> skillUsage",
    "usos": "~/.claude.json -> skillUsage",
    "last_used_at": "~/.claude.json -> skillUsage",
    # Entraram em 2026-08-13, na primeira assimilação real. Ao registrar que o
    # budget subestima o superpowers (o hook injeta a skill inteira em toda
    # sessão), a saída natural foi criar um campo `custo_real` com o número
    # dentro — e `check` deixou passar, porque a lista não previa o nome.
    #
    # É a mesma falha que a regra existe para impedir, chegando pela porta dos
    # fundos: número salvo no registry deriva do disco, tenha o nome que tiver.
    # Um ponto cego se documenta em PROSA, com o comando que o reconfere; nunca
    # como campo, que é o que convida a ler como dado.
    "custo_real": "arsenal budget (e o ponto cego, em prosa no campo ponto_cego)",
    "custo": "arsenal budget",
    "custo_tok": "arsenal budget",
    "tokens_reais": "arsenal budget",
}

# Teto do orçamento. 12.000 deixa ~4k de folga sobre a linha de base pós-poda
# (~7.9k medidos em 2026-08-12). Recalibrar quando a poda da Fase 2 terminar.
TETO_TOKENS_PADRAO = 12_000

# O roster injeta uma linha "- <id>: <description>" por skill. 5 chars de
# moldura por linha, medidos no formato real, não chutados.
MOLDURA_CHARS = 5
# Aproximação declarada, não medida: nenhum tokenizador roda aqui. Toda saída
# que usa isto reporta ANTES o número de chars, que é exato e conferível.
CHARS_POR_TOKEN = 4

# Colisão ENTRE plugins e colisão DENTRO de um plugin não são o mesmo problema,
# e um limiar só as mistura.
#
#   entre plugins   duas ferramentas disputam o mesmo trabalho. É o que faz o
#                   agente escolher a errada, e é acionável: você decide qual fica.
#   dentro de um    o autor do plugin escreveu descrições redundantes. Não é seu
#                   problema e você não conserta sem forkar. Informativo.
#
# Os limiares são diferentes porque as distribuições são diferentes: skills do
# mesmo plugin compartilham vocabulário e sobem o cosseno de graça. Medido em
# 2026-08-12 sobre 158 skills habilitadas: máximo interno 0.894
# (deepeval:deepeval x deepeval-tracing), máximo cruzado 0.833
# (discord:access x telegram:access). Usar o limiar interno no cruzado esconderia
# TODAS as colisões acionáveis.
COS_CRUZADO_ALERTA = 0.70
COS_CRUZADO_ERRO = 0.80
COS_INTERNO_ALERTA = 0.85

# Ponto cego declarado: este detector lê descrição de SKILL. Duplicação em
# servidor MCP é invisível para ele — em 2026-08-12 havia dois context7
# carregados (claude.ai e plugin) com instruções idênticas palavra por palavra, e
# cinco motores de browser, e nada disso aparece aqui porque esses plugins não
# expõem skill nenhuma. Cobrir isso exige comparar tool schemas de MCP, que é
# outro detector.
PONTO_CEGO_MCP = (
    "cobre apenas descrições de SKILL — duplicação de servidor MCP "
    "(ex.: context7 carregado duas vezes, 5 motores de browser) é invisível aqui"
)

IDX_DIR = Path(os.environ.get("HARNESS_DIR") or Path.home() / ".claude" / "harness") / "skills-index"

_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------

def registry_path(root: Path) -> Path:
    return Path(root) / REGISTRY_REL


def load_registry(path: Path) -> dict:
    """Lê o registry TOML. Erro de sintaxe sobe — registry quebrado não tem fallback.

    Mesma escolha do compendium.py: um TOML malformado é defeito de quem editou,
    e degradar para "vazio" faria toda ferramenta virar fantasma em silêncio.
    """
    with open(path, "rb") as handle:
        return tomllib.load(handle)


def load_dispensados(root: Path) -> dict[str, dict]:
    """Lê dispensados.toml. Ausente = vazio: dispensar é opcional, ter registry não é."""
    caminho = Path(root) / DISPENSADOS_REL
    if not caminho.is_file():
        return {}
    with open(caminho, "rb") as handle:
        dados = tomllib.load(handle)
    return {str(d.get("id")): d for d in dados.get("dispensados", []) if d.get("id")}


def _raizes_de_referencia(root: Path | None = None) -> list[Path]:
    """Onde procurar o alvo de `absorvido_em`.

    Absorção não cai num lugar só, e é isso que a torna difícil de rastrear: o
    `i-have-adhd` virou output style em ~/.claude, o `caveman` virou script
    dentro do plugin, e o padrão LLM Wiki virou a arquitetura do vault. As três
    raízes cobrem os três destinos reais.
    """
    raizes = [_REPO, _REPO.parent, Path.home() / ".claude"]
    if root is not None:
        raizes.append(Path(root))
    return raizes


def _referencia_existe(alvo: str, root: Path | None = None) -> bool:
    """Resolve 'caminho/arquivo.py:simbolo' contra as raízes conhecidas.

    Mesmo desenho do `verify_code_refs` do compendium.py, e pelo mesmo motivo:
    afirmação que ninguém consegue conferir apodrece sem avisar. A parte após ':'
    é o símbolo/seção e não é validada — o que precisa existir é o arquivo.
    """
    caminho = alvo.split(":", 1)[0].strip().replace("\\", "/")
    if not caminho:
        return False
    for raiz in _raizes_de_referencia(root):
        if (raiz / caminho).exists():
            return True
    return Path(caminho).expanduser().exists()


def validate_registry(registry: dict, dispensados: dict[str, dict],
                      root: Path | None = None) -> list[str]:
    """Contrato do registry. Lista de erros legíveis — vazia quando válido."""
    erros: list[str] = []

    if registry.get("schema_version") != SCHEMA_VERSION:
        erros.append(f"registry: schema_version deve ser {SCHEMA_VERSION}")
    try:
        date.fromisoformat(str(registry.get("updated", "")))
    except ValueError:
        erros.append("registry: updated deve ser data ISO (YYYY-MM-DD)")

    ferramentas = registry.get("tools")
    if not isinstance(ferramentas, list) or not ferramentas:
        erros.append("registry: 'tools' deve ser uma lista não vazia")
        return erros

    vistos: dict[str, int] = {}
    for i, item in enumerate(ferramentas):
        if not isinstance(item, dict):
            erros.append(f"tools[{i}]: deve ser uma tabela")
            continue
        rotulo = item.get("id") or f"tools[{i}]"

        for campo in CAMPOS_OBRIGATORIOS:
            if not str(item.get(campo, "")).strip():
                erros.append(f"{rotulo}: '{campo}' é obrigatório")

        # A regra central, como código: fato mensurável não entra aqui.
        for campo, onde in CAMPOS_MEDIVEIS.items():
            if campo in item:
                erros.append(
                    f"{rotulo}: '{campo}' é fato, não julgamento — remova daqui e leia de {onde}"
                )

        ident = str(item.get("id", "")).strip()
        if ident:
            if ident in vistos:
                erros.append(f"{rotulo}: id duplicado (já em tools[{vistos[ident]}])")
            vistos[ident] = i
            if ident in dispensados:
                erros.append(
                    f"{rotulo}: está em tools.toml E em dispensados.toml — decida uma coisa só"
                )

        if item.get("kind") not in KINDS:
            erros.append(f"{rotulo}: kind deve ser um de {list(KINDS)}")
        if item.get("decisao") not in DECISOES:
            erros.append(
                f"{rotulo}: decisao deve ser um de {list(DECISOES)}"
                " ('dispensado' vive em dispensados.toml)"
            )

        for campo in ("decidido_em", "prova_ate", "capturado_em"):
            valor = item.get(campo)
            if valor is not None and not _ISO_RE.match(str(valor)):
                erros.append(f"{rotulo}: {campo} deve ser data ISO (YYYY-MM-DD)")

        if item.get("decisao") == "prova" and not item.get("prova_ate"):
            erros.append(f"{rotulo}: decisao='prova' exige 'prova_ate' — prova sem prazo nunca termina")

        if item.get("decisao") == "absorvido":
            for campo in CAMPOS_DE_ABSORCAO:
                if not str(item.get(campo, "")).strip():
                    erros.append(
                        f"{rotulo}: decisao='absorvido' exige '{campo}' — sem dizer o que veio e "
                        "onde encaixou, 'absorvido' é só um jeito educado de dizer 'não usei'"
                    )
            alvo = str(item.get("absorvido_em", "")).strip()
            if alvo and not _referencia_existe(alvo, root):
                erros.append(
                    f"{rotulo}: absorvido_em aponta para '{alvo}', que não existe — "
                    "afirmação de absorção tem que ser verificável"
                )
        if item.get("fonte") and not item.get("capturado_em"):
            erros.append(f"{rotulo}: tem 'fonte' sem 'capturado_em' — fonte sem data não é verificável")

    for ident, item in dispensados.items():
        for campo in ("motivo", "decidido_em"):
            if not str(item.get(campo, "")).strip():
                erros.append(f"dispensados[{ident}]: '{campo}' é obrigatório")

    return erros


# --------------------------------------------------------------------------
# Disco: o que está realmente instalado, e quanto custa
# --------------------------------------------------------------------------

def roster() -> list[dict]:
    """Skills vistas no disco. Reusa scan_skills do build_skills_index, não recopia.

    Uma segunda varredura seria uma segunda verdade — exatamente o que este
    módulo existe para impedir.

    Os quatro caminhos vão explícitos de propósito. `scan_skills` os declara como
    argumentos-padrão, e o Python resolve default na DEFINIÇÃO da função: chamar
    `scan_skills()` sem argumentos congela os caminhos do momento do import e
    ignora qualquer troca posterior das constantes do módulo. O sintoma foi
    teste sintético lendo a configuração real da máquina e passando por acaso.
    """
    return bsi.scan_skills(
        bsi.INSTALLED_JSON, bsi.SETTINGS_JSON, bsi.CLAUDE_JSON, bsi.PERSONAL_SKILLS_DIR
    )


def comandos() -> list[dict]:
    """commands/*.md dos plugins habilitados.

    Existe porque cada comando também vira uma linha do roster e custa tokens
    todo prompt, e `scan_skills` só enxerga `skills/`. Sem isto o orçamento
    subestima em ~20 entradas — e um orçamento que subestima é pior que nenhum:
    ele autoriza gasto que não cabe.
    """
    instalados = bsi._load_json(bsi.INSTALLED_JSON, {}).get("plugins", {})
    habilitados = bsi._load_json(bsi.SETTINGS_JSON, {}).get("enabledPlugins", {})
    saida: list[dict] = []
    for pid, entradas in instalados.items():
        for entrada in entradas or []:
            raiz = entrada.get("installPath")
            if not raiz:
                continue
            base = Path(raiz) / "commands"
            if not base.is_dir():
                continue
            for md in sorted(base.rglob("*.md")):
                try:
                    fm = bsi.parse_frontmatter(md.read_text(encoding="utf-8", errors="replace")[:16384])
                except OSError:
                    continue
                nome = fm.get("name") or md.stem
                desc = (fm.get("description") or "").strip()
                # Mesmo motivo do prefix em scan_skills: o roster usa o nome do
                # manifest, não a chave de instalação.
                prefixo = bsi.manifest_name(raiz) or _short(pid)
                saida.append({
                    "id": f"{prefixo}:{nome}", "name": nome, "plugin": pid,
                    "source": "command", "enabled": bool(habilitados.get(pid, False)),
                    "path": str(md), "description": desc, "desc_chars": len(desc),
                    "usage_count": 0, "last_used_at": None,
                })
    return saida


def custo_skill(skill: dict) -> int:
    """Chars que a entrada injeta no roster: '- <id>: <description>'."""
    return len(skill["id"]) + skill["desc_chars"] + MOLDURA_CHARS


def _short(plugin_label: str) -> str:
    """'superpowers@claude-plugins-official' -> 'superpowers'."""
    return plugin_label.split("@")[0]


CATALOGO_JSON = Path.home() / ".claude" / "plugins" / "plugin-catalog-cache.json"
MODELO_CATALOGO = "claude-opus-4-7"


def catalogo() -> dict[str, dict]:
    """Catálogo de marketplace, chaveado por nome curto. Vazio se ausente/ilegível."""
    try:
        with open(CATALOGO_JSON, encoding="utf-8") as handle:
            bruto = json.load(handle)
    except (OSError, ValueError):
        return {}
    return {_short(k): v for k, v in (bruto.get("catalog", {}).get("plugins", {}) or {}).items()}


def custo_oficial(entrada: dict) -> int | None:
    """`always_on` do catálogo: os tokens que o plugin cobra por sessão, medidos na fonte.

    Preferido sobre a aproximação chars/4 porque é fato, não estimativa — e fato
    vem do disco. Conferido em 2026-08-12 contra a medição local: no item
    dominante (data-engineering) bate em 3% (3.751 oficial vs 3.876 estimado);
    nos pequenos desvia até 2x, porque hooks e comandos entram de forma diferente
    na conta da Anthropic. Só cobre plugin de marketplace — plugin local e skill
    pessoal não estão no catálogo, e para esses a aproximação continua valendo.
    """
    valor = ((entrada.get("tokens") or {}).get(MODELO_CATALOGO) or {}).get("always_on")
    return int(valor) if isinstance(valor, (int, float)) else None


def uso_de_plugin() -> dict[str, int]:
    """pluginUsage somado por nome curto.

    Existe porque uso de PLUGIN e uso de SKILL medem coisas diferentes, e
    confundi-los produz conselho destrutivo. Medido em 2026-08-12: `hookify` tem
    43.816 invocações de plugin e ZERO de skill; `remember`, 14.042 e zero.
    Um relatório que dissesse "37 ferramentas sem uso" convidaria a desinstalar
    justamente o componente mais exercitado do sistema.

    A distinção que fica:
      usos_skill   justifica os tokens que a ferramenta cobra do roster;
      usos_plugin  justifica mantê-la instalada (hooks, MCP, comandos).

    A soma é por nome curto porque o mesmo plugin aparece sob chaves diferentes
    ('@inline' de instalações antigas e '@marketplace' da atual).
    """
    bruto = bsi._load_json(bsi.CLAUDE_JSON, {}).get("pluginUsage", {}) or {}
    saida: dict[str, int] = {}
    for chave, valor in bruto.items():
        conta = valor.get("usageCount", 0) if isinstance(valor, dict) else (valor or 0)
        curto = _short(str(chave))
        saida[curto] = saida.get(curto, 0) + int(conta)
    return saida


def _novo_agregado(ident: str, kind: str, plugin_key: str | None, enabled: bool) -> dict:
    return {
        "id": ident, "kind": kind, "plugin_key": plugin_key, "enabled": enabled,
        "n_skills": 0, "n_comandos": 0, "chars": 0, "usos": 0, "usos_plugin": 0, "skills": [],
    }


def agregado_por_ferramenta(entradas: list[dict]) -> dict[str, dict]:
    """Agrupa na unidade que se instala e se remove: o plugin (ou a skill pessoal).

    Custo é por skill, mas `claude plugin disable` age por plugin. O registry
    decide na unidade acionável; a soma sobe daqui.

    O agregado é SEMEADO por enabledPlugins, não pelas skills encontradas: 10 dos
    plugins habilitados em 2026-08-12 não têm skill nenhuma (só MCP server ou
    hooks — browser-use, playwright, prisma, gitkraken, …). Se a semente viesse
    das skills, esses plugins seriam invisíveis ao reconcile e jamais poderiam
    ser acusados de fantasma: entrariam no sistema sem nunca passar por decisão.
    """
    habilitados = bsi._load_json(bsi.SETTINGS_JSON, {}).get("enabledPlugins", {})
    agregado: dict[str, dict] = {}
    for pid, on in habilitados.items():
        chave = _short(pid)
        agregado[chave] = _novo_agregado(chave, "plugin", pid, bool(on))

    for s in entradas:
        chave = _short(s["plugin"]) if s["source"] != "personal" else s["id"]
        alvo = agregado.get(chave)
        if alvo is None:
            alvo = _novo_agregado(
                chave,
                "plugin" if s["source"] != "personal" else "skill",
                s["plugin"] if s["source"] != "personal" else None,
                bool(s["enabled"]),
            )
            agregado[chave] = alvo
        alvo["n_comandos" if s["source"] == "command" else "n_skills"] += 1
        alvo["enabled"] = alvo["enabled"] or bool(s["enabled"])
        alvo["usos"] += int(s.get("usage_count") or 0)
        if s["enabled"]:
            alvo["chars"] += custo_skill(s)
        alvo["skills"].append(s["id"])

    plugin_usage = uso_de_plugin()
    cat = catalogo()
    for chave, alvo in agregado.items():
        alvo["usos_plugin"] = plugin_usage.get(chave, 0)
        oficial = custo_oficial(cat.get(chave) or {}) if alvo["enabled"] else None
        alvo["tokens_oficiais"] = oficial
        # Fato ganha de estimativa. `chars` continua exposto para conferência.
        alvo["tokens"] = oficial if oficial is not None else tokens(alvo["chars"])
        alvo["fonte_do_custo"] = "catalogo" if oficial is not None else "estimado"
    return agregado


def tokens(chars: int) -> int:
    return round(chars / CHARS_POR_TOKEN)


# --------------------------------------------------------------------------
# Comandos
# --------------------------------------------------------------------------

def command_check(root: Path) -> dict:
    caminho = registry_path(root)
    if not caminho.is_file():
        return {
            "comando": "check",
            "ready": False,
            "errors": [f"registry ausente: {caminho}"],
            "warnings": [],
            "resumo": {},
        }
    registry = load_registry(caminho)
    dispensados = load_dispensados(root)
    erros = validate_registry(registry, dispensados, root)
    return {
        "comando": "check",
        "ready": not erros,
        "errors": erros,
        "warnings": [],
        "resumo": {
            "ferramentas": len(registry.get("tools") or []),
            "dispensados": len(dispensados),
            "registry": str(caminho),
        },
    }


def command_reconcile(root: Path) -> dict:
    """Registry x disco x uso. Cada divergência tem nome, porque tem consequência distinta."""
    caminho = registry_path(root)
    warnings: list[str] = []
    if caminho.is_file():
        registry = load_registry(caminho)
        entradas = {str(t.get("id")): t for t in (registry.get("tools") or []) if t.get("id")}
    else:
        registry, entradas = {}, {}
        warnings.append(
            f"registry ausente ({caminho}) — toda ferramenta instalada aparece como fantasma. "
            "É o esperado antes da Fase 2."
        )
    dispensados = load_dispensados(root)

    skills = roster()
    disco = agregado_por_ferramenta(skills + comandos())
    habilitadas = {k: v for k, v in disco.items() if v["enabled"]}

    # Um id humano ('superpowers') que resolva para dois marketplaces é ambíguo:
    # o rollback iria para o alvo errado sem avisar.
    ambiguos: dict[str, set[str]] = {}
    for s in skills:
        if s["source"] == "marketplace":
            ambiguos.setdefault(_short(s["plugin"]), set()).add(s["plugin"])
    ambiguos = {k: v for k, v in ambiguos.items() if len(v) > 1}

    hoje = date.today().isoformat()
    achados: list[dict] = []

    def achado(tipo: str, ident: str, nivel: str, msg: str, **extra: Any) -> None:
        achados.append({"tipo": tipo, "id": ident, "nivel": nivel, "mensagem": msg, **extra})

    for ident, info in sorted(habilitadas.items()):
        if ident in entradas or ident in dispensados:
            continue
        achado(
            "fantasma", ident, "error",
            f"habilitado no disco, sem decisão no registry ({info['n_skills']} skill(s), "
            f"{info['tokens']} tok/sessão)",
            chars=info["chars"], n_skills=info["n_skills"], usos=info["usos"],
        )

    for ident, item in sorted(entradas.items()):
        info = disco.get(ident)
        # `absorvido` fica fora: por definição a ferramenta NÃO está instalada.
        # Cobrar presença dela seria acusar de órfã exatamente o caso de sucesso.
        if item.get("decisao") in ("adotado", "prova") and (info is None or not info["enabled"]):
            achado(
                "orfa", ident, "error",
                f"registry diz '{item.get('decisao')}', mas não está habilitado no disco",
            )
        if item.get("decisao") == "absorvido" and info and info["enabled"]:
            achado(
                "absorvido_mas_instalado", ident, "warning",
                f"a peça já foi absorvida em {item.get('absorvido_em')}, mas o pacote inteiro "
                f"continua habilitado ({info['tokens']} tok/sessão) — pagando duas vezes",
                chars=info["chars"],
            )
        if info and info["enabled"] and item.get("decisao") == "prova":
            prazo = str(item.get("prova_ate") or "")
            if prazo and prazo < hoje and info["usos"] == 0:
                achado(
                    "prova_falhou", ident, "warning",
                    f"prova venceu em {prazo} com zero invocações — decida adotar ou dispensar",
                    chars=info["chars"],
                )
            elif prazo and prazo < hoje:
                achado(
                    "prova_vencida", ident, "warning",
                    f"prova venceu em {prazo} com {info['usos']} uso(s) — falta confirmar a adoção",
                )
        if ident in ambiguos:
            achado(
                "id_ambiguo", ident, "error",
                f"id resolve para mais de um plugin: {sorted(ambiguos[ident])}",
            )

    for ident, item in sorted(dispensados.items()):
        info = disco.get(ident)
        if info and info["enabled"]:
            achado(
                "recaida", ident, "error",
                f"dispensado em {item.get('decidido_em')} ({item.get('motivo')}), "
                "mas está habilitado no disco",
            )

    erros = [a for a in achados if a["nivel"] == "error"]
    return {
        "comando": "reconcile",
        "ready": not erros,
        "errors": [f"{a['tipo']}: {a['id']} — {a['mensagem']}" for a in erros],
        "warnings": warnings + [
            f"{a['tipo']}: {a['id']} — {a['mensagem']}" for a in achados if a["nivel"] == "warning"
        ],
        "achados": achados,
        "resumo": {
            "no_registry": len(entradas),
            "dispensados": len(dispensados),
            "ferramentas_habilitadas": len(habilitadas),
            "skills_no_disco": len(skills),
            "fantasmas": sum(1 for a in achados if a["tipo"] == "fantasma"),
            "orfas": sum(1 for a in achados if a["tipo"] == "orfa"),
            "recaidas": sum(1 for a in achados if a["tipo"] == "recaida"),
        },
    }


PAGINA_REL = Path("wiki") / "arsenal" / "00 Arsenal.md"


def render_pagina(registry: dict) -> str:
    """Página navegável do arsenal, gerada do registry.

    Derivado, nunca fonte — igual às páginas do compêndio. O cabeçalho diz isso
    em voz alta porque a alternativa já aconteceu neste vault: nota escrita à mão
    apodrece, e entre 2026-05 e 2026-08 o índice ficou com 54 de 63 páginas fora.

    NÃO renderiza dispensados. Recusa vira página = recusa vira tema.
    """
    ferramentas = registry.get("tools") or []
    por_decisao: dict[str, list[dict]] = {}
    for t in ferramentas:
        por_decisao.setdefault(str(t.get("decisao")), []).append(t)

    hoje = date.today().isoformat()
    linhas = [
        "---", "type: index", f"created: {hoje}", f"updated: {hoje}",
        "status: active", "tags: [arsenal, ferramentas, meta]", "---", "",
        "# Arsenal — as ferramentas ativas e por que estão aqui", "",
        "> Gerado por `python tools/arsenal.py build --write` a partir de",
        "> `AI-Brain/arsenal/tools.toml`. **Editar aqui não adianta** — a próxima",
        "> geração sobrescreve; edite o registry.", "",
        "O registry guarda **julgamento**: por que entrou, com que limite, como sair.",
        "Todo **fato** — versão, custo, uso, se está habilitado — é lido do disco na",
        "hora por `arsenal reconcile`. Nada mensurável fica salvo aqui, porque fato",
        "salvo é fato que deriva.", "",
        f"Teto do orçamento: **{registry.get('teto_tokens', TETO_TOKENS_PADRAO)} tokens**.",
        "Confira o custo real com `python tools/arsenal.py budget --report`.", "",
    ]

    titulos = {
        "adotado": ("Adotadas", "Instaladas e em uso. O motivo de cada uma está escrito."),
        "absorvido": (
            "Absorvidas — a peça entrou, o pacote não",
            "*Solve et coagula.* Apresentar uma ferramenta não é instalá-la: às vezes já "
            "existe coisa melhor aqui, às vezes existe algo parecido mas a nova tem uma "
            "nuance melhor — e é a nuance que vale. Nenhuma destas está instalada, e "
            "todas mudaram algo aqui dentro.",
        ),
        "prova": ("Em prova", "Decisão com prazo. Vencido o prazo, o `reconcile` cobra."),
        "candidato": ("Candidatas", "Vistas, ainda não decididas."),
    }
    for chave in ("adotado", "absorvido", "prova", "candidato"):
        itens = sorted(por_decisao.get(chave) or [], key=lambda t: str(t.get("id")))
        if not itens:
            continue
        titulo, sub = titulos[chave]
        linhas += [f"## {titulo}", "", sub, ""]
        for t in itens:
            linhas.append(f"### {t['id']}  ·  `{t.get('kind')}`")
            linhas.append("")
            if chave == "absorvido":
                linhas.append(f"**O que veio.** {_prosa(t.get('o_que_veio'))}")
                if t.get("o_que_ficou_de_fora"):
                    linhas.append("")
                    linhas.append(f"**O que ficou de fora.** {_prosa(t['o_que_ficou_de_fora'])}")
                linhas.append("")
                linhas.append(f"**Onde encaixou.** `{t.get('absorvido_em')}`")
            linhas.append("")
            linhas.append(_prosa(t.get("por_que")))
            if t.get("quando_nao_usar"):
                linhas.append("")
                linhas.append(f"**Quando não usar.** {_prosa(t['quando_nao_usar'])}")
            if t.get("prova_ate"):
                linhas.append("")
                linhas.append(f"**Prazo da prova:** {t['prova_ate']}")
            linhas += ["", f"*Decidido em {t.get('decidido_em')} · saída: `{t.get('rollback')}`*", ""]

    linhas += [
        "## O que não está aqui", "",
        "Ferramenta dispensada não vira página. Ela vive em",
        "`AI-Brain/arsenal/dispensados.toml` e serve a um único propósito: quando algo",
        "reaparecer no funil, o `prior-art` saber que já foi olhado, para não relitigar",
        "a mesma decisão. Recusa que vira página vira tema, e tema mantém no centro",
        "justamente o que se decidiu não usar.", "",
    ]
    return "\n".join(linhas)


def _prosa(valor: object) -> str:
    """TOML multilinha vira parágrafo único."""
    return " ".join(str(valor or "").split())


def command_build(root: Path, escrever: bool) -> dict:
    caminho = registry_path(root)
    if not caminho.is_file():
        return {"comando": "build", "ready": False,
                "errors": [f"registry ausente: {caminho}"], "warnings": [], "resumo": {}}
    registry = load_registry(caminho)
    erros = validate_registry(registry, load_dispensados(root), root)
    if erros:
        # Não gera página a partir de registry inválido: a página herdaria o defeito
        # e passaria a parecer verdade só porque está renderizada.
        return {"comando": "build", "ready": False,
                "errors": ["registry inválido — rode `arsenal check`"] + erros,
                "warnings": [], "resumo": {}}
    texto = render_pagina(registry)
    destino = Path(root) / PAGINA_REL
    if escrever:
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(texto + "\n", encoding="utf-8")
    else:
        print(texto)
    return {
        "comando": "build", "ready": True, "errors": [], "warnings": [],
        "resumo": {"pagina": str(destino), "escrita": escrever,
                   "ferramentas": len(registry.get("tools") or []), "linhas": texto.count("\n") + 1},
    }


VISTOS_REL = Path("arsenal") / "vistos.toml"
CATALOGO_STALE_DIAS = 14


def _idade_do_catalogo() -> int | None:
    """Dias desde o fetch do catálogo. None se ilegível."""
    try:
        with open(CATALOGO_JSON, encoding="utf-8") as handle:
            carimbo = str(json.load(handle).get("fetchedAt", ""))[:10]
        return (date.today() - date.fromisoformat(carimbo)).days
    except (OSError, ValueError):
        return None


def load_vistos(root: Path) -> set[str]:
    caminho = Path(root) / VISTOS_REL
    if not caminho.is_file():
        return set()
    with open(caminho, "rb") as handle:
        return set(tomllib.load(handle).get("vistos") or [])


def _grava_vistos(root: Path, ids: set[str]) -> None:
    caminho = Path(root) / VISTOS_REL
    caminho.parent.mkdir(parents=True, exist_ok=True)
    corpo = "\n".join(f'  "{i}",' for i in sorted(ids))
    caminho.write_text(
        "# Marketplace já triado. NÃO é julgamento — é só memória de que algo já\n"
        "# passou pelos olhos, para o funil proativo reportar apenas a novidade.\n"
        "# Quem carrega decisão é tools.toml (entrou) e dispensados.toml (não entrou).\n"
        f'\nupdated = "{date.today().isoformat()}"\nvistos = [\n{corpo}\n]\n',
        encoding="utf-8",
    )


def command_candidates(root: Path, marketplaces: bool, sessoes: bool, aceitar: bool) -> dict:
    """Funil proativo: o que apareceu e ainda não passou por decisão.

    Roda sem gatilho do usuário, ao contrário da skill `assimilar` — aqui a fonte
    é fechada e verificável (catálogo local, notas de sessão locais), então não há
    material de terceiro para colocar em quarentena nem link para buscar.
    """
    if not marketplaces and not sessoes:
        marketplaces = sessoes = True

    decididos: set[str] = set()
    caminho = registry_path(root)
    if caminho.is_file():
        decididos |= {str(t.get("id")) for t in (load_registry(caminho).get("tools") or [])}
    decididos |= set(load_dispensados(root))
    vistos = load_vistos(root)

    avisos: list[str] = []
    novos: list[dict] = []

    if marketplaces:
        idade = _idade_do_catalogo()
        if idade is None:
            avisos.append(f"catálogo ilegível em {CATALOGO_JSON} — nenhuma novidade pode ser detectada")
        elif idade > CATALOGO_STALE_DIAS:
            # Sem este aviso, "nada novo" fica indistinguível de "não conferi", e o
            # funil proativo passa a dar a impressão de cobertura que não tem.
            avisos.append(
                f"catálogo tem {idade} dias (fetchedAt). 'Nada novo' aqui pode significar "
                "'catálogo não atualizou'. Rode `claude plugin marketplace update`."
            )
        cat = catalogo()
        for ident, entrada in sorted(cat.items()):
            if ident in decididos or ident in vistos:
                continue
            novos.append({
                "id": ident, "origem": "marketplace",
                "tokens": custo_oficial(entrada),
                "n_skills": len(((entrada.get("components") or {}).get("skills")) or []),
            })

    if sessoes:
        conhecidos = {i.lower() for i in decididos | vistos}
        for md in sorted((Path(root) / "raw").rglob("*.md")):
            try:
                texto = md.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for termo in set(re.findall(r"`([a-z][a-z0-9._@/-]{3,40})`", texto)):
                base = termo.split("/")[-1].split("@")[0].lower()
                if base in conhecidos or "." in base or len(base) < 4:
                    continue
                conhecidos.add(base)
                novos.append({"id": base, "origem": "sessao", "onde": md.name})

    if aceitar and novos:
        _grava_vistos(root, vistos | {n["id"] for n in novos})

    do_mkt = [n for n in novos if n["origem"] == "marketplace"]
    return {
        "comando": "candidates",
        "ready": True,  # candidato não é defeito: é trabalho a fazer.
        "errors": [],
        "warnings": avisos,
        "resumo": {
            "novos": len(novos),
            "de_marketplace": len(do_mkt),
            "de_sessao": len(novos) - len(do_mkt),
            "ja_decididos": len(decididos),
            "ja_vistos": len(vistos),
            "baseline_gravada": bool(aceitar and novos),
        },
        "candidatos": sorted(novos, key=lambda n: (-(n.get("tokens") or 0), n["id"]))[:60],
    }


def command_gate(root: Path, alvo: str) -> dict:
    """A única barreira dura do sistema: instalar exige decisão prévia e orçamento.

    Três motivos de bloqueio, e só três:
      1. sem entrada no registry  — instalar sem decidir é como o roster chegou a
         63% de peso morto sem ninguém perceber;
      2. em dispensados.toml      — a decisão já foi tomada; reverter é explícito,
         não por acidente de digitação;
      3. estoura o teto           — o custo entra ANTES, com o número na mão.

    NÃO bloqueia por colisão de gatilho, embora o plano original previsse: as
    descrições de um plugin ainda não instalado não estão em lugar nenhum para
    serem embedadas, e o catálogo traz só os nomes das skills. Colisão continua
    sendo detecção pós-instalação, via `arsenal collisions`. Prometer um bloqueio
    que não acontece é pior que não prometer.
    """
    ident = _short(alvo.strip())
    dispensados = load_dispensados(root)
    caminho = registry_path(root)
    entradas: dict[str, dict] = {}
    teto = TETO_TOKENS_PADRAO
    if caminho.is_file():
        registry = load_registry(caminho)
        entradas = {str(t.get("id")): t for t in (registry.get("tools") or []) if t.get("id")}
        teto = int(registry.get("teto_tokens") or TETO_TOKENS_PADRAO)

    def bloqueio(motivo: str, comoResolver: str) -> dict:
        return {"comando": "gate", "ready": False, "errors": [motivo],
                "warnings": [], "resumo": {"alvo": ident, "como_resolver": comoResolver}}

    if ident in dispensados:
        item = dispensados[ident]
        return bloqueio(
            f"'{ident}' foi dispensado em {item.get('decidido_em')}: {item.get('motivo')}",
            "Se a decisão mudou, tire de dispensados.toml e escreva a entrada nova em "
            "tools.toml dizendo o que mudou.",
        )

    if ident not in entradas:
        return bloqueio(
            f"'{ident}' não tem decisão registrada em arsenal/tools.toml",
            "Rode a skill `assimilar` com a fonte — ela decompõe, confronta com o que já "
            "existe aqui e escreve a decisão. Instalar é a exceção; absorver costuma bastar.",
        )

    item = entradas[ident]
    if item.get("decisao") == "absorvido":
        return bloqueio(
            f"'{ident}' está como 'absorvido': a peça já entrou em {item.get('absorvido_em')}",
            "Instalar agora paga o pacote inteiro por algo que já foi reimplementado aqui. "
            "Se o pacote inteiro passou a ser necessário, mude a decisão para 'adotado'.",
        )

    atual = command_budget(teto)
    gasto = atual["resumo"]["tokens"]
    custo = custo_oficial(catalogo().get(ident) or {})
    if custo is None:
        return {"comando": "gate", "ready": True, "errors": [],
                "warnings": [f"custo de '{ident}' não está no catálogo — orçamento não pôde ser "
                             f"conferido antes. Rode `arsenal budget` depois de instalar."],
                "resumo": {"alvo": ident, "gasto_atual": gasto, "teto": teto}}
    if gasto + custo > teto:
        return bloqueio(
            f"'{ident}' custa {custo} tok e o roster já gasta {gasto} de {teto} — "
            f"instalar estouraria em {gasto + custo - teto}",
            "Dispense algo de peso equivalente antes, ou suba o teto_tokens no registry "
            "com o motivo escrito.",
        )
    return {"comando": "gate", "ready": True, "errors": [], "warnings": [],
            "resumo": {"alvo": ident, "custo": custo, "gasto_atual": gasto,
                       "sobra_depois": teto - gasto - custo, "teto": teto}}


def _lista_load_bearing(itens: list[dict], limite: int = 6) -> str:
    return ", ".join(f"{v['id']}={v['usos_plugin']}" for v in itens[:limite])


def command_budget(teto: int) -> dict:
    entradas = roster() + comandos()
    disco = agregado_por_ferramenta(entradas)
    habilitadas = [v for v in disco.values() if v["enabled"]]

    chars = sum(v["chars"] for v in habilitadas)
    tok_total = sum(v["tokens"] for v in habilitadas)
    n_oficiais = sum(1 for v in habilitadas if v["fonte_do_custo"] == "catalogo")
    total_skills = sum(1 for s in entradas if s["enabled"])
    usadas = sum(1 for s in entradas if s["enabled"] and (s.get("usage_count") or 0) > 0)
    # "Sem retorno no roster" != "morto". Custa tokens de descrição e nenhuma
    # skill sua foi invocada; se usos_plugin for alto, o alvo certo é encurtar as
    # descrições, não desinstalar. Ferramenta sem chars não entra: não custa nada.
    sem_retorno = [v for v in habilitadas if v["usos"] == 0 and v["tokens"] > 0]
    tok_sem_retorno = sum(v["tokens"] for v in sem_retorno)
    load_bearing = [v for v in sem_retorno if v["usos_plugin"] >= 100]

    ranking = sorted(habilitadas, key=lambda v: (-v["tokens"], v["id"]))
    excedente = tok_total - teto

    erros = []
    if excedente > 0:
        erros.append(
            f"orçamento estourado: {tok_total} tok > teto {teto} (excedente {excedente})"
        )

    return {
        "comando": "budget",
        "ready": not erros,
        "errors": erros,
        "warnings": (
            ([f"{tok_sem_retorno} tok "
              f"({round(100 * tok_sem_retorno / tok_total) if tok_total else 0}%) em "
              f"{len(sem_retorno)} ferramenta(s) que custam roster e nunca tiveram skill invocada"]
             if sem_retorno else [])
            + ([f"dessas, {len(load_bearing)} têm uso pesado por hook/MCP "
                f"({_lista_load_bearing(load_bearing)}) — encurte a descrição, não desinstale"]
               if load_bearing else [])
        ),
        "resumo": {
            "tokens": tok_total,
            "teto": teto,
            "excedente": max(0, excedente),
            "chars_exatos": chars,
            "entradas_no_roster": total_skills,
            "entradas_com_uso": usadas,
            "ferramentas_habilitadas": len(habilitadas),
            "custo_do_catalogo": n_oficiais,
            "custo_estimado": len(habilitadas) - n_oficiais,
            "ferramentas_sem_retorno": len(sem_retorno),
            "tokens_sem_retorno": tok_sem_retorno,
            "dessas_load_bearing": len(load_bearing),
            "nota": (
                f"custo vem do catálogo ({MODELO_CATALOGO}.always_on) quando existe; "
                f"senão ~ chars/{CHARS_POR_TOKEN}. `fonte_do_custo` diz qual foi usada em cada linha."
            ),
        },
        "ranking": [
            {
                "id": v["id"], "n_skills": v["n_skills"], "n_comandos": v["n_comandos"],
                "chars": v["chars"], "tokens": v["tokens"], "fonte_do_custo": v["fonte_do_custo"],
                "usos": v["usos"], "usos_plugin": v["usos_plugin"],
                "tokens_por_uso": (v["tokens"] if v["usos"] == 0 else round(v["tokens"] / v["usos"])),
            }
            for v in ranking
        ],
    }


def _carrega_vetores() -> tuple[dict, list, list[str]]:
    """Índice + vetores + avisos. Nunca levanta: contrato herdado do skill_router."""
    avisos: list[str] = []
    try:
        with open(IDX_DIR / "skills-index.json", encoding="utf-8") as handle:
            index = json.load(handle)
        with open(IDX_DIR / "meta.json", encoding="utf-8") as handle:
            meta = json.load(handle)
    except (OSError, ValueError):
        return {}, [], [f"índice ausente ou ilegível em {IDX_DIR} — rode scripts/build_skills_index.py"]

    # Índice velho responde com confiança sobre um disco que mudou. Este é
    # exatamente o modo de falha silenciosa que o módulo persegue.
    try:
        atual = bsi.fingerprint(bsi.scan_skills())
        if atual != meta.get("fingerprint"):
            avisos.append(
                "índice STALE: o fingerprint não bate com o disco. As colisões abaixo "
                "descrevem um roster que já mudou. Rode: python scripts/build_skills_index.py"
            )
    except Exception as exc:  # noqa: BLE001 - diagnóstico nunca derruba o comando
        avisos.append(f"não foi possível conferir staleness do índice: {exc}")

    dim = int(meta.get("dim") or 0)
    if not dim:
        return index, [], avisos + ["meta.json sem 'dim'"]
    try:
        data = (IDX_DIR / "embeddings.f16.bin").read_bytes()
        linhas = len(data) // (2 * dim)
        flat = struct.unpack(f"<{linhas * dim}e", data[: linhas * dim * 2])
    except (OSError, struct.error) as exc:
        return index, [], avisos + [f"embeddings ilegíveis: {exc}"]
    return index, [flat[i * dim:(i + 1) * dim] for i in range(linhas)], avisos


def command_collisions(minimo: float, so_habilitadas: bool = True) -> dict:
    index, vecs, avisos = _carrega_vetores()
    registros = [r for r in (index.get("skills") or []) if r.get("vec_row", -1) >= 0]
    if so_habilitadas:
        registros = [r for r in registros if r.get("enabled")]
    if not vecs or not registros:
        # O ponto cego vai aqui também, e não só no caminho feliz: é justamente
        # quando a saída vem vazia que "nenhuma colisão" se lê como "nenhum
        # problema". Limite conhecido some da saída = limite esquecido.
        return {
            "comando": "collisions", "ready": True, "errors": [],
            "warnings": avisos or ["sem vetores para comparar"],
            "resumo": {"comparadas": 0, "ponto_cego": PONTO_CEGO_MCP}, "pares": [],
        }

    linhas = [r["vec_row"] for r in registros]
    pares: list[tuple[float, dict, dict]] = []
    try:
        import numpy as np

        matriz = np.array([vecs[i] for i in linhas], dtype=np.float32)
        # Vetores já saem normalizados do builder, então produto interno = cosseno.
        sim = matriz @ matriz.T
        ia, ib = np.triu_indices(len(registros), k=1)
        mask = sim[ia, ib] >= minimo
        for i, j, c in zip(ia[mask], ib[mask], sim[ia, ib][mask], strict=False):
            pares.append((float(c), registros[int(i)], registros[int(j)]))
    except ImportError:
        avisos.append("numpy ausente — comparação em Python puro, mais lenta")
        for a in range(len(registros)):
            va = vecs[linhas[a]]
            for b in range(a + 1, len(registros)):
                c = sum(x * y for x, y in zip(va, vecs[linhas[b]], strict=False))
                if c >= minimo:
                    pares.append((float(c), registros[a], registros[b]))

    pares.sort(key=lambda p: -p[0])

    def _plugin(reg: dict) -> str:
        return _short(str(reg.get("plugin") or reg.get("id", "")).split(":")[0])

    saida: list[dict] = []
    for c, ra, rb in pares:
        cruzado = _plugin(ra) != _plugin(rb)
        if cruzado:
            nivel = "error" if c >= COS_CRUZADO_ERRO else "warning"
        else:
            # Interno nunca vira erro: não há ação sua que o resolva.
            nivel = "warning" if c >= COS_INTERNO_ALERTA else "info"
        saida.append({
            "cos": round(float(c), 4), "a": ra["id"], "b": rb["id"],
            "cruzado": cruzado, "nivel": nivel,
        })

    erros = [p for p in saida if p["nivel"] == "error"]
    avisos_cruzados = [p for p in saida if p["nivel"] == "warning" and p["cruzado"]]
    avisos_internos = [p for p in saida if p["nivel"] == "warning" and not p["cruzado"]]
    return {
        "comando": "collisions",
        "ready": not erros,
        "errors": [f"colisão entre plugins {p['cos']}: {p['a']} <-> {p['b']}" for p in erros],
        "warnings": (
            avisos
            + [f"entre plugins {p['cos']}: {p['a']} <-> {p['b']}" for p in avisos_cruzados[:15]]
            + [f"interno (informativo) {p['cos']}: {p['a']} <-> {p['b']}"
               for p in avisos_internos[:8]]
        ),
        "resumo": {
            "comparadas": len(registros),
            "pares_acima_do_minimo": len(saida),
            "entre_plugins": sum(1 for p in saida if p["cruzado"]),
            "dentro_do_mesmo_plugin": sum(1 for p in saida if not p["cruzado"]),
            "minimo": minimo,
            "limiar_erro_cruzado": COS_CRUZADO_ERRO,
            "ponto_cego": PONTO_CEGO_MCP,
        },
        "pares": saida,
    }


# --------------------------------------------------------------------------
# Saída
# --------------------------------------------------------------------------

def render_report(res: dict) -> str:
    linhas = [f"# arsenal {res['comando']} — {'OK' if res['ready'] else 'REPROVADO'}", ""]
    resumo = res.get("resumo") or {}
    if resumo:
        linhas.append("| campo | valor |")
        linhas.append("|---|---|")
        linhas += [f"| {k} | {v} |" for k, v in resumo.items()]
        linhas.append("")
    for titulo, chave in (("Erros", "errors"), ("Avisos", "warnings")):
        itens = res.get(chave) or []
        linhas.append(f"## {titulo} ({len(itens)})")
        linhas += [f"- {i}" for i in itens] or ["- nenhum"]
        linhas.append("")
    ranking = res.get("ranking")
    if ranking:
        linhas += ["## Ranking por custo", "",
                   "| ferramenta | skills | cmds | tokens | usos skill | usos plugin | tok/uso |",
                   "|---|---:|---:|---:|---:|---:|---:|"]
        linhas += [
            f"| {r['id']} | {r['n_skills']} | {r['n_comandos']} | {r['tokens']} "
            f"| {r['usos']} | {r['usos_plugin']} | {r['tokens_por_uso']} |"
            for r in ranking[:25]
        ]
    return "\n".join(linhas)


def main() -> int:
    # --root/--report ficam num parent compartilhado para valerem nos DOIS lados do
    # subcomando. Sem isso `arsenal.py budget --report` morre com "unrecognized
    # arguments", e a flag só funciona na ordem que ninguém digita primeiro.
    comum = argparse.ArgumentParser(add_help=False)
    comum.add_argument("--root", help="raiz do vault (default: AI_BRAIN_PATH/VAULT_PATH)")
    comum.add_argument("--report", action="store_true", help="markdown em vez de JSON")

    parser = argparse.ArgumentParser(
        description=__doc__, parents=[comum],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="comando", required=True)
    sub.add_parser("check", parents=[comum], help="Valida o contrato do registry.")
    sub.add_parser("reconcile", parents=[comum], help="Registry x disco x uso.")
    construir = sub.add_parser("build", parents=[comum], help="Gera a página do arsenal.")
    construir.add_argument("--write", action="store_true", help="grava; sem isso imprime")
    orc = sub.add_parser("budget", parents=[comum], help="Custo do roster contra o teto.")
    orc.add_argument("--teto", type=int, default=TETO_TOKENS_PADRAO)
    portao = sub.add_parser("gate", parents=[comum],
                            help="Pode instalar? Exit 1 = bloqueado, com o motivo.")
    portao.add_argument("--tool", required=True, help="id do plugin (nome curto)")
    cand = sub.add_parser("candidates", parents=[comum],
                          help="Funil proativo: o que apareceu e não passou por decisão.")
    cand.add_argument("--marketplaces", action="store_true", help="só o catálogo")
    cand.add_argument("--sessions", action="store_true", help="só as notas de sessão")
    cand.add_argument("--accept", action="store_true",
                      help="marca os listados como vistos (baseline; não é decisão)")
    col = sub.add_parser("collisions", parents=[comum], help="Descrições que disputam o mesmo gatilho.")
    col.add_argument("--min", dest="minimo", type=float, default=COS_CRUZADO_ALERTA)
    col.add_argument("--todas", action="store_true", help="inclui skills desabilitadas")
    args = parser.parse_args()

    root = Path(args.root) if args.root else default_root()

    if args.comando == "check":
        res = command_check(root)
    elif args.comando == "reconcile":
        res = command_reconcile(root)
    elif args.comando == "build":
        res = command_build(root, args.write)
    elif args.comando == "gate":
        res = command_gate(root, args.tool)
    elif args.comando == "candidates":
        res = command_candidates(root, args.marketplaces, args.sessions, args.accept)
    elif args.comando == "budget":
        res = command_budget(args.teto)
    else:
        res = command_collisions(args.minimo, so_habilitadas=not args.todas)

    if args.report:
        print(render_report(res))
    else:
        print(json.dumps(res, indent=2, ensure_ascii=False))
    return 0 if res["ready"] else 1


if __name__ == "__main__":
    sys.exit(main())
