"""Testes de bench_stats — Mann-Whitney e bootstrap (D5).

Valida contra valores conhecidos, nao contra a propria implementacao: um teste
que so confirma o que o codigo faz nao detecta erro de formula.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
_spec = importlib.util.spec_from_file_location(
    "bench_stats", ROOT / "scripts" / "bench_stats.py"
)
bs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bs)


class TestRanking:
    def test_ranks_without_ties(self):
        assert bs._rank_with_ties([10.0, 20.0, 30.0]) == [1.0, 2.0, 3.0]

    def test_ties_get_average_rank(self):
        # dois empates na 2a/3a posicao -> ambos recebem 2.5
        assert bs._rank_with_ties([10.0, 20.0, 20.0, 30.0]) == [1.0, 2.5, 2.5, 4.0]

    def test_all_tied(self):
        assert bs._rank_with_ties([5.0, 5.0, 5.0]) == [2.0, 2.0, 2.0]


class TestMannWhitney:
    def test_u_matches_hand_computation(self):
        """a=[1,2,3], b=[4,5,6]: separacao total -> U=0."""
        res = bs.mann_whitney_u([1.0, 2.0, 3.0], [4.0, 5.0, 6.0])
        assert res["u"] == 0.0

    def test_identical_samples_give_no_evidence(self):
        res = bs.mann_whitney_u([1.0, 2.0, 3.0], [1.0, 2.0, 3.0])
        assert res["p"] == pytest.approx(1.0, abs=0.05)

    def test_u_is_symmetric_in_magnitude(self):
        a, b = [1.0, 2.0, 3.0], [4.0, 5.0, 6.0]
        assert bs.mann_whitney_u(a, b)["u"] == bs.mann_whitney_u(b, a)["u"]

    def test_small_sample_carries_warning(self):
        """Honestidade estatistica: n pequeno nao vira decisao silenciosa."""
        assert "warning" in bs.mann_whitney_u([1.0, 2.0], [3.0, 4.0])

    def test_empty_sample_raises(self):
        with pytest.raises(ValueError):
            bs.mann_whitney_u([], [1.0])

    def test_large_clearly_different_samples_are_significant(self):
        a = [100.0 + i * 0.1 for i in range(20)]
        b = [200.0 + i * 0.1 for i in range(20)]
        assert bs.mann_whitney_u(a, b)["p"] < 0.01


class TestBootstrap:
    def test_ci_brackets_the_median(self):
        sample = [10.0, 11.0, 12.0, 13.0, 14.0]
        lo, hi = bs.bootstrap_median_ci(sample, iterations=2000)
        assert lo <= 12.0 <= hi

    def test_is_reproducible(self):
        sample = [1.0, 5.0, 3.0, 9.0, 7.0]
        assert bs.bootstrap_median_ci(sample, iterations=1000) == bs.bootstrap_median_ci(
            sample, iterations=1000
        )

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            bs.bootstrap_median_ci([])
