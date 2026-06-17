"""Auditoria e manutenção conservadora de notas Markdown do Obsidian."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Callable


MOJIBAKE_REPLACEMENTS = {
    "â€“": "–",
    "â€”": "—",
    "â€˜": "‘",
    "â€™": "’",
    "â€œ": "“",
    "â€": "”",
    "â€¦": "…",
    "Â ": " ",
    "Â ": " ",
    "Ã¡": "á",
    "Ã ": "à",
    "Ã¢": "â",
    "Ã£": "ã",
    "Ã¤": "ä",
    "Ã©": "é",
    "Ãª": "ê",
    "Ã­": "í",
    "Ã³": "ó",
    "Ã´": "ô",
    "Ãµ": "õ",
    "Ã¶": "ö",
    "Ãº": "ú",
    "Ã¼": "ü",
    "Ã§": "ç",
    "Ã": "Á",
    "Ã€": "À",
    "Ã‚": "Â",
    "Ãƒ": "Ã",
    "Ã‰": "É",
    "ÃŠ": "Ê",
    "Ã": "Í",
    "Ã“": "Ó",
    "Ã”": "Ô",
    "Ã•": "Õ",
    "Ãš": "Ú",
    "Ã‡": "Ç",
}

PORTUGUESE_REPLACEMENTS = {
    "academico": "acadêmico",
    "academica": "acadêmica",
    "acao": "ação",
    "acoes": "ações",
    "anonimizacao": "anonimização",
    "aplicacao": "aplicação",
    "aplicacoes": "aplicações",
    "aprovacao": "aprovação",
    "codigo": "código",
    "codigos": "códigos",
    "comunicacao": "comunicação",
    "configuracao": "configuração",
    "configuracoes": "configurações",
    "conteudo": "conteúdo",
    "conteudos": "conteúdos",
    "criterio": "critério",
    "criterios": "critérios",
    "cronologico": "cronológico",
    "decisao": "decisão",
    "decisoes": "decisões",
    "definicao": "definição",
    "demonstracao": "demonstração",
    "diagnostico": "diagnóstico",
    "diretorio": "diretório",
    "diretorios": "diretórios",
    "documentacao": "documentação",
    "empirico": "empírico",
    "empirica": "empírica",
    "especifica": "específica",
    "especifico": "específico",
    "estatistica": "estatística",
    "execucao": "execução",
    "extracao": "extração",
    "geracao": "geração",
    "governanca": "governança",
    "historica": "histórica",
    "historico": "histórico",
    "implementacao": "implementação",
    "indice": "índice",
    "industria": "indústria",
    "informacao": "informação",
    "informacoes": "informações",
    "ingles": "inglês",
    "integracao": "integração",
    "logica": "lógica",
    "maximo": "máximo",
    "memoria": "memória",
    "memorias": "memórias",
    "metrica": "métrica",
    "metricas": "métricas",
    "minimo": "mínimo",
    "nucleo": "núcleo",
    "numero": "número",
    "numeros": "números",
    "observacao": "observação",
    "observacoes": "observações",
    "opcao": "opção",
    "opcoes": "opções",
    "organizacao": "organização",
    "otimizacao": "otimização",
    "pagina": "página",
    "paginas": "páginas",
    "padrao": "padrão",
    "padroes": "padrões",
    "pendencia": "pendência",
    "pendencias": "pendências",
    "periodo": "período",
    "periodos": "períodos",
    "portugues": "português",
    "possivel": "possível",
    "possiveis": "possíveis",
    "producao": "produção",
    "producoes": "produções",
    "proximo": "próximo",
    "proxima": "próxima",
    "proximos": "próximos",
    "proximas": "próximas",
    "recomendacao": "recomendação",
    "recomendacoes": "recomendações",
    "referencia": "referência",
    "referencias": "referências",
    "relatorio": "relatório",
    "relatorios": "relatórios",
    "relevancia": "relevância",
    "restricao": "restrição",
    "restricoes": "restrições",
    "reuniao": "reunião",
    "reunioes": "reuniões",
    "revisao": "revisão",
    "revisoes": "revisões",
    "secao": "seção",
    "secoes": "seções",
    "semantica": "semântica",
    "sessao": "sessão",
    "sessoes": "sessões",
    "sintese": "síntese",
    "sugestao": "sugestão",
    "tecnica": "técnica",
    "tecnicas": "técnicas",
    "tecnico": "técnico",
    "tecnicos": "técnicos",
    "tematica": "temática",
    "tematicas": "temáticas",
    "tematico": "temático",
    "tematicos": "temáticos",
    "transitorio": "transitório",
    "transitoria": "transitória",
    "usuario": "usuário",
    "usuarios": "usuários",
    "validacao": "validação",
    "validacoes": "validações",
    "variancia": "variância",
    "visao": "visão",
}

PROTECTED_PROSE_PREFIXES = (
    "AI-Brain/CLAUDE.md",
    "AI-Brain/raw/",
    "AI-Brain/wiki/log.md",
    "AI-Brain/wiki/sessions/",
    "copilot/",
    "templates/",
)

VAULT_PATH_RENAMES = {
    "Bem-vindo.md": "AI-Brain/raw/inbox/Bem-vindo ao Obsidian.md",
    "Perguntas para Tender.md": (
        "SLB/04 Reuniões e Stakeholders/Perguntas para Tender.md"
    ),
    "Project Picking.md": "projetos/Seleção de Projetos.md",
    "Sem título 1.md": "decisões/Checkpoint Humano - Auditoria de Código.md",
    "Sem título.md": ("SLB/03 Decisões e Lacunas/Perguntas para Fechar o One-Pager.md"),
    "decisoes/SDD-Research-Report-2026-04-10.md": (
        "decisões/SDD-Research-Report-2026-04-10.md"
    ),
    "Capital Em Regra hrdn_studio/00 Indice.md": (
        "Capital Em Regra hrdn_studio/00 Índice.md"
    ),
    "Capital Em Regra hrdn_studio/01 Fonte De Verdade.md": (
        "Capital Em Regra hrdn_studio/01 Fonte da Verdade.md"
    ),
    "Capital Em Regra hrdn_studio/02 Plano Mestre E Checklist.md": (
        "Capital Em Regra hrdn_studio/02 Plano Mestre e Checklist.md"
    ),
    "Capital Em Regra hrdn_studio/03 Decisoes.md": (
        "Capital Em Regra hrdn_studio/03 Decisões.md"
    ),
    "Capital Em Regra hrdn_studio/04 Ferramentas E Ambiente.md": (
        "Capital Em Regra hrdn_studio/04 Ferramentas e Ambiente.md"
    ),
    "Capital Em Regra hrdn_studio/99 Registro De Sessoes.md": (
        "Capital Em Regra hrdn_studio/99 Registro de Sessões.md"
    ),
    "Garage Dream Brasil Identidade Visual/00 Indice.md": (
        "Garage Dream Brasil Identidade Visual/00 Índice.md"
    ),
    "Garage Dream Brasil Identidade Visual/01 Fonte De Verdade.md": (
        "Garage Dream Brasil Identidade Visual/01 Fonte da Verdade.md"
    ),
    "Garage Dream Brasil Identidade Visual/02 Plano Mestre E Checklist.md": (
        "Garage Dream Brasil Identidade Visual/02 Plano Mestre e Checklist.md"
    ),
    "Garage Dream Brasil Identidade Visual/03 Decisoes.md": (
        "Garage Dream Brasil Identidade Visual/03 Decisões.md"
    ),
    "Garage Dream Brasil Identidade Visual/04 Ferramentas E Ambiente.md": (
        "Garage Dream Brasil Identidade Visual/04 Ferramentas e Ambiente.md"
    ),
    "Garage Dream Brasil Identidade Visual/05 Fase 1 Documentacao Base.md": (
        "Garage Dream Brasil Identidade Visual/05 Fase 1 Documentação Base.md"
    ),
    "Garage Dream Brasil Identidade Visual/06 Fase 2 Auditoria Tecnica Da Logo.md": (
        "Garage Dream Brasil Identidade Visual/06 Fase 2 Auditoria Técnica da Logo.md"
    ),
    "Garage Dream Brasil Identidade Visual/08 Fase 4 Sistema De Logo.md": (
        "Garage Dream Brasil Identidade Visual/08 Fase 4 Sistema de Logo.md"
    ),
    "Garage Dream Brasil Identidade Visual/10 Fase 6 Artefatos Para Redes.md": (
        "Garage Dream Brasil Identidade Visual/10 Fase 6 Artefatos para Redes.md"
    ),
    "Garage Dream Brasil Identidade Visual/11 Fase 7 Series De Conteudo.md": (
        "Garage Dream Brasil Identidade Visual/11 Fase 7 Séries de Conteúdo.md"
    ),
    "Garage Dream Brasil Identidade Visual/12 Fase 8 Motion E Audio Backlog.md": (
        "Garage Dream Brasil Identidade Visual/12 Fase 8 Motion e Áudio - Backlog.md"
    ),
    "Garage Dream Brasil Identidade Visual/13 Pacote Final E Governanca.md": (
        "Garage Dream Brasil Identidade Visual/13 Pacote Final e Governança.md"
    ),
    "Garage Dream Brasil Identidade Visual/99 Registro De Sessoes.md": (
        "Garage Dream Brasil Identidade Visual/99 Registro de Sessões.md"
    ),
    "SLB/01 Nucleo Conceitual/EcoSys - Modelo de Dados e Anatomia do Sistema.md": (
        "SLB/01 Núcleo Conceitual/EcoSys - Modelo de Dados e Anatomia do Sistema.md"
    ),
    "SLB/01 Nucleo Conceitual/EcoSys MoC e Critter - Fontes de Dados.md": (
        "SLB/01 Núcleo Conceitual/EcoSys MoC e Critter - Fontes de Dados.md"
    ),
    "SLB/01 Nucleo Conceitual/Framework - Motor de Contexto Historico.md": (
        "SLB/01 Núcleo Conceitual/Framework - Motor de Contexto Histórico.md"
    ),
    "SLB/01 Nucleo Conceitual/GFA1 XMT - Recorte Empirico.md": (
        "SLB/01 Núcleo Conceitual/GFA1 XMT - Recorte Empírico.md"
    ),
    "SLB/01 Nucleo Conceitual/Processo Tender - Fluxo e Artefatos.md": (
        "SLB/01 Núcleo Conceitual/Processo Tender - Fluxo e Artefatos.md"
    ),
    "SLB/01 Nucleo Conceitual/Projeto Mestrado Qualificacao - Fonte da Verdade.md": (
        "SLB/01 Núcleo Conceitual/Projeto Mestrado Qualificação - Fonte da Verdade.md"
    ),
    "SLB/02 Metodologia e Estrutura/Benchmark Validacao DSR Sem Caso Real - Lacuna 2.md": (
        "SLB/02 Metodologia e Estrutura/Benchmark Validação DSR Sem Caso Real - Lacuna 2.md"
    ),
    "SLB/02 Metodologia e Estrutura/Estrutura Qualificacao PUCPR.md": (
        "SLB/02 Metodologia e Estrutura/Estrutura Qualificação PUCPR.md"
    ),
    "SLB/02 Metodologia e Estrutura/Governanca Editorial e de Escopo.md": (
        "SLB/02 Metodologia e Estrutura/Governança Editorial e de Escopo.md"
    ),
    "SLB/02 Metodologia e Estrutura/Producoes Cientificas e Resultados Parciais.md": (
        "SLB/02 Metodologia e Estrutura/Produções Científicas e Resultados Parciais.md"
    ),
    "SLB/02 Metodologia e Estrutura/RSL CIMO - Sintese.md": (
        "SLB/02 Metodologia e Estrutura/RSL CIMO - Síntese.md"
    ),
    "SLB/03 Decisoes e Lacunas/B3 - Acesso e Integracao Critter.md": (
        "SLB/03 Decisões e Lacunas/B3 - Acesso e Integração Critter.md"
    ),
    "SLB/03 Decisoes e Lacunas/Decisoes Confirmadas Timeline.md": (
        "SLB/03 Decisões e Lacunas/Decisões Confirmadas - Linha do Tempo.md"
    ),
    "SLB/03 Decisoes e Lacunas/Lacunas E Perguntas De Coleta - Projeto Mestrado.md": (
        "SLB/03 Decisões e Lacunas/Lacunas e Perguntas de Coleta - Projeto Mestrado.md"
    ),
    "SLB/03 Decisoes e Lacunas/Lacunas Residuais do Projeto 2026-05-28.md": (
        "SLB/03 Decisões e Lacunas/Lacunas Residuais do Projeto 2026-05-28.md"
    ),
    "SLB/03 Decisoes e Lacunas/Lacunas Restantes Para Fortalecer Qualificacao.md": (
        "SLB/03 Decisões e Lacunas/Lacunas Restantes para Fortalecer a Qualificação.md"
    ),
    "SLB/03 Decisoes e Lacunas/Opcoes De Enquadramento Do Artefato.md": (
        "SLB/03 Decisões e Lacunas/Opções de Enquadramento do Artefato.md"
    ),
    "SLB/03 Decisoes e Lacunas/Pendencias Consolidadas.md": (
        "SLB/03 Decisões e Lacunas/Pendências Consolidadas.md"
    ),
    "SLB/04 Reunioes e Stakeholders/Ata Reunião Gerente Tender 2026-05-28.md": (
        "SLB/04 Reuniões e Stakeholders/Ata Reunião Gerente Tender 2026-05-28.md"
    ),
    "SLB/04 Reunioes e Stakeholders/Stakeholders e Reunioes PMT.md": (
        "SLB/04 Reuniões e Stakeholders/Stakeholders e Reuniões PMT.md"
    ),
    "SLB/05 Transitorio/Diagnostico e Perguntas 2026-06-09.md": (
        "SLB/05 Transitório/Diagnóstico e Perguntas 2026-06-09.md"
    ),
    "SLB/05 Transitorio/Estrutura Revisada do Trabalho.md": (
        "SLB/05 Transitório/Estrutura Revisada do Trabalho.md"
    ),
    "SLB/05 Transitorio/OnePager Validacao Orientador.md": (
        "SLB/05 Transitório/One-Pager - Validação com Orientador.md"
    ),
    "SLB/05 Transitorio/Requisitos Iniciais de Dados.md": (
        "SLB/05 Transitório/Requisitos Iniciais de Dados.md"
    ),
    "TimeSeries/Aprendizados - Extracao estruturada de literatura (corpus PdM offshore).md": (
        "TimeSeries/Aprendizados - Extração estruturada de literatura (corpus PdM offshore).md"
    ),
    "TimeSeries/NewResearch -  Modelos LSTM, GRU, Transformer, atenção.md": (
        "TimeSeries/NewResearch - Modelos LSTM, GRU, Transformer, atenção.md"
    ),
    "TimeSeries/NewResearch - Auditoria Vies Open Access NEW_SET2.md": (
        "TimeSeries/NewResearch - Auditoria Viés Open Access NEW_SET2.md"
    ),
}

PROTECTED_INLINE_RE = re.compile(
    r"(`+[^`\n]*`+|\[\[[^\]]+\]\]|\]\([^)]+\)|https?://\S+|#[\w/-]+)"
)
WIKILINK_RE = re.compile(r"(!?\[\[)([^\]]+)(\]\])")
FENCE_RE = re.compile(r"^\s*(```|~~~)")


def repair_mojibake(text: str) -> str:
    """Repara sequências comuns geradas por UTF-8 interpretado como Windows-1252."""
    markers = "ÃÂâÎðŸœ�"
    repaired_lines: list[str] = []
    for line in text.splitlines(keepends=True):
        repaired = line
        for _ in range(3):
            try:
                candidate = repaired.encode("cp1252").decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                break
            if sum(candidate.count(marker) for marker in markers) >= sum(
                repaired.count(marker) for marker in markers
            ):
                break
            repaired = candidate
        repaired_lines.append(repaired)
    text = "".join(repaired_lines)
    for broken, repaired in MOJIBAKE_REPLACEMENTS.items():
        text = text.replace(broken, repaired)
    return text


def _match_case(source: str, replacement: str) -> str:
    if source.isupper():
        return replacement.upper()
    if source[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def _accentuate_plain_text(text: str) -> str:
    def replace_word(match: re.Match[str]) -> str:
        source = match.group(0)
        replacement = PORTUGUESE_REPLACEMENTS.get(source.casefold())
        return _match_case(source, replacement) if replacement else source

    return re.sub(r"\b[A-Za-z]+\b", replace_word, text)


def _accentuate_wikilink_alias(token: str) -> str:
    if not token.startswith("[[") or "|" not in token:
        return token
    body = token[2:-2]
    target, alias = body.split("|", 1)
    return f"[[{target}|{_accentuate_plain_text(alias)}]]"


def _accentuate_unprotected_line(line: str) -> str:
    parts = PROTECTED_INLINE_RE.split(line)
    for index, part in enumerate(parts):
        if not part:
            continue
        if PROTECTED_INLINE_RE.fullmatch(part):
            parts[index] = _accentuate_wikilink_alias(part)
        else:
            parts[index] = _accentuate_plain_text(part)
    return "".join(parts)


def _transform_outside_fences(
    text: str,
    transform: Callable[[str], str],
    *,
    preserve_frontmatter: bool = False,
) -> str:
    lines = text.splitlines(keepends=True)
    in_fence = False
    in_frontmatter = preserve_frontmatter and bool(lines) and lines[0].strip() == "---"
    frontmatter_closed = not in_frontmatter
    output: list[str] = []

    for index, line in enumerate(lines):
        stripped = line.rstrip("\r\n")
        ending = line[len(stripped) :]

        if in_frontmatter and index > 0 and stripped.strip() == "---":
            in_frontmatter = False
            frontmatter_closed = True
            output.append(line)
            continue

        if FENCE_RE.match(stripped):
            in_fence = not in_fence
            output.append(line)
            continue

        if in_fence or (preserve_frontmatter and not frontmatter_closed):
            output.append(line)
        else:
            output.append(transform(stripped) + ending)

    return "".join(output)


def accentuate_prose(text: str) -> str:
    """Acentua termos portugueses inequívocos sem tocar código, URLs, tags ou alvos."""
    return _transform_outside_fences(
        text,
        _accentuate_unprotected_line,
        preserve_frontmatter=True,
    )


def _normalize_line(line: str) -> str:
    line = line.replace("\t", "    ").rstrip()
    line = re.sub(r"^(\s*)\* \*(.+\*\*.*)$", r"\1**\2", line)
    line = re.sub(
        r"^# ([a-z0-9][\w/-]*(?:\s+#[\w/-]+)+)$",
        r"#\1",
        line,
    )
    line = re.sub(r"^(#{1,6})([A-ZÀ-ÖØ-Þ0-9])", r"\1 \2", line)
    line = re.sub(r"^(\s*[-+])([^ \t-])", r"\1 \2", line)
    line = re.sub(r"^(\s*\*)(?!\*)([^ \t*])", r"\1 \2", line)
    line = re.sub(r"^(\s*\d+\.)([^ \t])", r"\1 \2", line)
    return line


def _collapse_blank_lines_outside_fences(text: str) -> str:
    output: list[str] = []
    in_fence = False
    previous_blank = False

    for line in text.split("\n"):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            previous_blank = False
            output.append(line)
            continue
        if in_fence:
            output.append(line)
            continue
        if line == "":
            if previous_blank:
                continue
            previous_blank = True
            output.append(line)
            continue
        previous_blank = False
        output.append(line)

    return "\n".join(output)


def normalize_markdown(text: str) -> str:
    """Normaliza Markdown sem alterar o conteúdo de blocos cercados."""
    text = unicodedata.normalize("NFC", repair_mojibake(text))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _transform_outside_fences(text, _normalize_line)
    text = _collapse_blank_lines_outside_fences(text)
    return text.rstrip("\n") + "\n"


def update_wikilinks(text: str, renames: dict[str, str]) -> str:
    """Atualiza alvos exatos de wikilinks, preservando alias, heading e embed."""

    def replace_link(match: re.Match[str]) -> str:
        body = match.group(2)
        target_and_heading, separator, alias = body.partition("|")
        target, heading_separator, heading = target_and_heading.partition("#")
        new_target = renames.get(target, target)
        new_body = new_target
        if heading_separator:
            new_body += f"#{heading}"
        if separator:
            new_body += f"|{alias}"
        return f"{match.group(1)}{new_body}{match.group(3)}"

    return WIKILINK_RE.sub(replace_link, text)


def iter_markdown_files(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*.md")
        if not any(part.startswith(".") for part in path.relative_to(root).parts)
    )


def extract_wikilink_targets(text: str) -> list[str]:
    targets: list[str] = []
    for match in WIKILINK_RE.finditer(text):
        body = match.group(2).split("|", 1)[0]
        target = body.split("#", 1)[0].strip().replace("\\", "/")
        if target:
            targets.append(target)
    return targets


def find_broken_wikilinks(root: Path) -> dict[str, list[str]]:
    """Retorna wikilinks de notas que não resolvem por caminho ou basename."""
    files = iter_markdown_files(root)
    relative_targets = {
        path.relative_to(root).with_suffix("").as_posix().casefold() for path in files
    }
    basenames = {path.stem.casefold() for path in files}
    broken: dict[str, list[str]] = {}

    for path in files:
        missing: list[str] = []
        text = path.read_text(encoding="utf-8")
        for target in extract_wikilink_targets(text):
            normalized = target.removesuffix(".md").casefold()
            suffix = Path(target).suffix
            if suffix and suffix.casefold() != ".md":
                continue
            if (
                normalized not in relative_targets
                and Path(normalized).name not in basenames
            ):
                missing.append(target)
        if missing:
            broken[path.relative_to(root).as_posix()] = sorted(set(missing))
    return broken


def _resolve_inside_root(root: Path, relative_path: str) -> Path:
    candidate = root / relative_path
    resolved_parent = candidate.parent.resolve()
    if not resolved_parent.is_relative_to(root):
        raise ValueError(f"Caminho fora do vault: {relative_path}")
    return resolved_parent / candidate.name


def _find_case_insensitive_file(path: Path) -> Path | None:
    if not path.parent.is_dir():
        return None
    return next(
        (
            candidate
            for candidate in path.parent.iterdir()
            if candidate.name.casefold() == path.name.casefold()
        ),
        None,
    )


def organize_notes(root: Path, path_renames: dict[str, str]) -> dict[str, int]:
    """Move notas e atualiza wikilinks por caminho e basename."""
    root = root.resolve()
    moves: list[tuple[Path, Path]] = []
    destinations: set[str] = set()
    link_renames: dict[str, str] = {}
    stem_targets: dict[str, set[str]] = defaultdict(set)

    for old_relative, new_relative in path_renames.items():
        source = _resolve_inside_root(root, old_relative)
        destination = _resolve_inside_root(root, new_relative)
        if source.suffix.casefold() != ".md" or destination.suffix.casefold() != ".md":
            raise ValueError("A organização aceita apenas notas Markdown.")

        old_target = Path(old_relative).with_suffix("").as_posix()
        new_target = Path(new_relative).with_suffix("").as_posix()
        link_renames[old_target] = new_target
        stem_targets[Path(old_relative).stem].add(Path(new_relative).stem)

        actual_source = _find_case_insensitive_file(source)
        actual_destination = _find_case_insensitive_file(destination)
        if actual_source is None:
            if actual_destination is not None:
                continue
            raise FileNotFoundError(source)
        if actual_destination is not None and not actual_source.samefile(
            actual_destination
        ):
            raise FileExistsError(destination)
        if (
            actual_source.parent.resolve() == destination.parent.resolve()
            and actual_source.name == destination.name
        ):
            continue

        destination_key = str(destination).casefold()
        if destination_key in destinations:
            raise ValueError(f"Destino duplicado: {destination}")
        destinations.add(destination_key)
        moves.append((actual_source, destination))

    for old_stem, new_stems in stem_targets.items():
        if len(new_stems) == 1:
            link_renames[old_stem] = next(iter(new_stems))

    for source, destination in moves:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and source.samefile(destination):
            temporary = source.with_name(f".vault-maintenance-{source.name}.tmp")
            source.replace(temporary)
            temporary.replace(destination)
        else:
            source.replace(destination)

    updated_link_sources = 0
    for path in iter_markdown_files(root):
        original = path.read_text(encoding="utf-8")
        updated = update_wikilinks(original, link_renames)
        if updated != original:
            path.write_text(updated, encoding="utf-8", newline="\n")
            updated_link_sources += 1

    return {
        "moved_notes": len(moves),
        "updated_link_sources": updated_link_sources,
    }


def audit_vault(root: Path) -> dict[str, object]:
    files = iter_markdown_files(root)
    hash_groups: dict[str, list[str]] = defaultdict(list)
    empty_notes: list[str] = []
    mojibake_sources: list[str] = []
    formatting_issue_sources: list[str] = []
    decode_error_sources: list[str] = []

    for path in files:
        relative = path.relative_to(root).as_posix()
        hash_groups[hashlib.sha256(path.read_bytes()).hexdigest()].append(relative)
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            decode_error_sources.append(relative)
            continue
        if not text.strip():
            empty_notes.append(relative)
        if repair_mojibake(text) != text or "�" in text:
            mojibake_sources.append(relative)
        if normalize_markdown(text) != text:
            formatting_issue_sources.append(relative)

    duplicate_details = sorted(
        (sorted(paths) for paths in hash_groups.values() if len(paths) > 1),
        key=lambda paths: paths[0],
    )
    broken = find_broken_wikilinks(root)
    return {
        "notes": len(files),
        "duplicate_groups": len(duplicate_details),
        "duplicate_notes": sum(len(paths) for paths in duplicate_details),
        "duplicate_details": duplicate_details,
        "empty_notes": sorted(empty_notes),
        "decode_error_sources": sorted(decode_error_sources),
        "mojibake_sources": sorted(mojibake_sources),
        "formatting_issue_sources": sorted(formatting_issue_sources),
        "broken_link_sources": len(broken),
        "broken_links": sum(len(targets) for targets in broken.values()),
        "broken_link_details": broken,
    }


def apply_text_normalization(root: Path) -> dict[str, int]:
    changed = 0
    accented = 0
    for path in iter_markdown_files(root):
        relative = path.relative_to(root).as_posix()
        original = path.read_text(encoding="utf-8")
        updated = normalize_markdown(original)
        if not relative.startswith(PROTECTED_PROSE_PREFIXES):
            with_accents = accentuate_prose(updated)
            if with_accents != updated:
                accented += 1
            updated = with_accents
        if updated != original:
            path.write_text(updated, encoding="utf-8", newline="\n")
            changed += 1
    return {"changed_notes": changed, "accented_notes": accented}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("audit", "normalize", "organize"))
    parser.add_argument("--root", type=Path, default=Path.cwd())
    return parser


def main() -> None:
    args = build_parser().parse_args()
    root = args.root.resolve()
    if args.command == "audit":
        result = audit_vault(root)
    elif args.command == "normalize":
        result = apply_text_normalization(root)
    else:
        result = organize_notes(root, VAULT_PATH_RENAMES)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
