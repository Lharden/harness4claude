"""Diretorio que nao e repositorio nao cunha projeto.

Medido em 2026-09-05: `~/.claude/harness/projects/` tinha **46 buckets** para
bem menos projetos reais, e o mecanismo mintava um para qualquer diretorio em
que uma sessao comecasse. Alguns dos nomes encontrados:

    1.1.0-ac1c74bb                             <- um diretorio de VERSAO
    james-yu.latex-workshop-10.16.1-e94f54c4   <- extensao do VSCode
    tecosaur.latex-utilities-0.4.14-0f79a0e9   <- outra
    System32-014aa9d2   Temp-bee27afd   scratchpad-b3c86573

Vinte e quatro deles tinham zero sessoes e ~200 bytes — um `state.json` vazio e
nada mais. Um custou caro: o B-19, em que a mesma sessao andou entre pastas e
ganhou tres tasks em tres buckets, travando o proprio portao de verificacao.

## O que muda, e o que deliberadamente nao muda

Muda so o caso **sem repositorio**: em vez de `<basename>-<hash>`, um unico
`sem-repositorio`, com as sessoes isoladas dentro como sempre.

**Slug de repositorio nao muda.** E a invariante que torna isto seguro, e ela
tem teste proprio: `slb-mestrado-projeto`, `.claude` e `mainframe` sao
repositorios git — sob a regra nova ficam identicos, byte a byte.

## Por que sem flag

`harness_paths` roda em todo hook e nao pode depender do `mh` — e o modulo
central, tem que funcionar sozinho. Ler `flags.json` por invocacao custaria mais
que o risco que evitaria, e o risco aqui e pequeno e medido: o unico bucket com
historia real afetado e o do HOME, que ja era, por acidente, o bucket
compartilhado do que nao e projeto. Rollback e `git revert` mais deploy.
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
    return _load("harness_paths_sem_repo", "scripts/harness_paths.py")


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
        },
    )


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "um-projeto"
    r.mkdir()
    (r / "a.txt").write_bytes(b"a\n")
    _git(r, "init", "-q", "-b", "main")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "base", "--no-verify")
    return r


class TestSemRepositorio:
    def test_diretorio_solto_nao_cunha_projeto(self, hp, tmp_path: Path) -> None:
        solto = tmp_path / "uma-pasta-qualquer"
        solto.mkdir()

        assert hp.project_slug(solto) == hp.SEM_REPOSITORIO

    def test_dois_diretorios_soltos_compartilham_o_bucket(self, hp, tmp_path: Path) -> None:
        """Antes, cada pasta ganhava o seu — foi assim que 24 buckets vazios nasceram."""
        a, b = tmp_path / "temp", tmp_path / "system32"
        a.mkdir()
        b.mkdir()

        assert hp.project_slug(a) == hp.project_slug(b) == hp.SEM_REPOSITORIO

    def test_o_nome_diz_o_que_e(self, hp) -> None:
        """`LHarden2-56540e5d` parecia projeto e era o HOME. O nome novo nao mente."""
        assert hp.SEM_REPOSITORIO == "sem-repositorio"

    def test_cwd_vazio_continua_unknown(self, hp) -> None:
        """Sem cwd nenhum e diferente de cwd fora de repo: um nao se sabe, o outro se sabe."""
        assert hp.project_slug(None) == "unknown"
        assert hp.project_slug("") == "unknown"


class TestInvarianteDoRepositorio:
    """A invariante que torna a mudanca segura: repositorio nao muda de slug."""

    def test_repositorio_mantem_slug_proprio(self, hp, repo: Path) -> None:
        assert hp.project_slug(repo) not in (hp.SEM_REPOSITORIO, "unknown")

    def test_subpasta_de_repo_segue_o_repo(self, hp, repo: Path) -> None:
        sub = repo / "src" / "fundo"
        sub.mkdir(parents=True)

        assert hp.project_slug(sub) == hp.project_slug(repo)

    def test_dois_repos_continuam_distintos(self, hp, tmp_path: Path, repo: Path) -> None:
        outro = tmp_path / "outro-projeto"
        outro.mkdir()
        (outro / "b.txt").write_bytes(b"b\n")
        _git(outro, "init", "-q", "-b", "main")
        _git(outro, "add", "-A")
        _git(outro, "commit", "-q", "-m", "base", "--no-verify")

        assert hp.project_slug(repo) != hp.project_slug(outro)

    @pytest.mark.parametrize(
        "caminho",
        [
            r"C:/Users/LHarden2/Documents/projects/harness4claude",
            r"C:/Users/LHarden2/Documents/projects/master_project/slb-mestrado-projeto",
            r"C:/Users/LHarden2/Documents/mainframe",
            r"C:/Users/LHarden2/.claude",
        ],
    )
    def test_slugs_reais_desta_maquina_nao_mudam(self, hp, caminho: str) -> None:
        """Os quatro buckets com historia que importam. Se um mudar, dados ficam orfaos.

        Os valores estao escritos a mao de proposito: se a formula mudar, este
        teste falha em vez de recalcular junto e concordar consigo mesmo.
        """
        esperados = {
            r"C:/Users/LHarden2/Documents/projects/harness4claude": "harness4claude-7aba6948",
            r"C:/Users/LHarden2/Documents/projects/master_project/slb-mestrado-projeto": "slb-mestrado-projeto-7c89ab0c",
            r"C:/Users/LHarden2/Documents/mainframe": "mainframe-2268cab9",
            r"C:/Users/LHarden2/.claude": ".claude-19e22822",
        }
        if not os.path.isdir(caminho):
            pytest.skip(f"{caminho} ausente nesta maquina")

        assert hp.project_slug(caminho) == esperados[caminho]


class TestEstadoAindaIsolaPorSessao:
    """Compartilhar bucket nao pode virar compartilhar estado.

    O incidente de 2026-07-28 — contador chegando a 130 arquivos sob um mesmo
    `task_id`, misturando dois projetos — foi o que motivou os buckets por
    projeto. Um bucket compartilhado sem isolamento por sessao o reintroduziria.
    """

    def test_sessoes_diferentes_tem_diretorios_diferentes(self, hp, tmp_path: Path) -> None:
        solto = tmp_path / "solto"
        solto.mkdir()
        raiz = str(tmp_path / "harness")

        a = hp.state_dir(raiz, str(solto), session_id="s-um")
        b = hp.state_dir(raiz, str(solto), session_id="s-dois")

        assert a != b
        assert hp.SEM_REPOSITORIO in str(a).replace("\\", "/")
        assert hp.SEM_REPOSITORIO in str(b).replace("\\", "/")
