#!/usr/bin/env python
"""branch_j_inicio.py — SessionStart da camada J em modo sombra (spec `docs/specs/branch-camada-j-ligacao-spec.md`, AC-1.0).

Marca a sessão nova (`source` startup ou clear) como parte da amostra, criando `<sid>.nova` na pasta do spool, se a
flag está ligada e a janela aberta. Não escreve nada em stdout (nada vai ao contexto do modelo), não faz rede, não abre
processo e sai 0 sempre: um hook de SessionStart que falha não pode atrapalhar a sessão.
"""
from __future__ import annotations

import json
import os
import sys


def main() -> int:
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace"))
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
        import branch_j_spool

        branch_j_spool.iniciar_sessao(payload)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
