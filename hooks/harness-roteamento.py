#!/usr/bin/env python3
"""harness-roteamento.py — roteamento de modelo/esforco para sessoes de chip.

Spec: docs/specs/roteamento-de-sessoes-spec-light.md. Um script, tres papeis:

- `--event PreToolUse` (mae, matcher `mcp__ccd_session__spawn_task`): nega o
  chip sem linha `Roteamento: <modelo> · <esforco> (<tipo>)` valida; aceita e
  anota o chip valido em `roteamento-chips.jsonl`.
- `--event UserPromptSubmit`, filha: prompt com a linha -> instrucao, uma vez
  por sessao (REQ-3).
- `--event UserPromptSubmit`, mae: mensagem `[roteamento] ... prompt_sha=` ->
  instrucao de aplicar (se bate com o registro) ou de recusar (REQ-4).
  `[roteamento-ok]`/`[roteamento-recusado...]` nao disparam nada (REQ-5).

O texto ao modelo sai pelo `emit.py`. A recusa do PreToolUse e decisao de
permissao, nao contexto, e o `emit.py` nao a modela: sai direto em
`hookSpecificOutput.permissionDecision` (doc oficial, "Decision control").
Falha interna nunca bloqueia o chip (fail-open, aviso em stderr).
`HARNESS_ROTEAMENTO=0` desliga tudo.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

import roteamento as rt  # noqa: E402

TOOL = "mcp__ccd_session__spawn_task"
FLAG = "roteamento-filha.flag"


def _emitter_mod():
    spec = importlib.util.spec_from_file_location("harness_emit", str(HERE / "emit.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_json(payload: dict) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))


def _negar(motivo: str) -> None:
    _write_json({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": motivo,
    }})


def pre_tool_use(d: dict) -> None:
    if d.get("tool_name") != TOOL:
        return
    prompt = str((d.get("tool_input") or {}).get("prompt") or "")
    tabela = rt.carregar_tabela()
    formato = "Roteamento: <modelo> · <esforco> (<tipo>)"
    tab = rt.tabela_texto(tabela)
    par = rt.parse_linha(prompt)
    if par is None or not rt.termina_na_linha(prompt):
        return _negar(
            f"Chip sem roteamento. Termine o prompt com a ultima linha `{formato}`, "
            f"com um dos 4 tipos da tabela ({tab}). Ex.: `Roteamento: sonnet · medium (execucao-mecanica)`."
        )
    if not rt.par_valido(par, tabela):
        modelo, esforco, tipo = par
        esperado = tabela.get(tipo)
        alvo = f"o cabecalho de {tipo} manda {esperado[0]} · {esperado[1]}" if esperado else (
            f"`{tipo}` nao e um tipo da tabela")
        return _negar(
            f"Roteamento invalido: `{modelo} · {esforco} ({tipo})`; {alvo}. Tabela: {tab}."
        )
    rt.registrar_chip(d.get("session_id"), d.get("cwd"), par, prompt)


def _instrucao_filha(par, sha) -> str:
    modelo, esforco, tipo = par
    script = HERE.parent / "scripts" / "roteamento.py"
    return (
        f"[roteamento] Este chip pede {modelo} · {esforco} ({tipo}). Antes de qualquer trabalho:\n"
        "1. Chame get_session(\"self\") e guarde modelo, esforco e parentSessionId.\n"
        f"2. Registre o nascimento: python \"{script}\" nascimento --session-id <seu id> "
        "--modelo <modelo atual> --esforco <esforco atual> "
        f"--tipo {tipo}\n"
        f"3. Se modelo ({modelo}) e esforco ({esforco}) ja batem, siga normalmente.\n"
        "4. Se nao batem, envie UMA mensagem a parentSessionId: "
        f"`[roteamento] {modelo} · {esforco} ({tipo}) prompt_sha={sha}`, "
        "encerre o turno com uma linha ao usuario e NAO comece o trabalho (o turno em voo "
        "termina no modelo antigo). Ao receber `[roteamento-ok]`, comece o trabalho.\n"
        "5. Sem mae que responda (arquivada, envio falhou, `[roteamento-recusado...]`): diga na "
        f"primeira resposta qual roteamento a tabela pedia ({modelo} · {esforco}) e siga no "
        "nivel herdado. Nao insista: no maximo 1 pedido."
    )


def _instrucao_mae(par, sha, remetente, aplicar, motivo="") -> str:
    modelo, esforco, tipo = par
    de = remetente or "o remetente da mensagem"
    if aplicar:
        return (
            f"[roteamento] Pedido conferido contra o registro desta sessao (prompt_sha={sha}). "
            f"Aplique em {de}: set_session_effort(\"{esforco}\") e set_session_model(\"{modelo}\", "
            "a familia vigente; use o id completo se a ferramenta exigir). Depois responda UMA vez a "
            f"{de} com `[roteamento-ok]`. Um aumento de esforco pode pedir aprovacao do usuario: "
            "espere-a; se for negada, responda `[roteamento-recusado: aprovacao negada]`."
        )
    return (
        f"[roteamento] Pedido sem correspondencia no registro desta sessao ({motivo}). NAO aplique "
        f"nenhum ajuste de modelo ou esforco. Responda UMA vez a {de} com "
        f"`[roteamento-recusado: {motivo}]`."
    )


def user_prompt_submit(d: dict) -> None:
    prompt = str(d.get("prompt") or d.get("user_prompt") or "")
    sid, cwd = d.get("session_id"), d.get("cwd")
    tabela = rt.carregar_tabela()
    texto, kind = "", "roteamento"

    pedido = rt.PEDIDO_RE.search(prompt)
    if pedido:  # mae (REQ-4); sai antes de olhar a linha da filha
        par = (pedido["modelo"].lower(), pedido["esforco"].lower(), pedido["tipo"])
        sha = pedido["sha"]
        m = rt.FROM_RE.search(prompt)
        remetente = m.group(1) if m else ""
        if not rt.par_valido(par, tabela):
            texto = _instrucao_mae(par, sha, remetente, False, "par fora da tabela")
        elif not rt.chip_registrado(sid, cwd, sha, par):
            texto = _instrucao_mae(par, sha, remetente, False, "chip nao registrado")
        else:
            texto = _instrucao_mae(par, sha, remetente, True)
    else:
        par = rt.parse_linha(prompt)
        if par is None or not rt.par_valido(par, tabela):
            return
        bucket = rt.pasta_da_sessao(sid, cwd)
        flag = bucket / FLAG
        if flag.exists():  # REQ-3: uma vez por sessao
            return
        try:
            flag.write_text(rt.prompt_sha(prompt) or "", encoding="utf-8")
        except OSError:
            pass
        texto = _instrucao_filha(par, rt.prompt_sha(prompt))

    if texto:
        _emitter_mod().Emitter("UserPromptSubmit", hook="roteamento", session_id=sid,
                               cwd=cwd).add(kind, texto).flush()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--event", required=True, choices=["PreToolUse", "UserPromptSubmit"])
    args = ap.parse_args(argv)
    if not rt.ligado():
        return 0
    try:
        d = json.load(sys.stdin)
        if not isinstance(d, dict):
            return 0
        (pre_tool_use if args.event == "PreToolUse" else user_prompt_submit)(d)
    except Exception as exc:  # fail-open: hook que quebra nao pode travar o chip
        sys.stderr.write(f"harness-roteamento: {type(exc).__name__}: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
