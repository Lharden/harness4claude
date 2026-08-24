"""Saida de terminal em UTF-8, independente do code page do host.

O defeito que originou este modulo (2026-08-24): `python tools/wiki_query.py
"..."` num console Windows devolvia

    UnicodeEncodeError: 'charmap' codec can't encode character '\\u2192'

em vez da resposta. O mesmo em `wiki_index.py` e `wiki_moc.py`. Python escolhe o
encoding do stdout pelo ambiente — cp1252 nesta maquina — e `→`/`←` nao existem
nessa tabela.

**A seta nao esta no codigo destas ferramentas; ela vem do conteudo do vault.**
Por isso a correcao nao pode ser trocar caractere por caractere: qualquer pagina
pode ganhar um simbolo fora do code page amanha, e a proxima ferramenta que a
ecoar quebra do mesmo jeito. O unico ponto que cobre todos os casos e o stream,
uma vez, na entrada do processo.

E a falha era total, nao parcial: o traceback substitui a resposta inteira. Uma
consulta que encontrou a pagina certa, com o score certo, devolvia stack trace —
o pior formato possivel, porque parece defeito da consulta e nao do terminal.

`errors="replace"` fica como segunda barreira para surrogate solto vindo de nome
de arquivo do Windows. Com UTF-8 no encoding ele praticamente nunca dispara; se
disparar, um caractere ilegivel e melhor que perder o relatorio inteiro. Isto
vale para stream de TERMINAL — nenhum dado escrito em disco passa por aqui.
"""

from __future__ import annotations

import sys


def usar_utf8() -> None:
    """Reconfigura stdout/stderr para UTF-8. Silencioso quando nao da.

    Nunca levanta: stdout capturado por pytest, por subprocess ou substituido por
    um dublê de teste pode nao ter `reconfigure`, e uma ferramenta de manutencao
    nao pode morrer por causa de como foi chamada.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            continue
