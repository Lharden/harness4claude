"""Toda fase declarada num pipeline tem que existir de verdade.

A SKILL.md do harness-workflow afirma "Zero skills fantasma: cada fase mapeia a
um mecanismo real". Entre 2026-08-28 e 2026-09-02 a afirmação foi **falsa**, e
o modo como ela ficou falsa é o que este arquivo existe para impedir.

O commit `d7fa6d8` trouxe as pipelines `L1-review`, `L1-docs`, `L2-review` e
`L2-docs` por paridade de contrato com o harness4codex. Elas declararam três
fases — `source-selection`, `documentation`, `code-review` — que não existiam
como skill, workflow, comando ou plugin em lugar nenhum. A tabela "Mapa fase →
mecanismo", que mapeia todas as outras, simplesmente pulava as três. E a linha
que negava a existência de fantasmas ficava três linhas abaixo delas.

Nada acusou por cinco dias, por dois motivos que se somam:

1. O `contract_adapter.py` verifica conformidade olhando se o **arquivo** de
   teste existe (`:96`), nunca se a fase tem implementação.
2. As quatro pipelines eram **inalcançáveis**: `classify_prompt.py` só emitia
   `{bug, refactor, architecture, feature}`, nunca `review` nem `docs`. Fase
   fantasma numa pipeline que ninguém alcança não produz erro — produz silêncio,
   que é a assinatura recorrente das falhas deste harness.

Os testes abaixo cobrem as duas metades: a fase existe, e alguém consegue
chegar nela.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
PIPELINES = ROOT / "contract" / "pipelines.json"
WORKFLOW_SKILL = ROOT / "skills" / "harness-workflow" / "SKILL.md"

#: Fases que NÃO são skill local por design, com o mecanismo que as atende.
#: Cada entrada é uma decisão registrada, não uma isenção de conveniência: a
#: lista existe para que adicionar uma fase sem implementação exija escrever
#: aqui por que ela não precisa de uma.
FORA_DO_REPO = {
    "brainstorming": "superpowers:brainstorming",
    "tdd": "superpowers:test-driven-development",
    "verify": "superpowers:verification-before-completion",
    "systematic-debugging": "superpowers:systematic-debugging",
    "code-review": "/code-review (comando built-in do Claude Code)",
    # Gates humanos: são pontos de decisão, não mecanismos executáveis.
    "approve-spec": "gate humano (AskUserQuestion)",
    "approve-plan": "gate humano (AskUserQuestion)",
}


def _passos() -> list[str]:
    dados = json.loads(PIPELINES.read_text(encoding="utf-8"))
    dados = dados.get("pipelines") or dados
    return sorted({p for v in dados.values() if isinstance(v, list) for p in v})


def _skills_locais() -> set[str]:
    return {d.name for d in (ROOT / "skills").iterdir() if d.is_dir()}


def _workflows() -> set[str]:
    return {f.stem[3:] for f in (ROOT / "scripts" / "workflows").glob("wf-*.js")}


class TestTodaFaseTemMecanismo:
    def test_nenhuma_fase_e_fantasma(self):
        locais, wfs = _skills_locais(), _workflows()
        fantasmas = [
            p for p in _passos()
            if p not in locais and p not in wfs and p not in FORA_DO_REPO
        ]
        assert not fantasmas, (
            f"fases declaradas sem implementacao: {fantasmas}. "
            "Ou escreva o mecanismo, ou registre em FORA_DO_REPO com o motivo, "
            "ou tire a fase da pipeline."
        )

    def test_o_mapa_da_skill_cobre_todas(self):
        """A tabela e a documentacao do contrato. Fase ausente dela e fase invisivel."""
        mapa = WORKFLOW_SKILL.read_text(encoding="utf-8")
        i = mapa.index("### Mapa fase → mecanismo")
        tabela = mapa[i:i + 6000]
        faltando = [p for p in _passos() if f"`{p}`" not in tabela]
        assert not faltando, f"fases sem linha no mapa fase->mecanismo: {faltando}"

    def test_isencoes_sao_justificadas(self):
        for fase, motivo in FORA_DO_REPO.items():
            assert len(motivo) > 8, f"{fase} isento sem motivo util"

    def test_isencao_nao_esconde_skill_que_existe(self):
        """Isencao para fase que TEM skill local seria mentira silenciosa."""
        locais = _skills_locais()
        conflito = [f for f in FORA_DO_REPO if f in locais]
        assert not conflito, f"isentas mas existem localmente: {conflito}"


class TestTodaPipelineEAlcancavel:
    """Fase real numa pipeline inalcançável ainda é fase morta.

    O classificador determinístico precisa conseguir emitir cada `kind` que o
    schema declara — senão a pipeline existe no papel e nunca roda, que foi
    exatamente o estado de review/docs entre `d7fa6d8` e 2026-09-02.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def classificador(cls):
        caminho = ROOT / "scripts" / "classify_prompt.py"
        spec = importlib.util.spec_from_file_location("classify_prompt", caminho)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        sys.modules["classify_prompt"] = mod
        spec.loader.exec_module(mod)
        return mod

    def test_todo_kind_de_pipeline_e_emitivel(self, classificador):
        dados = json.loads(PIPELINES.read_text(encoding="utf-8"))
        dados = dados.get("pipelines") or dados
        # Pipeline vazia nao precisa ser alcancavel: nao ha o que rodar nela.
        # `L0-question: []` e o sentinela do caminho L0, onde o hook responde
        # direto sem pipeline. Exigir alcance ali seria exigir que o
        # classificador emitisse um kind so para nao executar nada.
        kinds = {
            k.split("-", 1)[1]
            for k, v in dados.items()
            if "-" in k and isinstance(v, list) and v
        }
        fonte = (ROOT / "scripts" / "classify_prompt.py").read_text(encoding="utf-8")
        emitiveis = set(re.findall(r'kind = "([a-z-]+)"', fonte))
        mortos = kinds - emitiveis
        assert not mortos, (
            f"pipelines declaradas para kinds que o classificador nunca emite: "
            f"{sorted(mortos)}. A pipeline existe e nenhum prompt chega nela."
        )

    @pytest.mark.parametrize("prompt,esperado", [
        ("documenta o modulo de autenticacao no readme", "docs"),
        ("atualiza a documentacao do projeto", "docs"),
        ("escreve o changelog da versao", "docs"),
        ("revisa o codigo que acabei de escrever", "review"),
        ("faz um code review dessas mudancas", "review"),
        ("audita esse modulo", "review"),
    ])
    def test_prompts_reais_alcancam_docs_e_review(self, classificador, prompt, esperado):
        assert classificador.classify_prompt(prompt)[1] == esperado

    @pytest.mark.parametrize("prompt,esperado", [
        ("conserta o bug do login que quebrou", "bug"),
        ("refatora esse arquivo gigante", "refactor"),
        ("adiciona um botao de exportar", "feature"),
    ])
    def test_os_kinds_antigos_nao_foram_capturados(self, classificador, prompt, esperado):
        """`docs` e `review` vem primeiro na ordem de decisao; nao podem roubar o resto."""
        assert classificador.classify_prompt(prompt)[1] == esperado
