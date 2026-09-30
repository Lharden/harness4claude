"""Digest total para texto vindo do payload (surrogate solitario).

Defeito (2026-09-30): `session_id` ou `cwd` com surrogate solitario — escape JSON
valido (`"\\ud800"`), e realista em cwd no NTFS — derrubava o hook com
`UnicodeEncodeError` (exit 1) antes de tocar na task, porque o digest do slug
fazia `str.encode("utf-8")`.

Dois contratos, e os dois sao medidos:
  1. entrada com surrogate NAO levanta, e e deterministica e distinta da vizinha;
  2. entrada ja codificavel produz o MESMO slug de antes. Mudar o digest de uma
     entrada comum orfanaria todo balde ja gravado em disco.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
SCRIPTS = ROOT / "scripts"
HOOK = ROOT / "hooks" / "harness-transactional.py"

sys.path.insert(0, str(SCRIPTS))
import _escopo  # noqa: E402
import harness_paths  # noqa: E402


def _carrega(nome: str):
    spec = importlib.util.spec_from_file_location(f"_digest_{nome}", SCRIPTS / f"{nome}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


LONE = "\ud800"


class TestSessaoComSurrogate:
    def test_session_slug_nao_levanta_e_e_deterministico(self):
        a = harness_paths.session_slug("s" + LONE)
        assert a == harness_paths.session_slug("s" + LONE)
        assert a and a.startswith("s-")

    def test_session_slug_distingue_de_vizinha_sem_surrogate(self):
        assert harness_paths.session_slug("s" + LONE) != harness_paths.session_slug("s")

    def test_de_sessao_nao_levanta(self):
        a = _escopo.de_sessao("claude", "s" + LONE)
        assert a == _escopo.de_sessao("claude", "s" + LONE)
        assert a != _escopo.de_sessao("claude", "s")


class TestCwdComSurrogate:
    def test_de_caminho_nao_levanta_e_e_deterministico(self):
        a = _escopo.de_caminho("C:/tmp/r" + LONE, escopo_env="").id
        assert a == _escopo.de_caminho("C:/tmp/r" + LONE, escopo_env="").id

    def test_distingue_de_vizinho_sem_surrogate(self):
        assert (
            _escopo.de_caminho("C:/tmp/r" + LONE, escopo_env="").id
            != _escopo.de_caminho("C:/tmp/r", escopo_env="").id
        )


class TestSlugsComunsNaoMudam:
    """Golden: valores gerados pelo codigo de antes da correcao (utf-8 estrito)."""

    @pytest.mark.parametrize("sid,esperado", [
        ("86459dbf-1234", "86459dbf-1234-b34c03a4"),
        ("s", "s-043a7187"),
        ("sessão-ç", "sess-o-" + hashlib.sha256("sessão-ç".encode("utf-8")).hexdigest()[:8]),
    ])
    def test_session_slug(self, sid, esperado):
        assert harness_paths.session_slug(sid) == esperado

    def test_de_sessao(self):
        assert _escopo.de_sessao("claude", "abc-1") == "claude:abc-1-65397a5f"

    def test_de_caminho_usa_o_digest_utf8_do_caminho_normalizado(self):
        esc = _escopo.de_caminho("C:/Users/x/proj", escopo_env="")
        esperado = hashlib.sha256(os.path.normcase(esc.raiz).encode("utf-8")).hexdigest()[:8]
        assert esc.id == f"proj-{esperado}"

    @pytest.mark.skipif(sys.platform != "win32", reason="normcase do Windows")
    def test_de_caminho_golden_windows(self):
        assert _escopo.de_caminho("C:/Users/x/proj", escopo_env="").id == "proj-bcff3642"


class TestOutrosDigestsDoPayload:
    def test_hash_do_prompt_da_task_aceita_surrogate(self, tmp_path):
        state = _carrega("transactional_state")
        db = state.HarnessDatabase(tmp_path)
        task = db.start_task(
            scope_id="s|r|w", legacy_level="L1-feature", tier="L1", kind="feature",
            pipeline=["tdd"], prompt="ola" + LONE,
        )
        assert task["task_id"]

    def test_hash_do_prompt_comum_nao_muda(self, tmp_path):
        state = _carrega("transactional_state")
        db = state.HarnessDatabase(tmp_path)
        task = db.start_task(
            scope_id="s|r|w", legacy_level="L1-feature", tier="L1", kind="feature",
            pipeline=["tdd"], prompt="ola ç",
        )
        import sqlite3
        con = sqlite3.connect(str(Path(db.path)))
        try:
            (h,) = con.execute(
                "SELECT prompt_hash FROM tasks WHERE task_id = ?", (task["task_id"],)
            ).fetchone()
        finally:
            con.close()
        assert h == hashlib.sha256("ola ç".encode("utf-8")).hexdigest()

    def test_hash_do_bloco_entregue_aceita_surrogate(self, tmp_path):
        liveness = _carrega("check_hook_liveness")
        linha = json.dumps({
            "attachment": {"type": "hook_success", "content": "a\n\nb" + LONE},
        })
        arq = tmp_path / "t.jsonl"
        arq.write_text(linha + "\n", encoding="utf-8")
        assert isinstance(liveness._sha8_entregues(arq), set)


class TestHookEmProcesso:
    """Como o host roda: JSON no stdin, `--event PostToolUse`, exit code medido."""

    @pytest.mark.parametrize("cwd,sid", [
        ("C:/tmp/r" + LONE, "s"),
        ("C:/tmp/r", "s" + LONE),
    ])
    def test_posttooluse_nao_sai_com_1(self, tmp_path, cwd, sid):
        payload = json.dumps({
            "hook_event_name": "PostToolUse", "tool_name": "Bash",
            "tool_input": {"command": "echo ok"}, "cwd": cwd, "session_id": sid,
        })  # ensure_ascii=True -> o surrogate segue como escape `\ud800`
        env = {**os.environ, "HARNESS_DIR": str(tmp_path / "h")}
        res = subprocess.run(
            [sys.executable, str(HOOK), "--event", "PostToolUse"],
            input=payload.encode("ascii"), capture_output=True, env=env,
        )
        assert res.returncode == 0, res.stderr.decode("utf-8", "replace")[-600:]
