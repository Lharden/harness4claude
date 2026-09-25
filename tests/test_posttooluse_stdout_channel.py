"""PostToolUse nao entrega stdout cru ao modelo — so additionalContext.

## A fonte

https://code.claude.com/docs/en/hooks: "For most events, Claude Code writes
stdout to the debug log and doesn't show it in the transcript. The exceptions
are `UserPromptSubmit`, `UserPromptExpansion`, `SessionStart`, and
`PostModelSwitch`, where Claude Code adds plain-text stdout as context that
Claude can see and act on." `PostToolUse` NAO esta nessa lista de excecoes: o
stdout cru dele so alimenta o log de debug. O canal que a doc da para
"Add context for Claude" nesse evento e `hookSpecificOutput.additionalContext`
com `hookEventName: "PostToolUse"`.

`hooks/emit.py` afirmava o oposto (`CHANNEL_BY_EVENT["posttooluse"] = STDOUT`,
docstring: "additionalContext | NUNCA observado — nao usar"), medido por
observacao de transcript, nao pela doc. Dois hooks deste evento mandavam texto
destinado ao modelo por esse canal morto:

- `harness-transactional.py`, via `emit.py` com o canal errado no mapa;
- `harness-reclassify.sh`, com `print()` cru, nem passando por `emit.py`.

Este arquivo trava que nenhum dos dois volte a fazer isso.
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


emit = _load("posttooluse_channel_emit", "hooks/emit.py")
transactional = _load("posttooluse_channel_transactional", "hooks/harness-transactional.py")

sys.path.insert(0, str(ROOT / "scripts"))
from harness_paths import ensure_state_dir  # type: ignore[import-not-found]  # noqa: E402
from transactional_state import HarnessDatabase  # type: ignore[import-not-found]  # noqa: E402


def _stdout_cru(saida: str) -> bool:
    """True quando `saida` e o canal que o PostToolUse NAO entrega ao modelo.

    So `hookSpecificOutput.additionalContext` chega; qualquer outra coisa nao
    vazia — JSON sem essa chave, ou texto que nem e JSON — e stdout cru.
    """
    texto = saida.strip()
    if not texto:
        return False
    try:
        payload = json.loads(texto)
    except ValueError:
        return True
    bloco = payload.get("hookSpecificOutput") or {}
    return "additionalContext" not in bloco


class TestCanalDoEmissor:
    def test_posttooluse_usa_additional_context(self):
        assert emit.resolve_channel("PostToolUse") == "additionalContext"


class TestTransactionalNaoFalaPorStdoutCru:
    def _active_task(self, root: Path, cwd: Path, session_id: str = "s1") -> Path:
        bucket = ensure_state_dir(root, cwd, session_id=session_id)
        database = HarnessDatabase(bucket)
        task = database.start_task(
            scope_id=f"{session_id}|repo|worktree", legacy_level="L1-bug",
            tier="L1", kind="bug", pipeline=["verify"], prompt="fix",
        )
        (bucket / "state.json").write_text(
            json.dumps({"task_id": task["task_id"], "scope_id": task["scope_id"]}),
            encoding="utf-8",
        )
        return bucket

    def test_aviso_de_composicao_sai_por_additional_context(self, tmp_path, capsys):
        cwd = tmp_path / "repo"
        cwd.mkdir()
        root = tmp_path / "harness"
        self._active_task(root, cwd)

        payload = {
            "hook_event_name": "PostToolUse", "cwd": str(cwd), "session_id": "s1",
            "tool_name": "Bash",
            "tool_input": {"command": "python -m pytest -q 2>&1 | tail -12"},
        }
        aviso = transactional.handle_payload(payload, harness_root=root)
        assert "NAO gravada" in aviso, "fixture nao produziu o aviso de composicao esperado"

        transactional._emitir(payload, "PostToolUse", aviso)
        saida = capsys.readouterr().out
        assert not _stdout_cru(saida), (
            f"aviso saiu por stdout cru; PostToolUse nao entrega isso ao modelo: {saida!r}"
        )
        assert json.loads(saida)["hookSpecificOutput"]["additionalContext"] == aviso


@pytest.mark.skipif(BASH is None, reason="bash nao encontrado no PATH")
class TestReclassifyNaoFalaPorStdoutCru:
    def _run(self, payload: dict, harness_dir: Path):
        return subprocess.run(
            [BASH, str(RECLASSIFY)],
            input=json.dumps(payload), capture_output=True, text=True, timeout=15,
            env={**os.environ, "PYTHONUTF8": "1", "HARNESS_DIR": str(harness_dir)},
        )

    def test_reclassificacao_sai_por_additional_context(self, tmp_path):
        harness_dir = tmp_path / "harness"
        bucket = ensure_state_dir(harness_dir, None, session_id=None)
        (bucket / "state.json").write_text(json.dumps({
            "task_id": "t-canal", "classification": "L0-feature", "status": "done",
            "pipeline": [], "current_step": None, "artifacts_so_far": [],
        }), encoding="utf-8")
        (bucket / ".session-files-count").write_text(
            json.dumps({"count": 0, "files": [], "task_id": "t-canal"}), encoding="utf-8"
        )

        saida = ""
        for arquivo in ("a.py", "b.py", "c.py"):
            proc = self._run({"tool_name": "Edit", "tool_input": {"file_path": arquivo}}, harness_dir)
            assert proc.returncode == 0, proc.stderr
            saida = proc.stdout

        assert saida.strip(), "esperava sinal de promocao L0->L1 no toque do 3o arquivo"
        assert not _stdout_cru(saida), (
            f"reclassificacao saiu por stdout cru; PostToolUse nao entrega isso ao modelo: {saida!r}"
        )
