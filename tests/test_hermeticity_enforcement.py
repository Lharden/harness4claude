"""Enforcement do hermetismo — Fase 2 da task P-1.b.

Cobre US-2 (AC-1, AC-2, AC-4, AC-6) e REQ-F5/F7.

Os meta-testes rodam pytest em subprocess sobre arquivos sinteticos, porque a
propriedade sob teste e o comportamento do proprio conftest — nao da algo que
possa ser importado e chamado.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REAL_HARNESS = Path.home() / ".claude" / "harness"
ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])


@pytest.fixture
def synthetic_test(request):
    """Cria um arquivo de teste DENTRO de tests/, e o remove ao final.

    Precisa ser dentro da arvore do projeto: o pytest descobre `conftest.py`
    pelo caminho do arquivo de teste, nao pelo cwd. Um arquivo em /tmp nao
    enxergaria o conftest sob teste — e o meta-teste passaria por engano.
    """
    created: list[Path] = []

    def _make(name: str, source: str) -> Path:
        f = Path(__file__).parent / f"_synthetic_{name}.py"
        f.write_text(source, encoding="utf-8")
        created.append(f)
        return f

    yield _make

    for f in created:
        f.unlink(missing_ok=True)


def _run_pytest(
    target: Path,
    extra: list[str] | None = None,
    harness_dir: str | None = None,
) -> subprocess.CompletedProcess:
    """Roda pytest em subprocess, com o conftest do projeto no caminho.

    `harness_dir` simula o cenario real de vazamento: a variavel vem do
    ambiente EXTERNO. `pytest_runtest_setup` roda antes de qualquer fixture,
    entao e o unico vetor que o assert pode observar — nenhuma fixture ou
    monkeypatch dentro do teste consegue engana-lo.
    """
    env = os.environ.copy()
    env["HARNESS_PLUGIN_ROOT"] = str(ROOT)
    env.pop("HARNESS_DIR", None)
    if harness_dir is not None:
        env["HARNESS_DIR"] = harness_dir
    return subprocess.run(
        [sys.executable, "-m", "pytest", str(target), "-q", "-p", "no:cacheprovider",
         *(extra or [])],
        capture_output=True, text=True, timeout=120, env=env, cwd=str(ROOT),
    )


# ---------------------------------------------------------------------------
# AC-1: a fixture isola por padrao
# ---------------------------------------------------------------------------
def test_fixture_isolates_by_default():
    """Given a suite rodando, Then HARNESS_DIR aponta para tmp, nao para o real."""
    env_dir = os.environ.get("HARNESS_DIR")
    assert env_dir, "a fixture autouse deveria ter definido HARNESS_DIR"
    assert Path(env_dir).resolve() != REAL_HARNESS.resolve(), (
        f"HARNESS_DIR aponta para o diretorio real: {env_dir}"
    )


def test_fixture_dir_exists_and_is_writable():
    d = Path(os.environ["HARNESS_DIR"])
    assert d.is_dir()
    (d / "probe.tmp").write_text("ok", encoding="utf-8")
    assert (d / "probe.tmp").read_text(encoding="utf-8") == "ok"


# ---------------------------------------------------------------------------
# AC-4: classes distintas nao compartilham diretorio
# ---------------------------------------------------------------------------
class TestScopeA:
    def test_writes_marker(self):
        Path(os.environ["HARNESS_DIR"], "marker-a").write_text("a", encoding="utf-8")

    def test_sees_own_marker(self):
        assert Path(os.environ["HARNESS_DIR"], "marker-a").exists()


class TestScopeB:
    def test_does_not_see_other_class_marker(self):
        assert not Path(os.environ["HARNESS_DIR"], "marker-a").exists(), (
            "vazamento entre classes: o diretorio nao e por classe"
        )


# ---------------------------------------------------------------------------
# AC-2 / AC-6: o assert falha por padrao e a marca libera
# ---------------------------------------------------------------------------
class TestSafetyAssert:
    _PLAIN = '''
def test_noop():
    assert True
'''

    _MARKED = '''
import pytest

@pytest.mark.touches_real
def test_deliberately_uses_real():
    assert True
'''

    def test_unmarked_escape_fails_the_suite(self, synthetic_test):
        """AC-2: HARNESS_DIR externo apontando para o real derruba a suite."""
        f = synthetic_test("escape_unmarked", self._PLAIN)
        proc = _run_pytest(f, harness_dir=str(REAL_HARNESS))
        assert proc.returncode != 0, (
            "com HARNESS_DIR=real no ambiente, o teste deveria ter falhado.\n"
            f"stdout:\n{proc.stdout[-1500:]}"
        )
        combined = (proc.stdout + proc.stderr).upper()
        assert "REAL" in combined, (
            f"a mensagem de falha deveria nomear o problema.\n{proc.stdout[-1500:]}"
        )

    def test_marked_test_is_allowed(self, synthetic_test):
        """AC-6: com @pytest.mark.touches_real, o mesmo cenario e permitido."""
        f = synthetic_test("escape_marked", self._MARKED)
        proc = _run_pytest(f, harness_dir=str(REAL_HARNESS))
        assert proc.returncode == 0, (
            "teste marcado touches_real deveria ser permitido.\n"
            f"stdout:\n{proc.stdout[-1500:]}"
        )


# ---------------------------------------------------------------------------
# REQ-F5: as marcas estao registradas (sem warning de marca desconhecida)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("marker", ["touches_real", "integration"])
def test_markers_are_registered(marker, synthetic_test):
    f = synthetic_test(
        f"marker_{marker}",
        f"import pytest\n\n@pytest.mark.{marker}\ndef test_noop():\n    assert True\n",
    )
    proc = _run_pytest(f, extra=["-W", "error::pytest.PytestUnknownMarkWarning"])
    assert proc.returncode == 0, (
        f"marca '{marker}' nao registrada em pytest_configure.\n{proc.stdout[-1200:]}"
    )
