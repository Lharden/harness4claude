"""O git-guard nao passa o que nao leu (AC-2..AC-11 de
`docs/specs/git-guard-prazo-sob-carga-spec-light.md`).

Tres caminhos de falha aberta medidos em 2026-10-07, alem do prazo
(`test_git_guard_prazo.py`):

1. **Parser.** Um `'` solto num comentario ou heredoc fazia a politica devolver
   `unknown` para o comando INTEIRO, e o guarda tratava `unknown` como "passa
   com aviso" — `git reset --hard` na linha seguinte passava.
2. **Opcao global do git.** `git -C <dir> branch -D x` passava como `allow`: a
   politica lia `-C` como subcomando. Nos transcripts: 12 `branch -D` reais
   passaram assim, e 41 pushes sem o aviso.
3. **PowerShell.** O matcher era so `Bash`; a ferramenta PowerShell nao passava
   por guarda nenhum.

E uma falsa ilegibilidade: 973 dos 1 112 avisos "nao conseguiu analisar" eram
comandos que a politica le — a falha era passar o comando por argv do bash para
o Python nativo (aspas duplas + quebra de linha).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
HOOKS = ROOT / "hooks"
GUARD = "harness-git-guard.sh"

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


def _run(command, tmp_path: Path, *, tool: str = "Bash", hooks_dir: Path = HOOKS,
         env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["HARNESS_DIR"] = str(tmp_path / "harness")
    env.pop("PYTHONUTF8", None)  # o host nao roda com ela
    env.update(env_extra or {})
    payload = json.dumps({"tool_name": tool, "tool_input": {"command": command}})
    return subprocess.run([BASH, str(hooks_dir / GUARD)], input=payload,
                          capture_output=True, text=True, timeout=60, env=env)


class TestParserQueFalhaNaoLiberaDestrutivo:
    """AC-2..AC-4: o fallback reusa a politica e bloqueia o destrutivo."""

    @pytest.mark.parametrize("command", [
        "# don't do this\ngit reset --hard HEAD~1",
        "cat <<'EOF2'\nit's\nEOF2\ngit push --force origin main",
        "echo it's && git clean -fd",
    ], ids=["comentario-com-apostrofo", "heredoc-com-apostrofo", "apostrofo-na-mesma-linha"])
    def test_destrutivo_ilegivel_bloqueia(self, tmp_path, command):
        res = _run(command, tmp_path)
        assert res.returncode == 2, res.stdout + res.stderr
        assert "BLOCKED" in res.stderr

    @pytest.mark.parametrize("command", [
        "echo it's fine",
        "git commit -F - <<'EOF'\ndon't use git reset --hard here\nEOF",
        "echo it's \"git push --force\"",
    ], ids=["benigno-ilegivel", "commit-por-heredoc-cita-destrutivo", "destrutivo-so-citado"])
    def test_ilegivel_sem_destrutivo_executado_nao_bloqueia(self, tmp_path, command):
        """AC-6: texto citado continua nao executando, mesmo no fallback."""
        res = _run(command, tmp_path)
        assert res.returncode == 0, res.stdout + res.stderr


class TestRestauracaoAmplaBloqueia:
    """AC-5: o guard inteiro, nao so a politica."""

    @pytest.mark.parametrize("command", ["git checkout .", "git restore ."])
    def test_bloqueia(self, tmp_path, command):
        assert _run(command, tmp_path).returncode == 2


class TestOpcoesGlobaisDoGit:
    """AC-8: o subcomando e julgado depois das opcoes globais."""

    @pytest.mark.parametrize("command", [
        "git -C repo reset --hard",
        'git -C "$M" branch -D tmp/x',
        "git --no-pager push --force",
        "git -c core.x=y clean -fd",
        "git --git-dir=/r/.git --work-tree /r checkout .",
    ])
    def test_destrutivo_com_opcao_global_bloqueia(self, tmp_path, command):
        res = _run(command, tmp_path)
        assert res.returncode == 2, res.stdout + res.stderr

    def test_push_com_opcao_global_avisa(self, tmp_path):
        res = _run("git -C repo push origin main", tmp_path)
        assert res.returncode == 0
        assert "Warning" in res.stdout

    @pytest.mark.parametrize("command", ["git -C repo status", "git --version", "git -C repo branch -d feito"])
    def test_benigno_com_opcao_global_passa_mudo(self, tmp_path, command):
        res = _run(command, tmp_path)
        assert res.returncode == 0
        assert res.stdout.strip() == ""


class TestPowerShell:
    """AC-9: a ferramenta PowerShell passa pelo guarda."""

    def test_hooks_json_registra_o_guarda_para_powershell(self):
        dados = json.loads((HOOKS / "hooks.json").read_text(encoding="utf-8"))
        matchers = [g.get("matcher", "") for g in dados["hooks"]["PreToolUse"]
                    for h in g["hooks"] if GUARD in h["command"]]
        assert matchers, "git-guard nao registrado em PreToolUse"
        for ferramenta in ("Bash", "PowerShell"):
            assert any(re.fullmatch(m, ferramenta) for m in matchers), (
                f"git-guard nao casa a ferramenta {ferramenta}: {matchers}")

    def test_comando_no_estilo_powershell_bloqueia(self, tmp_path):
        res = _run("git -C $m reset --hard; Write-Host ok", tmp_path, tool="PowerShell")
        assert res.returncode == 2, res.stdout + res.stderr


class TestInterpretadorNomeado:
    """AC-7: marcador do interpretador em forma ruim nao derruba o bloqueio."""

    @pytest.mark.parametrize("conteudo", [
        "C:/nao/existe/python.exe\n",
        "{py}\r\n",
        "{py}",
        "\ufeff{py}\n",
    ], ids=["caminho-inexistente", "crlf", "sem-newline", "bom-utf8"])
    def test_marcador_ruim_ainda_bloqueia(self, tmp_path, conteudo):
        home = tmp_path / "mh"
        home.mkdir()
        py = sys.executable.replace("\\", "/")
        (home / "interpretador").write_text(conteudo.format(py=py), encoding="utf-8", newline="")
        res = _run("git reset --hard", tmp_path, env_extra={"MASTER_HARNESS_HOME": str(home)})
        assert res.returncode == 2, res.stdout + res.stderr


class TestFalhaDeInfraestruturaNaoBloqueiaNemCala:
    """AC-10: sem o script Python, o guarda avisa e passa — nunca bloqueia."""

    def test_script_ausente_avisa_com_rate_limit(self, tmp_path):
        hooks = tmp_path / "plugin" / "hooks"
        hooks.mkdir(parents=True)
        shutil.copy2(HOOKS / GUARD, hooks / GUARD)
        primeiro = _run("git status", tmp_path, hooks_dir=hooks)
        assert primeiro.returncode == 0, primeiro.stderr
        assert "falhou" in primeiro.stdout and "INATIVO" in primeiro.stdout, primeiro.stdout
        segundo = _run("git status", tmp_path, hooks_dir=hooks)
        assert segundo.returncode == 0
        assert segundo.stdout.strip() == "", "o aviso tem rate-limit de 1 h"


class TestComandoRealNaoViraIlegivel:
    """AC-11: forma real do corpus (aspas duplas + quebra de linha) e decidida."""

    def test_aspas_e_quebra_de_linha_sao_lidas(self, tmp_path):
        command = (
            'cd "C:/Users/x/projects/harness4claude/.claude/worktrees/w" && \\\n'
            'echo "HEAD=$(git rev-parse --short HEAD) sujo=[$(git status --short | wc -l)]"\n'
            'git log --oneline -3'
        )
        res = _run(command, tmp_path)
        assert res.returncode == 0
        assert res.stdout.strip() == "", res.stdout
