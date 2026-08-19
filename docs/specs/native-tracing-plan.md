---
type: spec
status: draft
project: harness4claude
created: 2026-07-18
updated: 2026-08-12
applies_to:
  - scripts/harness_trace.py
  - scripts/record_signal.py
  - scripts/health-check.sh
  - schemas/harness-trace-v1.schema.json
  - hooks/harness-classify.sh
  - hooks/harness-reclassify.sh
  - hooks/harness-session-start.sh
  - skills/harness-workflow/SKILL.md
  - skills/harness-workflow/references/trace-format.md
  - tests/test_harness_trace.py
  - tests/test_trace_*.py
  - tests/test_record_signal.py
---

# Native tracing — plano de implementação (não executado)

> **Proveniência.** Este plano foi escrito em 2026-07-18 numa worktree de
> `feat/native-tracing` e vivia em `.superpowers/sdd/`, que é ignorado pelo git. A
> branch acumulou **zero commits**: as quatro tarefas estavam pendentes e nenhuma foi
> executada. A worktree foi removida em 2026-08-12; o plano entrou no repositório para
> não sumir com ela.
>
> **Por que ainda importa.** Duas evidências de 2026-08-12 dizem que o problema que este
> plano ataca continua aberto:
>
> - `~/.claude/harness/traces/` tem **0 arquivos**. O espelhamento para o vault existe e
>   não tem o que espelhar.
> - `classification_meta.agreed` está `null` em **27 de 27** tarefas do histórico
>   ([#14](https://github.com/Lharden/harness4claude/issues/14)), e 24 delas foram
>   ceifadas pelo TTL ([#15](https://github.com/Lharden/harness4claude/issues/15)).
>
> São o mesmo padrão: instrumentação declarada que não produz dado. A Task 2 deste plano
> — instrumentar hooks e a gravação terminal de sinal — é exatamente o que as duas issues
> pedem.
>
> **Estado.** Rascunho de julho, não revalidado. As referências `arquivo:linha` apontam
> para o código daquela data e a `main` mudou bastante desde então — trate como intenção
> de desenho, não como instrução executável.

## Situação declarada em julho


# Harness4Claude native tracing SDD progress

- Task 1: pending
- Task 2: pending
- Task 3: pending
- Task 4: pending


---

### Task 1: Add the contract and standard-library trace runtime

**Files:**
- Create: `schemas/harness-trace-v1.schema.json`
- Create: `tests/fixtures/harness-trace-v1.json`
- Create: `scripts/harness_trace.py`
- Create: `tests/test_harness_trace.py`

**Interfaces:**
- Consumes: approved `harness.trace.v1` design.
- Produces: `build_event`, `validate_event`, `TraceWriter`, `read_events`, `validate_timeline`, `summarize_events`, and `main(argv=None)`.

- [ ] **Step 1: Copy the canonical contract and verify its bytes**

Copy the schema and fixture content exactly from Task 1 of the Harness4Codex plan. Before implementation, run:

```powershell
$codexRoot = "C:\Users\LHarden2\Documents\Codex\2026-07-17\estou-tentando-montar-um-fluxograma\work\worktrees\harness4codex-native-tracing"
$codexSchema = Join-Path $codexRoot "schemas\harness-trace-v1.schema.json"
$claudeSchema = ".\schemas\harness-trace-v1.schema.json"
$codexFixture = Join-Path $codexRoot "tests\fixtures\harness-trace-v1.json"
$claudeFixture = ".\tests\fixtures\harness-trace-v1.json"
New-Item -ItemType Directory -Force ".\schemas", ".\tests\fixtures" | Out-Null
Copy-Item -LiteralPath $codexSchema -Destination $claudeSchema
Copy-Item -LiteralPath $codexFixture -Destination $claudeFixture
(Get-FileHash $codexSchema).Hash -eq (Get-FileHash $claudeSchema).Hash
(Get-FileHash $codexFixture).Hash -eq (Get-FileHash $claudeFixture).Hash
```

Expected: both expressions print `True`. The committed fixture retains the `harness4codex`/`0.4.0` values in both repositories for byte-level conformance; runtime tests construct Harness4Claude-specific events separately.

- [ ] **Step 2: Write failing import, privacy, validation, and writer tests**

Create `tests/test_harness_trace.py` using the repository's importlib pattern:

```python
from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
TRACE_PATH = ROOT / "scripts" / "harness_trace.py"


@pytest.fixture(scope="module")
def trace_mod():
    spec = importlib.util.spec_from_file_location("harness_trace", TRACE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["harness_trace"] = module
    spec.loader.exec_module(module)
    return module


def test_build_event_excludes_sensitive_and_unknown_fields(trace_mod):
    event = trace_mod.build_event(
        "task.classified",
        harness="harness4claude",
        harness_version="3.3.0",
        status="completed",
        task_id="task-1",
        payload={
            "classification": "L2-feature",
            "source": "semantic",
            "prompt_excerpt": "private prompt",
            "token": "sk-secret",
        },
        now=datetime(2026, 7, 18, tzinfo=timezone.utc),
        event_id="event-1",
    )
    assert event["payload"] == {"classification": "L2-feature", "source": "semantic"}
    assert event["redacted_fields"] == 2
    assert trace_mod.validate_event(event) == []


def test_writer_is_fail_open(trace_mod, tmp_path, monkeypatch):
    writer = trace_mod.TraceWriter(tmp_path)
    event = trace_mod.build_event(
        "task.started", harness="harness4claude", harness_version="3.3.0",
        status="started", task_id="task-1",
    )
    def fail(_line):
        raise OSError("simulated write failure")
    monkeypatch.setattr(writer, "_append_locked", fail)
    assert writer.emit(event) is False


def test_cli_validate_reports_malformed_line(trace_mod, tmp_path, capsys):
    (tmp_path / "trace-events.jsonl").write_text("{broken\n", encoding="utf-8")
    assert trace_mod.main(["validate", "--harness-dir", str(tmp_path)]) == 1
    assert ":1:" in capsys.readouterr().out
```

- [ ] **Step 3: Run tests and observe the missing runtime**

```powershell
python -m pytest tests/test_harness_trace.py -q
```

Expected: fixture setup fails because `scripts/harness_trace.py` does not exist.

- [ ] **Step 4: Implement the event core with exact public constants**

Copy the completed core runtime from the fixed Codex worktree, then adapt its module defaults and path-facing names:

```powershell
$codexTrace = "C:\Users\LHarden2\Documents\Codex\2026-07-17\estou-tentando-montar-um-fluxograma\work\worktrees\harness4codex-native-tracing\harness4codex\trace.py"
Copy-Item -LiteralPath $codexTrace -Destination ".\scripts\harness_trace.py"
```

Retain the copied schema version, event types, statuses, payload allowlists, UTC validation, phase-parent pairing, terminal consistency, length bounds, secret pattern, rotation values, and owner-aware lock behavior. Set:

```python
HARNESS_NAME = "harness4claude"
HARNESS_VERSION = "3.3.0"
DEFAULT_HARNESS_DIR = Path.home() / ".claude" / "harness"
```

Keep these exact copied public interfaces: `build_event -> dict[str, Any]`, `validate_event(event: Mapping[str, Any]) -> list[str]`, `TraceWriter.__init__(harness_dir, *, max_bytes, rotations, lock_timeout)`, `TraceWriter.emit(event) -> bool`, `TraceWriter.has_event(task_id: str, event_types: set[str]) -> bool`, `read_events(harness_dir) -> tuple[list[dict[str, Any]], list[str]]`, `validate_timeline(events) -> list[str]`, and `summarize_events(events) -> dict[str, Any]`. Rename only the path parameter from `home` to `harness_dir` consistently. No method may read `state.json` implicitly except the CLI `emit` helper described below.

Change the copied `build_event` signature so `harness: str = HARNESS_NAME` and `harness_version: str = HARNESS_VERSION`; retain every other parameter name, type, and default. Change the copied default storage path to `DEFAULT_HARNESS_DIR`, and use it whenever the CLI receives no `--harness-dir` argument.

- [ ] **Step 5: Implement the restricted CLI**

`main(argv=None)` uses argparse with:

```text
emit EVENT_TYPE --status STATUS [--harness-dir PATH] [--task-id ID] [--phase PHASE] [--gate-type TYPE] [--verification passed|failed|blocked] [--files-modified N] [--steps-count N]
list [--harness-dir PATH]
show TASK_ID [--harness-dir PATH]
summary [--harness-dir PATH]
validate [--harness-dir PATH]
```

`emit` accepts only `phase.started`, `phase.completed`, `phase.failed`, `gate.waiting`, and `verification.completed`. It reads `state.json` only to fill a missing task ID, classification, and current step; it never reads `prompt_excerpt`. It constructs payload from the typed flags above and rejects arbitrary text or JSON.

Return codes: `0` success, `1` invalid trace/timeline, `2` invalid CLI request. `HARNESS_TRACE_MODE=off` returns `0` without creating a file.

- [ ] **Step 6: Add multi-process, rotation, off-mode, and deterministic summary tests**

Spawn ten `sys.executable -c` child processes that import `scripts/harness_trace.py` through the absolute file path and each emit one unique event into `tmp_path`. Assert all children exit `0`, ten unique IDs are present, every line parses, and no lock remains. Copy the Codex runtime cases for an unavailable directory, invalid trace mode, a stale lock whose current-PID owner is still live, a stale lock whose owner PID is absent, parent task/session/scope mismatch, phase pairing, conflicting terminal events, and full deterministic summary dimensions.

Use `TraceWriter(tmp_path, max_bytes=300, rotations=2)` to force rotation and assert no more than two numbered files. Set `HARNESS_TRACE_MODE=off`, emit, and assert no JSONL is created. Feed fixed fixture events to `summarize_events` and assert stable counts and incomplete-task IDs.

- [ ] **Step 7: Run runtime tests**

```powershell
python -m pytest tests/test_harness_trace.py -q
```

Expected: all tests pass and the active `~/.claude/harness` directory is unchanged.

- [ ] **Step 8: Commit the contract and runtime**

```powershell
git add schemas/harness-trace-v1.schema.json tests/fixtures/harness-trace-v1.json scripts/harness_trace.py tests/test_harness_trace.py
git commit -m "feat(trace): add native trace runtime"
```

---

---

### Task 2: Instrument hooks and terminal signal recording

**Files:**
- Modify: `hooks/harness-classify.sh:14-24,80-379`
- Modify: `hooks/harness-reclassify.sh:39-122`
- Modify: `hooks/harness-session-start.sh:116-148`
- Modify: `scripts/record_signal.py:23-151`
- Modify: `tests/test_record_signal.py`
- Create: `tests/test_trace_hooks.py`

**Interfaces:**
- Consumes: `build_event` and `TraceWriter` from Task 1.
- Produces: committed task start/classification/resume/reclassification/verification/terminal events.

- [ ] **Step 1: Write isolated hook lifecycle tests**

Create `tests/test_trace_hooks.py` with a helper that passes a temporary HOME and `HARNESS_DIR` to Git Bash:

```python
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
BASH = shutil.which("bash")
assert BASH


def run_hook(name: str, payload: dict, tmp_path: Path, env_extra: dict[str, str] | None = None):
    home = tmp_path / "home"
    harness_dir = home / ".claude" / "harness"
    harness_dir.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "HOME": str(home).replace(os.sep, "/"),
        "HARNESS_DIR": str(harness_dir).replace(os.sep, "/"),
        "CLAUDE_PLUGIN_ROOT": str(ROOT).replace(os.sep, "/"),
        "PYTHONUTF8": "1",
    }
    env.update(env_extra or {})
    proc = subprocess.run(
        [BASH, str(ROOT / "hooks" / name)],
        input=json.dumps(payload), text=True, capture_output=True, env=env, timeout=20,
    )
    return proc, harness_dir


def read_trace(harness_dir: Path):
    return [json.loads(line) for line in (harness_dir / "trace-events.jsonl").read_text(encoding="utf-8").splitlines()]


def test_classify_emits_metadata_only_start_and_classification(tmp_path):
    proc, harness_dir = run_hook("harness-classify.sh", {"user_prompt": "cria modulo auth"}, tmp_path)
    assert proc.returncode == 0
    events = read_trace(harness_dir)
    assert [event["event_type"] for event in events] == ["task.started", "task.classified"]
    assert "cria modulo auth" not in json.dumps(events)
```

Add cases for active `SessionStart` emitting `task.resumed`, third-file promotion emitting a second `task.classified` with source `file-count`, and `HARNESS_TRACE_MODE=off` leaving hook stdout and state unchanged.

- [ ] **Step 2: Confirm hooks produce no structured trace**

```powershell
python -m pytest tests/test_trace_hooks.py -q
```

Expected: tests fail because `trace-events.jsonl` is absent.

- [ ] **Step 3: Instrument classification after both atomic state writes**

In `harness-classify.sh`, export the scripts directory before the embedded Python:

```bash
export HARNESS_SCRIPTS_DIR="${HOOK_DIR_REL}/../scripts"
```

Inside the embedded Python, add `import sys` to the existing import block, then import `harness_trace` from that directory. Immediately after `_atomic_write_json(counter_file, counter)`, emit:

```python
sys.path.insert(0, os.environ["HARNESS_SCRIPTS_DIR"])
from harness_trace import TraceWriter, build_event

writer = TraceWriter(os.path.dirname(state_file))
writer.emit(build_event(
    "task.started", status="started", task_id=task_id,
    payload={"classification": classification, "pipeline_length": len(pipeline)},
))
writer.emit(build_event(
    "task.classified", status="completed", task_id=task_id,
    payload={"classification": classification, "source": "regex", "agreed": None},
))
```

Move the imports to the existing import block rather than duplicating them near the write. Do not pass `msg`, `prompt_excerpt`, paths, or input JSON.

- [ ] **Step 4: Instrument reclassification and resume**

In `harness-reclassify.sh`, expose the scripts directory and import the same module in the embedded Python. After the promoted state write, emit `task.classified` with `classification=new_class`, `source=file-count`, and `agreed=None`.

In `harness-session-start.sh`, pass the Harness directory and scripts directory to the final Python block. When the existing active-pipeline condition succeeds, emit `task.resumed` before printing the system message, with classification and current step only.

- [ ] **Step 5: Add failing `record_signal` terminal tests**

Extend `tests/test_record_signal.py` so a completed run with `--verification passed` creates `verification.completed` followed by `task.completed`; an abandoned run creates `task.abandoned`; an `emit` monkeypatch returning `False` still leaves `main() == 0` and `signals.json` valid.

- [ ] **Step 6: Extend `record_signal.py` with explicit verification semantics**

Import `TraceWriter` and `build_event`. Add:

```python
parser.add_argument(
    "--verification",
    choices=["passed", "failed", "blocked"],
    default=None,
    help="resultado explicito do gate final para o trace estruturado",
)
```

After the `record` call succeeds, emit `verification.completed` only when the flag is present, with status equal to the flag and payload `passed=args.verification == "passed"` plus `finding_count=0`. Then emit `task.completed(completed)` or `task.abandoned(abandoned)` with `steps_count`, `files_modified`, and a normalized `reason_code` that accepts only `user_switch`, `canceled`, `verification_blocked`, or `other`.

Use `TraceWriter.has_event` to avoid duplicate terminal events on an idempotent `record_signal.py` retry.

- [ ] **Step 7: Run hook and signal tests**

```powershell
python -m pytest tests/test_trace_hooks.py tests/test_record_signal.py -q
```

Expected: all new and existing tests pass; hook stdout contracts remain unchanged.

- [ ] **Step 8: Commit lifecycle integration**

```powershell
git add hooks/harness-classify.sh hooks/harness-reclassify.sh hooks/harness-session-start.sh scripts/record_signal.py tests/test_trace_hooks.py tests/test_record_signal.py
git commit -m "feat(trace): instrument claude lifecycle"
```

---

---

### Task 3: Make phases and gates explicit in the workflow skill

**Files:**
- Modify: `skills/harness-workflow/SKILL.md:22-37,56-113,241-279`
- Modify: `skills/harness-workflow/references/trace-format.md`
- Create: `tests/test_trace_skill_contract.py`

**Interfaces:**
- Consumes: restricted `scripts/harness_trace.py emit` CLI.
- Produces: documented, agent-executed phase, gate, verification, and terminal trace commands.

- [ ] **Step 1: Add failing skill-contract tests**

Create `tests/test_trace_skill_contract.py`:

```python
from pathlib import Path
import os

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])


def test_workflow_skill_requires_metadata_only_phase_and_gate_events():
    text = (ROOT / "skills" / "harness-workflow" / "SKILL.md").read_text(encoding="utf-8")
    assert "harness.trace.v1" in text
    assert "harness_trace.py emit phase.started" in text
    assert "harness_trace.py emit phase.completed" in text
    assert "harness_trace.py emit gate.waiting" in text
    assert "--verification passed" in text
    assert "prompt" not in text.split("## Operational tracing", 1)[1].split("## ", 1)[0].lower()
```

- [ ] **Step 2: Run the test and observe the missing protocol**

```powershell
python -m pytest tests/test_trace_skill_contract.py -q
```

Expected: assertions fail because the operational tracing section is absent.

- [ ] **Step 3: Add concrete phase and gate commands**

After the workflow protocol, add `## Operational tracing`. The controller assigns `HARNESS_TASK_ID`, `HARNESS_PHASE`, and `HARNESS_GATE_TYPE` from the active state and then uses these commands:

```bash
python ~/.claude/plugins/local/harness4claude/scripts/harness_trace.py emit phase.started --status started --task-id "$HARNESS_TASK_ID" --phase "$HARNESS_PHASE"
python ~/.claude/plugins/local/harness4claude/scripts/harness_trace.py emit phase.completed --status completed --task-id "$HARNESS_TASK_ID" --phase "$HARNESS_PHASE"
python ~/.claude/plugins/local/harness4claude/scripts/harness_trace.py emit phase.failed --status failed --task-id "$HARNESS_TASK_ID" --phase "$HARNESS_PHASE"
python ~/.claude/plugins/local/harness4claude/scripts/harness_trace.py emit gate.waiting --status waiting --task-id "$HARNESS_TASK_ID" --phase "$HARNESS_PHASE" --gate-type "$HARNESS_GATE_TYPE"
```

Require the command immediately after the corresponding state transition. A trace command failure is recorded in the final handoff but does not block or alter the pipeline.

- [ ] **Step 4: Update finalization commands**

Change successful completion examples to include `--verification passed`. Gap-closure exhaustion uses `--verification blocked` only when recording abandonment or escalation. Preserve `--expect-task` on every completion command.

- [ ] **Step 5: Reconcile Markdown and structured traces**

Rewrite `references/trace-format.md` to state:

- `trace-current.md` remains the concise human handoff surface;
- `trace-events.jsonl` is the machine-readable lifecycle source;
- Markdown may contain project detail under existing policy, while JSONL is metadata-only;
- phase and gate entries are emitted through `harness_trace.py`, never by editing JSONL;
- `validate` reports gaps but does not reconstruct state;
- rotation constants are 10 MiB and five files for JSONL; existing Markdown rotation remains unchanged.

- [ ] **Step 6: Run skill tests**

```powershell
python -m pytest tests/test_trace_skill_contract.py tests/test_workflow_returns.py -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit the workflow protocol**

```powershell
git add skills/harness-workflow/SKILL.md skills/harness-workflow/references/trace-format.md tests/test_trace_skill_contract.py
git commit -m "docs(trace): define workflow trace protocol"
```

---

---

### Task 4: Add health checks, documentation, and version alignment

**Files:**
- Modify: `scripts/health-check.sh:47-99`
- Modify: `README.md:11-32,139-190,190-216,324-379`
- Modify: `.claude-plugin/plugin.json`
- Create: `tests/test_trace_packaging.py`

**Interfaces:**
- Consumes: completed runtime and workflow integration.
- Produces: Harness4Claude `3.3.0` source ready for full verification and conformance.

- [ ] **Step 1: Add failing packaging and health-check tests**

Create `tests/test_trace_packaging.py` asserting:

```python
import json
from pathlib import Path
import os

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])


def test_trace_runtime_schema_and_version_ship_with_plugin():
    manifest = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "3.3.0"
    assert (ROOT / "scripts" / "harness_trace.py").is_file()
    assert (ROOT / "schemas" / "harness-trace-v1.schema.json").is_file()


def test_health_check_covers_trace_runtime_and_validation():
    text = (ROOT / "scripts" / "health-check.sh").read_text(encoding="utf-8")
    assert "harness_trace.py" in text
    assert "harness-trace-v1.schema.json" in text
    assert "trace-events.jsonl" in text
```

- [ ] **Step 2: Extend health checks without touching active content**

Change the health-check initialization from an unconditional assignment to `: "${HARNESS_DIR:=$HOME/.claude/harness}"`, so an isolated caller can override it. Add required-file checks for the runtime and schema. If `trace-events.jsonl` exists, run `python scripts/harness_trace.py validate --harness-dir "$HARNESS_DIR"`; if absent, report `[OK] no structured trace yet`. Validation failure sets `EXIT_CODE=1`. Do not create a trace from health-check.

- [ ] **Step 3: Update version and README**

Set `.claude-plugin/plugin.json` to `3.3.0`. Document event storage, privacy allowlists, CLI commands, lifecycle map, rotation, fail-open behavior, relationship to state/signals/Markdown trace, and the disabled-by-environment `HARNESS_TRACE_MODE=off` option.

Correct the architecture text to identify classification as `UserPromptSubmit`, matching `hooks/hooks.json`, while editing the same documentation section.

- [ ] **Step 4: Run focused packaging tests**

```powershell
python -m pytest tests/test_trace_packaging.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Run the complete source suite**

```powershell
python -m pytest tests/ -v
```

Expected: exit code `0`, no failed tests, and test logs demonstrate temporary Harness homes for every new trace test.

- [ ] **Step 6: Validate workflows and run health-check against a disposable home**

```bash
node scripts/workflows/validate_workflows.cjs
TRACE_SMOKE_ROOT="$(mktemp -d)"
export HARNESS_DIR="$TRACE_SMOKE_ROOT/harness"
mkdir -p "$HARNESS_DIR"
bash hooks/harness-session-start.sh </dev/null
bash scripts/health-check.sh
```

When executing on PowerShell/Git Bash, create and retain the temporary path in a task-specific variable before the commands; do not reuse `$HOME` or point health-check at the active Harness home.

- [ ] **Step 7: Commit source readiness**

```powershell
git add scripts/health-check.sh README.md .claude-plugin/plugin.json tests/test_trace_packaging.py
git commit -m "docs(trace): document and validate native tracing"
```

- [ ] **Step 8: Run the completion gate**

Invoke `superpowers:verification-before-completion`, then freshly rerun `python -m pytest tests/ -v`, `node scripts/workflows/validate_workflows.cjs`, and the disposable-home health check from Step 6. Confirm the feature worktree is clean after commits and that the original main-worktree dirty-file hashes still match the rollout baseline.

- [ ] **Step 9: Record handoff evidence**

Capture branch name, commits, pytest totals, workflow validation result, health-check result, and trace validation output. Do not merge into the dirty main worktree or install the plugin before the rollout gate.
