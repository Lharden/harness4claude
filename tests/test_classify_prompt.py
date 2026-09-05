"""Testes para scripts/classify_prompt.py — o default do caminho nao reconhecido.

## O que este arquivo trava

O classificador decide o nivel por presenca de palavra. O caminho em que
**nenhuma palavra casa** nao e um caso de borda: medido em 2026-09-04 sobre
1.195 pares reais colhidos de 357 transcripts, ele e o caminho DOMINANTE —
673 pares, 56% do corpus.

Enquanto o default desse caminho foi `L1`, ele acertava 229 de 673 (0.340) e
abria pipeline em vazio nos outros 444. Com `L0` a conta inverte: acerta 436 de
656 (0.665). A troca foi decisao explicita do usuario, com os dois numeros na
mesa, e o custo dela esta declarado no proprio codigo.

O segundo teste e o que impede a troca de custar caro. `DOCS_PATTERNS` e
`REVIEW_PATTERNS` NAO estao dentro de `L1_PATTERNS`, entao "revisa o codigo" e
"documenta o modulo" chegam ao mesmo caminho de "nada casou". Forcar L0 ali sem
olhar o kind tornaria `L1-docs` e `L1-review` inalcancaveis — o defeito exato
que `test_pipeline_phases_are_real.py` existe para impedir, reintroduzido por
uma porta nova. Custo medido de preservar: 17 pares a menos, precisao
praticamente igual (0.665 contra 0.660).
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])


@pytest.fixture(scope="module")
def cp():
    caminho = ROOT / "scripts" / "classify_prompt.py"
    spec = importlib.util.spec_from_file_location("classify_prompt", caminho)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["classify_prompt"] = mod
    spec.loader.exec_module(mod)
    return mod


class TestDefaultDoCaminhoNaoReconhecido:
    """Nada casou = nao ha evidencia de trabalho. O default deixou de ser L1."""

    @pytest.mark.parametrize("prompt", [
        "Sim!",
        "pode seguir",
        "faca",
        "Continue",
        "manda ver",
        "isso mesmo, era isso",
        "quero entender melhor o que aconteceu ali",
    ])
    def test_prompt_sem_marcador_vira_l0_question(self, cp, prompt):
        assert cp.classify_prompt(prompt) == ("L0", "question")

    def test_o_unico_l0_emitido_e_o_que_existe_na_tabela(self, cp):
        """`L0-feature` nao existe em pipelines.json e nao pode ser emitido.

        O hook monta `f"{level}-{kind}"` e busca na tabela. Um rotulo fora dela
        passa direto pelo guard `pipeline_unmapped` (que so vale para L1+) e
        chega ao `confirm_classification`, que o recusa. O caminho novo tem de
        cair no rotulo que ja existe.
        """
        # A tabela mora na arvore de contrato desde 2026-09-05.
        # `scripts/pipelines.json` era uma copia byte-identica com leitores
        # proprios, e a autoridade entre as duas era disputada por comentario.
        tabela = json.loads(
            (ROOT / "contract" / "pipelines.json").read_text(encoding="utf-8"))
        rotulos = set(tabela.get("pipelines", tabela))
        for prompt in ("Sim!", "pode seguir", "faca", "qualquer coisa solta"):
            nivel, kind = cp.classify_prompt(prompt)
            assert f"{nivel}-{kind}" in rotulos, f"{prompt!r} -> {nivel}-{kind}"


class TestATrocaNaoEngoleOResto:
    """Cada caminho que JA decidia continua decidindo igual."""

    @pytest.mark.parametrize("prompt,nivel,kind", [
        ("conserta o bug do login que quebrou", "L1", "bug"),
        ("refatora esse arquivo gigante", "L1", "refactor"),
        ("adiciona um botao de exportar", "L1", "feature"),
    ])
    def test_l1_por_palavra_continua_l1(self, cp, prompt, nivel, kind):
        assert cp.classify_prompt(prompt) == (nivel, kind)

    @pytest.mark.parametrize("prompt", [
        "planeje a arquitetura do servico novo",
        "cria um sistema de autenticacao do zero",
        "monta um pipeline de deploy",
    ])
    def test_l2_continua_l2(self, cp, prompt):
        assert cp.classify_prompt(prompt)[0] == "L2"

    @pytest.mark.parametrize("prompt,kind", [
        ("revisa o codigo que acabei de escrever", "review"),
        ("audita esse modulo", "review"),
        ("documenta o modulo de autenticacao no readme", "docs"),
        ("escreve o changelog da versao", "docs"),
    ])
    def test_docs_e_review_continuam_l1_e_nao_caem_para_l0(self, cp, prompt, kind):
        """A trava que separa esta mudanca de uma que quebra o contrato.

        Sem ela, `L1-docs` e `L1-review` viram pipelines que nenhum prompt
        alcanca — declaradas na tabela e mortas na pratica.
        """
        nivel, achado = cp.classify_prompt(prompt)
        assert achado == kind
        assert nivel == "L1", f"{prompt!r} caiu para {nivel}; docs/review exigem pipeline"

    def test_pergunta_explicita_continua_l0(self, cp):
        assert cp.classify_prompt("o que e um hook de UserPromptSubmit?")[0] == "L0"
