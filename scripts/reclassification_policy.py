from __future__ import annotations

from typing import Any

LOCKING_SOURCES = {"semantic", "human_override"}


def should_promote(state: dict[str, Any], file_count: int) -> bool:
    """Return whether an L0 task should enter the canonical L1 edit pipeline.

    A trava e a FONTE da decisao, nunca a concordancia dela com o regex.

    Ate 2026-09-02 esta funcao exigia `agreed is True` junto da fonte. Como
    `agreed` compara o veredicto final com o palpite do regex, ele vale False
    exatamente quando alguem CORRIGIU a classificacao — e a protecao ficava
    invertida: confirmar "o regex acertou" travava, corrigir "nao, e L0" nao
    travava. Ate `human_override` era revogavel pela contagem de arquivos.

    Observado em producao: uma task corrigida para L0-question voltou sozinha
    para L1-feature/active depois de alguns arquivos editados no mesmo repo, e
    o gate de Stop passou a bloquear por causa dela. Os testes existentes so
    exercitavam `agreed=True`, que e justamente o caso que nao precisa da trava.
    """
    classification = str(state.get("classification") or "")
    metadata = state.get("classification_meta") or {}
    semantically_locked = metadata.get("source") in LOCKING_SOURCES
    return (
        file_count >= 3
        and classification.startswith("L0")
        and state.get("status") != "active"
        and not semantically_locked
    )
