"""Resolvedor portavel do plugin root — universalizacao entre maquinas.

Problema: CLAUDE_PLUGIN_ROOT so existe no ambiente dos HOOKS. O LLM, ao executar
comandos das skills via Bash, nao a enxerga. Por isso as skills nao podem
referenciar nem `${CLAUDE_PLUGIN_ROOT}` (vazia) nem um caminho absoluto de uma
maquina especifica (`~/.claude/plugins/local/...`, que pode nem existir).

Solucao: o hook SessionStart — que TEM a variavel — persiste o caminho resolvido
em `$HARNESS_DIR/plugin-root`. As skills leem esse arquivo.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
HOOKS = ROOT / "hooks"

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
    env["HARNESS_SKIP_DEPCHECK"] = "1"
    env.pop("HARNESS_DIR", None)
    env.pop("CLAUDE_PLUGIN_ROOT", None)
    env.update(extra)
    return env


def _run_session_start(env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [BASH, str(HOOKS / "harness-session-start.sh")],
        input="{}", capture_output=True, text=True, timeout=60, env=env,
    )


def _fake_plugin(path: Path) -> Path:
    """Arvore minima que o hook aceita como plugin.

    Desde o incidente de 2026-07-28 o SessionStart so grava em `plugin-root` uma
    arvore que contenha `scripts/record_signal.py` — um valor podre quebra TODAS
    as skills de uma vez, entao um diretorio vazio deixou de ser um plugin root
    valido. Ver tests/test_plugin_root_integrity.py.
    """
    (path / "scripts").mkdir(parents=True, exist_ok=True)
    (path / "scripts" / "record_signal.py").write_text("", encoding="utf-8")
    return path


class TestPluginRootPersistence:
    def test_writes_plugin_root_from_env(self, tmp_path):
        """Given CLAUDE_PLUGIN_ROOT definida, Then persiste em plugin-root."""
        harness = tmp_path / "h"
        fake_plugin = _fake_plugin(tmp_path / "plugin")
        env = _env(HARNESS_DIR=str(harness), CLAUDE_PLUGIN_ROOT=str(fake_plugin))
        proc = _run_session_start(env)
        assert proc.returncode == 0, proc.stderr[:400]

        marker = harness / "plugin-root"
        assert marker.is_file(), "SessionStart deveria persistir o plugin root"
        # Comparacao por Path, nao por string: no Windows o valor e gravado em
        # formato misto (C:/... com barras normais), o unico que funciona tanto
        # no Git Bash quanto no PowerShell.
        assert Path(marker.read_text(encoding="utf-8").strip()) == fake_plugin

    def test_falls_back_when_env_absent(self, tmp_path):
        """Sem a variavel, cai no diretorio do proprio hook — nunca vazio."""
        harness = tmp_path / "h"
        env = _env(HARNESS_DIR=str(harness))
        proc = _run_session_start(env)
        assert proc.returncode == 0, proc.stderr[:400]

        content = (harness / "plugin-root").read_text(encoding="utf-8").strip()
        assert content, "plugin-root nunca pode ficar vazio"
        assert (Path(content) / "hooks").is_dir(), (
            f"o fallback deve apontar para uma arvore de plugin valida: {content}"
        )

    def test_is_rewritten_on_each_session(self, tmp_path):
        """Plugin movido/atualizado entre sessoes -> marker acompanha."""
        harness = tmp_path / "h"
        first = _fake_plugin(tmp_path / "p1")
        second = _fake_plugin(tmp_path / "p2")

        _run_session_start(_env(HARNESS_DIR=str(harness), CLAUDE_PLUGIN_ROOT=str(first)))
        _run_session_start(_env(HARNESS_DIR=str(harness), CLAUDE_PLUGIN_ROOT=str(second)))

        assert Path((harness / "plugin-root").read_text(encoding="utf-8").strip()) == second


class TestSkillsAreMachineAgnostic:
    """As skills nao podem conter caminho absoluto de uma maquina especifica."""

    FORBIDDEN = ("plugins/local/harness4claude", "Documents/projects/harness4claude")

    def test_skills_have_no_machine_specific_paths(self):
        offenders: list[str] = []
        for skill in (ROOT / "skills").rglob("*.md"):
            for n, line in enumerate(
                skill.read_text(encoding="utf-8", errors="replace").splitlines(), 1
            ):
                for bad in self.FORBIDDEN:
                    if bad in line:
                        rel = skill.relative_to(ROOT)
                        offenders.append(f"{rel}:{n}")
        assert not offenders, (
            "skills com caminho especifico de maquina (quebram em outra instalacao):\n  "
            + "\n  ".join(offenders)
        )

    def test_sync_templates_have_no_machine_specific_paths(self):
        offenders: list[str] = []
        for tpl in (ROOT / "sync" / "templates").rglob("*"):
            if not tpl.is_file():
                continue
            for n, line in enumerate(
                tpl.read_text(encoding="utf-8", errors="replace").splitlines(), 1
            ):
                for bad in self.FORBIDDEN:
                    if bad in line:
                        offenders.append(f"{tpl.name}:{n}")
        assert not offenders, (
            "templates de sync propagam caminho especifico para novas maquinas:\n  "
            + "\n  ".join(offenders)
        )
