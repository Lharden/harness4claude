import importlib.util
import json
import os
from pathlib import Path

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])


def _load(name: str, relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


state = _load("transactional_hook_state", "scripts/transactional_state.py")
paths = _load("transactional_hook_paths", "scripts/harness_paths.py")
hook = _load("transactional_hook", "hooks/harness-transactional.py")


def _active_task(root: Path, cwd: Path, session_id: str = "session-a"):
    bucket = paths.ensure_state_dir(root, cwd, session_id=session_id)
    database = state.HarnessDatabase(bucket)
    task = database.start_task(
        scope_id=f"{session_id}|repo|worktree",
        legacy_level="L1-bug",
        tier="L1",
        kind="bug",
        pipeline=["verify"],
        prompt="fix",
    )
    (bucket / "state.json").write_text(
        json.dumps({"task_id": task["task_id"], "scope_id": task["scope_id"]}),
        encoding="utf-8",
    )
    return bucket, database, task


def _payload(event: str, cwd: Path, **extra):
    return {
        "hook_event_name": event,
        "cwd": str(cwd),
        "session_id": "session-a",
        **extra,
    }


def test_atomic_test_command_records_fresh_evidence(tmp_path: Path):
    cwd = tmp_path / "repo"
    cwd.mkdir()
    bucket, database, task = _active_task(tmp_path / "harness", cwd)

    output = hook.handle_payload(
        _payload(
            "PostToolUse",
            cwd,
            tool_name="Bash",
            tool_input={"command": "python -m pytest -q"},
            tool_response={"exit_code": 0, "output": "3 passed"},
        ),
        harness_root=tmp_path / "harness",
    )

    assert output == ""
    assert database.task(task["task_id"])["verified"] is True
    assert json.loads((bucket / "state.json").read_text(encoding="utf-8"))["verified"] is True


def test_composed_test_command_cannot_record_evidence(tmp_path: Path):
    cwd = tmp_path / "repo"
    cwd.mkdir()
    _, database, task = _active_task(tmp_path / "harness", cwd)

    hook.handle_payload(
        _payload(
            "PostToolUse",
            cwd,
            tool_name="Bash",
            tool_input={"command": "python -m pytest --bad; echo '1 passed'"},
            tool_response={"exit_code": 0, "output": "1 passed"},
        ),
        harness_root=tmp_path / "harness",
    )

    assert database.task(task["task_id"])["verified"] is False


def test_stop_blocks_twice_then_opens_escalation_gate(tmp_path: Path):
    cwd = tmp_path / "repo"
    cwd.mkdir()
    _, database, task = _active_task(tmp_path / "harness", cwd)
    payload = _payload("Stop", cwd)

    first = json.loads(hook.handle_payload(payload, harness_root=tmp_path / "harness"))
    second = json.loads(hook.handle_payload(payload, harness_root=tmp_path / "harness"))
    third = json.loads(hook.handle_payload(payload, harness_root=tmp_path / "harness"))

    assert first["decision"] == second["decision"] == third["decision"] == "block"
    current = database.task(task["task_id"])
    assert current["status"] == "awaiting_gate"
    assert current["pending_gate"] == "escalation"
    assert hook.handle_payload(payload, harness_root=tmp_path / "harness") == ""


def test_stop_allows_freshly_verified_task_and_avoids_recursion(tmp_path: Path):
    cwd = tmp_path / "repo"
    cwd.mkdir()
    _, database, task = _active_task(tmp_path / "harness", cwd)
    database.record_evidence(
        task["task_id"], evidence_type="test", command="pytest", exit_code=0,
        tests_collected=1, tests_passed=1, output_hash="ok",
    )

    assert hook.handle_payload(
        _payload("Stop", cwd), harness_root=tmp_path / "harness"
    ) == ""
    assert hook.handle_payload(
        _payload("Stop", cwd, stop_hook_active=True), harness_root=tmp_path / "harness"
    ) == ""


def test_hook_manifest_wires_transactional_handler_to_post_tool_and_stop():
    manifest = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]

    post_commands = [item["command"] for group in manifest["PostToolUse"] for item in group["hooks"]]
    stop_commands = [item["command"] for group in manifest["Stop"] for item in group["hooks"]]

    assert any("harness-transactional.py" in command and "PostToolUse" in command for command in post_commands)
    assert any("harness-transactional.py" in command and "Stop" in command for command in stop_commands)


def test_state_cli_nao_invalida_a_evidencia(tmp_path: Path):
    """Rodar o CLI de estado nao e alteracao de codigo.

    `_handle_post_tool` trata todo comando de shell como possivel alteracao e
    chama `touch_file`, que zera `verified` e sobe `code_revision`. A
    heuristica e certa para `sed -i` ou `npm install` — e criava um deadlock
    estrutural: `state_cli.py complete` so pode ser invocado por shell, e a
    invocacao invalidava, no mesmo PostToolUse, a evidencia que o `complete`
    exige. Nenhuma task podia ser concluida pelo caminho previsto.

    Medido em 2026-09-02 nesta maquina: `code_revision` foi 501 -> 507 -> 511
    entre gravar a evidencia e tentar fechar, sem uma linha de codigo mudar.
    """
    cwd = tmp_path / "repo"
    cwd.mkdir()
    _, database, task = _active_task(tmp_path / "harness", cwd)

    database.record_evidence(
        task["task_id"], evidence_type="test", command="python -m pytest -q",
        exit_code=0, tests_collected=10, tests_passed=10, output_hash=None,
    )
    antes = database.task(task["task_id"])
    assert antes["verified"] is True

    hook.handle_payload(_payload(
        "PostToolUse", cwd, tool_name="Bash",
        tool_input={"command": 'python "/plugin/scripts/state_cli.py" --home /h complete --task t-1 --expect-revision 3'},
    ), harness_root=tmp_path / "harness")

    depois = database.task(task["task_id"])
    assert depois["verified"] is True, "o CLI de estado invalidou a evidencia"
    assert depois["code_revision"] == antes["code_revision"]


def test_comando_comum_continua_invalidando(tmp_path: Path):
    """Contraste: a protecao original nao pode ter sido afrouxada em geral."""
    cwd = tmp_path / "repo"
    cwd.mkdir()
    _, database, task = _active_task(tmp_path / "harness", cwd)
    database.record_evidence(
        task["task_id"], evidence_type="test", command="python -m pytest -q",
        exit_code=0, tests_collected=10, tests_passed=10, output_hash=None,
    )
    assert database.task(task["task_id"])["verified"] is True

    hook.handle_payload(_payload(
        "PostToolUse", cwd, tool_name="Bash",
        tool_input={"command": "sed -i s/a/b/ src/app.py"},
    ), harness_root=tmp_path / "harness")
    assert database.task(task["task_id"])["verified"] is False


def test_isencao_nao_vale_com_composicao_de_shell(tmp_path: Path):
    """`state_cli.py ... && sed -i ...` altera codigo na segunda metade."""
    cwd = tmp_path / "repo"
    cwd.mkdir()
    _, database, task = _active_task(tmp_path / "harness", cwd)
    database.record_evidence(
        task["task_id"], evidence_type="test", command="python -m pytest -q",
        exit_code=0, tests_collected=10, tests_passed=10, output_hash=None,
    )
    hook.handle_payload(_payload(
        "PostToolUse", cwd, tool_name="Bash",
        tool_input={"command": "python scripts/state_cli.py --home /h complete && sed -i s/a/b/ x.py"},
    ), harness_root=tmp_path / "harness")
    assert database.task(task["task_id"])["verified"] is False
