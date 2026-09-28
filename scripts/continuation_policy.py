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

#: O que `classify_prompt` devolve como nivel, mais `None` para quem pergunta sem prompt.
NIVEIS_DO_PROMPT = (None, "L0", "L1", "L2")


@dataclass(frozen=True)
class Pergunta:
    resposta: str
    task: dict[str, Any] | None = None
    erro: str | None = None


def entregue(task: dict[str, Any]) -> bool:
    """Fase final, evidencia fresca, nenhum gate humano pendente: so falta o `complete`.

    O banco nao distingue "o modelo esqueceu o `complete`" de "o modelo esta
    esperando a resposta a uma pergunta que fez em texto": os gates formais
    (`HUMAN_GATES`) nao cobrem "posso fazer o merge?", entao `pending_gate` fica
    vazio nos dois casos. Quem decide o que o prompt seguinte faz com a entrega e
    o nivel desse prompt — ver `continua`.
    """
    pipeline = task.get("pipeline") or []
    return (
        bool(pipeline)
        and task.get("phase") == pipeline[-1]
        and bool(task.get("verified"))
        and not task.get("pending_gate")
    )


def continua(task: dict[str, Any], *, nivel_do_prompt: str | None = None) -> bool:
    """Uma task do banco que o proximo prompt deve continuar, e nao substituir.

    A excecao e a entrega sem `complete` (`entregue`). Antes do conserto de R1 a
    evidencia punha `status='verified'`, que o classify nao continuava, e o
    prompt seguinte fechava essa task como `superseded` — era, na pratica, o
    fechamento das tasks que o modelo esquecia de completar. Sem esta regra, o
    conserto do HC-00h transformaria trabalho entregue em CONTINUING por 24 h
    (F2, achado do /code-review). No MEIO do pipeline a evidencia continua sem
    efeito: la ela e so a prova que o portao de Stop pediu.

    A F2 so vale para prompt que abre pipeline. Ate 2026-09-28 ela olhava so a
    task, e o `yes` que respondia a aprovacao pedida em texto fechou a entrega
    como `superseded` (t-20260928-203110092956) — contra a decisao D2 do usuario
    (2026-09-23): task L0 nao encerra pipeline vivo; so troca explicita ou L1/L2
    novo encerra. O que a F2 impede e trabalho NOVO ser engolido pela task
    velha, e trabalho novo e prompt L1/L2; prompt L0 e conversa sobre a entrega.
    Ver `docs/specs/sim-nao-fecha-entrega-diagnostico.md`.

    `nivel_do_prompt` e o nivel que `classify_prompt` deu ao prompt ("L0", "L1",
    "L2"). Quem pergunta sem prompt — o session-start — passa `None` e fica com
    a F2 inteira: sem prompt nao ha resposta a esperar. Valor fora do dominio
    levanta: "L0-question" comparado com "L0" cairia calado no ramo que fecha.
    """
    from transactional_state import ACTIVE_STATUSES

    if nivel_do_prompt not in NIVEIS_DO_PROMPT:
        raise ValueError(f"nivel_do_prompt fora do dominio {NIVEIS_DO_PROMPT}: {nivel_do_prompt!r}")
    pipeline = task.get("pipeline") or []
    if task.get("status") not in ACTIVE_STATUSES or not pipeline:
        return False
    if not entregue(task):
        return True
    return nivel_do_prompt == "L0"


def task_viva(balde: str | Path, *, nivel_do_prompt: str | None = None) -> Pergunta:
    """Pergunta ao `harness.db` do balde. Nunca levanta, nunca cria nem escreve no banco.

    `scope_id` e o proprio balde, como `harness-classify.sh` grava em
    `start_task(scope_id=os.path.dirname(state_file))`. `nivel_do_prompt` so
    muda a resposta para a task entregue (`continua`).
    """
    balde = str(balde)
    if not os.path.isfile(os.path.join(balde, "harness.db")):
        return Pergunta(NENHUMA)
    try:
        from transactional_state import ler_task_corrente

        # Somente leitura: `HarnessDatabase(...)` migraria o schema, e a
        # pergunta roda em todo prompt (achado do /code-review).
        task = ler_task_corrente(balde, balde)
        viva = task is not None and continua(task, nivel_do_prompt=nivel_do_prompt)
    except Exception as exc:  # noqa: BLE001 - a causa e o produto desta resposta
        return Pergunta(DESCONHECIDA, erro=f"{type(exc).__name__}: {exc}")
    if viva:
        return Pergunta(VIVA, task=task)
    return Pergunta(NENHUMA)
