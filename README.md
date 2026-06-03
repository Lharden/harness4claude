# Harness v3 SDD

Spec-Driven Development orchestrator for Claude Code.

Harness v3 SDD brings structured, specification-first development pipelines to Claude Code.
It classifies every prompt by complexity, routes it through the appropriate pipeline, and
ensures implementations match their specs before you ship.

---

## What It Does

- Classifies prompts as L0 (trivial), L1 (moderate), or L2 (complex) via bilingual keyword analysis
- Routes L1/L2 prompts through structured SDD pipelines with quality gates
- Generates formal specs with prioritized user stories (P1/P2/P3), acceptance criteria (Given/When/Then), and boundary definitions (ALWAYS/NEVER/ASK)
- Challenges specs via adversarial review (`grill-me`) to surface gaps before implementation
- Verifies implementation against the spec with evidence-based coverage reports

---

## Pipelines

| Level | Type | Pipeline |
|-------|------|----------|
| L0 | any | Direct execution (no pipeline) |
| L1 | feature | `write-spec-light` -> `brainstorming` -> `tdd` -> `verify-against-spec` |
| L1 | bug | `systematic-debugging` -> `tdd` -> `verify-against-spec` |
| L2 | feature | `discuss` -> `write-spec` -> `grill-me` -> `design-doc` -> `validate-plan` -> `brainstorming` -> `tdd` -> `verify-against-spec` |
| L2 | architecture | `discuss` -> `write-spec` -> `grill-me` -> `design-doc` -> `validate-plan` -> `verify-against-spec` |
| L2 | refactor | `discuss` -> `write-spec` -> `grill-me` -> `validate-plan` -> `tdd` -> `verify-against-spec` |

Each pipeline ends with `verify-against-spec`, which ensures nothing ships without
evidence that the implementation satisfies every requirement in the spec.

---

## Install

Add the plugin to your Claude Code `settings.json` under `extraKnownMarketplaces`:

```json
{
  "extraKnownMarketplaces": {
    "harness4claude": {
      "source": {
        "source": "github",
        "repo": "Lharden/harness4claude"
      }
    }
  }
}
```

Then install from the CLI:

```
/plugin install harness
```

Verify the installation:

```
/plugin list
```

You should see `harness4claude` in the output with status `installed`.

---

## First Run

On first launch, the `SessionStart` hook automatically:

1. Creates the state directory at `~/.claude/harness/` if it does not exist
2. Initializes `state.json` and `signals.json` with default values
3. Checks for required dependencies (Python, jq, pytest)
4. Installs pip requirements from `requirements.txt` if missing

If anything goes wrong, run the diagnostic script:

```bash
bash /path/to/harness4claude/scripts/health-check.sh
```

This reports the status of every dependency, file, and hook registration.

Example output:

```
[OK] Python 3.12.4
[OK] jq 1.7.1
[OK] pytest 8.2.0
[OK] state.json exists
[OK] signals.json exists
[OK] classify hook registered
```

---

## System Requirements

| Dependency | Minimum Version | Install (Windows) | Install (macOS) | Install (Linux) |
|------------|----------------|--------------------|-----------------|-----------------|
| Python | 3.10+ | `winget install Python.Python.3.12` | `brew install python@3.12` | `apt install python3` |
| jq | 1.7+ | `winget install jqlang.jq` | `brew install jq` | `apt install jq` |
| pytest | 7+ | `pip install pytest` | `pip install pytest` | `pip install pytest` |

---

## Plugin Dependencies

| Plugin | Skills Used | Status |
|--------|------------|--------|
| [superpowers](https://github.com/anthropics/claude-code-superpowers) | brainstorming, test-driven-development, systematic-debugging | Required |
| [autoresearch](https://github.com/anthropics/claude-code-autoresearch) | debug, fix, ship, security | Recommended |
| [hookify](https://github.com/anthropics/claude-code-hookify) | 9 Python quality rules (bare-except, star-import, mutable-default, etc.) | Recommended |

The `superpowers` plugin is mandatory -- Harness v3 delegates brainstorming, TDD, and debugging
phases to it. The other plugins enhance the pipeline but are not strictly required.

---

## Skills Included

| Skill | Description |
|-------|-------------|
| `harness-workflow` | Core orchestrator. Reads hook classification (L0/L1/L2), selects the correct pipeline, manages state transitions, and records metrics. |
| `write-spec` | Generates a full formal spec: user stories with P1/P2/P3 priority, acceptance criteria in Given/When/Then format, boundary markers, and `[NEEDS CLARIFICATION]` flags. |
| `write-spec-light` | Lightweight spec (~50 lines) for L1 pipelines. Captures objective, requirements, acceptance criteria, and minimal boundaries with ~2 minutes of human overhead. |
| `design-doc` | Produces a technical design document from an approved spec, covering architecture, data model, API contracts, test strategy, and risks. Used in L2 pipelines between grill-me and validate-plan. |
| `verify-against-spec` | Item-by-item verification of implementation against spec. Every requirement, AC, user story, boundary, and success criterion is checked with concrete evidence (test, file, log). Outputs a gap report. |
| `grill-me` | Adversarial spec review. Challenges the spec from multiple angles to surface missing edge cases, ambiguous requirements, and unstated assumptions before implementation begins. |
| `discuss` | Upstream alignment phase. Captures user decisions in three tiers -- Locked, Deferred, and Discretion -- before planning. Generates `docs/CONTEXT.md` that constrains all downstream steps. |
| `validate-plan` | Pre-execution plan validation. Checks that the implementation plan covers all requirements from CONTEXT.md and the spec. Detects gaps, broken dependencies, and scope drift. Auto-revises up to 2 times. |
| `security-scan-python` | On-demand Python security scan using `bandit` (SAST) and `pip-audit` (dependency CVEs). Free, no token/login. Triggered only when asked ("scan de segurança", "run bandit", "check vulnerabilities"). `bandit`/`pip-audit` are auto-installed via `requirements.txt` on bootstrap. |

---

## Architecture

```
                         +------------------+
                         |   User Prompt    |
                         +--------+---------+
                                  |
                                  v
                     +------------+------------+
                     |  PreToolUse Classify     |
                     |  (bilingual keywords)    |
                     +------------+------------+
                                  |
                    +-------------+-------------+
                    |             |              |
                    v             v              v
                  [L0]         [L1]           [L2]
                Direct     Light pipeline   Full pipeline
                  |             |              |
                  |        spec-light     discuss -> spec
                  |        brainstorm     grill-me -> design
                  |        tdd            validate -> brainstorm
                  |        verify         tdd -> verify
                  |             |              |
                  +------+------+--------------+
                         |
                         v
                  +------+------+
                  |  state.json |  ~/.claude/harness/
                  | signals.json|
                  +-------------+
```

**State management:** All runtime state lives in `~/.claude/harness/` (per-machine, not
per-project). Two files track execution:

- `state.json` -- Current pipeline step, classification level, timestamps, and metrics
- `signals.json` -- Inter-skill communication signals (e.g., spec-approved, plan-validated)

The classify hook injects a `<harness-classification>` tag into the conversation context.
The `harness-workflow` skill reads this tag to determine which pipeline to execute.

**Key design decisions:**

- Classification runs as a `PreToolUse` hook, so it fires before Claude acts on the prompt
- State is per-machine (not per-project) to allow cross-project pipeline continuity
- Skills communicate via `signals.json` rather than direct invocation, enabling loose coupling
- The orchestrator (`harness-workflow`) is the only skill that reads classification tags directly

---

## Testing

Run the full test suite:

```bash
cd /path/to/harness4claude && python -m pytest tests/ -v
```

Tests cover:

- Classification logic (keyword matching, bilingual support, edge cases)
- Pipeline routing (L0/L1/L2 with all sub-types)
- State machine transitions
- Signal propagation between skills
- Hook integration

To run a specific test module:

```bash
python -m pytest tests/test_classify.py -v
```

To run with coverage:

```bash
python -m pytest tests/ -v --cov=hooks --cov=scripts --cov-report=term-missing
```

---

## Auxiliary Skills & Hooks

These ship alongside the SDD pipeline and are independently useful:

### `compress-memory` skill

Safe compression for secondary memory files (recent.md, archive.md, today-*.md).
Inspired by [caveman-compress](https://github.com/JuliusBrussee/caveman) but with
hard blacklist for critical files (CLAUDE.md, MEMORY.md, specs, design docs,
verification reports). Refuses any file with spec markers (Given/When/Then,
[NEEDS CLARIFICATION], REQ-###). Always creates `.original.md` backup.

```bash
python skills/compress-memory/compress.py path/to/recent.md --dry-run
python skills/compress-memory/compress.py path/to/recent.md --stats
```

Typical savings: 30-50% on prose-heavy memory files. Code, URLs, paths,
versions, dates, headings, tables, and frontmatter are preserved verbatim.

### `state-lock` (file mutex for `state.json`)

Cooperative `mkdir`-based mutex protecting `~/.claude/harness/state.json`
from concurrent read-modify-write across parallel sessions. Required when
running multiple Claude Code Desktop App windows simultaneously, since all
sessions share the same singleton state file.

Hooks that mutate state (`harness-classify.sh`, `harness-reclassify.sh`)
acquire the lock before touching `state.json` and release it on EXIT via
`trap`. Stale locks are auto-removed after 30 seconds (configurable via
`STATE_LOCK_STALE_SECS`).

Design choices:

- **Cross-platform**: `mkdir` is atomic on every common filesystem,
  including NTFS via Git Bash. No `flock` dependency.
- **Fail-closed**: on lock timeout (5s default), the hook exits silently
  rather than classifying with stale data. Worst case: the user loses one
  pipeline turn. Best case: zero state corruption.
- **Owner-aware**: `release_state_lock` only removes the lockdir if the
  current PID matches the owner recorded in `state.json.lockdir/owner`.
  Prevents one process from accidentally releasing another's lock.

CLI for manual use and testing:

```bash
bash scripts/state-lock.sh acquire   # exit 0 on success, 1 on timeout
bash scripts/state-lock.sh release
bash scripts/state-lock.sh is-locked # exit 0 if locked, 1 if free
bash scripts/state-lock.sh age-secs  # mtime age of the lockdir
```

Validated under stress: 10 concurrent workers each performing
read-modify-write on `state.json` produce exactly 10 increments and 10
unique writer IDs — zero lost updates, zero JSON corruption.

### `context7-trigger` hook

UserPromptSubmit hook that detects mentions of libraries, frameworks, SDKs,
or APIs in user prompts and injects a `[context7-hint]` reminder to consult
[Context7 MCP](https://github.com/upstash/context7) before generating
library-specific code. Conservative keyword list (~120 entries) plus verb
patterns ("how to install X", "docs of Y") to avoid false positives.

Wire it in your `~/.claude/settings.json`:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "${CLAUDE_PLUGIN_ROOT}/hooks/context7-trigger.sh",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

The hook is silent for refactor/debug prompts and skips itself when the
prompt already references context7 tools (no echo loop).

---

## Configuration

The plugin works out of the box with sensible defaults. Advanced users can customize
behavior by editing `~/.claude/harness/state.json` directly or by setting fields in the
plugin's CLAUDE.md overrides.

| Setting | Location | Default | Description |
|---------|----------|---------|-------------|
| Classification language | classify hook | `bilingual` | Keyword matching language (`en`, `pt`, `bilingual`) |
| Auto-install deps | SessionStart hook | `true` | Whether to pip-install missing requirements on start |
| Max grill-me rounds | signals.json | `3` | Maximum adversarial review iterations before auto-approve |
| Verify strictness | verify-against-spec | `strict` | `strict` requires evidence for every AC; `lenient` allows partial coverage |

---

## Multi-Machine Setup

Harness is designed to run across **multiple machines** (e.g. Claude Code on a
desktop and a laptop). Runtime state lives in `~/.claude/harness/` and is
**per-machine, not per-project or synced** — each machine keeps its own
`state.json` and `signals.json`. There is no central store and none is needed:
specs and design docs live in each project's `./docs/`, which travels with the
repo via git.

What ports automatically vs. what each machine needs once:

| Concern | Travels with the plugin? | Action per machine |
|---------|--------------------------|--------------------|
| Hooks, skills, scripts, schemas | ✅ Yes (plugin install) | `/plugin install harness` |
| `state.json` / `signals.json` | ❌ No (per-machine, by design) | Created/migrated automatically on first `SessionStart` |
| Schema version (v2 → v3) | n/a | **Auto-migrated** — the `SessionStart` hook runs `migrate_state.py` when it detects pre-v3 files, creating a timestamped `.bak-migrate-*` backup |
| Obsidian `vault-bridge` MCP server | ❌ No (MCP config is host-local) | Configure the MCP server + Obsidian app plugins manually (see below) |

### Upgrading an existing install to v3.1

On any machine that ran a previous Harness version, the v2 `signals.json` is
**migrated to v3 automatically** the next time a session starts — no manual step.
The hook only migrates when something is actually below v3 (idempotent, gated on
a version check) and always writes a `*.bak-migrate-<timestamp>` backup first.

To migrate manually (or to preview):

```bash
python scripts/migrate_state.py            # migrate in place, with backups
python scripts/migrate_state.py --dry-run  # report only, no writes
```

### Obsidian (vault-bridge) per machine

The `vault_sync.py` script and the precompact auto-sync ship with the plugin, but
the `vault-bridge` **MCP server configuration and the Obsidian app plugins are
host-local** and are intentionally kept out of this repo. On each machine where
you want vault sync, configure the MCP server in your `~/.claude/settings.json`
and point it at that machine's vault path. Without this config, the rest of the
pipeline still works — vault sync simply no-ops.

---

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feat/my-feature`)
3. Run tests before submitting (`python -m pytest tests/ -v`)
4. Open a pull request against `main`

---

## License

[MIT](LICENSE)
