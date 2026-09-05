"""Worktree e o mesmo projeto que o repositorio dele.

Medido em 2026-09-05: `project_slug` devolvia `equipotence-v1-bf70fe21` para
`harness4codex-worktrees/equipotence-v1` e `harness4codex-adfb74ad` para o repo
dono dele. Dois projetos onde ha um, e o plano original pede o contrario, com
todas as letras — aceite E1: "reconhecer worktrees como partes do mesmo
projeto".

A consequencia media: trabalhar num worktree abria um bucket de estado proprio,
e uma tarefa reivindicada de um lado era invisivel do outro.

## Por que ler o arquivo, e nao chamar `git`

`find_repo_root` e deliberadamente sem subprocess, e o docstring dele diz por
que: roda em todo `UserPromptSubmit`, e um `git rev-parse` por prompt custaria
mais que a resolucao inteira. A correcao respeita isso — num worktree, `.git` e
um **arquivo** de uma linha com `gitdir: <caminho>`, e ler um arquivo e mais
barato que abrir um processo.

## O caso que separa worktree de submodulo

Submodulo tambem tem `.git` como arquivo, apontando para
`<pai>/.git/modules/<nome>`. Mas submodulo **e outro repositorio** — colapsa-lo
no pai misturaria dois projetos de verdade, que e o oposto do que se quer aqui.
So `worktrees/` colapsa.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(nome: str, relativo: str):
    spec = importlib.util.spec_from_file_location(nome, ROOT / relativo)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def hp():
    return _load("harness_paths_worktree", "scripts/harness_paths.py")


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
        },
    ).stdout


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "projeto"
    r.mkdir()
    (r / "a.txt").write_bytes(b"a\n")
    _git(r, "init", "-q", "-b", "main")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "base", "--no-verify")
    return r


@pytest.fixture()
def worktree(repo: Path, tmp_path: Path) -> Path:
    destino = tmp_path / "projeto-worktrees" / "ramo"
    _git(repo, "worktree", "add", "-q", "-b", "ramo", str(destino))
    return destino


class TestColapsoDeWorktree:
    def test_worktree_resolve_para_o_repo_dono(self, hp, repo: Path, worktree: Path) -> None:
        assert Path(hp.find_repo_root(worktree)).resolve() == repo.resolve()

    def test_worktree_e_repo_tem_o_mesmo_slug(self, hp, repo: Path, worktree: Path) -> None:
        """O aceite E1 do plano, em uma linha."""
        assert hp.project_slug(worktree) == hp.project_slug(repo)

    def test_subpasta_do_worktree_tambem_colapsa(self, hp, repo: Path, worktree: Path) -> None:
        sub = worktree / "fundo" / "mais-fundo"
        sub.mkdir(parents=True)

        assert hp.project_slug(sub) == hp.project_slug(repo)

    def test_repo_normal_continua_como_estava(self, hp, repo: Path) -> None:
        assert Path(hp.find_repo_root(repo)).resolve() == repo.resolve()

    def test_dois_worktrees_do_mesmo_repo_sao_um_projeto(
        self, hp, repo: Path, worktree: Path, tmp_path: Path
    ) -> None:
        outro = tmp_path / "projeto-worktrees" / "outro"
        _git(repo, "worktree", "add", "-q", "-b", "outro", str(outro))

        assert hp.project_slug(worktree) == hp.project_slug(outro) == hp.project_slug(repo)


class TestSubmoduloNaoColapsa:
    """Submodulo tem `.git` como arquivo e **nao** e o mesmo projeto que o pai."""

    def test_gitdir_de_submodulo_nao_sobe_para_o_pai(self, hp, tmp_path: Path) -> None:
        pai = tmp_path / "pai"
        (pai / ".git").mkdir(parents=True)
        sub = pai / "vendor" / "lib"
        sub.mkdir(parents=True)
        (sub / ".git").write_text(
            f"gitdir: {pai / '.git' / 'modules' / 'lib'}\n", encoding="utf-8"
        )

        assert Path(hp.find_repo_root(sub)).resolve() == sub.resolve()

    def test_submodulo_tem_slug_proprio(self, hp, tmp_path: Path) -> None:
        pai = tmp_path / "pai2"
        (pai / ".git").mkdir(parents=True)
        sub = pai / "vendor" / "lib"
        sub.mkdir(parents=True)
        (sub / ".git").write_text(
            f"gitdir: {pai / '.git' / 'modules' / 'lib'}\n", encoding="utf-8"
        )

        assert hp.project_slug(sub) != hp.project_slug(pai)


class TestDegradacao:
    """`.git` ilegivel nao pode derrubar o hook: cai no diretorio, como antes."""

    def test_git_arquivo_com_lixo_cai_no_diretorio(self, hp, tmp_path: Path) -> None:
        d = tmp_path / "quebrado"
        d.mkdir()
        (d / ".git").write_bytes(b"\x00\x01 isto nao e um ponteiro\n")

        assert Path(hp.find_repo_root(d)).resolve() == d.resolve()

    def test_git_arquivo_vazio_cai_no_diretorio(self, hp, tmp_path: Path) -> None:
        d = tmp_path / "vazio"
        d.mkdir()
        (d / ".git").write_bytes(b"")

        assert Path(hp.find_repo_root(d)).resolve() == d.resolve()

    def test_gitdir_apontando_para_nada_cai_no_diretorio(self, hp, tmp_path: Path) -> None:
        d = tmp_path / "orfao"
        d.mkdir()
        (d / ".git").write_text("gitdir: C:/nao/existe/.git/worktrees/x\n", encoding="utf-8")

        assert Path(hp.find_repo_root(d)).resolve() == d.resolve()

    def test_sem_repo_continua_none(self, hp, tmp_path: Path) -> None:
        d = tmp_path / "sem-repo"
        d.mkdir()

        assert hp.find_repo_root(d) is None


class TestCustoDaResolucao:
    """A restricao que o proprio codigo documenta: isto roda em todo prompt."""

    def test_resolucao_nao_abre_processo(self, hp, worktree: Path, monkeypatch) -> None:
        """Um `git rev-parse` por prompt custaria mais que a resolucao inteira."""

        def _proibido(*a, **k):  # pragma: no cover - so existe para falhar
            raise AssertionError("find_repo_root abriu um subprocesso")

        monkeypatch.setattr(subprocess, "run", _proibido)
        monkeypatch.setattr(subprocess, "Popen", _proibido)
        monkeypatch.setattr(subprocess, "check_output", _proibido)

        assert hp.find_repo_root(worktree) is not None
