from __future__ import annotations

import importlib.util
import os
from pathlib import Path

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
SPEC = importlib.util.spec_from_file_location(
    "reclassification_policy",
    ROOT / "scripts" / "reclassification_policy.py",
)
policy = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(policy)


def _state(*, source: str, agreed: bool | None, status: str = "done") -> dict:
    return {
        "classification": "L0-question",
        "status": status,
        "classification_meta": {"source": source, "agreed": agreed},
    }


def test_regex_l0_can_promote_after_three_files_even_when_regex_agreed():
    assert policy.should_promote(_state(source="regex", agreed=True), 3) is True


def test_semantic_or_human_confirmation_locks_classification():
    assert policy.should_promote(_state(source="semantic", agreed=True), 3) is False
    assert policy.should_promote(_state(source="human_override", agreed=True), 3) is False


def test_active_pipeline_or_insufficient_file_count_does_not_promote():
    assert policy.should_promote(_state(source="regex", agreed=True, status="active"), 3) is False
    assert policy.should_promote(_state(source="regex", agreed=True), 2) is False


def test_correcao_semantica_tambem_trava():
    """Corrigir o nivel e o caso PARA O QUAL a trava existe.

    `agreed` compara o veredicto final com o palpite do regex, entao vale False
    justamente quando alguem CORRIGIU a classificacao. Exigir `agreed is True`
    invertia a protecao: confirmar "o regex acertou" travava, e corrigir "nao,
    e L0" nao travava — e a proxima promocao por contagem de arquivos devolvia
    a task para L1-feature.

    Observado em producao em 2026-09-02: uma task corrigida para L0-question
    voltou sozinha para L1-feature/active depois de alguns arquivos editados no
    mesmo repo, e o gate de Stop voltou a bloquear por causa dela. Os testes
    anteriores so exercitavam `agreed=True`, que e o caso que nao precisa da
    trava.
    """
    assert policy.should_promote(_state(source="semantic", agreed=False), 3) is False


def test_correcao_humana_tambem_trava():
    """`human_override` e o usuario dizendo o nivel. Nada de regex o revoga."""
    assert policy.should_promote(_state(source="human_override", agreed=False), 3) is False


def test_confirmacao_sem_veredicto_ainda_trava():
    """`agreed=None` aparece em task promovida antes de confirmacao semantica.

    Se a fonte ja e semantica, quem escreveu foi a skill; a ausencia de
    comparacao com o regex nao autoriza o regex a decidir por cima.
    """
    assert policy.should_promote(_state(source="semantic", agreed=None), 3) is False


def test_regex_continua_promovendo():
    """Contraste: a promocao automatica nao foi desligada em geral."""
    assert policy.should_promote(_state(source="regex", agreed=False), 3) is True
    assert policy.should_promote(_state(source="regex", agreed=None), 3) is True
