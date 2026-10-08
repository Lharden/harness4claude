#!/usr/bin/env python
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import sys

SERVIDOR = "science_harness"


def _config_path() -> str:
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    return os.path.join(base or os.path.expanduser("~"), ".claude.json")


def _norm(caminho: str) -> str:
    return os.path.normcase(os.path.abspath(caminho))


def _ancestrais(cwd: str) -> list[str]:
    atual, saida = _norm(cwd), []
    while True:
        saida.append(atual)
        pai = os.path.dirname(atual)
        if pai == atual:
            return saida
        atual = pai


def _alcancavel(spec: object) -> bool:
    """Executavel presente no disco ou no PATH. Nao sobe o processo: o hook roda em todo prompt."""
    if not isinstance(spec, dict):
        return False
    if spec.get("url"):
        return True
    comando = os.path.expandvars(str(spec.get("command") or ""))
    return bool(comando) and (os.path.isfile(comando) or shutil.which(comando) is not None)


def registro_science(cwd: str) -> dict | None:
    """O registro do `science_harness` que o Claude carrega neste cwd, ou None.

    Le o `.claude.json` (o mesmo que `claude mcp add` grava): escopo local do
    projeto antes do de usuario, e respeita o desligamento por projeto que o
    `/mcp` grava em `disabledMcpServers`. Qualquer falha de leitura conta como
    ausente — a skill `science-evidence` trata o MCP ausente como UNOBSERVED.
    """
    try:
        with open(_config_path(), encoding="utf-8") as arquivo:
            config = json.load(arquivo)
        projetos = {_norm(k): v for k, v in (config.get("projects") or {}).items() if isinstance(v, dict)}
        projeto = next((projetos[d] for d in _ancestrais(cwd) if d in projetos), {})
        if SERVIDOR in (projeto.get("disabledMcpServers") or []):
            return None
        for escopo in (projeto, config):
            spec = (escopo.get("mcpServers") or {}).get(SERVIDOR)
            if _alcancavel(spec):
                return spec
    except (OSError, ValueError, AttributeError, TypeError):
        return None
    return None


def _emit(payload: dict, texto: str) -> None:
    """Entrega pelo emissor central, com o canal provado como rede."""
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "emit.py")
        spec = importlib.util.spec_from_file_location("harness_emit", path)
        if spec is None or spec.loader is None:
            raise ImportError
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.Emitter(
            payload.get("hook_event_name") or "UserPromptSubmit",
            hook="science_intent",
            session_id=payload.get("session_id"),
            cwd=payload.get("cwd"),
        ).add("science", texto).flush()
    except Exception:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": payload.get("hook_event_name") or "UserPromptSubmit",
            "additionalContext": texto,
        }}, ensure_ascii=False))


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    prompt = str(payload.get("prompt") or payload.get("user_prompt") or "")
    if re.search(
        r"\b(scientific|science|evidence|paper|papers|claim|claims|estudo|evid[eê]ncia|artigo)\b", prompt, re.I
    ) and registro_science(str(payload.get("cwd") or os.getcwd())):
        # UserPromptSubmit + instrucao => additionalContext. Por `systemMessage`
        # esta linha nunca chegou ao modelo; por stdout cru chegaria sem marca
        # de proveniencia, indistinguivel de fala do usuario.
        _emit(
            payload,
            "SCIENCE HARNESS: invoke skill='science-evidence'; use "
            "science_harness read-only and preserve corpus provenance.",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
