"""conftest.py — resolucao de paths e hermetismo dos testes do Harness v3.

Duas responsabilidades:

1. `HARNESS_PLUGIN_ROOT` — resolucao do plugin (comportamento pre-existente).
2. Hermetismo (task P-1.b): cada classe de teste recebe um `HARNESS_DIR`
   temporario, e a suite falha se algum teste resolver para o diretorio real
   sem declarar `@pytest.mark.touches_real`.

O padrao da fixture foi PROMOVIDO de `tests/test_state_lock.py:38-55`, onde ja
estava validado por 9 testes de concorrencia. Divergir dele seria regressao.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

REAL_HARNESS_DIR = Path.home() / ".claude" / "harness"
BASH_REQUIRED_CLASSES = {
    "test_arsenal_gate.py": {"TestInvocacaoBloqueia", "TestMencaoNaoBloqueia", "TestPassaDireto", "TestFalhaAberta"},
    "test_harness_dir_resolution.py": {
        "TestOverrideRedirectsWrites",
        "TestDefaultFallback",
        "TestEdgeCases",
        "TestInlinePythonLayer",
        "TestOverrideLeavesTrace",
    },
    "test_health_check_smoke.py": {"TestSmokeDetectaSabotagem", "TestSmokeNaoTocaEstadoReal"},
    "test_hook_liveness.py": {"TestHooksGravamHeartbeat"},
    "test_host_contract_resilience.py": {"TestGitGuardFailsLoud", "TestCrlfNaoFragmentaBucket"},
    "test_plugin_root_integrity.py": {"TestPluginRootIntegrity"},
    "test_plugin_root_resolver.py": {"TestPluginRootPersistence"},
    "test_state_lock.py": {
        "TestBasicLifecycle",
        "TestConcurrency",
        "TestStaleHandling",
        "TestWriteRaceProtection",
        "TestReentrancySemantics",
    },
}


def _bash_executable() -> str | None:
    discovered = shutil.which("bash")
    if discovered:
        return discovered
    if os.name == "nt":
        for candidate in (
            Path(r"C:\Program Files\Git\bin\bash.exe"),
            Path(r"C:\Program Files\Git\usr\bin\bash.exe"),
            Path.home() / "scoop" / "apps" / "git" / "current" / "bin" / "bash.exe",
        ):
            if candidate.exists():
                return str(candidate)
    return None


def pytest_configure(config):
    """Resolve o plugin root e registra as marcas da suite."""
    if "HARNESS_PLUGIN_ROOT" not in os.environ:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        os.environ["HARNESS_PLUGIN_ROOT"] = root

    config.addinivalue_line(
        "markers",
        "touches_real: o teste usa o ~/.claude/harness REAL de proposito. "
        "Exige justificativa; aparece nomeado no sumario da execucao.",
    )
    config.addinivalue_line(
        "markers",
        "integration: requer ambiente externo (Ollama, indice real de skills). "
        "Fora do gate hermetico; ver docs/self-reform/claude/TEST_MATRIX.md.",
    )


def pytest_collection_modifyitems(config, items):
    """Bash integration is a separate, explicit platform gate.

    Python/unit coverage remains mandatory on every host. Shell suites are skipped
    with a visible reason when no executable exists, instead of failing 60+ tests
    with the same FileNotFoundError and obscuring product regressions.
    """
    if _bash_executable() is not None:
        return
    marker = pytest.mark.skip(reason="Bash integration runtime is not installed on this host")
    for item in items:
        module = Path(str(item.fspath)).name
        class_name = item.cls.__name__ if item.cls is not None else ""
        if (
            class_name in BASH_REQUIRED_CLASSES.get(module, set())
            and item.name != "test_cobre_todos_os_eventos_registrados"
        ):
            item.add_marker(marker)


@pytest.fixture(scope="class", autouse=True)
def harness_dir(tmp_path_factory, request):
    """HARNESS_DIR isolado por classe de teste.

    `monkeypatch` do pytest e function-scoped, entao o escopo de classe exige
    `pytest.MonkeyPatch()` explicito.

    Testes marcados `touches_real` nao recebem isolamento — e o unico caminho
    para usar o diretorio real, e ele precisa ser declarado no teste.
    """
    if request.node.get_closest_marker("touches_real"):
        yield None
        return

    d = tmp_path_factory.mktemp("harness")
    mp = pytest.MonkeyPatch()
    mp.setenv("HARNESS_DIR", str(d))
    # Evita que o dep-check do session-start dispare "pip install --user" a cada
    # invocacao: com HARNESS_DIR temporario o flag .bootstrap-done nunca existe.
    mp.setenv("HARNESS_SKIP_DEPCHECK", "1")
    yield d
    mp.undo()


def pytest_runtest_setup(item):
    """Assert de seguranca: falha se o teste resolver para o diretorio real.

    Falha por padrao, com escape possivel mas nunca acidental — a marca precisa
    estar escrita no teste.
    """
    if item.get_closest_marker("touches_real"):
        return

    env = os.environ.get("HARNESS_DIR")
    if not env:
        return

    try:
        resolved = Path(env).resolve()
    except OSError:  # path invalido: o proprio teste lidara com isso
        return

    if resolved == REAL_HARNESS_DIR.resolve():
        pytest.fail(
            f"{item.nodeid} resolveu HARNESS_DIR para o diretorio REAL "
            f"({REAL_HARNESS_DIR}).\n"
            f"Use a fixture 'harness_dir', ou declare @pytest.mark.touches_real "
            f"se o teste precisa mesmo do ambiente real.",
            pytrace=False,
        )
