"""Restaura acentuacao em paginas do vault, sem chutar.

O vault e escrito em portugues e varias paginas nasceram sem acento — hoje 19 delas.
A causa nao foi decisao editorial: e o habito de escrever ASCII para nao arriscar
mojibake no console do Windows. Os arquivos sao UTF-8; o problema so existia na tela.

**Nao adivinha.** Duas listas:

  - `SEGURAS` — a forma sem acento nao e palavra valida em portugues, entao a
    substituicao e determinada: `decisao` so pode ser `decisão`.
  - `AMBIGUAS` — a forma sem acento TAMBEM e palavra ("analise" o substantivo vs. o
    verbo, "pratica" adj. vs. verbo, "esta"/"está"). Estas sao apenas **relatadas**;
    trocar automaticamente corromperia texto correto.

Nunca toca em: bloco de codigo, codigo inline, wikilink, frontmatter, URL. Frontmatter
fica de fora porque `tags: [decisao]` e um identificador, nao prosa — acentuar mudaria
a tag.

Fronteira de escopo: `wiki/specs/` e **espelhado** de `docs/specs/` dos repos pelo
vault_sync. Corrigir a copia a faria divergir da origem e o proximo sync poderia
sobrescrever. Ficam de fora por padrao; `--incluir-espelhadas` forca.

Uso:
    python tools/wiki_accents.py --root DIR [--fix] [--incluir-espelhadas]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wiki_index import default_root

# Areas espelhadas de fora: a fonte da verdade nao esta no vault.
MIRRORED_AREAS = ("specs", "sessions")

SEGURAS: dict[str, str] = {
    # adverbios e conectivos
    "nao": "não", "sao": "são", "voce": "você", "entao": "então", "tambem": "também",
    "ja": "já", "apos": "após", "atraves": "através", "alem": "além", "porem": "porém",
    "ate": "até", "sera": "será", "estara": "estará", "havera": "haverá", "tera": "terá",
    # -cao / -sao
    "decisao": "decisão", "operacao": "operação", "informacao": "informação",
    "execucao": "execução", "integracao": "integração", "configuracao": "configuração",
    "navegacao": "navegação", "geracao": "geração", "validacao": "validação",
    "verificacao": "verificação", "classificacao": "classificação",
    "sincronizacao": "sincronização", "documentacao": "documentação",
    "implementacao": "implementação", "organizacao": "organização",
    "reconciliacao": "reconciliação", "anotacao": "anotação", "citacao": "citação",
    "versao": "versão", "razao": "razão", "sessao": "sessão", "revisao": "revisão",
    "conclusao": "conclusão", "extensao": "extensão", "dimensao": "dimensão",
    # proparoxitonas e afins
    "codigo": "código", "memoria": "memória", "historico": "histórico",
    "pagina": "página", "indice": "índice", "titulo": "título", "unico": "único",
    "publico": "público", "criterio": "critério", "metrica": "métrica",
    "automatico": "automático", "semantico": "semântico", "generico": "genérico",
    "estatico": "estático", "proprio": "próprio", "multiplo": "múltiplo",
    "referencia": "referência", "evidencia": "evidência", "experiencia": "experiência",
    "consequencia": "consequência", "frequencia": "frequência", "ausencia": "ausência",
    "hipotese": "hipótese", "sintese": "síntese", "estrategia": "estratégia",
    "obrigatorio": "obrigatório", "necessario": "necessário", "usuario": "usuário",
    "relatorio": "relatório", "diretorio": "diretório", "repositorio": "repositório",
    "cenario": "cenário", "ciclico": "cíclico", "veredito": "veredito",
    # -vel / -il
    "possivel": "possível", "disponivel": "disponível", "responsavel": "responsável",
    "nivel": "nível", "util": "útil", "dificil": "difícil", "facil": "fácil",
    # diversos
    "area": "área", "duvida": "dúvida", "saude": "saúde", "orfa": "órfã",
    "orfao": "órfão", "orfas": "órfãs", "orfaos": "órfãos",
    # cauda encontrada por varredura apos a primeira aplicacao — nenhuma tem
    # homografo verbal, ao contrario de valida/critica/publica, que ficaram fora.
    "tres": "três", "ninguem": "ninguém", "alguem": "alguém",
    "tecnica": "técnica", "tecnicas": "técnicas",
    "tecnico": "técnico", "tecnicos": "técnicos",
    "residuo": "resíduo", "residuos": "resíduos",
    "cronologico": "cronológico", "cronologica": "cronológica",
    "semantica": "semântica", "logica": "lógica", "logico": "lógico",
    "metodo": "método", "metodos": "métodos",
    "numero": "número", "numeros": "números",
    "proximo": "próximo", "proxima": "próxima",
    "ultimo": "último", "ultima": "última",
    "minimo": "mínimo", "maximo": "máximo", "basico": "básico",
    "parametro": "parâmetro", "parametros": "parâmetros",
    "modulo": "módulo", "modulos": "módulos",
    "estatistica": "estatística", "matematica": "matemática",
    "paragrafo": "parágrafo", "paragrafos": "parágrafos",
    "capitulo": "capítulo", "capitulos": "capítulos",
    "grafico": "gráfico", "graficos": "gráficos",
    "topico": "tópico", "topicos": "tópicos",
    "dominio": "domínio", "dominios": "domínios",
    "principio": "princípio", "principios": "princípios",
    "inicio": "início", "exercicio": "exercício",
    "periodo": "período", "periodos": "períodos",
    "veiculo": "veículo", "multiplos": "múltiplos",
    "classico": "clássico", "academico": "acadêmico", "canonica": "canônica",
    "canonico": "canônico", "canonicos": "canônicos",
}

# Forma sem acento TAMBEM e palavra valida: so relata, nunca troca.
#
# Criterio de entrada: as DUAS leituras precisam ser plausiveis no texto deste vault.
# "e"/"da"/"as" saem — sao corretas em praticamente toda ocorrencia, e relata-las
# produzia dezenas de falsos positivos por pagina, tornando o relatorio ilegivel. Uma
# lista de revisao que ninguem le nao revisa nada.
AMBIGUAS: tuple[str, ...] = (
    "analise", "pratica", "pratico", "esta", "so",
    # Adjetivo acentuado vs. verbo na 3a pessoa: "a regra e válida" x "o lint valida".
    "valida", "critica", "publica", "especifica",
    # "series" depende do idioma da frase: "séries temporais" x "time series".
    "series",
)

# Regras morfologicas. Listar palavra por palavra nao se sustenta: a primeira versao
# tinha 80 entradas e ainda deixava 61 escapando (plurais, variantes, palavras novas).
# Estes sufixos sao deterministas em portugues — nao existe palavra terminada em "-cao"
# que nao seja "-ção". A regra cobre o que ainda nao foi escrito.
#
# Deliberadamente FORA: "-logia" (metodologia, cronologia nao levam acento) e "-aria"
# (maquinaria sem acento vs. necessaria com). Sufixo com excecao nao e regra.
SUFIXOS: tuple[tuple[str, str], ...] = (
    ("coes", "ções"), ("cao", "ção"),
    ("encias", "ências"), ("encia", "ência"),
    ("ancias", "âncias"), ("ancia", "ância"),
    ("orios", "órios"), ("orio", "ório"),
    ("arios", "ários"), ("ario", "ário"),
    ("iveis", "íveis"), ("ivel", "ível"),
    ("aveis", "áveis"), ("avel", "ável"),
    ("ssao", "ssão"), ("nsao", "nsão"),
    # Hiato u+i tonico: construido, reconstruida, distribuidos.
    ("uidos", "uídos"), ("uidas", "uídas"), ("uido", "uído"), ("uida", "uída"),
)
MIN_RAIZ = 3  # evita casar palavra curta que so parece ter o sufixo

_SUFIXO_RE = re.compile(
    r"(?<![\w\-/])([a-zA-ZÀ-ÿ]{" + str(MIN_RAIZ) + r",}?)("
    + "|".join(s for s, _ in SUFIXOS) + r")(?![\w\-/])"
)

_FENCE = re.compile(r"```.*?```", re.S)
_INLINE = re.compile(r"`[^`\n]+`")
_LINK = re.compile(r"\[\[[^\]]+\]\]")
_URL = re.compile(r"https?://\S+")
_FRONTMATTER = re.compile(r"\A---\s*\n.*?\n---\s*\n", re.S)

_SEGURAS_RE = re.compile(
    r"(?<![\w\-/])(" + "|".join(sorted(SEGURAS, key=len, reverse=True)) + r")(?![\w\-/])",
    re.I,
)
_AMBIGUAS_RE = re.compile(
    r"(?<![\w\-/])(" + "|".join(AMBIGUAS) + r")(?![\w\-/])", re.I
)


def _match_case(original: str, substituto: str) -> str:
    """Preserva o padrao de caixa do original."""
    if original.isupper():
        return substituto.upper()
    if original[0].isupper():
        return substituto[0].upper() + substituto[1:]
    return substituto


def protected_spans(text: str) -> list[tuple[int, int]]:
    """Trechos intocaveis: codigo, wikilink, URL e frontmatter."""
    spans = []
    for padrao in (_FRONTMATTER, _FENCE, _INLINE, _LINK, _URL):
        spans += [m.span() for m in padrao.finditer(text)]
    return sorted(spans)


def _protegido(pos: int, spans: list[tuple[int, int]]) -> bool:
    return any(inicio <= pos < fim for inicio, fim in spans)


_SUFIXO_MAP: dict[str, str] = dict(SUFIXOS)


def fix_text(text: str) -> tuple[str, int]:
    """Aplica dicionario explicito e regras de sufixo, fora dos trechos protegidos."""
    spans = protected_spans(text)
    trocas = 0

    def por_dicionario(match: re.Match[str]) -> str:
        nonlocal trocas
        if _protegido(match.start(), spans):
            return match.group(0)
        trocas += 1
        return _match_case(match.group(0), SEGURAS[match.group(0).lower()])

    def por_sufixo(match: re.Match[str]) -> str:
        nonlocal trocas
        if _protegido(match.start(), spans):
            return match.group(0)
        raiz, sufixo = match.group(1), match.group(2)
        acentuado = _SUFIXO_MAP[sufixo.lower()]
        trocas += 1
        return raiz + (acentuado.upper() if sufixo.isupper() else acentuado)

    resultado = _SEGURAS_RE.sub(por_dicionario, text)
    # Recalcula os trechos protegidos: a primeira passada mudou os offsets.
    spans = protected_spans(resultado)
    return _SUFIXO_RE.sub(por_sufixo, resultado), trocas


def report_ambiguous(text: str) -> list[str]:
    """Palavras ambiguas presentes — para revisao humana, nunca troca automatica."""
    spans = protected_spans(text)
    achadas = {
        m.group(0).lower()
        for m in _AMBIGUAS_RE.finditer(text)
        if not _protegido(m.start(), spans)
    }
    return sorted(achadas)


def target_pages(root: Path, *, incluir_espelhadas: bool = False) -> list[Path]:
    """Paginas do vault sob revisao, fora de `graphs/` e das areas espelhadas."""
    wiki = root / "wiki"
    if not wiki.is_dir():
        return []
    paginas = []
    for path in sorted(wiki.rglob("*.md")):
        partes = path.relative_to(wiki).parts
        if "graphs" in partes:
            continue
        if not incluir_espelhadas and partes[0] in MIRRORED_AREAS:
            continue
        paginas.append(path)
    return paginas


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--fix", action="store_true", help="Aplica; sem a flag, so relata.")
    parser.add_argument(
        "--incluir-espelhadas", action="store_true",
        help="Inclui wiki/specs e wiki/sessions, espelhadas de fora do vault.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    root = args.root or default_root()
    total, tocadas, ambiguas = 0, [], {}

    for page in target_pages(root, incluir_espelhadas=args.incluir_espelhadas):
        texto = page.read_text(encoding="utf-8")
        novo, trocas = fix_text(texto)
        rel = page.relative_to(root / "wiki").as_posix()
        if trocas:
            total += trocas
            tocadas.append((rel, trocas))
            if args.fix:
                page.write_text(novo, encoding="utf-8")
        duvidosas = report_ambiguous(novo)
        if duvidosas:
            ambiguas[rel] = duvidosas

    verbo = "corrigidas" if args.fix else "a corrigir"
    print(f"{total} ocorrencias {verbo} em {len(tocadas)} paginas")
    for rel, n in sorted(tocadas, key=lambda x: -x[1]):
        print(f"  {n:>4}  {rel}")
    if ambiguas:
        print(f"\nAmbiguas — revisao humana ({len(ambiguas)} paginas):")
        for rel, palavras in sorted(ambiguas.items()):
            print(f"  {rel}: {', '.join(palavras)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
