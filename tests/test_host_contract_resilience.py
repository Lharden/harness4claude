"""Resiliencia ao contrato do CLI host (investigacao 2026-07-29).

Duas familias de regressao, ambas encontradas por injecao de falha:

1. **git-guard falhando aberto.** Com `tool_input` renomeado, o extrator devolvia
   string vazia e o hook saia 0 — parava de bloquear operacoes git destrutivas
   sem emitir sinal nenhum. Um guard que some em silencio e pior que um guard
   ausente, porque voce conta com ele. Bloquear tudo tambem nao serve: numa
   mudanca de schema do host, travar todo Bash da maquina seria pior que o
   problema. A regra: passa, mas avisa.

2. **CRLF corrompendo o bucket do projeto.** O `print()` do Python no Windows
   emite `\\r\\n`; os hooks fatiam por `\\n` e sobrava um `\\r` no cwd. Caminho com
   `\\r` nao existe, entao `find_repo_root` falhava e caia no cwd cru: abrir a
   sessao na raiz do repo ou num subdiretorio gerava buckets DIFERENTES,
   fragmentando o estado de um mesmo projeto.
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

# Montado em pedacos para nao disparar o proprio guard quando esta suite roda
# dentro de uma sessao do Claude Code com o hook PreToolUse ativo.
DESTRUCTIVE = "git re" + "set --ha" + "rd HEAD~1"


def _run(hook: str, payload, harness_dir: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["HARNESS_SKIP_DEPCHECK"] = "1"
    env["HARNESS_DIR"] = str(harness_dir)
    stdin = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run([BASH, str(HOOKS / hook)], input=stdin,
                          capture_output=True, text=True, timeout=60, env=env)


class TestGitGuardFailsLoud:
    """Payload em formato desconhecido: passa (nunca trava), mas avisa."""

    def test_bloqueia_destrutivo(self, tmp_path):
        res = _run("harness-git-guard.sh", {"tool_input": {"command": DESTRUCTIVE}}, tmp_path)
        assert res.returncode == 2

    def test_benigno_passa_mudo(self, tmp_path):
        res = _run("harness-git-guard.sh", {"tool_input": {"command": "ls -la"}}, tmp_path)
        assert res.returncode == 0
        assert res.stdout.strip() == ""

    def test_command_vazio_passa_mudo(self, tmp_path):
        """Payload valido com comando vazio nao e anomalia — nao pode alarmar."""
        res = _run("harness-git-guard.sh", {"tool_input": {"command": ""}}, tmp_path)
        assert res.returncode == 0
        assert res.stdout.strip() == ""

    @pytest.mark.parametrize("payload", [
        {"toolInput": {"command": "ls"}},   # chave renomeada pelo host
        {"tool_input": {"cmd": "ls"}},      # subchave renomeada
        {"tool_input": "ls"},               # tipo trocado
        "nao e json",                       # payload nao estruturado
    ], ids=["tool_input-renomeado", "command-renomeado", "tipo-trocado", "nao-json"])
    def test_forma_desconhecida_avisa_sem_bloquear(self, tmp_path, payload):
        res = _run("harness-git-guard.sh", payload, tmp_path)
        assert res.returncode == 0, "nunca bloquear: travaria todo Bash da maquina"
        assert "git-guard nao reconheceu" in res.stdout
        assert "INATIVO" in res.stdout

    def test_aviso_tem_rate_limit(self, tmp_path):
        """Sem isto o aviso sairia em toda chamada de Bash e viraria ruido."""
        payload = {"toolInput": {"command": "ls"}}
        primeiro = _run("harness-git-guard.sh", payload, tmp_path)
        segundo = _run("harness-git-guard.sh", payload, tmp_path)
        assert "git-guard nao reconheceu" in primeiro.stdout
        assert segundo.stdout.strip() == ""


class TestCrlfNaoFragmentaBucket:
    """Regressao: raiz do repo e subdiretorio precisam cair no MESMO bucket."""

    def _repo(self, base: Path) -> Path:
        repo = base / "repo"
        (repo / ".git").mkdir(parents=True)
        (repo / "src" / "deep").mkdir(parents=True)
        return repo

    def _classify_from(self, cwd: Path, harness_dir: Path) -> None:
        _run("harness-classify.sh",
             {"prompt": "cria um sistema completo de autenticacao", "cwd": str(cwd)},
             harness_dir)

    def test_raiz_e_subdiretorio_no_mesmo_bucket(self, tmp_path):
        harness = tmp_path / "h"
        repo = self._repo(tmp_path)

        self._classify_from(repo, harness)
        self._classify_from(repo / "src" / "deep", harness)

        buckets = sorted(p.name for p in (harness / "projects").iterdir())
        assert len(buckets) == 1, (
            f"raiz e subdiretorio do mesmo repo geraram buckets distintos: {buckets}"
        )

    def test_bucket_bate_com_o_resolvedor(self, tmp_path):
        """O nome criado pelo hook tem de ser o mesmo que o resolvedor calcula."""
        sys.path.insert(0, str(ROOT / "scripts"))
        from harness_paths import project_slug  # noqa: PLC0415

        harness = tmp_path / "h"
        repo = self._repo(tmp_path)
        self._classify_from(repo, harness)

        criado = next((harness / "projects").iterdir()).name
        assert criado == project_slug(str(repo)), (
            "hook e resolvedor discordam — sinal de sujeira no cwd vinda do shell"
        )

    def test_cwd_com_cr_nao_cria_bucket_novo(self, tmp_path):
        """Segunda barreira: mesmo com \\r no payload, o resolvedor limpa."""
        harness = tmp_path / "h"
        repo = self._repo(tmp_path)

        self._classify_from(repo, harness)
        _run("harness-classify.sh",
             {"prompt": "adiciona um endpoint novo", "cwd": str(repo) + "\r"},
             harness)

        buckets = sorted(p.name for p in (harness / "projects").iterdir())
        assert len(buckets) == 1, f"\\r no cwd criou bucket paralelo: {buckets}"
