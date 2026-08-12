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
DECISOES = ("candidato", "prova", "adotado")
KINDS = ("plugin", "skill", "mcp", "cli")

CAMPOS_OBRIGATORIOS = ("id", "kind", "decisao", "decidido_em", "por_que", "rollback")

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


def validate_registry(registry: dict, dispensados: dict[str, dict]) -> list[str]:
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
    for chave, alvo in agregado.items():
        alvo["usos_plugin"] = plugin_usage.get(chave, 0)
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
    erros = validate_registry(registry, dispensados)
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
            f"~{tokens(info['chars'])} tok/sessão)",
            chars=info["chars"], n_skills=info["n_skills"], usos=info["usos"],
        )

    for ident, item in sorted(entradas.items()):
        info = disco.get(ident)
        if item.get("decisao") in ("adotado", "prova") and (info is None or not info["enabled"]):
            achado(
                "orfa", ident, "error",
                f"registry diz '{item.get('decisao')}', mas não está habilitado no disco",
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


def _lista_load_bearing(itens: list[dict], limite: int = 6) -> str:
    return ", ".join(f"{v['id']}={v['usos_plugin']}" for v in itens[:limite])


def command_budget(teto: int) -> dict:
    entradas = roster() + comandos()
    disco = agregado_por_ferramenta(entradas)
    habilitadas = [v for v in disco.values() if v["enabled"]]

    chars = sum(v["chars"] for v in habilitadas)
    total_skills = sum(1 for s in entradas if s["enabled"])
    usadas = sum(1 for s in entradas if s["enabled"] and (s.get("usage_count") or 0) > 0)
    # "Sem retorno no roster" != "morto". Custa tokens de descrição e nenhuma
    # skill sua foi invocada; se usos_plugin for alto, o alvo certo é encurtar as
    # descrições, não desinstalar. Ferramenta sem chars não entra: não custa nada.
    sem_retorno = [v for v in habilitadas if v["usos"] == 0 and v["chars"] > 0]
    chars_sem_retorno = sum(v["chars"] for v in sem_retorno)
    load_bearing = [v for v in sem_retorno if v["usos_plugin"] >= 100]

    ranking = sorted(habilitadas, key=lambda v: (-v["chars"], v["id"]))
    excedente = tokens(chars) - teto

    erros = []
    if excedente > 0:
        erros.append(
            f"orçamento estourado: ~{tokens(chars)} tok > teto {teto} (excedente ~{excedente})"
        )

    return {
        "comando": "budget",
        "ready": not erros,
        "errors": erros,
        "warnings": (
            ([f"~{tokens(chars_sem_retorno)} tok "
              f"({round(100 * chars_sem_retorno / chars) if chars else 0}%) em "
              f"{len(sem_retorno)} ferramenta(s) que custam roster e nunca tiveram skill invocada"]
             if sem_retorno else [])
            + ([f"dessas, {len(load_bearing)} têm uso pesado por hook/MCP "
                f"({_lista_load_bearing(load_bearing)}) — encurte a descrição, não desinstale"]
               if load_bearing else [])
        ),
        "resumo": {
            "chars_exatos": chars,
            "tokens_aprox": tokens(chars),
            "teto": teto,
            "excedente": max(0, excedente),
            "entradas_no_roster": total_skills,
            "entradas_com_uso": usadas,
            "ferramentas_habilitadas": len(habilitadas),
            "ferramentas_sem_retorno": len(sem_retorno),
            "tokens_sem_retorno": tokens(chars_sem_retorno),
            "dessas_load_bearing": len(load_bearing),
            "nota": f"chars é exato; tokens ~ chars/{CHARS_POR_TOKEN}, aproximação declarada",
        },
        "ranking": [
            {
                "id": v["id"], "n_skills": v["n_skills"], "n_comandos": v["n_comandos"],
                "chars": v["chars"], "tokens": tokens(v["chars"]),
                "usos": v["usos"], "usos_plugin": v["usos_plugin"],
                "tokens_por_uso": (tokens(v["chars"]) if v["usos"] == 0 else round(tokens(v["chars"]) / v["usos"])),
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
    orc = sub.add_parser("budget", parents=[comum], help="Custo do roster contra o teto.")
    orc.add_argument("--teto", type=int, default=TETO_TOKENS_PADRAO)
    col = sub.add_parser("collisions", parents=[comum], help="Descrições que disputam o mesmo gatilho.")
    col.add_argument("--min", dest="minimo", type=float, default=COS_CRUZADO_ALERTA)
    col.add_argument("--todas", action="store_true", help="inclui skills desabilitadas")
    args = parser.parse_args()

    root = Path(args.root) if args.root else default_root()

    if args.comando == "check":
        res = command_check(root)
    elif args.comando == "reconcile":
        res = command_reconcile(root)
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
