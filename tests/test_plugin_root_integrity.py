"""Integridade do `plugin-root` (incidente 2026-07-28).

O arquivo `~/.claude/harness/plugin-root` e o prefixo com que TODA skill executa
os scripts do plugin. Ele e compartilhado entre CLIs no mesmo `$HOME`
(last-writer-wins): o Codex apontou para o proprio cache, esse cache foi removido
no upgrade de versao, e o arquivo passou a nomear um caminho inexistente. Nesse
estado nao havia degradacao parcial — toda skill que o usa falharia de uma vez.

A regra travada aqui: so grava arvore que contem `scripts/record_signal.py`, e
repara um valor podre em vez de preserva-lo.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
HOOK = ROOT / "hooks" / "harness-session-start.sh"

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


def _run(harness_dir: Path, plugin_root: str | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["HARNESS_SKIP_DEPCHECK"] = "1"
    env["HARNESS_DIR"] = str(harness_dir)
    if plugin_root is not None:
        env["CLAUDE_PLUGIN_ROOT"] = plugin_root
    else:
        env.pop("CLAUDE_PLUGIN_ROOT", None)
    return subprocess.run([BASH, str(HOOK)], input="{}", capture_output=True,
                          text=True, timeout=60, env=env)


def _value(harness_dir: Path) -> str:
    return (harness_dir / "plugin-root").read_text(encoding="utf-8").strip()


class TestPluginRootIntegrity:
    def test_cria_quando_ausente(self, tmp_path):
        _run(tmp_path)
        assert (Path(_value(tmp_path)) / "scripts" / "record_signal.py").is_file()

    def test_repara_caminho_inexistente(self, tmp_path):
        """O incidente: o valor apontava para um cache que sumiu no upgrade."""
        (tmp_path / "plugin-root").write_text("C:/nao/existe\n", encoding="utf-8")
        _run(tmp_path)
        assert _value(tmp_path) != "C:/nao/existe"
        assert (Path(_value(tmp_path)) / "scripts" / "record_signal.py").is_file()

    def test_repara_valor_vazio(self, tmp_path):
        (tmp_path / "plugin-root").write_text("", encoding="utf-8")
        _run(tmp_path)
        assert (Path(_value(tmp_path)) / "scripts" / "record_signal.py").is_file()

    def test_repara_arvore_incompleta(self, tmp_path):
        """Diretorio existe mas nao tem os scripts — igualmente inutil."""
        vazio = tmp_path / "arvore-vazia"
        vazio.mkdir()
        (tmp_path / "plugin-root").write_text(str(vazio), encoding="utf-8")
        _run(tmp_path)
        assert Path(_value(tmp_path)).resolve() != vazio.resolve()

    def test_nao_grava_arvore_invalida(self, tmp_path):
        """CLAUDE_PLUGIN_ROOT apontando para lixo nao pode destruir um valor bom."""
        bom = str(ROOT).replace(os.sep, "/")
        (tmp_path / "plugin-root").write_text(bom + "\n", encoding="utf-8")
        lixo = tmp_path / "sem-scripts"
        lixo.mkdir()

        _run(tmp_path, plugin_root=str(lixo))

        assert Path(_value(tmp_path)).resolve() == ROOT.resolve(), (
            "valor valido foi sobrescrito por uma arvore sem scripts/"
        )
