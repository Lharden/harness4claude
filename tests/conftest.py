"""conftest.py — resolucao de paths e hermetismo dos testes do Harness v3.

Tres responsabilidades:

1. `HARNESS_PLUGIN_ROOT` — resolucao do plugin (comportamento pre-existente).
2. Hermetismo (task P-1.b): cada classe de teste recebe um `HARNESS_DIR`
   temporario, e a suite falha se algum teste resolver para o diretorio real
   sem declarar `@pytest.mark.touches_real`.
3. Hermetismo do vault: a suite inteira nunca resolve o AI-Brain real. Sem
   excecao por marca — nenhum teste tem motivo para escrever no vault
   sincronizado.

O padrao da fixture foi PROMOVIDO de `tests/test_state_lock.py:38-55`, onde ja
estava validado por 9 testes de concorrencia. Divergir dele seria regressao.

Por que o vault precisa de isolamento proprio (2026-09-24): o PreCompact roda
`vault_sync.py` sem `--vault`, e o destino sai de `AI_BRAIN_PATH`, senao
`VAULT_PATH/AI-Brain`, senao `~/Documents/Obsidian Vault/AI-Brain`. A fixture de
`HARNESS_DIR` nao tocava nenhuma das duas variaveis, e tres testes que chamam o
hook (`test_27`, `test_30`, o caso PreCompact de `test_hook_liveness`)
espelhavam `docs/specs`, `docs/CONTEXT.md` e `.remember` do cwd do pytest para
o vault real. Reproduzido com sentinela: um `test_27` escreveu 29 arquivos.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

REAL_HARNESS_DIR = Path.home() / ".claude" / "harness"
SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"

# Capturado no import do conftest, antes de qualquer isolamento: e o ambiente de
# quem chamou o pytest, e portanto o que aponta para o vault de verdade.
_VAULT_VARS = ("AI_BRAIN_PATH", "VAULT_PATH")
_VAULT_ENV_ORIGINAL = {k: os.environ.get(k) for k in _VAULT_VARS}
_vault_session_mp: pytest.MonkeyPatch | None = None
_vault_session_dir: str | None = None
REAL_VAULTS: frozenset[Path] = frozenset()
BASH_REQUIRED_CLASSES = {
    "test_arsenal_gate.py": {"TestInvocacaoBloqueia", "TestMencaoNaoBloqueia", "TestPassaDireto", "TestFalhaAberta"},
    "test_harness_dir_resolution.py": {
        "TestOverrideRedirectsWrites",
        "TestDefaultFallback",
        "TestEdgeCases",
        "TestInlinePythonLayer",
        "TestOverrideLeavesTrace",
    },
    "test_classify_anuncio_fantasma.py": {"TestAnuncioSoPromete0QueOBancoRecebeu"},
    "test_ciclo_de_vida_da_task.py": {
        "TestClassifyPerguntaAoBanco", "TestSessionStartPerguntaAoBanco", "TestRevisaoReparoGuardaMeta",
    },
    "test_health_check_smoke.py": {"TestSmokeDetectaSabotagem", "TestSmokeNaoTocaEstadoReal"},
    "test_hermeticity_enforcement.py": {"TestVaultNuncaReal"},
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

    _isolar_vault_na_sessao()


def _resolver_vault(env: dict[str, str | None]) -> Path:
    """Destino que o `vault_sync` de producao escolheria sob `env`.

    Mede com a funcao de producao, nao com uma copia da precedencia: uma copia
    concordaria com ela mesma enquanto o script mudasse por baixo.
    """
    import vault_sync

    with pytest.MonkeyPatch.context() as mp:
        for k in _VAULT_VARS:
            valor = env.get(k)
            if valor:
                mp.setenv(k, valor)
            else:
                mp.delenv(k, raising=False)
        return vault_sync._default_vault()


def _isolar_vault_na_sessao() -> None:
    """Aponta a suite para um AI-Brain temporario ANTES da coleta.

    Tem de ser aqui, e nao numa fixture: `vault_sync.DEFAULT_VAULT` e calculado
    no import, e `test_vault_sync.py`/`test_wiki_e2e.py` importam o modulo no
    topo, durante a coleta. O conftest importa o modulo logo depois de trocar o
    ambiente, entao o `DEFAULT_VAULT` em cache ja nasce apontando para o tmp.

    `REAL_VAULTS` guarda os tres candidatos a vault de verdade: o que o ambiente
    original resolve e o fallback de `~`, que vale quando as duas variaveis
    estao vazias.
    """
    global _vault_session_mp, _vault_session_dir, REAL_VAULTS
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))

    _vault_session_dir = tempfile.mkdtemp(prefix="pytest-ai-brain-")
    _vault_session_mp = pytest.MonkeyPatch()
    _vault_session_mp.setenv("AI_BRAIN_PATH", _vault_session_dir)
    _vault_session_mp.delenv("VAULT_PATH", raising=False)

    import vault_sync  # noqa: F401  (fixa DEFAULT_VAULT no tmp)

    candidatos = {_resolver_vault(_VAULT_ENV_ORIGINAL), _resolver_vault({})}
    REAL_VAULTS = frozenset(p.resolve() for p in candidatos)


def pytest_unconfigure(config):
    global _vault_session_mp, _vault_session_dir
    if _vault_session_mp is not None:
        _vault_session_mp.undo()
        _vault_session_mp = None
    if _vault_session_dir is not None:
        shutil.rmtree(_vault_session_dir, ignore_errors=True)
        _vault_session_dir = None


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


@pytest.fixture(scope="class", autouse=True)
def ai_brain_dir(tmp_path_factory):
    """AI-Brain isolado por classe, sem excecao para `touches_real`.

    O isolamento de sessao (`_isolar_vault_na_sessao`) ja tira a suite do vault
    real; este da a cada classe um vault proprio, para que paginas escritas por
    uma classe nao aparecam na seguinte. Quem precisa de outro destino continua
    passando `AI_BRAIN_PATH` explicito, como os testes de `vault_sync`.
    """
    d = tmp_path_factory.mktemp("ai-brain")
    mp = pytest.MonkeyPatch()
    mp.setenv("AI_BRAIN_PATH", str(d))
    mp.delenv("VAULT_PATH", raising=False)
    yield d
    mp.undo()


def _vault_real_resolvido() -> str | None:
    """Descreve o primeiro caminho que ainda aponta para um vault real, ou None."""
    import vault_sync

    atual = vault_sync._default_vault().resolve()
    if atual in REAL_VAULTS:
        return f"o ambiente resolve {atual}"
    cache = Path(vault_sync.DEFAULT_VAULT).resolve()
    if cache in REAL_VAULTS:
        return f"vault_sync.DEFAULT_VAULT (fixado no import) vale {cache}"
    return None


def pytest_runtest_setup(item):
    """Asserts de seguranca: falha se o teste resolver para o estado real.

    Vault primeiro e sem excecao: `touches_real` libera o `HARNESS_DIR`, nunca o
    AI-Brain sincronizado. Depois o `HARNESS_DIR`, que falha por padrao com
    escape possivel mas nunca acidental — a marca precisa estar escrita no teste.
    """
    vazamento = _vault_real_resolvido()
    if vazamento is not None:
        pytest.fail(
            f"{item.nodeid} resolveria o vault AI-Brain REAL: {vazamento}.\n"
            f"Candidatos reais: {sorted(str(p) for p in REAL_VAULTS)}.\n"
            f"O isolamento de sessao do conftest foi removido ou desfeito; "
            f"nenhum teste pode escrever no vault sincronizado.",
            pytrace=False,
        )

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
