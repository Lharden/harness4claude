"""Resolucao de HARNESS_DIR — Fase 1 da task P-1.b.

Cobre US-1 (AC-1 a AC-5) da spec p1b-testes-hermeticos-spec.md.

Estrategia de teste: nenhum teste aqui escreve no diretorio real. O caso
"default sem HARNESS_DIR" e exercitado sobrescrevendo HOME para um tmp, o que
verifica a regra de fallback sem tocar em producao.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
HOOKS = ROOT / "hooks"
SCRIPTS = ROOT / "scripts"

BASH = "bash"
if sys.platform == "win32":
    for candidate in (
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
        "bash",
    ):
        if Path(candidate).exists() or candidate == "bash":
            BASH = candidate
            break


def _env(**extra: str) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    # Com HARNESS_DIR temporario o flag .bootstrap-done nunca existe, e o
    # dep-check do session-start dispararia "pip install --user" a cada
    # invocacao — lento, dependente de rede e com efeito colateral fora do
    # diretorio isolado (chegou a criar pip/cache/ no repo).
    env["HARNESS_SKIP_DEPCHECK"] = "1"
    # Remove heranca do ambiente do pytest para nao mascarar o caso "ausente"
    env.pop("HARNESS_DIR", None)
    env.update(extra)
    return env


def _run(argv: list[str], env: dict[str, str], stdin: str = "") -> subprocess.CompletedProcess:
    return subprocess.run(
        argv, input=stdin, capture_output=True, text=True, timeout=30, env=env
    )


# ---------------------------------------------------------------------------
# AC-2: HARNESS_DIR definida redireciona a escrita
# ---------------------------------------------------------------------------
class TestOverrideRedirectsWrites:
    """Given HARNESS_DIR definida, When o hook roda, Then escreve la."""

    def test_session_start_bootstraps_into_override(self, tmp_path):
        target = tmp_path / "h"
        env = _env(HARNESS_DIR=str(target))
        _run([BASH, str(HOOKS / "harness-session-start.sh")], env, stdin="{}")
        assert (target / "state.json").exists(), (
            "harness-session-start.sh ignorou HARNESS_DIR e nao criou o state no destino"
        )

    def test_classify_writes_state_into_override(self, tmp_path):
        target = tmp_path / "h"
        target.mkdir()
        env = _env(HARNESS_DIR=str(target))
        payload = json.dumps({"prompt": "corrija o bug de autenticacao no login"})
        _run([BASH, str(HOOKS / "harness-classify.sh")], env, stdin=payload)
        assert (target / "state.json").exists(), (
            "harness-classify.sh ignorou HARNESS_DIR ao escrever state.json"
        )

    def test_init_state_writes_into_override(self, tmp_path):
        target = tmp_path / "h"
        env = _env(HARNESS_DIR=str(target))
        _run([BASH, str(SCRIPTS / "init-state.sh")], env)
        assert (target / "state.json").exists()
        assert (target / "signals.json").exists()


# ---------------------------------------------------------------------------
# AC-1: sem HARNESS_DIR, o default e $HOME/.claude/harness
# ---------------------------------------------------------------------------
class TestDefaultFallback:
    """Given HARNESS_DIR ausente, Then resolve para $HOME/.claude/harness.

    Verificado com HOME sobrescrito — nunca tocando o diretorio real.
    """

    def test_session_start_uses_home_default(self, tmp_path):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        env = _env(HOME=str(fake_home), USERPROFILE=str(fake_home))
        _run([BASH, str(HOOKS / "harness-session-start.sh")], env, stdin="{}")
        assert (fake_home / ".claude" / "harness" / "state.json").exists(), (
            "fallback para $HOME/.claude/harness nao ocorreu"
        )


# ---------------------------------------------------------------------------
# Edge cases da US-1
# ---------------------------------------------------------------------------
class TestEdgeCases:
    def test_empty_harness_dir_treated_as_absent(self, tmp_path):
        """Given HARNESS_DIR="", Then trata como ausente e usa o default."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        env = _env(HOME=str(fake_home), USERPROFILE=str(fake_home), HARNESS_DIR="")
        _run([BASH, str(HOOKS / "harness-session-start.sh")], env, stdin="{}")
        assert (fake_home / ".claude" / "harness" / "state.json").exists(), (
            "HARNESS_DIR vazia deveria ser tratada como ausente"
        )

    def test_nonexistent_dir_is_created(self, tmp_path):
        """AC-3: diretorio inexistente e criado, hook conclui exit 0."""
        target = tmp_path / "nao" / "existe" / "ainda"
        env = _env(HARNESS_DIR=str(target))
        proc = _run([BASH, str(HOOKS / "harness-session-start.sh")], env, stdin="{}")
        assert proc.returncode == 0, f"exit {proc.returncode}: {proc.stderr[:300]}"
        assert target.exists()

    def test_path_with_space(self, tmp_path):
        """AC-4: path com espaco funciona (Windows/Git Bash)."""
        target = tmp_path / "com espaco" / "h"
        env = _env(HARNESS_DIR=str(target))
        proc = _run([BASH, str(HOOKS / "harness-session-start.sh")], env, stdin="{}")
        assert proc.returncode == 0, f"exit {proc.returncode}: {proc.stderr[:300]}"
        assert (target / "state.json").exists()


# ---------------------------------------------------------------------------
# Camada 2: python inline dentro de heredoc (REQ-F2)
# ---------------------------------------------------------------------------
class TestInlinePythonLayer:
    """O debug-classify.log e composto DENTRO do python inline, com
    expanduser('~') — imune a mudancas no bash acima. REQ-F2 / INV-4."""

    def test_debug_log_honors_override_on_malformed_input(self, tmp_path):
        target = tmp_path / "h"
        target.mkdir()
        fake_home = tmp_path / "home"
        (fake_home / ".claude" / "harness").mkdir(parents=True)
        env = _env(
            HARNESS_DIR=str(target), HOME=str(fake_home), USERPROFILE=str(fake_home)
        )
        # JSON malformado forca o caminho de erro que escreve o log de debug
        _run([BASH, str(HOOKS / "harness-classify.sh")], env, stdin="{nao é json}")

        leaked = fake_home / ".claude" / "harness" / "debug-classify.log"
        assert not leaked.exists(), (
            "debug-classify.log foi escrito via expanduser('~'), ignorando HARNESS_DIR"
        )


# ---------------------------------------------------------------------------
# Camada 3: scripts python com --harness-dir (REQ-F3, REQ-F13)
# ---------------------------------------------------------------------------
class TestPythonModuleLayer:
    def test_record_signal_default_follows_env(self, tmp_path):
        """REQ-F3: sem --harness-dir, o default vem de HARNESS_DIR."""
        target = tmp_path / "h"
        target.mkdir()
        (target / "signals.json").write_text(
            json.dumps({"version": 3, "tasks": [], "aggregates": {}}), encoding="utf-8"
        )
        (target / "state.json").write_text(
            json.dumps({"task_id": "t-20260724-000001", "classification": "L1-bug",
                        "status": "active", "pipeline": ["tdd"]}), encoding="utf-8"
        )
        env = _env(HARNESS_DIR=str(target))
        proc = _run(
            [sys.executable, str(SCRIPTS / "record_signal.py"), "--completed"], env
        )
        assert proc.returncode == 0, f"exit {proc.returncode}: {proc.stderr[:300]}"
        data = json.loads((target / "signals.json").read_text(encoding="utf-8"))
        assert len(data["tasks"]) == 1, "record_signal nao usou HARNESS_DIR como default"

    def test_flag_overrides_env_with_warning(self, tmp_path):
        """REQ-F13: --harness-dir vence, mas avisa no stderr quando diverge."""
        env_dir = tmp_path / "pelo-env"
        flag_dir = tmp_path / "pela-flag"
        for d in (env_dir, flag_dir):
            d.mkdir()
            (d / "signals.json").write_text(
                json.dumps({"version": 3, "tasks": [], "aggregates": {}}), encoding="utf-8"
            )
            (d / "state.json").write_text(
                json.dumps({"task_id": "t-20260724-000002", "classification": "L1-bug",
                            "status": "active", "pipeline": ["tdd"]}), encoding="utf-8"
            )
        env = _env(HARNESS_DIR=str(env_dir))
        proc = _run(
            [sys.executable, str(SCRIPTS / "record_signal.py"),
             "--harness-dir", str(flag_dir), "--completed"], env
        )
        assert proc.returncode == 0, f"exit {proc.returncode}: {proc.stderr[:300]}"

        flag_data = json.loads((flag_dir / "signals.json").read_text(encoding="utf-8"))
        env_data = json.loads((env_dir / "signals.json").read_text(encoding="utf-8"))
        assert len(flag_data["tasks"]) == 1, "a flag deveria ter precedencia"
        assert len(env_data["tasks"]) == 0, "a env nao deveria ter sido usada"
        assert "HARNESS_DIR" in proc.stderr, (
            "divergencia entre flag e env deve gerar aviso no stderr"
        )


# ---------------------------------------------------------------------------
# REQ-F12: override divergente deixa rastro (mitigacao do risco R10)
# ---------------------------------------------------------------------------
class TestOverrideLeavesTrace:
    def test_classify_logs_override(self, tmp_path):
        target = tmp_path / "h"
        target.mkdir()
        env = _env(HARNESS_DIR=str(target))
        payload = json.dumps({"prompt": "corrija o bug de autenticacao no login"})
        _run([BASH, str(HOOKS / "harness-classify.sh")], env, stdin=payload)
        log = target / "debug-classify.log"
        assert log.exists(), "override ativo deveria deixar rastro no debug log"
        assert "override" in log.read_text(encoding="utf-8").lower()


# ---------------------------------------------------------------------------
# INV-4: nenhum expanduser compondo caminho de estado
# ---------------------------------------------------------------------------
def test_inv4_no_unguarded_expanduser_state_paths():
    """Criterio de conclusao da Fase 1, nao sugestao de revisao.

    INV-4 nao proibe expanduser: o fallback legitimo PRECISA compor
    ~/.claude/harness quando a variavel esta ausente. O que a regra proibe e
    compor esse caminho *sem consultar HARNESS_DIR primeiro*.

    A deteccao e por linha: uma linha que compoe o caminho de estado deve, ela
    mesma, conter a consulta a variavel (o padrao `os.environ.get("HARNESS_DIR")
    or ...`). Linha que compoe sem consultar e violacao.
    """
    offenders: list[str] = []
    for d in (HOOKS, SCRIPTS):
        for f in list(d.glob("*.sh")) + list(d.glob("*.py")):
            for n, line in enumerate(
                f.read_text(encoding="utf-8", errors="replace").splitlines(), 1
            ):
                composes_state_path = "expanduser" in line and "harness" in line
                consults_env = "HARNESS_DIR" in line
                if composes_state_path and not consults_env:
                    offenders.append(f"{f.name}:{n}  |  {line.strip()[:70]}")
    assert not offenders, (
        "caminho de estado composto sem consultar HARNESS_DIR (viola INV-4):\n  "
        + "\n  ".join(offenders)
    )
