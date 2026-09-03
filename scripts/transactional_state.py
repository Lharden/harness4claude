from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class StateTransitionError(RuntimeError):
    pass


ACTIVE_STATUSES = ("suggested", "active", "awaiting_gate", "verified")
# Um desfecho ja registrado nao muda mais. Fora do indice de task unica e
# fora do alcance de qualquer evidencia que chegue depois.
TERMINAL_STATUSES = frozenset({"done", "abandoned", "superseded"})
HUMAN_GATES = {"approve-spec", "approve-plan", "answer-clarifications", "escalation", "branch-open"}
ARTIFACT_OBLIGATIONS = {
    "graph-context": "graph-context",
    "write-spec-light": "spec-light",
    "write-spec": "spec",
    "design-doc": "design",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class HarnessDatabase:
    def __init__(self, home: str | Path):
        self.home = Path(home)
        self.home.mkdir(parents=True, exist_ok=True)
        self.path = self.home / "harness.db"
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _ensure_schema(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS scopes (
                    scope_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    scope_id TEXT NOT NULL REFERENCES scopes(scope_id),
                    legacy_level TEXT NOT NULL,
                    tier TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    pipeline_json TEXT NOT NULL,
                    phase_index INTEGER NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 0,
                    code_revision INTEGER NOT NULL DEFAULT 0,
                    owner_epoch INTEGER NOT NULL DEFAULT 1,
                    verified INTEGER NOT NULL DEFAULT 0,
                    stop_continuations INTEGER NOT NULL DEFAULT 0,
                    prompt_hash TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_task_per_scope
                ON tasks(scope_id) WHERE status IN ('suggested', 'active', 'awaiting_gate', 'verified');
                CREATE TABLE IF NOT EXISTS classifications (
                    task_id TEXT PRIMARY KEY REFERENCES tasks(task_id),
                    suggested TEXT NOT NULL,
                    final TEXT,
                    source TEXT NOT NULL,
                    confidence REAL,
                    agreed INTEGER
                );
                CREATE TABLE IF NOT EXISTS files (
                    task_id TEXT NOT NULL REFERENCES tasks(task_id),
                    normalized_path TEXT NOT NULL,
                    first_seen_code_revision INTEGER NOT NULL,
                    PRIMARY KEY(task_id, normalized_path)
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                    task_id TEXT NOT NULL REFERENCES tasks(task_id),
                    artifact_type TEXT NOT NULL,
                    path TEXT NOT NULL,
                    content_hash TEXT,
                    phase TEXT,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(task_id, artifact_type, path)
                );
                CREATE TABLE IF NOT EXISTS evidence (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL REFERENCES tasks(task_id),
                    code_revision INTEGER NOT NULL,
                    evidence_type TEXT NOT NULL,
                    command TEXT,
                    exit_code INTEGER,
                    tests_collected INTEGER,
                    tests_passed INTEGER,
                    output_hash TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS gates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL REFERENCES tasks(task_id),
                    gate_type TEXT NOT NULL,
                    subject_id TEXT,
                    status TEXT NOT NULL,
                    decision TEXT,
                    created_at TEXT NOT NULL,
                    resolved_at TEXT
                );
                CREATE TABLE IF NOT EXISTS transitions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL REFERENCES tasks(task_id),
                    from_phase TEXT,
                    to_phase TEXT,
                    revision INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT,
                    scope_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS leases (
                    scope_id TEXT PRIMARY KEY REFERENCES scopes(scope_id),
                    owner_token TEXT NOT NULL,
                    owner_epoch INTEGER NOT NULL,
                    expires_at REAL NOT NULL,
                    heartbeat_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS branches (
                    branch_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES tasks(task_id),
                    scope_id TEXT NOT NULL REFERENCES scopes(scope_id),
                    slug TEXT NOT NULL,
                    name TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    topic_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    offered_turn INTEGER NOT NULL,
                    seed_path TEXT,
                    conclusion TEXT,
                    approved_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(task_id, slug),
                    UNIQUE(task_id, topic_hash)
                );
                """
            )
            task_columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(tasks)").fetchall()
            }
            if "stop_continuations" not in task_columns:
                connection.execute(
                    "ALTER TABLE tasks ADD COLUMN stop_continuations INTEGER NOT NULL DEFAULT 0"
                )
            gate_columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(gates)").fetchall()
            }
            if "subject_id" not in gate_columns:
                connection.execute("ALTER TABLE gates ADD COLUMN subject_id TEXT")
                connection.execute(
                    "UPDATE gates SET status = 'cancelled', decision = 'identity-migration', "
                    "resolved_at = ? WHERE gate_type = 'branch-open' AND status = 'pending' "
                    "AND subject_id IS NULL",
                    (utc_now(),),
                )
                connection.execute(
                    "INSERT INTO gates(task_id, gate_type, subject_id, status, created_at) "
                    "SELECT task_id, 'branch-open', branch_id, 'pending', ? FROM branches "
                    "WHERE approved_at IS NULL AND status = 'pending'",
                    (utc_now(),),
                )
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS one_pending_gate_per_subject "
                "ON gates(task_id, gate_type, subject_id) "
                "WHERE status = 'pending' AND subject_id IS NOT NULL"
            )

    def start_task(
        self,
        *,
        scope_id: str,
        legacy_level: str,
        tier: str,
        kind: str,
        pipeline: list[str],
        prompt: str,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        task_id = task_id or f"t-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
        status = "active" if pipeline else "done"
        with self._write() as connection:
            connection.execute("INSERT OR IGNORE INTO scopes(scope_id, created_at) VALUES (?, ?)", (scope_id, now))
            connection.execute(
                "UPDATE gates SET status = 'cancelled', decision = 'task-switch', resolved_at = ? "
                "WHERE status = 'pending' AND task_id IN ("
                "SELECT task_id FROM tasks WHERE scope_id = ? "
                "AND status IN ('suggested', 'active', 'awaiting_gate', 'verified'))",
                (now, scope_id),
            )
            # A task anterior sai do caminho, mas o que aconteceu com ela nao e
            # sempre a mesma coisa. Ate 2026-09-03 tudo virava 'abandoned',
            # inclusive task que tinha rodado a suite e passado: o prompt
            # seguinte do usuario chega antes de dar tempo de fechar o pipeline.
            # Medido nos harness.db da maquina: 6 abandonadas, 3 com verified=1.
            # Metade do "abandono" era trabalho concluido.
            #
            # 'superseded' fica FORA de `one_active_task_per_scope`, entao libera
            # o indice igual a 'abandoned' — sem mentir sobre o desfecho.
            connection.execute(
                "UPDATE tasks SET status = CASE WHEN verified = 1 THEN 'superseded' ELSE 'abandoned' END, "
                "revision = revision + 1, updated_at = ? "
                "WHERE scope_id = ? AND status IN ('suggested', 'active', 'awaiting_gate', 'verified')",
                (now, scope_id),
            )
            connection.execute(
                """
                INSERT INTO tasks(
                    task_id, scope_id, legacy_level, tier, kind, status, pipeline_json,
                    phase_index, revision, code_revision, owner_epoch, verified,
                    prompt_hash, started_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 1, 0, ?, ?, ?)
                """,
                (
                    task_id,
                    scope_id,
                    legacy_level,
                    tier,
                    kind,
                    status,
                    json.dumps(pipeline),
                    0 if pipeline else -1,
                    hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                    now,
                    now,
                ),
            )
            connection.execute(
                "INSERT INTO classifications(task_id, suggested, final, source, confidence, agreed) "
                "VALUES (?, ?, NULL, 'regex', NULL, NULL)",
                (task_id, f"{tier}-{kind}"),
            )
        return self.task(task_id)

    def classification(self, task_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT suggested, final, source, confidence, agreed FROM classifications WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        if row is None:
            raise StateTransitionError(f"classification not found for task: {task_id}")
        return {
            "suggested": row["suggested"],
            "final": row["final"],
            "source": row["source"],
            "confidence": row["confidence"],
            "agreed": None if row["agreed"] is None else bool(row["agreed"]),
        }

    def confirm_classification(
        self,
        task_id: str,
        *,
        tier: str,
        kind: str,
        pipeline: list[str],
        source: str,
        confidence: float,
    ) -> dict[str, Any]:
        if source not in {"semantic", "human_override"}:
            raise StateTransitionError(f"invalid classification source: {source}")
        if not 0 <= confidence <= 1:
            raise StateTransitionError("classification confidence must be between 0 and 1")
        final = f"{tier}-{kind}"
        with self._write() as connection:
            row = self._locked_task(connection, task_id)
            decision = connection.execute(
                "SELECT suggested FROM classifications WHERE task_id = ?", (task_id,)
            ).fetchone()
            if decision is None:
                raise StateTransitionError(f"classification not found for task: {task_id}")
            agreed = decision["suggested"] == final
            connection.execute(
                "UPDATE classifications SET final = ?, source = ?, confidence = ?, agreed = ? WHERE task_id = ?",
                (final, source, confidence, 1 if agreed else 0, task_id),
            )
            # `legacy_level` acompanha `tier-kind`. Deixa-lo para tras faz a linha
            # mentir para quem le a coluna: relatorio, auditoria e migracao
            # recebem o rotulo do regex que a correcao semantica descartou.
            # Medido em 2026-09-03: tier='L1', kind='bug', legacy_level='L1-feature'.
            connection.execute(
                """
                UPDATE tasks
                SET legacy_level = ?, tier = ?, kind = ?, pipeline_json = ?, phase_index = ?,
                    status = ?, verified = 0, revision = revision + 1, updated_at = ?
                WHERE task_id = ?
                """,
                (
                    final,
                    tier,
                    kind,
                    json.dumps(pipeline),
                    0 if pipeline else -1,
                    "active" if pipeline else "done",
                    utc_now(),
                    task_id,
                ),
            )
            connection.execute(
                "INSERT INTO transitions(task_id, from_phase, to_phase, revision, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    task_id,
                    self._phase(row),
                    pipeline[0] if pipeline else None,
                    int(row["revision"]) + 1,
                    utc_now(),
                ),
            )
        return self.task(task_id)

    def task(self, task_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
            if row is None:
                raise StateTransitionError(f"task not found: {task_id}")
            gate = connection.execute(
                "SELECT gate_type, subject_id FROM gates WHERE task_id = ? AND status = 'pending' "
                "ORDER BY id DESC LIMIT 1",
                (task_id,),
            ).fetchone()
            # `record_artifact` sempre gravou aqui corretamente, mas nada lia a
            # tabela de volta: a projecao `state.json` carregava
            # `artifacts_so_far: []` para sempre, e quem le o state — inclusive
            # o hook de lifecycle — via zero artefatos numa task que tinha
            # varios. O dado existia; faltava o caminho de volta.
            artifacts = [
                {"type": r["artifact_type"], "path": r["path"], "phase": r["phase"]}
                # `rowid` e nao `created_at`: o upsert de `record_artifact`
                # reescreve `created_at`, entao ordenar por ele faria o artefato
                # reaparecer no fim da lista a cada regravacao. `rowid` guarda a
                # ordem de primeira insercao, que e a ordem em que o trabalho
                # aconteceu. A tabela nao tem coluna `id` — a chave e composta.
                for r in connection.execute(
                    "SELECT artifact_type, path, phase FROM artifacts WHERE task_id = ? "
                    "ORDER BY rowid",
                    (task_id,),
                )
            ]
        pending_gate = None
        if gate:
            pending_gate = str(gate["gate_type"])
            if gate["subject_id"]:
                pending_gate += f":{gate['subject_id']}"
        return self._render_task(row, pending_gate, artifacts)

    def current_task(self, scope_id: str) -> dict[str, Any] | None:
        placeholders = ",".join("?" for _ in ACTIVE_STATUSES)
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT task_id FROM tasks WHERE scope_id = ? AND status IN ({placeholders}) "
                "ORDER BY started_at DESC LIMIT 1",
                (scope_id, *ACTIVE_STATUSES),
            ).fetchone()
        return self.task(str(row["task_id"])) if row else None

    def expire_stale_task(
        self,
        scope_id: str,
        *,
        ttl_seconds: float,
        now: float | None = None,
        expected_task_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Abandon the scoped non-terminal task after its pipeline TTL."""
        current_time = time.time() if now is None else float(now)
        ttl = max(float(ttl_seconds), 0.001)
        placeholders = ",".join("?" for _ in ACTIVE_STATUSES)
        expired_task_id: str | None = None
        with self._write() as connection:
            row = connection.execute(
                f"SELECT * FROM tasks WHERE scope_id = ? AND status IN ({placeholders}) "
                "ORDER BY started_at DESC LIMIT 1",
                (scope_id, *ACTIVE_STATUSES),
            ).fetchone()
            if row is None:
                return None
            if expected_task_id is not None and str(row["task_id"]) != expected_task_id:
                return None
            try:
                started = datetime.fromisoformat(str(row["started_at"]))
                if started.tzinfo is None:
                    started = started.replace(tzinfo=timezone.utc)
                expired = current_time - started.timestamp() > ttl
            except (TypeError, ValueError, OverflowError):
                expired = True
            if not expired:
                return None
            expired_task_id = str(row["task_id"])
            occurred_at = datetime.fromtimestamp(current_time, timezone.utc).isoformat()
            connection.execute(
                "UPDATE tasks SET status = 'abandoned', verified = 0, revision = revision + 1, "
                "updated_at = ? WHERE task_id = ?",
                (occurred_at, expired_task_id),
            )
            connection.execute(
                "UPDATE gates SET status = 'cancelled', decision = 'ttl-expired', resolved_at = ? "
                "WHERE task_id = ? AND status = 'pending'",
                (occurred_at, expired_task_id),
            )
            connection.execute(
                "INSERT INTO events(task_id, scope_id, event_type, payload_json, created_at) "
                "VALUES (?, ?, 'pipeline-expired', ?, ?)",
                (
                    expired_task_id,
                    scope_id,
                    json.dumps({"ttl_seconds": ttl}, sort_keys=True),
                    occurred_at,
                ),
            )
        return self.task(expired_task_id)

    def acquire_lease(
        self,
        scope_id: str,
        owner_token: str,
        *,
        ttl_seconds: float = 30.0,
        now: float | None = None,
    ) -> dict[str, Any]:
        current_time = time.time() if now is None else float(now)
        expires_at = current_time + max(float(ttl_seconds), 0.001)
        with self._write() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO scopes(scope_id, created_at) VALUES (?, ?)",
                (scope_id, utc_now()),
            )
            current = connection.execute(
                "SELECT owner_token, owner_epoch, expires_at FROM leases WHERE scope_id = ?",
                (scope_id,),
            ).fetchone()
            if current is None:
                epoch = 1
                connection.execute(
                    "INSERT INTO leases(scope_id, owner_token, owner_epoch, expires_at, heartbeat_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (scope_id, owner_token, epoch, expires_at, current_time),
                )
            elif current["owner_token"] == owner_token:
                epoch = int(current["owner_epoch"])
                connection.execute(
                    "UPDATE leases SET expires_at = ?, heartbeat_at = ? WHERE scope_id = ?",
                    (expires_at, current_time, scope_id),
                )
            elif float(current["expires_at"]) > current_time:
                raise StateTransitionError(
                    f"scope {scope_id} has an active lease owned by another writer"
                )
            else:
                epoch = int(current["owner_epoch"]) + 1
                connection.execute(
                    "UPDATE leases SET owner_token = ?, owner_epoch = ?, expires_at = ?, heartbeat_at = ? "
                    "WHERE scope_id = ?",
                    (owner_token, epoch, expires_at, current_time, scope_id),
                )
            connection.execute(
                "UPDATE tasks SET owner_epoch = ?, revision = revision + 1, updated_at = ? "
                "WHERE scope_id = ? AND status IN ('suggested', 'active', 'awaiting_gate', 'verified') "
                "AND owner_epoch <> ?",
                (epoch, utc_now(), scope_id, epoch),
            )
        return {
            "scope_id": scope_id,
            "owner_token": owner_token,
            "owner_epoch": epoch,
            "expires_at": expires_at,
        }

    def record_artifact(
        self,
        task_id: str,
        artifact_type: str,
        path: str,
        content_hash: str | None,
    ) -> dict[str, Any]:
        with self._write() as connection:
            row = self._locked_task(connection, task_id)
            phase = self._phase(row)
            connection.execute(
                """
                INSERT INTO artifacts(task_id, artifact_type, path, content_hash, phase, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id, artifact_type, path) DO UPDATE SET
                    content_hash = excluded.content_hash,
                    phase = excluded.phase,
                    created_at = excluded.created_at
                """,
                (task_id, artifact_type, path, content_hash, phase, utc_now()),
            )
            self._bump(connection, task_id)
        return self.task(task_id)

    def create_branch(
        self,
        task_id: str,
        *,
        branch_id: str,
        slug: str,
        name: str,
        topic: str,
        topic_hash: str,
        offered_turn: int,
        max_offers: int = 3,
        cooldown_turns: int = 8,
    ) -> dict[str, Any]:
        if max_offers < 1:
            raise StateTransitionError("branch offer limit must be positive")
        if cooldown_turns < 0:
            raise StateTransitionError("branch offer cooldown cannot be negative")
        now = utc_now()
        with self._write() as connection:
            task = self._locked_task(connection, task_id)
            offer_stats = connection.execute(
                "SELECT COUNT(*) AS count, MAX(offered_turn) AS last_turn FROM branches WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if int(offer_stats["count"]) >= max_offers:
                raise StateTransitionError("branch offer limit reached")
            if (
                offer_stats["last_turn"] is not None
                and offered_turn - int(offer_stats["last_turn"]) < cooldown_turns
            ):
                raise StateTransitionError("branch offer cooldown is active")
            connection.execute(
                """
                INSERT INTO branches(
                    branch_id, task_id, scope_id, slug, name, topic, topic_hash,
                    status, offered_turn, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
                """,
                (
                    branch_id,
                    task_id,
                    task["scope_id"],
                    slug,
                    name,
                    topic,
                    topic_hash,
                    offered_turn,
                    now,
                    now,
                ),
            )
            connection.execute(
                "INSERT INTO gates(task_id, gate_type, subject_id, status, created_at) "
                "VALUES (?, 'branch-open', ?, 'pending', ?)",
                (task_id, branch_id, now),
            )
            connection.execute(
                "UPDATE tasks SET status = 'awaiting_gate', revision = revision + 1, updated_at = ? WHERE task_id = ?",
                (now, task_id),
            )
        return self.branch(branch_id)

    def branch(self, branch_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM branches WHERE branch_id = ?", (branch_id,)).fetchone()
        if row is None:
            raise StateTransitionError(f"branch not found: {branch_id}")
        return dict(row)

    def list_branches(self, task_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM branches WHERE task_id = ? ORDER BY created_at, branch_id", (task_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    def approve_branch(self, branch_id: str) -> dict[str, Any]:
        return self.resolve_branch_decision(branch_id, "approve")

    def request_branch_approval(self, branch_id: str) -> dict[str, Any]:
        now = utc_now()
        with self._write() as connection:
            branch = connection.execute("SELECT * FROM branches WHERE branch_id = ?", (branch_id,)).fetchone()
            if branch is None:
                raise StateTransitionError(f"branch not found: {branch_id}")
            if branch["approved_at"]:
                return dict(branch)
            gate = connection.execute(
                "SELECT id FROM gates WHERE task_id = ? AND gate_type = 'branch-open' AND status = 'pending' "
                "AND subject_id = ? ORDER BY id DESC LIMIT 1",
                (branch["task_id"], branch_id),
            ).fetchone()
            if gate is None:
                connection.execute(
                    "INSERT INTO gates(task_id, gate_type, subject_id, status, created_at) "
                    "VALUES (?, 'branch-open', ?, 'pending', ?)",
                    (branch["task_id"], branch_id, now),
                )
                connection.execute(
                    "UPDATE tasks SET status = 'awaiting_gate', revision = revision + 1, "
                    "updated_at = ? WHERE task_id = ?",
                    (now, branch["task_id"]),
                )
        return self.branch(branch_id)

    def resolve_branch_decision(self, branch_id: str, decision: str) -> dict[str, Any]:
        if decision not in {"approve", "park", "discard"}:
            raise StateTransitionError(f"invalid branch decision: {decision}")
        now = utc_now()
        with self._write() as connection:
            branch = connection.execute("SELECT * FROM branches WHERE branch_id = ?", (branch_id,)).fetchone()
            if branch is None:
                raise StateTransitionError(f"branch not found: {branch_id}")
            gate = connection.execute(
                "SELECT id FROM gates WHERE task_id = ? AND gate_type = 'branch-open' AND status = 'pending' "
                "AND subject_id = ? ORDER BY id DESC LIMIT 1",
                (branch["task_id"], branch_id),
            ).fetchone()
            if gate is None:
                raise StateTransitionError("pending branch-open gate not found")
            connection.execute(
                "UPDATE gates SET status = 'resolved', decision = ?, resolved_at = ? WHERE id = ?",
                (decision, now, gate["id"]),
            )
            if decision == "approve":
                connection.execute(
                    "UPDATE branches SET approved_at = ?, updated_at = ? WHERE branch_id = ?",
                    (now, now, branch_id),
                )
            elif decision == "discard":
                connection.execute(
                    "UPDATE branches SET status = 'closed', conclusion = 'discarded', "
                    "updated_at = ? WHERE branch_id = ?",
                    (now, branch_id),
                )
            still_pending = connection.execute(
                "SELECT 1 FROM gates WHERE task_id = ? AND status = 'pending' LIMIT 1",
                (branch["task_id"],),
            ).fetchone()
            connection.execute(
                "UPDATE tasks SET status = ?, revision = revision + 1, updated_at = ? WHERE task_id = ?",
                ("awaiting_gate" if still_pending else "active", now, branch["task_id"]),
            )
        return self.branch(branch_id)

    def open_branch(self, branch_id: str, *, seed_path: str, max_open: int = 3) -> dict[str, Any]:
        if max_open < 1:
            raise StateTransitionError("open branch limit must be positive")
        with self._write() as connection:
            branch = connection.execute("SELECT * FROM branches WHERE branch_id = ?", (branch_id,)).fetchone()
            if branch is None:
                raise StateTransitionError(f"branch not found: {branch_id}")
            if not branch["approved_at"]:
                raise StateTransitionError("branch-open approval is required")
            open_count = connection.execute(
                "SELECT COUNT(*) AS count FROM branches WHERE task_id = ? AND status = 'open'",
                (branch["task_id"],),
            ).fetchone()
            if int(open_count["count"]) >= max_open:
                raise StateTransitionError("open branch limit reached")
            connection.execute(
                "UPDATE branches SET status = 'open', seed_path = ?, updated_at = ? WHERE branch_id = ?",
                (seed_path, utc_now(), branch_id),
            )
        return self.branch(branch_id)

    def update_branch(
        self,
        branch_id: str,
        *,
        status: str,
        seed_path: str | None = None,
        conclusion: str | None = None,
    ) -> dict[str, Any]:
        if status not in {"pending", "open", "parked", "recalled", "closed"}:
            raise StateTransitionError(f"invalid branch status: {status}")
        with self._write() as connection:
            branch = connection.execute("SELECT 1 FROM branches WHERE branch_id = ?", (branch_id,)).fetchone()
            if branch is None:
                raise StateTransitionError(f"branch not found: {branch_id}")
            connection.execute(
                "UPDATE branches SET status = ?, seed_path = COALESCE(?, seed_path), "
                "conclusion = COALESCE(?, conclusion), updated_at = ? WHERE branch_id = ?",
                (status, seed_path, conclusion, utc_now(), branch_id),
            )
        return self.branch(branch_id)

    def reclassify(
        self,
        task_id: str,
        *,
        legacy_level: str,
        tier: str,
        kind: str,
        pipeline: list[str],
    ) -> dict[str, Any]:
        with self._write() as connection:
            self._locked_task(connection, task_id)
            connection.execute(
                """
                UPDATE tasks
                SET legacy_level = ?, tier = ?, kind = ?, pipeline_json = ?,
                    phase_index = ?, status = ?, verified = 0,
                    revision = revision + 1, updated_at = ?
                WHERE task_id = ?
                """,
                (
                    legacy_level,
                    tier,
                    kind,
                    json.dumps(pipeline),
                    0 if pipeline else -1,
                    "active" if pipeline else "done",
                    utc_now(),
                    task_id,
                ),
            )
        return self.task(task_id)

    def open_gate(self, task_id: str, gate_type: str) -> dict[str, Any]:
        with self._write() as connection:
            self._locked_task(connection, task_id)
            pending = connection.execute(
                "SELECT 1 FROM gates WHERE task_id = ? AND gate_type = ? AND status = 'pending'",
                (task_id, gate_type),
            ).fetchone()
            if pending is None:
                connection.execute(
                    "INSERT INTO gates(task_id, gate_type, status, created_at) VALUES (?, ?, 'pending', ?)",
                    (task_id, gate_type, utc_now()),
                )
            connection.execute(
                "UPDATE tasks SET status = 'awaiting_gate', revision = revision + 1, updated_at = ? WHERE task_id = ?",
                (utc_now(), task_id),
            )
        return self.task(task_id)

    def transition(
        self,
        task_id: str,
        to_phase: str,
        *,
        expected_revision: int,
        owner_epoch: int | None = None,
    ) -> dict[str, Any]:
        with self._write() as connection:
            row = self._locked_task(connection, task_id)
            self._expect_revision(row, expected_revision)
            if owner_epoch is not None and int(row["owner_epoch"]) != owner_epoch:
                raise StateTransitionError(
                    f"owner epoch mismatch: expected {owner_epoch}, actual {row['owner_epoch']}"
                )
            pipeline = json.loads(row["pipeline_json"])
            current_index = int(row["phase_index"])
            next_index = current_index + 1
            if next_index >= len(pipeline) or pipeline[next_index] != to_phase:
                expected = pipeline[next_index] if next_index < len(pipeline) else "<terminal>"
                raise StateTransitionError(f"next phase must be {expected}, got {to_phase}")
            current_phase = pipeline[current_index]
            obligation = ARTIFACT_OBLIGATIONS.get(current_phase)
            if obligation and not self._has_artifact(connection, task_id, obligation):
                raise StateTransitionError(f"phase {current_phase} requires artifact {obligation}")
            new_revision = int(row["revision"]) + 1
            status = "awaiting_gate" if to_phase in HUMAN_GATES else "active"
            connection.execute(
                "UPDATE tasks SET phase_index = ?, status = ?, revision = ?, updated_at = ? WHERE task_id = ?",
                (next_index, status, new_revision, utc_now(), task_id),
            )
            connection.execute(
                "INSERT INTO transitions(task_id, from_phase, to_phase, revision, created_at) VALUES (?, ?, ?, ?, ?)",
                (task_id, current_phase, to_phase, new_revision, utc_now()),
            )
            if to_phase in HUMAN_GATES:
                connection.execute(
                    "INSERT INTO gates(task_id, gate_type, status, created_at) VALUES (?, ?, 'pending', ?)",
                    (task_id, to_phase, utc_now()),
                )
        return self.task(task_id)

    def resolve_gate(
        self,
        task_id: str,
        gate_type: str,
        decision: str,
        *,
        expected_revision: int,
    ) -> dict[str, Any]:
        if decision != "approve":
            raise StateTransitionError(f"unsupported gate decision: {decision}")
        with self._write() as connection:
            row = self._locked_task(connection, task_id)
            self._expect_revision(row, expected_revision)
            pending = connection.execute(
                "SELECT id FROM gates WHERE task_id = ? AND gate_type = ? AND status = 'pending' "
                "ORDER BY id DESC LIMIT 1",
                (task_id, gate_type),
            ).fetchone()
            if pending is None:
                raise StateTransitionError(f"pending gate not found: {gate_type}")
            pipeline = json.loads(row["pipeline_json"])
            current_index = int(row["phase_index"])
            if pipeline[current_index] != gate_type or current_index + 1 >= len(pipeline):
                raise StateTransitionError(f"gate is not at an advanceable phase: {gate_type}")
            next_phase = pipeline[current_index + 1]
            new_revision = int(row["revision"]) + 1
            connection.execute(
                "UPDATE gates SET status = 'resolved', decision = ?, resolved_at = ? WHERE id = ?",
                (decision, utc_now(), pending["id"]),
            )
            connection.execute(
                "UPDATE tasks SET phase_index = ?, status = 'active', revision = ?, updated_at = ? WHERE task_id = ?",
                (current_index + 1, new_revision, utc_now(), task_id),
            )
            connection.execute(
                "INSERT INTO transitions(task_id, from_phase, to_phase, revision, created_at) VALUES (?, ?, ?, ?, ?)",
                (task_id, gate_type, next_phase, new_revision, utc_now()),
            )
        return self.task(task_id)

    def touch_file(self, task_id: str, path: str) -> dict[str, Any]:
        return self.touch_files(task_id, [path])

    def touch_files(self, task_id: str, paths) -> dict[str, Any]:
        """Registra N caminhos como UMA alteracao.

        Uma chamada de ferramenta e uma alteracao, mesmo tocando tres arquivos.
        Chamar `touch_file` em laco incrementaria `code_revision` uma vez por
        arquivo, e `code_revision` e o que invalida evidencia: um `pytest`
        seguinte pareceria obsoleto sem que nada tivesse mudado depois dele.
        """
        normalizados = []
        for caminho in paths:
            texto = str(Path(caminho))
            if texto not in normalizados:
                normalizados.append(texto)
        if not normalizados:
            return self.task(task_id)
        with self._write() as connection:
            row = self._locked_task(connection, task_id)
            if row["status"] in TERMINAL_STATUSES:
                # A task acabou. Continuar contando arquivo nela atribuiria
                # trabalho novo a uma entrega ja fechada, e `code_revision`
                # existe para invalidar evidencia de uma task viva — numa
                # morta nao invalida nada, so cresce.
                return self.task(task_id)
            next_code_revision = int(row["code_revision"]) + 1
            connection.executemany(
                "INSERT OR IGNORE INTO files(task_id, normalized_path, first_seen_code_revision) VALUES (?, ?, ?)",
                [(task_id, texto, next_code_revision) for texto in normalizados],
            )
            connection.execute(
                "UPDATE tasks SET code_revision = ?, revision = revision + 1, verified = 0, "
                "status = CASE WHEN status = 'verified' THEN 'active' ELSE status END, "
                "updated_at = ? WHERE task_id = ?",
                (next_code_revision, utc_now(), task_id),
            )
        return self.task(task_id)

    def files(self, task_id: str) -> list[str]:
        """Caminhos ja atribuidos a esta task, na ordem em que apareceram."""
        with self._connect() as connection:
            linhas = connection.execute(
                "SELECT normalized_path FROM files WHERE task_id = ? "
                "ORDER BY first_seen_code_revision, normalized_path",
                (task_id,),
            ).fetchall()
        return [linha["normalized_path"] for linha in linhas]

    def record_evidence(
        self,
        task_id: str,
        *,
        evidence_type: str,
        command: str | None,
        exit_code: int | None,
        tests_collected: int | None,
        tests_passed: int | None,
        output_hash: str | None,
    ) -> dict[str, Any]:
        with self._write() as connection:
            row = self._locked_task(connection, task_id)
            valid_test = (
                evidence_type == "test"
                and exit_code == 0
                and isinstance(tests_collected, int)
                and tests_collected > 0
                and tests_passed == tests_collected
            )
            connection.execute(
                """
                INSERT INTO evidence(
                    task_id, code_revision, evidence_type, command, exit_code,
                    tests_collected, tests_passed, output_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    row["code_revision"],
                    evidence_type,
                    command,
                    exit_code,
                    tests_collected,
                    tests_passed,
                    output_hash,
                    utc_now(),
                ),
            )
            # A evidencia entra sempre — o registro acima e o historico e nao
            # depende do estado da task. O que segue e o ciclo de vida, e ele
            # para em status terminal: a validacao final roda DEPOIS do
            # `complete`, entao deixar a evidencia mexer no status faria toda
            # entrega bem-feita ser desfeita pelo proprio ato de conferi-la.
            # 'verified' ainda esta dentro de `one_active_task_per_scope`, entao
            # com uma task nova ja aberta a ressurreicao nem falhava em silencio:
            # estourava IntegrityError e derrubava o hook.
            terminal = row["status"] in TERMINAL_STATUSES
            if terminal:
                novo_verified = int(row["verified"])
                novo_status = row["status"]
            elif valid_test:
                novo_verified = 1
                novo_status = "verified"
            elif evidence_type == "test":
                novo_verified = 0
                novo_status = "active" if row["status"] == "verified" else row["status"]
            else:
                novo_verified = int(row["verified"])
                novo_status = row["status"]
            connection.execute(
                "UPDATE tasks SET verified = ?, status = ?, "
                "stop_continuations = CASE WHEN ? THEN 0 ELSE stop_continuations END, "
                "revision = revision + 1, updated_at = ? WHERE task_id = ?",
                (
                    novo_verified,
                    novo_status,
                    1 if (valid_test and not terminal) else 0,
                    utc_now(),
                    task_id,
                ),
            )
        return self.task(task_id)

    def register_stop_continuation(self, task_id: str, *, limit: int = 2) -> dict[str, Any]:
        if limit < 1:
            raise StateTransitionError("stop continuation limit must be positive")
        now = utc_now()
        with self._write() as connection:
            row = self._locked_task(connection, task_id)
            pipeline = json.loads(row["pipeline_json"])
            if row["status"] != "active" or not pipeline or bool(row["verified"]):
                raise StateTransitionError("task does not require a stop continuation")
            continuations = int(row["stop_continuations"])
            if continuations >= limit:
                pending = connection.execute(
                    "SELECT 1 FROM gates WHERE task_id = ? AND gate_type = 'escalation' "
                    "AND status = 'pending'",
                    (task_id,),
                ).fetchone()
                if pending is None:
                    connection.execute(
                        "INSERT INTO gates(task_id, gate_type, status, created_at) "
                        "VALUES (?, 'escalation', 'pending', ?)",
                        (task_id, now),
                    )
                connection.execute(
                    "UPDATE tasks SET status = 'awaiting_gate', revision = revision + 1, "
                    "updated_at = ? WHERE task_id = ?",
                    (now, task_id),
                )
            else:
                connection.execute(
                    "UPDATE tasks SET stop_continuations = stop_continuations + 1, "
                    "revision = revision + 1, updated_at = ? WHERE task_id = ?",
                    (now, task_id),
                )
        return self.task(task_id)

    def complete(self, task_id: str, *, expected_revision: int) -> dict[str, Any]:
        with self._write() as connection:
            row = self._locked_task(connection, task_id)
            self._expect_revision(row, expected_revision)
            if not bool(row["verified"]) or not self._has_fresh_test_evidence(connection, row):
                raise StateTransitionError("task requires fresh verification evidence")
            pipeline = json.loads(row["pipeline_json"])
            if pipeline and int(row["phase_index"]) != len(pipeline) - 1:
                raise StateTransitionError(f"task is not at final phase: {self._phase(row)}")
            connection.execute(
                "UPDATE tasks SET status = 'done', revision = revision + 1, updated_at = ? WHERE task_id = ?",
                (utc_now(), task_id),
            )
        return self.task(task_id)

    def log_event(self, scope_id: str, event_type: str, payload: dict[str, Any], task_id: str | None = None) -> None:
        with self._write() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO scopes(scope_id, created_at) VALUES (?, ?)", (scope_id, utc_now())
            )
            connection.execute(
                "INSERT INTO events(task_id, scope_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (task_id, scope_id, event_type, json.dumps(payload, sort_keys=True), utc_now()),
            )

    def _locked_task(self, connection: sqlite3.Connection, task_id: str) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if row is None:
            raise StateTransitionError(f"task not found: {task_id}")
        return row

    @staticmethod
    def _expect_revision(row: sqlite3.Row, expected: int) -> None:
        actual = int(row["revision"])
        if actual != expected:
            raise StateTransitionError(f"revision mismatch: expected {expected}, actual {actual}")

    @staticmethod
    def _phase(row: sqlite3.Row) -> str | None:
        pipeline = json.loads(row["pipeline_json"])
        index = int(row["phase_index"])
        return pipeline[index] if pipeline and 0 <= index < len(pipeline) else None

    @staticmethod
    def _has_artifact(connection: sqlite3.Connection, task_id: str, artifact_type: str) -> bool:
        return connection.execute(
            "SELECT 1 FROM artifacts WHERE task_id = ? AND artifact_type = ? LIMIT 1",
            (task_id, artifact_type),
        ).fetchone() is not None

    @staticmethod
    def _has_fresh_test_evidence(connection: sqlite3.Connection, row: sqlite3.Row) -> bool:
        return connection.execute(
            """
            SELECT 1 FROM evidence
            WHERE task_id = ? AND code_revision = ? AND evidence_type = 'test'
              AND exit_code = 0 AND tests_collected > 0 AND tests_passed = tests_collected
              AND id = (
                  SELECT MAX(id) FROM evidence
                  WHERE task_id = ? AND code_revision = ? AND evidence_type = 'test'
              )
            LIMIT 1
            """,
            (row["task_id"], row["code_revision"], row["task_id"], row["code_revision"]),
        ).fetchone() is not None

    @staticmethod
    def _bump(connection: sqlite3.Connection, task_id: str) -> None:
        connection.execute(
            "UPDATE tasks SET revision = revision + 1, updated_at = ? WHERE task_id = ?",
            (utc_now(), task_id),
        )

    @classmethod
    def _render_task(
        cls,
        row: sqlite3.Row,
        pending_gate: str | None,
        artifacts: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        pipeline = json.loads(row["pipeline_json"])
        index = int(row["phase_index"])
        return {
            "artifacts": artifacts or [],
            "task_id": row["task_id"],
            "scope_id": row["scope_id"],
            "legacy_level": row["legacy_level"],
            "tier": row["tier"],
            "kind": row["kind"],
            "status": row["status"],
            "pipeline": pipeline,
            "phase": pipeline[index] if pipeline and 0 <= index < len(pipeline) else None,
            "revision": int(row["revision"]),
            "code_revision": int(row["code_revision"]),
            "owner_epoch": int(row["owner_epoch"]),
            "verified": bool(row["verified"]),
            "stop_continuations": int(row["stop_continuations"]),
            "pending_gate": pending_gate,
            "started_at": row["started_at"],
            "updated_at": row["updated_at"],
        }
