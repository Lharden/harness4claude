"""Confere que cada achado nao adjudicado do wf-verify-multimodel tem destino escrito.

Spec: docs/specs/verify-adjudica-tudo-spec.md (US-2). O `pass` do Workflow so
sai verde sem achado aberto; este script diz se a triagem dos abertos acabou.
Ate 2026-09-30 o `pass` ignorava medium/low, e tres rodadas seguidas saíram
verdes com 25 defeitos reais entre 49 achados abertos
(docs/specs/verify-adjudica-tudo-medicao.md).

Uso (secao verify da skill harness-workflow):

    python scripts/checar_triagem.py --plano docs/closure-plan.md --saidas <STATE_DIR>/verify

`--saidas` recebe copias da saida do host (`<run id>.json`) ou um diretorio com
elas. A rodada e o nome do arquivo sem extensao. A tabela mora na secao
`## Destino dos achados não adjudicados` do plano:

    | Rodada | # | Achado | Destino | Referência |
    |---|---|---|---|---|
    | wf_6bd9e71a-c4f | 10 | Exit code is read from the first | consertado | G1/D2, test_codigo_ancorado |

Sai 0 (todo nao adjudicado de toda rodada com destino, e a ultima rodada sem
bloqueio), 1 (pendente, linha invalida ou bloqueio — com as linhas prontas para
colar) ou 2 (entrada ilegivel). Toda duvida erra reprovando.

O que ele NAO garante: que o destino e verdadeiro (um "limite" pode ser defeito),
nem que a copia e a saida do host sem edicao — o host nao assina a saida.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

DESTINOS = ("consertado", "limite", "nao-defeito")
MARCADOR_DESTINO = "<consertado|limite|nao-defeito>"
MARCADOR_REFERENCIA = "<teste, commit ou motivo>"
REFERENCIAS_VAZIAS = {"n/a", "na", "-", "?", "ok", "tbd", "todo"}
MIN_REFERENCIA = 7
MIN_TITULO = 20
TAM_PRONTA = 40
SECAO = re.compile(r"^##\s+destino dos achados n[aã]o adjudicados\s*$", re.IGNORECASE)
OUTRA_SECAO = re.compile(r"^##\s")
CERCA = re.compile(r"^\s*(```|~~~)")


class EntradaIlegivel(Exception):
    """Copia de rodada que nao da para ler com seguranca."""


@dataclass(frozen=True)
class Achado:
    n: int
    titulo: str
    adjudicado: bool


@dataclass(frozen=True)
class Rodada:
    id: str
    inicio: float
    achados: tuple[Achado, ...]
    critical_count: int
    nos_mortos: tuple[str, ...]

    @property
    def pendentes(self) -> list[Achado]:
        return [a for a in self.achados if not a.adjudicado]


@dataclass(frozen=True)
class Linha:
    rodada: str
    n: int
    achado: str
    destino: str
    referencia: str
    lineno: int


def _normaliza(texto: str) -> str:
    """Caixa, espaco repetido e `|` (que quebraria a tabela) nao distinguem titulo."""
    return " ".join(texto.replace("|", "/").split()).casefold()


def _arquivos(saidas: list[Path]) -> list[Path]:
    arquivos: list[Path] = []
    for s in saidas:
        if s.is_dir():
            achados = sorted(s.glob("*.json"))
            if not achados:
                raise EntradaIlegivel(f"{s}: diretorio sem copia de rodada (*.json)")
            arquivos.extend(achados)
        elif s.is_file():
            arquivos.append(s)
        else:
            raise EntradaIlegivel(f"{s}: nao existe")
    return arquivos


def _ler_rodada(caminho: Path) -> Rodada:
    try:
        dado = json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EntradaIlegivel(f"{caminho}: {exc}") from exc
    if not isinstance(dado, dict):
        raise EntradaIlegivel(f"{caminho}: o JSON nao e um objeto")
    # Formato do host: {summary, result, workflowProgress, ...}; formato do retorno: {findings, ...}.
    result = dado.get("result", dado)
    if not isinstance(result, dict) or not isinstance(result.get("findings"), list):
        raise EntradaIlegivel(f"{caminho}: sem lista `findings`")
    for campo in ("critical_count", "nos_mortos"):
        if campo not in result:
            raise EntradaIlegivel(f"{caminho}: sem `{campo}` — sem ele nao da para saber se houve bloqueio")
    critical_count = result["critical_count"]
    nos_mortos = result["nos_mortos"]
    if not isinstance(critical_count, int) or not isinstance(nos_mortos, list):
        raise EntradaIlegivel(f"{caminho}: `critical_count` ou `nos_mortos` com tipo inesperado")

    achados = []
    for pos, f in enumerate(result["findings"], 1):
        if not isinstance(f, dict):
            raise EntradaIlegivel(f"{caminho}: achado na posicao {pos} nao e objeto")
        if "n" in f and f["n"] != pos:
            raise EntradaIlegivel(f"{caminho}: achado na posicao {pos} traz n={f['n']!r}")
        # So `adjudicado: true` tira o achado da triagem; ausente conta como aberto.
        achados.append(Achado(pos, str(f.get("title", "")), f.get("adjudicado") is True))

    progresso = dado.get("workflowProgress")
    inicios = [
        w["startedAt"]
        for w in (progresso if isinstance(progresso, list) else [])
        if isinstance(w, dict) and isinstance(w.get("startedAt"), (int, float))
    ]
    inicio = max(inicios) if inicios else caminho.stat().st_mtime * 1000
    return Rodada(caminho.stem, float(inicio), tuple(achados), critical_count, tuple(str(m) for m in nos_mortos))


def _ler_tabela(plano: Path) -> tuple[list[Linha], list[str]]:
    """Linhas da secao de destino, fora de bloco de codigo, e os problemas de formato."""
    if not plano.is_file():
        return [], []
    linhas: list[Linha] = []
    problemas: list[str] = []
    dentro = cerca = False
    for i, bruta in enumerate(plano.read_text(encoding="utf-8").splitlines(), 1):
        texto = bruta.strip()
        if CERCA.match(bruta):
            cerca = not cerca
            continue
        if cerca:
            continue
        if SECAO.match(texto):
            dentro = True
            continue
        if OUTRA_SECAO.match(texto):
            dentro = False
            continue
        if not dentro or not texto.startswith("|"):
            continue
        celulas = [c.strip() for c in texto.strip("|").split("|")]
        if celulas[0].casefold() == "rodada" or all(set(c) <= set("-: ") for c in celulas):
            continue
        onde = f"{plano.name}:{i}"
        if len(celulas) != 5:
            problemas.append(
                f"INVALIDA {onde}: {len(celulas)} celula(s), esperado 5 — se colou a linha pronta, "
                "troque o destino e a referencia"
            )
            continue
        if not celulas[1].isdigit():
            problemas.append(f'INVALIDA {onde}: "#" = "{celulas[1]}" nao e numero')
            continue
        linhas.append(Linha(celulas[0], int(celulas[1]), celulas[2], celulas[3], celulas[4], i))
    return linhas, problemas


def _erros_da_linha(linha: Linha, achado: Achado) -> list[str]:
    erros = []
    titulo, celula = _normaliza(achado.titulo), _normaliza(linha.achado)
    if len(celula) < min(MIN_TITULO, len(titulo)) or not titulo.startswith(celula):
        erros.append(f'"{linha.achado}" nao e o inicio do titulo "{achado.titulo}"')
    destino = linha.destino.strip().casefold().replace("ã", "a")
    if destino not in DESTINOS:
        erros.append(f'destino "{linha.destino}" (use consertado, limite ou nao-defeito)')
    ref = linha.referencia.strip()
    if ref == MARCADOR_REFERENCIA or ref.casefold() in REFERENCIAS_VAZIAS or len("".join(ref.split())) < MIN_REFERENCIA:
        erros.append(f'referencia "{ref}" (diga o teste, o commit ou o motivo, com {MIN_REFERENCIA}+ caracteres)')
    return erros


def _linha_pronta(rodada: Rodada, achado: Achado) -> str:
    inicio = achado.titulo.replace("|", "/")[:TAM_PRONTA].rstrip()
    return f"| {rodada.id} | {achado.n} | {inicio} | {MARCADOR_DESTINO} | {MARCADOR_REFERENCIA} |"


def checar(plano: Path, saidas: list[Path]) -> tuple[int, list[str]]:
    """Devolve (codigo de saida, mensagens)."""
    try:
        rodadas = [_ler_rodada(a) for a in _arquivos(saidas)]
    except EntradaIlegivel as exc:
        return 2, [f"ENTRADA ILEGIVEL: {exc}"]
    ids = [r.id for r in rodadas]
    repetidos = sorted({i for i in ids if ids.count(i) > 1})
    if repetidos:
        return 2, [f"ENTRADA ILEGIVEL: rodada(s) com o mesmo id em arquivos diferentes: {', '.join(repetidos)}"]

    por_id = {r.id: r for r in rodadas}
    linhas, problemas = _ler_tabela(plano)
    vistas: dict[tuple[str, int], int] = {}
    decididos: set[tuple[str, int]] = set()
    for linha in linhas:
        rodada = por_id.get(linha.rodada)
        if rodada is None:
            continue  # linha de rodada que nao veio em --saidas
        chave = (linha.rodada, linha.n)
        onde = f"{plano.name}:{linha.lineno}"
        if chave in vistas:
            problemas.append(f"DUPLICADA {onde}: {linha.rodada} #{linha.n} ja tem linha em :{vistas[chave]}")
            decididos.discard(chave)
            continue
        vistas[chave] = linha.lineno
        achado = next((a for a in rodada.pendentes if a.n == linha.n), None)
        if achado is None:
            problemas.append(f"INVALIDA {onde}: {linha.rodada} #{linha.n} nao e achado nao adjudicado desta rodada")
            continue
        erros = _erros_da_linha(linha, achado)
        if erros:
            problemas.append(f"INVALIDA {onde}: " + "; ".join(erros))
        else:
            decididos.add(chave)

    ordem = sorted(rodadas, key=lambda r: r.inicio)
    faltam = [(r, a) for r in ordem for a in r.pendentes if (r.id, a.n) not in decididos]
    problemas.extend(f'PENDENTE {r.id} #{a.n} (sem linha valida): "{a.titulo}"' for r, a in faltam)
    final = ordem[-1]
    if final.critical_count or final.nos_mortos:
        problemas.append(
            f"BLOQUEIO {final.id}: critical_count={final.critical_count}, nos_mortos={list(final.nos_mortos)} — "
            "resolva pelo gap-closure (secao verify da skill harness-workflow)"
        )

    if not problemas:
        total = sum(len(r.pendentes) for r in rodadas)
        ok = (
            f"OK: {total} achado(s) nao adjudicado(s) em {len(rodadas)} rodada(s), todos com destino; "
            f"ultima rodada ({final.id}) sem bloqueio."
        )
        return 0, [ok]
    mensagens = list(problemas)
    if faltam:
        cabeca = 'Linhas para colar sob "## Destino dos achados não adjudicados" (troque destino e referencia):'
        mensagens += ["", cabeca]
        mensagens += [_linha_pronta(r, a) for r, a in faltam]
    saidas_txt = " ".join(f'"{s}"' for s in saidas)
    mensagens += ["", f'Depois rode de novo: python scripts/checar_triagem.py --plano "{plano}" --saidas {saidas_txt}']
    return 1, mensagens


def main(argv: list[str] | None = None) -> int:
    # stdout nasce cp1252 no Windows e come acento e travessao dos titulos.
    reconfigurar = getattr(sys.stdout, "reconfigure", None)
    try:
        if reconfigurar is not None:
            reconfigurar(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Confere o destino de cada achado nao adjudicado do verify.")
    ap.add_argument("--plano", type=Path, required=True, help="closure-plan.md com a secao de destino")
    ap.add_argument("--saidas", type=Path, nargs="+", required=True, help="copias <run id>.json ou diretorio com elas")
    args = ap.parse_args(argv)
    codigo, mensagens = checar(args.plano, args.saidas)
    print("\n".join(mensagens))
    return codigo


if __name__ == "__main__":
    sys.exit(main())
