"""Escopo de estado por projeto (auditoria 2026-07-28).

Evidencia do bug: `~/.claude/harness/.session-files-count` continha 130 arquivos
sob UM `task_id` — 41 de `master_project`, 39 de `harness4claude`, o resto de
temporarios. `harness-reclassify.sh` usa esse contador para promover L0 -> L1,
entao editar arquivos num repositorio escalava a classificacao de outro. E
`harness-session-start.sh` oferecia retomar a mesma task em toda sessao de todo
projeto, porque a checagem era apenas `status == "active"`.

O teste central e `TestIsolamento`: dois projetos, dois estados, zero contato.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
PATHS_PY = ROOT / "scripts" / "harness_paths.py"


@pytest.fixture(scope="module")
def hp():
    spec = importlib.util.spec_from_file_location("harness_paths", PATHS_PY)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["harness_paths"] = mod
    spec.loader.exec_module(mod)
    return mod


def _repo(base: Path, name: str) -> Path:
    """Cria um diretorio que parece um repo git (basta existir `.git`)."""
    d = base / name
    (d / ".git").mkdir(parents=True)
    return d


class TestFindRepoRoot:
    def test_encontra_na_raiz(self, hp, tmp_path):
        r = _repo(tmp_path, "proj")
        assert hp.find_repo_root(str(r)) == str(r)

    def test_encontra_subindo_de_subdiretorio(self, hp, tmp_path):
        r = _repo(tmp_path, "proj")
        sub = r / "src" / "deep"
        sub.mkdir(parents=True)
        assert hp.find_repo_root(str(sub)) == str(r)

    def test_worktree_com_git_arquivo(self, hp, tmp_path):
        """Em worktree `.git` e um ARQUIVO — checar so por diretorio erraria."""
        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /outro/lugar", encoding="utf-8")
        assert hp.find_repo_root(str(wt)) == str(wt)

    def test_sem_repo_retorna_none(self, hp, tmp_path):
        d = tmp_path / "solto"
        d.mkdir()
        assert hp.find_repo_root(str(d)) is None

    def test_cwd_vazio_retorna_none(self, hp):
        assert hp.find_repo_root(None) is None
        assert hp.find_repo_root("") is None


class TestProjectSlug:
    def test_estavel_entre_chamadas(self, hp, tmp_path):
        r = _repo(tmp_path, "alpha")
        assert hp.project_slug(str(r)) == hp.project_slug(str(r))

    def test_subdiretorio_da_o_mesmo_slug_da_raiz(self, hp, tmp_path):
        r = _repo(tmp_path, "alpha")
        sub = r / "a" / "b"
        sub.mkdir(parents=True)
        assert hp.project_slug(str(sub)) == hp.project_slug(str(r))

    def test_projetos_distintos_dao_slugs_distintos(self, hp, tmp_path):
        a = _repo(tmp_path, "alpha")
        b = _repo(tmp_path, "beta")
        assert hp.project_slug(str(a)) != hp.project_slug(str(b))

    def test_mesmo_basename_em_caminhos_diferentes_nao_colide(self, hp, tmp_path):
        """Dois checkouts de mesmo nome sao projetos diferentes."""
        a = _repo(tmp_path / "x", "proj")
        b = _repo(tmp_path / "y", "proj")
        assert hp.project_slug(str(a)) != hp.project_slug(str(b))

    def test_slug_comeca_pelo_basename_legivel(self, hp, tmp_path):
        r = _repo(tmp_path, "harness4claude")
        assert hp.project_slug(str(r)).startswith("harness4claude-")

    def test_caracteres_invalidos_sanitizados(self, hp, tmp_path):
        r = _repo(tmp_path, "meu proj@2026")
        slug = hp.project_slug(str(r))
        assert not set(slug) & set(" @/\\:")


class TestScope:
    def test_default_e_por_projeto(self, hp, tmp_path, monkeypatch):
        monkeypatch.delenv("HARNESS_SCOPE", raising=False)
        r = _repo(tmp_path, "alpha")
        d = hp.state_dir(tmp_path / "root", str(r))
        assert d.parent.name == hp.PROJECTS_SUBDIR

    def test_global_usa_a_raiz(self, hp, tmp_path):
        r = _repo(tmp_path, "alpha")
        root = tmp_path / "root"
        assert hp.state_dir(root, str(r), scope="global") == root

    def test_global_via_env(self, hp, tmp_path, monkeypatch):
        monkeypatch.setenv("HARNESS_SCOPE", "GLOBAL")
        r = _repo(tmp_path, "alpha")
        root = tmp_path / "root"
        assert hp.state_dir(root, str(r)) == root

    def test_signals_sempre_na_raiz(self, hp, tmp_path):
        """Telemetria e agregada de proposito: registros sao chaveados por task_id."""
        root = tmp_path / "root"
        assert hp.signals_dir(root) == root


class TestIsolamento:
    """O bug original: contador de um projeto mexendo na classificacao de outro."""

    def test_dois_projetos_dois_diretorios(self, hp, tmp_path):
        root = tmp_path / "root"
        a = hp.ensure_state_dir(root, str(_repo(tmp_path, "alpha")))
        b = hp.ensure_state_dir(root, str(_repo(tmp_path, "beta")))
        assert a != b
        assert a.exists() and b.exists()

    def test_contador_de_um_nao_afeta_o_outro(self, hp, tmp_path):
        root = tmp_path / "root"
        a = hp.ensure_state_dir(root, str(_repo(tmp_path, "alpha")))
        b = hp.ensure_state_dir(root, str(_repo(tmp_path, "beta")))

        (a / ".session-files-count").write_text(
            json.dumps({"count": 130, "files": [], "task_id": "t-a"}), encoding="utf-8")
        (b / ".session-files-count").write_text(
            json.dumps({"count": 0, "files": [], "task_id": "t-b"}), encoding="utf-8")

        lido = json.loads((b / ".session-files-count").read_text(encoding="utf-8"))
        assert lido["count"] == 0
        assert lido["task_id"] == "t-b"

    def test_pipeline_ativo_de_um_nao_aparece_no_outro(self, hp, tmp_path):
        root = tmp_path / "root"
        a = hp.ensure_state_dir(root, str(_repo(tmp_path, "alpha")))
        b = hp.ensure_state_dir(root, str(_repo(tmp_path, "beta")))

        (a / "state.json").write_text(
            json.dumps({"task_id": "t-a", "status": "active", "pipeline": ["tdd"]}),
            encoding="utf-8")

        assert not (b / "state.json").exists(), \
            "state de um projeto nao pode ser visivel do bucket de outro"

    def test_duas_sessoes_no_mesmo_worktree_tem_estado_independente(self, hp, tmp_path):
        root = tmp_path / "root"
        repo = _repo(tmp_path, "alpha")
        a = hp.ensure_state_dir(root, str(repo), session_id="session-a")
        b = hp.ensure_state_dir(root, str(repo), session_id="session-b")

        assert a != b
        assert a.parent == b.parent
        assert a.name.startswith("session-a-")


class TestCli:
    """Contrato com os hooks em bash."""

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env.pop("HARNESS_SCOPE", None)
        return subprocess.run([sys.executable, str(PATHS_PY), *args],
                              capture_output=True, text=True, check=False, env=env)

    def test_imprime_diretorio_e_cria(self, tmp_path):
        r = _repo(tmp_path, "alpha")
        res = self._run("--root", str(tmp_path / "root"), "--cwd", str(r))
        assert res.returncode == 0, res.stderr
        assert Path(res.stdout.strip()).is_dir()

    def test_slug_isolado(self, tmp_path):
        r = _repo(tmp_path, "alpha")
        res = self._run("--root", str(tmp_path / "root"), "--cwd", str(r), "--slug")
        assert res.returncode == 0
        assert res.stdout.strip().startswith("alpha-")

    def test_signals_aponta_para_raiz(self, tmp_path):
        root = tmp_path / "root"
        res = self._run("--root", str(root), "--cwd", str(_repo(tmp_path, "alpha")), "--signals")
        assert Path(res.stdout.strip()) == root
