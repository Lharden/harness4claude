"""Todo `hookSpecificOutput` que o harness emite tem que passar na validacao do host.

## O defeito que isto trava

Depois de `/compact`, `harness-lifecycle.py --event PostCompact` emitia
`{"hookSpecificOutput": {"hookEventName": "PostCompact", ...}}` e o Claude Code
rejeitava a saida inteira:

    Hook JSON output validation failed — hookSpecificOutput.hookEventName:
    expected one of PreToolUse | UserPromptSubmit | UserPromptExpansion |
    SessionStart | Setup | PreModelSwitch | ...

A instrucao `HARNESS v3 RESUMING` nunca chegava ao modelo. O mapa de canais do
`emit.py` marcava `postcompact` como "nao verificado" e o mandava para
`additionalContext` mesmo assim.

## A fonte

https://code.claude.com/docs/en/hooks — secoes "Add context for Claude" e
"Decision control". `additionalContext` dentro de `hookSpecificOutput` so e
aceito nos eventos de `ACEITA_ADDITIONAL_CONTEXT` abaixo, e sempre com
`hookEventName` igual ao nome do proprio evento. `PostCompact` esta na linha
"None — No decision control": nao tem `hookSpecificOutput` nenhum.

## O que e verificado

1. `emit.py`: para cada evento que o harness conhece (mapa de canais, mapa de
   nomes canonicos, e cada evento registrado em `hooks.json`), o que `flush`
   escreve no stdout.
2. `harness-lifecycle.py`: rodado de verdade, com um pipeline retomavel no
   bucket, para cada evento com que `hooks.json` o invoca.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
EMIT_PATH = ROOT / "hooks" / "emit.py"
LIFECYCLE = ROOT / "hooks" / "harness-lifecycle.py"
HOOKS_JSON = ROOT / "hooks" / "hooks.json"

sys.path.insert(0, str(ROOT / "scripts"))
from harness_paths import ensure_state_dir  # type: ignore[import-not-found]

# Eventos de hook documentados em https://code.claude.com/docs/en/hooks.
EVENTOS_DOCUMENTADOS = (
    "SessionStart", "Setup", "InstructionsLoaded", "UserPromptSubmit",
    "UserPromptExpansion", "MessageDisplay", "PreToolUse", "PermissionRequest",
    "PostToolUse", "PostToolUseFailure", "PostToolBatch", "PermissionDenied",
    "Notification", "SubagentStart", "SubagentStop", "TaskCreated",
    "TaskCompleted", "Stop", "StopFailure", "TeammateIdle", "ConfigChange",
    "CwdChanged", "DirectoryAdded", "FileChanged", "WorktreeCreate",
    "WorktreeRemove", "PreCompact", "PostCompact", "PreModelSwitch",
    "PostModelSwitch", "SessionEnd", "Elicitation", "ElicitationResult",
)
NOME_DOCUMENTADO = {e.lower(): e for e in EVENTOS_DOCUMENTADOS}

# "Add context for Claude": onde o reminder aparece, evento por evento.
ACEITA_ADDITIONAL_CONTEXT = frozenset({
    "SessionStart", "SubagentStart",
    "UserPromptSubmit", "UserPromptExpansion",
    "PreToolUse", "PostToolUse", "PostToolUseFailure", "PostToolBatch",
    "Stop", "SubagentStop",
    "PostModelSwitch",
})


def _emit_mod():
    spec = importlib.util.spec_from_file_location("harness_emit_event_name", EMIT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _hooks_registrados() -> dict:
    return json.loads(HOOKS_JSON.read_text(encoding="utf-8"))["hooks"]


def _eventos_do_emissor() -> list[str]:
    mod = _emit_mod()
    nomes = set(mod.CHANNEL_BY_EVENT) | set(mod.CANONICAL_EVENT)
    nomes |= set(mod.CANONICAL_EVENT.values()) | set(_hooks_registrados())
    return sorted(nomes)


def _eventos_do_lifecycle() -> list[str]:
    eventos = set()
    for evento, grupos in _hooks_registrados().items():
        for grupo in grupos:
            for h in grupo.get("hooks", []):
                cmd = h.get("command", "")
                if "harness-lifecycle.py" in cmd:
                    m = re.search(r"--event\s+(\S+)", cmd)
                    eventos.add(m.group(1) if m else evento)
    return sorted(eventos)


def _violacao(evento: str, saida: str) -> str | None:
    """Motivo pelo qual o host rejeitaria `saida` no `evento`, ou None."""
    texto = saida.strip()
    if not (texto.startswith("{") and texto.endswith("}")):
        return None  # texto cru: o host nao valida como JSON
    payload = json.loads(texto)
    bloco = payload.get("hookSpecificOutput")
    if bloco is None:
        return None
    nome = bloco.get("hookEventName")
    real = NOME_DOCUMENTADO.get(evento.strip().lower(), evento)
    if real not in ACEITA_ADDITIONAL_CONTEXT:
        return f"{real} nao aceita hookSpecificOutput; emitiu hookEventName={nome!r}"
    if nome != real:
        return f"{real} exige hookEventName={real!r}; emitiu {nome!r}"
    return None


@pytest.mark.parametrize("evento", _eventos_do_emissor())
def test_emissor_so_usa_hook_event_name_aceito(evento, tmp_path):
    buf = io.StringIO()
    em = _emit_mod().Emitter(evento, hook="t", root=tmp_path)
    em.add("resume", "HARNESS v3 RESUMING: texto de sonda")
    em.flush(stream=buf)
    assert _violacao(evento, buf.getvalue()) is None, _violacao(evento, buf.getvalue())


@pytest.mark.parametrize("evento", _eventos_do_lifecycle())
def test_lifecycle_so_usa_hook_event_name_aceito(evento, tmp_path):
    harness_root = tmp_path / "harness"
    cwd = tmp_path / "repo"
    cwd.mkdir()
    bucket = ensure_state_dir(harness_root, cwd, session_id="session-evt")
    (bucket / "state.json").write_text(json.dumps({
        "task_id": "t-evt",
        "classification": "L2-feature",
        "status": "active",
        "pipeline": ["write-spec", "design-doc"],
        "current_step": "write-spec",
    }), encoding="utf-8")
    env = os.environ.copy()
    env["HARNESS_DIR"] = str(harness_root)
    env.pop("AI_BRAIN_PATH", None)
    env.pop("VAULT_PATH", None)

    result = subprocess.run(
        [sys.executable, str(LIFECYCLE), "--event", evento],
        input=json.dumps({"session_id": "session-evt", "cwd": str(cwd),
                          "hook_event_name": evento}),
        capture_output=True, text=True, check=False, env=env,
    )

    assert result.returncode == 0, result.stderr
    assert _violacao(evento, result.stdout) is None, _violacao(evento, result.stdout)


def test_a_lista_do_lifecycle_nao_esta_vazia():
    """Sem isto, um hooks.json reescrito zeraria a parametrizacao em silencio."""
    assert "PostCompact" in _eventos_do_lifecycle()
    assert "SubagentStart" in _eventos_do_lifecycle()
