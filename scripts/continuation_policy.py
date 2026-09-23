"""Ha task viva neste balde? Uma pergunta, uma resposta, um lugar.

Ate 2026-09-23 "task viva" estava definida em cinco lugares, e os dois que
importavam discordavam:

- o banco (`ACTIVE_STATUSES`) contava 'verified' como viva e FECHAVA a task
  quando um prompt novo abria outra;
- o classify (`CONTINUABLE_STATUSES`, aqui) nao contava 'verified' e NAO a
  continuava — lendo, alem disso, a projecao `state.json` em vez do banco.

Os dois incidentes do ramo `ciclo-de-vida-da-task` sairam dessa costura:

- HC-00h: task L2 na fase 2 de 11, com suite verde, virou `superseded` com o
  prompt seguinte.
- Incidente 2: task L2 ativa virou `abandoned` quando uma mensagem entre sessoes
  chegou. A leitura do `state.json` falha no Windows enquanto o PostToolUse o
  regrava — medido 5,5% (1 escritor) a 12,3% (4) — e o `except` tratava "nao
  consegui ler" como "nao ha pipeline".

Agora a resposta vem do banco, que e a autoridade, e tem TRES valores. "Nao sei"
nao se confunde com "nao ha": quem decide algo destrutivo sobre `DESCONHECIDA`
tem de recusar, e a causa vai junto. A mesma leitura contra o banco (WAL,
busy_timeout=5000) deu 0 falhas em 4 137 sob 4 escritores — o terceiro valor e
raro, mas quando acontece e dito.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_AQUI = os.path.dirname(os.path.abspath(__file__))
if _AQUI not in sys.path:
    sys.path.insert(0, _AQUI)

# `transactional_state` e importado DENTRO das funcoes, nunca aqui. O classify
# importa este modulo fora de qualquer `try`; se o import do banco estivesse no
# topo, um `transactional_state` quebrado derrubaria o hook com exit 1 e stdout
# vazio — o caso que `test_classify_anuncio_fantasma` existe para pegar, e que
# pegou. Banco quebrado e resposta (DESCONHECIDA), nao queda.

VIVA = "viva"
NENHUMA = "nenhuma"
DESCONHECIDA = "desconhecida"


@dataclass(frozen=True)
class Pergunta:
    resposta: str
    task: dict[str, Any] | None = None
    erro: str | None = None


def continua(task: dict[str, Any]) -> bool:
    """Uma task do banco que o proximo prompt deve continuar, e nao substituir."""
    from transactional_state import ACTIVE_STATUSES

    return task.get("status") in ACTIVE_STATUSES and bool(task.get("pipeline"))


def task_viva(balde: str | Path) -> Pergunta:
    """Pergunta ao `harness.db` do balde. Nunca levanta; nunca cria o banco.

    `scope_id` e o proprio balde, como `harness-classify.sh` grava em
    `start_task(scope_id=os.path.dirname(state_file))`.
    """
    balde = str(balde)
    if not os.path.isfile(os.path.join(balde, "harness.db")):
        return Pergunta(NENHUMA)
    try:
        from transactional_state import HarnessDatabase

        task = HarnessDatabase(balde).current_task(balde)
        viva = task is not None and continua(task)
    except Exception as exc:  # noqa: BLE001 - a causa e o produto desta resposta
        return Pergunta(DESCONHECIDA, erro=f"{type(exc).__name__}: {exc}")
    if viva:
        return Pergunta(VIVA, task=task)
    return Pergunta(NENHUMA)
