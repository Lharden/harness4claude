"""Fecha o loop de accuracy da classificacao (auditoria 2026-07-28).

Antes destes testes o bloco `aggregates.classify` era prova viva de metrica
morta: `harness-classify.sh` gravava `agreed=None` delegando a um passo
`wf-classify-semantic` que nunca existiu como codigo, e
`recompute_aggregates` so conta tasks com `agreed is not None`. Resultado:
`total_classified: 0` e `avg_classify_accuracy: null` para sempre.

A cobertura aqui trava as duas pontas: `apply_confirmation` preenchendo o meta,
e o agregado deixando de ser nulo depois que uma task confirmada e registrada.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
CONFIRM_PATH = ROOT / "scripts" / "confirm_classification.py"


@pytest.fixture(scope="module")
def cc():
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("confirm_classification", CONFIRM_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["confirm_classification"] = mod
    spec.loader.exec_module(mod)
    return mod


def _state(suggested="L2-feature", pipeline=("discuss", "tdd"), current_step=None):
    return {
        "task_id": "t-20260728-120000",
        "schema_version": 3,
        "classification": suggested,
        "classification_meta": {"suggested": suggested, "final": None,
                                "source": "regex", "confidence": None, "agreed": None},
        "status": "active",
        "pipeline": list(pipeline),
        "current_step": current_step,
        "artifacts_so_far": [],
        "started_at": "2026-07-28T12:00:00+00:00",
    }


class TestPipelinesSource:
    """A arvore de contrato e a fonte, compartilhada com o hook de classify.

    Ate 2026-09-05 era `scripts/pipelines.json`, e o docstring dizia "fonte
    unica" — nao era: havia uma copia byte-identica em `contract/pipelines.json`
    com leitores diferentes, e o caminho que moldava o comportamento nunca
    tocava `contract/`. O arquivo de `scripts/` foi apagado.
    """

    def test_arquivo_existe_e_tem_as_classificacoes_do_contrato(self, cc):
        table = cc.load_pipelines()
        assert set(table) == {
            "L0-question",
            "L1-feature", "L1-bug", "L1-refactor",
            "L1-review", "L1-docs",
            "L2-feature", "L2-bug", "L2-refactor", "L2-architecture",
            "L2-review", "L2-docs",
        }

    def test_bate_com_o_fallback_do_hook(self, cc):
        """Divergir do literal em harness-classify.sh reintroduziria duas verdades."""
        hook = (ROOT / "hooks" / "harness-classify.sh").read_text(encoding="utf-8")
        for cls, fases in cc.load_pipelines().items():
            assert f'"{cls}":' in hook, f"{cls} ausente do fallback do hook"
            for fase in fases:
                assert f'"{fase}"' in hook, f"fase {fase} ausente do fallback do hook"

    def test_diretorio_sem_arquivo_retorna_vazio(self, cc, tmp_path):
        assert cc.load_pipelines(tmp_path) == {}


class TestApplyConfirmation:
    def test_concordancia_marca_agreed_true(self, cc):
        st = cc.apply_confirmation(_state("L2-feature"), "L2-feature", "semantic")
        meta = st["classification_meta"]
        assert meta["agreed"] is True
        assert meta["final"] == "L2-feature"
        assert meta["source"] == "semantic"

    def test_divergencia_marca_agreed_false(self, cc):
        st = cc.apply_confirmation(_state("L2-feature"), "L1-bug", "semantic")
        assert st["classification_meta"]["agreed"] is False

    def test_divergencia_corrige_classificacao_e_pipeline(self, cc):
        st = cc.apply_confirmation(_state("L2-feature"), "L1-bug", "semantic")
        assert st["classification"] == "L1-bug"
        assert st["pipeline"] == ["systematic-debugging", "tdd", "verify"]

    def test_concordancia_preserva_pipeline(self, cc):
        st = cc.apply_confirmation(_state("L2-feature", ("discuss", "tdd")), "L2-feature", "semantic")
        assert st["pipeline"] == ["discuss", "tdd"]

    def test_current_step_invalido_e_zerado(self, cc):
        """Manter um step que nao existe no pipeline novo deixaria a task perdida."""
        st = cc.apply_confirmation(_state("L2-feature", current_step="discuss"), "L1-bug", "semantic")
        assert st["current_step"] is None

    def test_current_step_valido_sobrevive(self, cc):
        st = cc.apply_confirmation(_state("L2-feature", current_step="tdd"), "L1-bug", "semantic")
        assert st["current_step"] == "tdd"

    def test_correcao_para_l0_encerra(self, cc):
        st = cc.apply_confirmation(_state("L2-feature"), "L0-feature", "semantic")
        assert st["status"] == "done"
        assert st["pipeline"] == []

    def test_human_override_registrado_na_source(self, cc):
        st = cc.apply_confirmation(_state("L2-feature"), "L1-bug", "human_override")
        assert st["classification_meta"]["source"] == "human_override"

    def test_meta_ausente_usa_classification_como_suggested(self, cc):
        st = {"task_id": "t-x", "classification": "L1-feature", "status": "active",
              "pipeline": ["tdd"], "current_step": None}
        out = cc.apply_confirmation(st, "L1-feature", "semantic")
        assert out["classification_meta"]["suggested"] == "L1-feature"
        assert out["classification_meta"]["agreed"] is True


class TestAgregadoDeixaDeSerNulo:
    """O ponto do exercicio: a metrica sai de null."""

    def test_accuracy_calculada_apos_confirmacao(self, cc, harness_dir):
        sys.path.insert(0, str(ROOT / "scripts"))
        from migrate_state import recompute_aggregates

        confirmado = cc.apply_confirmation(_state("L2-feature"), "L2-feature", "semantic")
        divergente = cc.apply_confirmation(_state("L2-feature"), "L1-bug", "semantic")

        tasks = [
            {"task_id": "t-1", "classification": "L2-feature", "files_modified": 5,
             "pipeline_completed": True, "classification_meta": confirmado["classification_meta"]},
            {"task_id": "t-2", "classification": "L1-bug", "files_modified": 2,
             "pipeline_completed": True, "classification_meta": divergente["classification_meta"]},
        ]
        agg = recompute_aggregates(tasks)

        assert agg["classify"]["total_classified"] == 2
        assert agg["classify"]["avg_classify_accuracy"] == 0.5

    def test_meta_do_hook_nao_conta(self, cc, harness_dir):
        """Regressao: agreed=None e exatamente o estado que zerava a metrica."""
        sys.path.insert(0, str(ROOT / "scripts"))
        from migrate_state import recompute_aggregates

        agg = recompute_aggregates([
            {"task_id": "t-1", "classification": "L2-feature", "files_modified": 5,
             "pipeline_completed": True,
             "classification_meta": {"suggested": "L2-feature", "final": None,
                                     "source": "regex", "agreed": None}},
        ])
        assert agg["classify"]["total_classified"] == 0
        assert agg["classify"]["avg_classify_accuracy"] is None

    def test_human_override_contabilizado(self, cc, harness_dir):
        sys.path.insert(0, str(ROOT / "scripts"))
        from migrate_state import recompute_aggregates

        st = cc.apply_confirmation(_state("L2-feature"), "L1-bug", "human_override")
        agg = recompute_aggregates([
            {"task_id": "t-1", "classification": "L1-bug", "files_modified": 2,
             "pipeline_completed": True, "classification_meta": st["classification_meta"]},
        ])
        assert agg["classify"]["human_override_count"] == 1


class TestCli:
    def _run(self, harness_dir: Path, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(CONFIRM_PATH), "--harness-dir", str(harness_dir), *args],
            capture_output=True, text=True, check=False,
        )

    def test_grava_no_state(self, harness_dir):
        (harness_dir / "state.json").write_text(json.dumps(_state()), encoding="utf-8")

        res = self._run(harness_dir, "--final", "L2-feature")
        assert res.returncode == 0, res.stderr

        meta = json.loads((harness_dir / "state.json").read_text(encoding="utf-8"))["classification_meta"]
        assert meta["agreed"] is True
        assert meta["final"] == "L2-feature"

    def test_state_idle_recusa(self, harness_dir):
        (harness_dir / "state.json").write_text(
            json.dumps({"task_id": None, "status": "idle"}), encoding="utf-8")
        assert self._run(harness_dir, "--final", "L1-bug").returncode == 2

    def test_expect_task_divergente_recusa(self, harness_dir):
        (harness_dir / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
        res = self._run(harness_dir, "--final", "L1-bug", "--expect-task", "t-outra")
        assert res.returncode == 2

        meta = json.loads((harness_dir / "state.json").read_text(encoding="utf-8"))["classification_meta"]
        assert meta["agreed"] is None, "state nao pode ser tocado quando a task diverge"

    def test_source_invalida_recusa(self, harness_dir):
        (harness_dir / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
        assert self._run(harness_dir, "--final", "L1-bug", "--source", "chute").returncode != 0

    def test_rotulo_fora_da_tabela_recusa_e_lista_as_opcoes(self, harness_dir):
        """`--final L0` era o erro mais provavel, e o menos legivel.

        `L0` parece rotulo valido — o CLAUDE.md fala em "L0" o tempo todo —, mas
        a tabela so tem `L0-question`. Sem esta porta, o valor atravessava ate
        `args.final.split("-", 1)` e voltava como `not enough values to unpack
        (expected 2, got 1)`: um erro de desempacotamento no lugar de uma
        instrucao. Justamente no caminho que existe para corrigir o palpite do
        regex, que acerta ~30%.
        """
        (harness_dir / "state.json").write_text(json.dumps(_state()), encoding="utf-8")

        res = self._run(harness_dir, "--final", "L0")

        assert res.returncode == 2
        assert "unpack" not in res.stderr, "o erro de desempacotamento nao pode vazar ao chamador"
        assert "L0-question" in res.stderr, "a mensagem tem de entregar o rotulo certo"

        meta = json.loads((harness_dir / "state.json").read_text(encoding="utf-8"))["classification_meta"]
        assert meta["agreed"] is None, "rotulo invalido nao pode tocar o state"

    def test_todo_rotulo_da_tabela_e_aceito(self, harness_dir, cc):
        """A porta recusa o que esta fora; ela nao pode recusar o que esta dentro."""
        for rotulo in sorted(cc.load_pipelines()):
            (harness_dir / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
            res = self._run(harness_dir, "--final", rotulo)
            assert res.returncode == 0, f"{rotulo} recusado: {res.stderr}"
