"""Sessao fora de qualquer repositorio nao vira pipeline de TDD.

## O incidente

2026-09-25, sessao "PPEGPS Digital Transformation presentation": o cwd era uma
pasta de apresentacao, sem `.git`. `find_repo_root` devolvia None, e com raiz
vazia `harness-reclassify.sh` contava toda escrita. Tres `Write` (roteiro,
notas, script dos slides) promoviam a task de L0 para L1-feature com pipeline
`write-spec-light -> tdd -> verify-against-spec`, e desde o merge `27c0002` o
aviso passou a chegar ao modelo por additionalContext. O portao de Stop, em
seguida, bloqueava toda resposta final pedindo "evidencia de teste fresca" numa
pasta onde nao existe suite nenhuma para rodar.

## O que este arquivo trava

- Sessao E arquivo fora de qualquer repositorio: a escrita nao toca a task nem
  conta para a promocao, e o Stop nao bloqueia.
- Todo o resto continua igual: cwd num repositorio, payload sem cwd
  (fail-closed) e sessao aberta numa pasta-mae que edita arquivo dentro de um
  repositorio continuam promovendo e bloqueando.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
RECLASSIFY = ROOT / "hooks" / "harness-reclassify.sh"
BASH = shutil.which("bash")


def _load(name: str, relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


policy = _load("fora_de_repo_policy", "scripts/post_tool_policy.py")
hook = _load("fora_de_repo_transactional", "hooks/harness-transactional.py")

sys.path.insert(0, str(ROOT / "scripts"))
from harness_paths import ensure_state_dir, find_repo_root  # type: ignore[import-not-found]
from transactional_state import HarnessDatabase  # type: ignore[import-not-found]


def _repo(caminho: Path) -> Path:
    (caminho / ".git").mkdir(parents=True)
    return caminho


class TestPolitica:
    def test_sessao_e_arquivo_fora_de_repositorio(self, tmp_path):
        pasta = tmp_path / "ppegps"
        pasta.mkdir()
        assert policy.fora_de_qualquer_repositorio(
            str(pasta / "roteiro.md"), str(pasta), find_repo_root
        ) is True

    def test_caminho_relativo_resolve_contra_o_cwd(self, tmp_path):
        pasta = tmp_path / "ppegps"
        pasta.mkdir()
        assert policy.fora_de_qualquer_repositorio("slides/a.md", str(pasta), find_repo_root) is True

    def test_cwd_num_repositorio_nao_e_fora(self, tmp_path):
        repo = _repo(tmp_path / "repo")
        assert policy.fora_de_qualquer_repositorio(
            str(tmp_path / "solto.md"), str(repo), find_repo_root
        ) is False

    def test_pasta_mae_editando_dentro_de_um_repo_nao_e_fora(self, tmp_path):
        repo = _repo(tmp_path / "projects" / "x")
        assert policy.fora_de_qualquer_repositorio(
            str(repo / "src" / "a.py"), str(tmp_path / "projects"), find_repo_root
        ) is False

    def test_sem_cwd_nao_e_fora(self, tmp_path):
        """Sem cwd nao ha projeto declarado: continua contando (fail-closed)."""
        assert policy.fora_de_qualquer_repositorio(str(tmp_path / "a.md"), "", find_repo_root) is False

    def test_erro_na_resolucao_conta(self, tmp_path):
        def quebra(_):
            raise OSError("disco")

        assert policy.fora_de_qualquer_repositorio(str(tmp_path / "a.md"), str(tmp_path), quebra) is False


@pytest.mark.skipif(BASH is None, reason="bash nao encontrado no PATH")
class TestReclassify:
    def _preparar(self, harness_dir: Path, cwd: Path, session_id: str) -> Path:
        bucket = ensure_state_dir(harness_dir, cwd, session_id=session_id)
        (bucket / "state.json").write_text(json.dumps({
            "task_id": "t-fora", "classification": "L0-question", "status": "done",
            "pipeline": [], "current_step": None, "artifacts_so_far": [],
        }), encoding="utf-8")
        (bucket / ".session-files-count").write_text(
            json.dumps({"count": 0, "files": [], "task_id": "t-fora"}), encoding="utf-8"
        )
        return bucket

    def _escrever_tres(self, harness_dir: Path, cwd: Path, session_id: str, pasta: Path) -> str:
        saida = ""
        for nome in ("roteiro.md", "notas.md", "slides.py"):
            proc = subprocess.run(
                [BASH, str(RECLASSIFY)],
                input=json.dumps({
                    "session_id": session_id, "cwd": str(cwd), "tool_name": "Write",
                    "tool_input": {"file_path": str(pasta / nome)},
                }),
                capture_output=True, text=True, timeout=30,
                env={**os.environ, "PYTHONUTF8": "1", "HARNESS_DIR": str(harness_dir)},
            )
            assert proc.returncode == 0, proc.stderr
            saida += proc.stdout
        return saida

    def test_fora_de_repositorio_nao_promove(self, tmp_path):
        pasta = tmp_path / "ppegps"
        pasta.mkdir()
        harness_dir = tmp_path / "harness"
        bucket = self._preparar(harness_dir, pasta, "s-fora")

        saida = self._escrever_tres(harness_dir, pasta, "s-fora", pasta)

        assert saida.strip() == ""
        estado = json.loads((bucket / "state.json").read_text(encoding="utf-8"))
        assert estado["classification"] == "L0-question"
        assert estado["status"] == "done"

    def test_contraste_dentro_de_repositorio_ainda_promove(self, tmp_path):
        repo = _repo(tmp_path / "repo")
        harness_dir = tmp_path / "harness"
        bucket = self._preparar(harness_dir, repo, "s-repo")

        saida = self._escrever_tres(harness_dir, repo, "s-repo", repo)

        assert "<harness-reclassification>" in saida
        estado = json.loads((bucket / "state.json").read_text(encoding="utf-8"))
        assert estado["classification"] == "L1-feature"


class TestPortaoDoStop:
    def _task_l1(self, root: Path, cwd: Path, session_id: str = "s-stop"):
        bucket = ensure_state_dir(root, cwd, session_id=session_id)
        database = HarnessDatabase(bucket)
        task = database.start_task(
            scope_id=f"{session_id}|dir|x", legacy_level="L1-feature",
            tier="L1", kind="feature", pipeline=["write-spec-light", "tdd", "verify-against-spec"],
            prompt="apresentacao",
        )
        (bucket / "state.json").write_text(
            json.dumps({"task_id": task["task_id"], "scope_id": task["scope_id"]}),
            encoding="utf-8",
        )
        return database, task

    def _stop(self, root: Path, cwd: Path, session_id: str = "s-stop") -> str:
        return hook.handle_payload(
            {"hook_event_name": "Stop", "cwd": str(cwd), "session_id": session_id},
            harness_root=root,
        )

    def test_task_ja_promovida_fora_de_repositorio_nao_bloqueia(self, tmp_path):
        """A task que a sessao PPEGPS ja carregava antes do conserto."""
        pasta = tmp_path / "ppegps"
        pasta.mkdir()
        root = tmp_path / "harness"
        database, task = self._task_l1(root, pasta)
        database.touch_files(task["task_id"], [str(pasta / "roteiro.md")], origem="edit")
        database.touch_files(task["task_id"], ["shell-command"], origem="shell-placeholder")

        assert self._stop(root, pasta) == ""

    def test_pasta_mae_que_tocou_repositorio_continua_bloqueando(self, tmp_path):
        mae = tmp_path / "projects"
        repo = _repo(mae / "x")
        root = tmp_path / "harness"
        database, task = self._task_l1(root, mae)
        database.touch_files(task["task_id"], [str(repo / "src" / "a.py")], origem="edit")

        assert json.loads(self._stop(root, mae))["decision"] == "block"

    def test_contraste_cwd_num_repositorio_continua_bloqueando(self, tmp_path):
        repo = _repo(tmp_path / "repo")
        root = tmp_path / "harness"
        self._task_l1(root, repo)

        assert json.loads(self._stop(root, repo))["decision"] == "block"
