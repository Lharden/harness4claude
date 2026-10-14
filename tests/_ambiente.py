# DERIVADO - nao edite este arquivo.
#
# A fonte e `master-harness/tests/_ambiente.py`, e ela e a unica
# dona do nucleo da recusa fora do ambiente do repositorio. O repositorio do
# host `claude` nao importa o master-harness: o que muda entre os repositorios
# (a pasta do ambiente, os caminhos isentos) mora em `tests/conftest.py`.
#
# Para mudar o que esta aqui: edite a fonte e rode `mh identidade semear`.
# Para saber se derivou: `mh identidade check` (sai 1 se divergir).
#
# ---- fim do cabecalho vendorizado; daqui para baixo e copia exata ----
"""Nucleo da recusa: a sessao do `pytest` so roda no ambiente declarado do repositorio (Onda 3, lote 3-B).

Este arquivo e o MESMO nos tres repositorios (master-harness, harness4claude, harness4codex): o master-harness e o
dono, e as copias dos hosts sao geradas por `mh identidade semear --artefato ambiente`. O que muda entre os
repositorios (a pasta do ambiente e os caminhos isentos) mora no `tests/conftest.py` de cada um, que nao e derivado.
Editar este arquivo num host da `divergente` no `mh identidade check`.

Defeito (D-026): o `python -m pytest` do Python do sistema carregava o site do usuario, e um pacote instalado ali com
`pip install --user` declarava um plugin `pytest11` que quebrava a partida. O teste verde num ambiente que a producao
nao tem nao prova nada (LL-02). O `mh testar`, o `mh pronto` e o `mh integrar` ja escolhem o `.venv` do repositorio
(lote 3-A); esta recusa fecha o caminho de quem chama o `pytest` a mao com outro interpretador.

Regras, na ordem (design `docs/specs/ambiente-isolado-design.md`, componente 2):

1. `MH_PROVA_VENV` definida: aceita so se `sys.prefix` resolve para ela e a pasta temporaria desta sessao
   (`tempfile.gettempdir()`) mora dentro da pasta-mae dela. A prova dinamica monta um venv por base em
   `<execucao>/<host>/venv` e redireciona o TEMP de cada rodada para `<execucao>/<host>/<rodada>/tmp`: o venv e a
   rodada sao irmaos na pasta do host, e o venv nunca fica dentro do TEMP da rodada (medido no lote 3-B, item 3.3).
2. Todos os argumentos de caminho comecam por um prefixo de `isentos` (no MH, `tests/s1`, que roda no `.venv-s1`):
   aceita.
3. `sys.prefix` diferente de `<raiz>/<pasta>`: recusa, nomeando o interpretador usado, a versao e o comando certo.
4. `PYTHONPATH` nao vazio: recusa (o isolamento voltaria por ele).

So biblioteca padrao: o `pytest` entra apenas em `conferir`, que o conftest chama dentro do `pytest_configure`.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Iterable, Mapping

#: A variavel com que a prova dinamica declara o venv da rodada.
PROVA_VENV = "MH_PROVA_VENV"


def _normal(caminho: str | os.PathLike[str]) -> str:
    return os.path.normcase(os.path.realpath(os.fspath(caminho)))


def _dentro(filho: str, pai: str) -> bool:
    try:
        return os.path.commonpath([filho, pai]) == pai
    except ValueError:  # unidades diferentes no Windows
        return False


def python_do_ambiente(raiz: Path, pasta: str = ".venv") -> Path:
    """O interpretador de `<raiz>/<pasta>`: `Scripts/python.exe` no Windows, `bin/python` fora dele."""
    base = Path(raiz) / pasta
    return base / "Scripts" / "python.exe" if os.name == "nt" else base / "bin" / "python"


def _caminhos(args: Iterable[str], invocacao: Path, raiz: Path) -> list[str] | None:
    """Os argumentos de caminho relativos a `raiz`, em barras normais; None se algum sai da raiz."""
    rels = []
    for arg in args:
        caminho = str(arg).split("::", 1)[0]
        if not caminho or caminho.startswith("-"):
            continue
        absoluto = Path(caminho) if Path(caminho).is_absolute() else Path(invocacao) / caminho
        alvo, base = _normal(absoluto), _normal(raiz)
        if not _dentro(alvo, base):
            return None
        rels.append(os.path.relpath(alvo, base).replace(os.sep, "/"))
    return rels


def _isento(rel: str, isentos: Iterable[str]) -> bool:
    return any(rel == p or rel.startswith(p.rstrip("/") + "/") for p in isentos)


def motivo_da_recusa(*, raiz: Path, pasta: str = ".venv", isentos: Iterable[str] = (), args: Iterable[str] = (),
                     invocacao: Path | None = None, prefixo: str | None = None, executavel: str | None = None,
                     versao: str | None = None, env: Mapping[str, str] | None = None,
                     temporaria: str | None = None, comando: Iterable[str] = ()) -> str | None:
    """None se a sessao pode rodar; senao, a mensagem da recusa. Funcao pura: tudo o que le chega por argumento, com o
    processo atual como padrao."""
    env = os.environ if env is None else env
    prefixo = sys.prefix if prefixo is None else prefixo
    executavel = sys.executable if executavel is None else executavel
    versao = ".".join(map(str, sys.version_info[:3])) if versao is None else versao
    invocacao = Path.cwd() if invocacao is None else Path(invocacao)
    isentos = tuple(isentos)
    certo = python_do_ambiente(raiz, pasta)
    sugestao = " ".join([str(certo), "-m", "pytest", *comando])

    prova = env.get(PROVA_VENV, "")
    if prova:
        temporaria = tempfile.gettempdir() if temporaria is None else temporaria
        if _normal(prefixo) == _normal(prova) and _dentro(_normal(temporaria), os.path.dirname(_normal(prova))):
            return None
        return (f"{PROVA_VENV}={prova} nao e o venv desta sessao ({prefixo}) ou a pasta temporaria ({temporaria}) "
                f"nao mora ao lado dele: a prova dinamica roda no venv que ela mesma monta. Nada rodou.")

    rels = _caminhos(args, invocacao, raiz)
    if isentos and rels and all(_isento(r, isentos) for r in rels):
        return None

    if _normal(prefixo) != _normal(Path(raiz) / pasta):
        return (f"este pytest roda em {executavel} (Python {versao}), fora do ambiente do repositorio "
                f"({Path(raiz) / pasta}). Teste verde num ambiente que a producao nao tem nao prova nada (D-026). "
                f"Rode: {sugestao}  (ou `mh testar`, que cria o ambiente do uv.lock). Nada rodou.")
    if env.get("PYTHONPATH", "").strip():
        return (f"PYTHONPATH={env['PYTHONPATH']} poe no caminho de importacao o que o ambiente do repositorio nao "
                f"declara. Rode sem ele: {sugestao}. Nada rodou.")
    return None


def conferir(config, raiz: Path, pasta: str = ".venv", isentos: tuple[str, ...] = ()) -> None:
    """Chamada no `pytest_configure` de cada `tests/conftest.py`: `UsageError` com o comando certo, ou nada."""
    import pytest

    motivo = motivo_da_recusa(raiz=Path(raiz), pasta=pasta, isentos=isentos, args=config.args,
                              invocacao=config.invocation_params.dir, comando=config.invocation_params.args)
    if motivo:
        raise pytest.UsageError(motivo)
