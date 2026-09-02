from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

L0_PATTERNS = (
    r"\?",
    r"\bexplique\b",
    r"\bexplain\b",
    r"\bo que e\b",
    r"\bwhat is\b",
    r"\bcomo funciona\b",
    r"\bhow does\b",
    r"\bpor que\b",
    r"\bwhy\b",
    r"\bqual a diferenca\b",
    r"\bme diga\b",
    r"\btell me\b",
    r"\bdescreva\b",
    r"\bdescribe\b",
    r"\bmostre\b",
    r"\bshow\b",
    r"\bliste\b",
    r"\blist\b",
    r"\brenomeie\b",
    r"\brename\b",
    r"\bformate\b",
    r"\bformat\b",
    r"\bcorrija typo\b",
    r"\bfix typo\b",
    r"\bajuste indentacao\b",
    r"\bfix indent\b",
    r"\bmude o nome\b",
    r"\batualize comentario\b",
    r"\bupdate comment\b",
    r"\btraduza\b",
    r"\blembre\b",
    r"\bremember\b",
    r"\besqueca\b",
    r"\bforget\b",
    r"\bsalve na memoria\b",
    r"\bcommit\b",
    r"\bpush\b",
)

L2_ARCHITECTURE_PATTERNS = (
    r"\barquitetura\b",
    r"\barchitecture\b",
    r"\bredesign\b",
    r"\breestrutura\b",
    r"\brestructure\b",
    r"\bmigracao\b",
    r"\bmigration\b",
    r"\bmigrar\b",
    r"\bmigrate\b",
    r"\breescreve\b",
    r"\brewrite\b",
    r"\bdo zero\b",
    r"\bfrom scratch\b",
    r"\bsubstituir sistema\b",
    r"\breplace system\b",
)

L2_PATTERNS = (
    r"\bfeature\b",
    r"\bfuncionalidade\b",
    r"\bsistema completo\b",
    r"\bsistema\b",
    r"\bnew system\b",
    r"\bmodulo novo\b",
    r"\bnew module\b",
    r"\bservico\b",
    r"\bservice\b",
    r"\bendpoint novo\b",
    r"\bnew endpoint\b",
    r"\bnovo componente\b",
    r"\bnew component\b",
    r"\bintegracao\b",
    r"\bintegration\b",
    r"\bapi nova\b",
    r"\bnew api\b",
    *L2_ARCHITECTURE_PATTERNS,
    r"\bpipeline\b",
    r"\bworkflow\b",
    r"\borquestracao\b",
    r"\borchestration\b",
    r"\bfluxo completo\b",
    r"\bfull flow\b",
    r"\bplano\b",
    r"\bplan\b",
    r"\bprd\b",
    r"\bspec\b",
    r"\bdesign\b",
    r"\bproposta\b",
    r"\bproposal\b",
    r"\bestrategia\b",
    r"\bstrategy\b",
    r"\bplaneje\b",
    r"\bdesenhe\b",
    r"\bprojete\b",
    r"\barquitete\b",
    r"\belabore\b",
    r"\barchitect\b",
    r"\btoda a base\b",
    r"\bentire codebase\b",
    r"\btodo o projeto\b",
    r"\bwhole project\b",
    r"\brefatora tudo\b",
    r"\brefactor everything\b",
    r"\bem todos os\b",
    r"\bacross all\b",
    r"\bbase inteira\b",
    r"\bde ponta a ponta\b",
    r"\bend-to-end\b",
    r"\bcri[ae] um\b",
    r"\bbuild an app\b",
    r"\bconstrua\b",
    r"\bcriar um\b",
    r"\bimplemente do zero\b",
    r"\bimplement from scratch\b",
    r"\bmonte um\b",
    r"\bset up\b",
    r"banco.*api.*tela",
    r"database.*api.*ui",
    r"frontend.*backend",
    r"schema.*endpoint",
)

#: Revisao de codigo ja escrito. Vem ANTES de bug e refactor na ordem de
#: decisao porque "revisa isso" e um pedido de leitura, nao de mudanca — quem
#: Revisao de codigo ja escrito. Vem ANTES de bug e refactor na ordem de
#: decisao porque "revisa isso" e um pedido de leitura, nao de mudanca — quem
#: pede review nao autorizou edicao, e tratar como refactor inverteria isso.
REVIEW_PATTERNS = (
    r"\brevisa\b",
    r"\brevise\b",
    r"\brevisar\b",
    r"\breview\b",
    r"\bcode review\b",
    r"\bavalia o codigo\b",
    r"\bavaliar o codigo\b",
    r"\bda uma olhada n[oa]\b",
    r"\bcritica\b",
    r"\bcritique\b",
    r"\bauditar?\b",
    r"\baudit\b",
    r"\bo que voce acha d[oa]\b",
    r"\bwhat do you think of\b",
    r"\besta bom\b",
    r"\blooks? good\b",
)

#: Documentacao. `source-selection` abre esses pipelines de proposito:
#: escrever doc sem decidir antes qual fonte manda produz texto plausivel e
#: errado, que e pior que doc ausente — foi o que aconteceu com o README
#: deste repo, que afirmou por semanas que todo pipeline termina em
#: verify-against-spec.
DOCS_PATTERNS = (
    r"\bdocumenta\b",
    r"\bdocumentar\b",
    r"\bdocument\b",
    r"\bdocumentacao\b",
    r"\bdocumentation\b",
    r"\bdocs\b",
    r"\breadme\b",
    r"\bchangelog\b",
    r"\bdocstring\b",
    r"\bcomenta o codigo\b",
    r"\bguia de uso\b",
    r"\busage guide\b",
    r"\btutorial\b",
    r"\bexplica no (readme|doc)\b",
    r"\bescreve[r]? (a )?doc\b",
    r"\bwrite (the )?docs?\b",
)

BUG_PATTERNS = (
    r"\bbug\b",
    r"\bfix\b",
    r"\berro\b",
    r"\berror\b",
    r"\bquebrou\b",
    r"\bbroke\b",
    r"\bfalha\b",
    r"\bfailure\b",
    r"\btraceback\b",
    r"\bexception\b",
    r"\bcrash\b",
    r"\bnao funciona\b",
    r"\bnot working\b",
    r"\bparou de funcionar\b",
    r"\bstopped working\b",
    r"\bdeu ruim\b",
    r"\bcomportamento errado\b",
    r"\bwrong behavior\b",
    r"\binesperado\b",
    r"\bunexpected\b",
    r"\bregressao\b",
    r"\bregression\b",
)

REFACTOR_PATTERNS = (
    r"\brefatora\b",
    r"\brefactor\b",
    r"\blimpa\b",
    r"\bclean\b",
    r"\bmelhora\b",
    r"\bimprove\b",
    r"\bsimplifica\b",
    r"\bsimplify\b",
    r"\bextrai\b",
    r"\bextract\b",
    r"\bsepara\b",
    r"\bseparate\b",
    r"\bdesacopla\b",
    r"\bdecouple\b",
    r"\breorganiza\b",
    r"\breorganize\b",
    r"\breduz duplicacao\b",
    r"\breduce duplication\b",
    r"\bmove para\b",
    r"\bmove to\b",
    r"\botimiza\b",
    r"\boptimize\b",
)

L1_PATTERNS = (
    *BUG_PATTERNS,
    *REFACTOR_PATTERNS,
    r"\badiciona\b",
    r"\badd\b",
    r"\binclui\b",
    r"\binclude\b",
    r"\bimplementa\b",
    r"\bimplement\b",
)


def _normalize(prompt: str) -> str:
    decomposed = unicodedata.normalize("NFKD", str(prompt).lower().strip())
    return "".join(character for character in decomposed if not unicodedata.combining(character))


def _matches(patterns: Iterable[str], prompt: str) -> bool:
    return any(re.search(pattern, prompt) for pattern in patterns)


def classify_prompt(prompt: str) -> tuple[str, str]:
    normalized = _normalize(prompt)
    has_l0 = _matches(L0_PATTERNS, normalized)
    has_l1 = _matches(L1_PATTERNS, normalized)
    has_l2 = _matches(L2_PATTERNS, normalized)
    if has_l0 and not has_l1 and not has_l2:
        level = "L0"
    elif has_l2:
        level = "L2"
    else:
        level = "L1"

    # Ordem importa. `docs` vem primeiro porque "documenta o modulo novo" tem
    # marcador de feature e o pedido e doc. `review` vem antes de bug/refactor
    # porque "revisa o fix" pede leitura, nao conserto: classificar como bug
    # autorizaria edicao que ninguem pediu.
    if _matches(DOCS_PATTERNS, normalized):
        kind = "docs"
    elif _matches(REVIEW_PATTERNS, normalized):
        kind = "review"
    elif _matches(BUG_PATTERNS, normalized):
        kind = "bug"
    elif _matches(REFACTOR_PATTERNS, normalized):
        kind = "refactor"
    elif _matches(L2_ARCHITECTURE_PATTERNS, normalized):
        kind = "architecture"
    else:
        kind = "feature"
    return level, kind
