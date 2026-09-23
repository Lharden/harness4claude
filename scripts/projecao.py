"""Gravar JSON de estado de forma atomica, num lugar so.

A projecao `state.json` (e o contador ao lado dela) e escrita por cinco
programas: classify, PostToolUse transacional, reclassify, confirm_classification
e o disjuntor de TTL. Ate 2026-09-23 cada um tinha a sua copia do laco
tmp -> replace, e as copias divergiram:

- o PostToolUse usava tmp de NOME FIXO: dois PostToolUse concorrentes (comandos
  em segundo plano que terminam juntos) escreviam o mesmo tmp;
- o reclassify nem tinha laco: `open(path, 'w')` TRUNCA antes de escrever, e um
  leitor no meio lia arquivo vazio;
- ninguem tinha retentativa. No Windows `os.replace` falha enquanto outro
  processo tem o destino aberto. Medido no ramo ciclo-de-vida-da-task: com o
  escritor antigo, 56% das escritas levantavam com 1 concorrente e 86% com 4.

A funcao NUNCA levanta. Devolve o erro para quem chama decidir; quem decide se ha
task viva e o banco, nao a projecao, entao projecao atrasada e degradacao — e
hook morto por causa dela seria o defeito maior.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

#: Esperas entre tentativas, em segundos. A janela do replace e de milissegundos;
#: seis tentativas somam ~87 ms no pior caso, dentro do orcamento de um hook.
ESPERAS = (0.0, 0.002, 0.005, 0.01, 0.02, 0.05)


def gravar_json_atomico(caminho: str | Path, dados: Any, *, indent: int = 2) -> OSError | None:
    """tmp de nome unico -> replace com retentativa. `None` se gravou."""
    destino = Path(caminho)
    tmp = destino.with_name(f"{destino.name}.{os.getpid()}.{time.monotonic_ns()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(dados, fh, indent=indent, ensure_ascii=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
    except OSError as exc:
        _apagar(tmp)
        return exc
    ultimo: OSError | None = None
    for espera in ESPERAS:
        if espera:
            time.sleep(espera)
        try:
            os.replace(tmp, destino)
            return None
        except OSError as exc:
            ultimo = exc
    _apagar(tmp)
    return ultimo


def _apagar(caminho: Path) -> None:
    try:
        caminho.unlink()
    except OSError:
        pass
