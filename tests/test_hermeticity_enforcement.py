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


# ---------------------------------------------------------------------------
# Vault AI-Brain: a suite nunca resolve o vault real (2026-09-24)
# ---------------------------------------------------------------------------
def _run_pytest_vault(target: str, vault_env: dict[str, str]) -> subprocess.CompletedProcess:
    """Roda pytest com o ambiente de vault que um usuario teria no shell.

    Tira `AI_BRAIN_PATH`/`VAULT_PATH` herdados (o processo pai ja esta isolado)
    e poe os de `vault_env`, que o conftest do filho trata como o vault REAL.
    """
    env = os.environ.copy()
    env["HARNESS_PLUGIN_ROOT"] = str(ROOT)
    env["PYTHONUTF8"] = "1"
    for k in ("HARNESS_DIR", "AI_BRAIN_PATH", "VAULT_PATH"):
        env.pop(k, None)
    env.update(vault_env)
    return subprocess.run(
        [sys.executable, "-m", "pytest", target, "-q", "-p", "no:cacheprovider"],
        capture_output=True, text=True, timeout=300, env=env, cwd=str(ROOT),
    )


class TestVaultNuncaReal:
    """O defeito: `harness-precompact.sh` chama `vault_sync.py` sem `--vault`.

    Com `AI_BRAIN_PATH` vazio e `VAULT_PATH` apontando para o vault do usuario,
    `test_27` espelhava `docs/specs`, `docs/CONTEXT.md` e `.remember` do cwd no
    AI-Brain sincronizado — 29 arquivos na reproducao com sentinela.
    """

    def test_precompact_nao_escreve_no_vault_do_ambiente(self, tmp_path):
        """Given VAULT_PATH=sentinela com AI-Brain e AI_BRAIN_PATH vazio,
        When test_27 roda o PreCompact de verdade,
        Then a sentinela continua vazia."""
        sentinela = tmp_path / "vault-do-usuario"
        (sentinela / "AI-Brain").mkdir(parents=True)
        proc = _run_pytest_vault(
            "tests/test_harness.py::TestPrecompact::test_27_snapshot_written",
            {"VAULT_PATH": str(sentinela)},
        )
        assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-1000:]
        escritos = sorted(p.relative_to(sentinela) for p in sentinela.rglob("*") if p.is_file())
        assert not escritos, f"a suite escreveu no vault do ambiente: {escritos[:10]}"


class TestGuardaDoVault:
    """O guarda de `pytest_runtest_setup` pega quem desfaz o isolamento."""

    _ESCAPE_ENV = '''
import os
os.environ["AI_BRAIN_PATH"] = os.environ["_VAULT_SENTINELA"]

def test_noop():
    assert True
'''

    _ESCAPE_CACHE = '''
import os
from pathlib import Path
import vault_sync
vault_sync.DEFAULT_VAULT = Path(os.environ["_VAULT_SENTINELA"])

def test_noop():
    assert True
'''

    _ESCAPE_MARCADO = '''
import os
import pytest
os.environ["AI_BRAIN_PATH"] = os.environ["_VAULT_SENTINELA"]

@pytest.mark.touches_real
def test_noop():
    assert True
'''

    @pytest.mark.parametrize("nome,fonte", [
        ("vault_env", _ESCAPE_ENV),
        ("vault_cache", _ESCAPE_CACHE),
        ("vault_marcado", _ESCAPE_MARCADO),
    ])
    def test_guarda_derruba_quem_volta_ao_vault_real(self, synthetic_test, tmp_path, nome, fonte):
        """Metade 'pega': teste que desfaz o isolamento falha, com ou sem marca."""
        sentinela = tmp_path / "AI-Brain"
        sentinela.mkdir()
        f = synthetic_test(f"escape_{nome}", fonte)
        proc = _run_pytest_vault(
            str(f), {"AI_BRAIN_PATH": str(sentinela), "_VAULT_SENTINELA": str(sentinela)},
        )
        assert proc.returncode != 0, (
            f"o guarda deveria ter derrubado '{nome}'.\n{proc.stdout[-1500:]}"
        )
        assert "AI-BRAIN REAL" in (proc.stdout + proc.stderr).upper(), proc.stdout[-1500:]

    def test_guarda_deixa_passar_quem_nao_escapa(self, synthetic_test, tmp_path):
        """Metade 'libera': mesmo ambiente, teste sem escape passa."""
        sentinela = tmp_path / "AI-Brain"
        sentinela.mkdir()
        f = synthetic_test("vault_limpo", "def test_noop():\n    assert True\n")
        proc = _run_pytest_vault(str(f), {"AI_BRAIN_PATH": str(sentinela)})
        assert proc.returncode == 0, proc.stdout[-1500:]
