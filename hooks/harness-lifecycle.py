#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from harness_paths import ensure_state_dir  # type: ignore[import-not-found]


def _load_state(bucket: Path) -> dict:
    try:
        value = json.loads((bucket / "state.json").read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _resume_message(event: str, state: dict) -> str:
    task_id = state.get("task_id") or "none"
    pipeline = state.get("pipeline") or []
    artifacts = state.get("artifacts_so_far") or []
    parts = [
        f"HARNESS v3 RESUMING: scoped task {task_id}.",
        f"Classification: {state.get('classification') or 'unknown'}; status: {state.get('status') or 'idle'}.",
        f"Current step: {state.get('current_step') or (pipeline[0] if pipeline else 'none')}.",
        f"Pipeline: {' -> '.join(str(item) for item in pipeline) if pipeline else 'none'}.",
    ]
    if state.get("pending_gate"):
        parts.append(f"Pending human gate: {state['pending_gate']}.")
    if artifacts:
        parts.append("Artifacts: " + ", ".join(str(item) for item in artifacts) + ".")
    parts.append("Invoke skill='harness-workflow' and continue from this exact state.")
    if event == "SubagentStart":
        parts.append("Return a NodeResult with role, status, findings, evidence_refs, coverage, and errors.")
    return " ".join(parts)


def _emit(payload: dict, event: str, texto: str) -> None:
    """Entrega a retomada pelo emissor central.

    `systemMessage` e canal de UI e nunca chegou. PostCompact e SubagentStart
    tambem nunca foram observados entregando por canal nenhum, entao o mapa do
    emissor os marca como nao verificados — mas o que sai daqui e instrucao de
    retomada, e nao ha regressao possivel: hoje ja se perde.
    """
    if not texto:
        return
    try:
        import importlib.util

        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "emit.py")
        spec = importlib.util.spec_from_file_location("harness_emit", path)
        if spec is None or spec.loader is None:
            raise ImportError
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.Emitter(
            event,
            hook="lifecycle",
            session_id=payload.get("session_id"),
            cwd=payload.get("cwd"),
        ).add("resume", texto).flush()
    except Exception:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": event, "additionalContext": texto,
        }}, ensure_ascii=False))


def _fechar_sessao(payload: dict, root) -> None:
    """SessionEnd: registra a sessao para a busca cross-sessao encontra-la.

    Duas escritas baratas, nenhuma reconstrucao. Reindexar 2900 chunks no fim
    de cada sessao custaria ~30s de Ollama num momento em que ninguem esta
    esperando resultado; marcar `.stale` deixa o proximo build saber, e o
    SessionStart reporta o atraso pelo canal que agora chega ao modelo.

    A pagina no vault e escrita DIRETO no destino. O caminho projetado —
    `traces/*.md` -> `vault_sync.py` -> `wiki/sessions/` — nunca produziu um
    arquivo: a rotacao so cria `traces/` quando um `trace-current.md` passa de
    50 KB, e o maior em disco tem 3,3 KB. `wiki/sessions/` esta vazio desde
    sempre. Consertar a rotacao seria consertar um cano que ninguem usa.
    """
    sid = str(payload.get("session_id") or "")
    cwd = str(payload.get("cwd") or "")
    if not sid:
        return

    # 1. indice sujo — barato, e o SessionStart passa a avisar
    try:
        import importlib.util

        caminho = Path(__file__).resolve().parent.parent / "scripts" / "build_sessions_index.py"
        spec = importlib.util.spec_from_file_location("build_sessions_index", caminho)
        if spec is not None and spec.loader is not None:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod.mark_stale(str(Path(root) / "sessions-index"))
    except Exception:
        pass

    # 2. cartao no vault, direto no destino
    try:
        vault = os.environ.get("AI_BRAIN_PATH") or (
            os.environ.get("VAULT_PATH", "") and os.path.join(os.environ["VAULT_PATH"], "AI-Brain"))
        if not vault or not os.path.isdir(vault):
            return
        destino = Path(vault) / "wiki" / "sessions"
        destino.mkdir(parents=True, exist_ok=True)
        agora = datetime.now(timezone.utc)
        slug = os.path.basename(str(cwd).rstrip("/\\")) or "sessao"
        arquivo = destino / f"{agora:%Y-%m-%d}-{slug}-{sid[:8]}.md"
        if arquivo.exists():
            return
        linhas = [
            "---",
            "type: session",
            f"session_id: {sid}",
            f"project: {slug}",
            f"cwd: {cwd}",
            f"created: {agora:%Y-%m-%d}",
            f"updated: {agora:%Y-%m-%d}",
            "---",
            "",
            f"# Sessao {sid[:8]} — {slug}",
            "",
            f"Encerrada em {agora:%Y-%m-%d %H:%M} UTC.",
            "",
            f"- Retomar: `claude --resume {sid}`",
            f"- Buscar no conteudo: `python tools/session_query.py \"<pergunta>\" --session {sid[:6]}`",
            "",
        ]
        arquivo.write_text(chr(10).join(linhas), encoding="utf-8")
    except Exception:
        pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", default=None)
    args = parser.parse_args()
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        payload = {}
    event = str(args.event or payload.get("hook_event_name") or os.environ.get("CLAUDE_HOOK_EVENT") or "Lifecycle")
    root = Path(os.environ.get("HARNESS_DIR") or Path.home() / ".claude" / "harness")
    root.mkdir(parents=True, exist_ok=True)
    heartbeats = root / "heartbeats"
    heartbeats.mkdir(parents=True, exist_ok=True)
    (heartbeats / event).write_text(str(datetime.now(timezone.utc).timestamp()), encoding="utf-8")
    try:
        bucket = ensure_state_dir(
            root,
            payload.get("cwd") or None,
            session_id=payload.get("session_id") or None,
        )
    except (OSError, ValueError):
        bucket = root
    database = bucket / "lifecycle.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS lifecycle_events("
            "id INTEGER PRIMARY KEY, event TEXT, session_id TEXT, "
            "cwd TEXT, created_at TEXT)"
        )
        connection.execute(
            "INSERT INTO lifecycle_events(event, session_id, cwd, created_at) VALUES (?, ?, ?, ?)",
            (event, payload.get("session_id"), payload.get("cwd"), datetime.now(timezone.utc).isoformat()),
        )
    if event == "SessionEnd":
        _fechar_sessao(payload, root)
    if event in {"PostCompact", "SubagentStart"}:
        _emit(payload, event, _resume_message(event, _load_state(bucket)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
