"""harness_git_guard.py — a decisao do git-guard, num processo so.

Chamado por `harness-git-guard.sh` (PreToolUse Bash|PowerShell) com o payload
do host na stdin. Spec: docs/specs/git-guard-prazo-sob-carga-spec-light.md.

Por que um processo so (medido em 2026-10-07): o guarda antigo era uma cadeia
de ~15 processos por chamada — 4 interpretadores Python e ~11 do bash do
Windows — e custava 2,1 s ocioso, 3,8 s com a maquina carregada; em producao,
531 chamadas passaram de 10 s entre 20/09 e 07/10. O host mata o hook no prazo
e a ferramenta segue: a checagem nao acontecia. A decisao em si e
microssegundos; o custo era criar processo.

Contrato de saida com o involucro: 0 = passa (stdout pode trazer aviso);
3 = bloqueia (o JSON do bloqueio ja foi escrito em stderr). O involucro traduz
3 para o exit 2 do host e trata QUALQUER outro codigo como falha de
infraestrutura (aviso, exit 0) — o 2 que o proprio Python devolve quando nao
acha o script nunca vira bloqueio.

Mensagens so em ASCII: o host nao roda com PYTHONUTF8.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import command_policy

EXIT_PASSA = 0
EXIT_BLOQUEIA = 3
RATE_LIMIT_S = 3600


def _harness_dir() -> Path:
    return Path(os.environ.get("HARNESS_DIR") or Path.home() / ".claude" / "harness")


def _avisar_cego(raiz: Path, status: str) -> None:
    """Payload em formato desconhecido: nunca bloquear (travaria todo Bash da
    maquina numa mudanca de schema), mas nunca em silencio. Rate-limit de 1 h:
    aviso em toda chamada vira ruido, e ruido vira alarme ignorado."""
    marcador = raiz / ".git-guard-blind"
    agora = int(time.time())
    try:
        ultimo = int(marcador.read_text(encoding="utf-8").strip() or 0)
    except (OSError, ValueError):
        ultimo = 0
    if agora - ultimo < RATE_LIMIT_S:
        return
    try:
        raiz.mkdir(parents=True, exist_ok=True)
        marcador.write_text(f"{agora}\n", encoding="utf-8")
    except OSError:
        pass
    print(f"## Harness Warning: git-guard nao reconheceu o payload do PreToolUse ({status}).")
    print("   O bloqueio de operacoes git destrutivas esta INATIVO ate isto ser corrigido.")
    print("   Provavel mudanca no formato do hook pelo CLI host. Rode: bash scripts/health-check.sh")


def _extrair(cru: bytes) -> tuple[str, str]:
    """(status, comando). O status distingue "nao havia comando" de "o payload
    nao tem a forma que eu conheco" — colapsar os dois em string vazia fazia o
    guard devolver exit 0 para tudo numa mudanca de schema do host."""
    try:
        dados = json.loads(cru.decode("utf-8", errors="replace"))
    except ValueError:
        return "SHAPE_UNKNOWN", ""
    if not isinstance(dados, dict):
        return "SHAPE_UNKNOWN", ""
    entrada = dados.get("tool_input")
    if not isinstance(entrada, dict):
        return "SHAPE_UNKNOWN", ""
    if "command" not in entrada:
        return "NO_COMMAND_KEY", ""
    comando = entrada.get("command")
    if comando is None:
        return "OK", ""
    if not isinstance(comando, str):
        return "SHAPE_UNKNOWN", ""
    return "OK", comando


# --- Fallback para comando que o parser nao le ---------------------------------
#
# A politica devolve `unknown` para o comando INTEIRO quando o shlex nao fecha
# uma aspa — basta um `'` num comentario ou num heredoc. Ate 2026-10-07 o guarda
# tratava `unknown` como "passa com aviso", e `git reset --hard` na linha
# seguinte passava. O fallback reusa a propria politica em pedacos menores: nao
# existe segunda lista de padroes destrutivos para divergir da primeira.
# Medido no corpus desta maquina: dos 296 comandos que o parser nao le, 290
# passam a ser decididos e nenhum e bloqueado; as sondas destrutivas bloqueiam.

_HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
_ORDEM = {"allow": 0, "unknown": 1, "warn": 2, "require_approval": 3, "deny": 4}


def _sem_heredoc_nem_comentario(texto: str) -> str:
    saida: list[str] = []
    fim: str | None = None
    for linha in texto.splitlines():
        if fim is not None:
            if linha.strip() == fim:
                fim = None
            continue
        if linha.lstrip().startswith("#"):
            continue
        achado = _HEREDOC.search(linha)
        if achado:
            fim = achado.group(2)
        saida.append(linha)
    return "\n".join(saida)


def _linhas(texto: str) -> list[str]:
    juntas = texto.replace("\\\r\n", " ").replace("\\\n", " ")
    return [linha for linha in juntas.splitlines() if linha.strip()]


def _pior(a: command_policy.PolicyDecision, b: command_policy.PolicyDecision) -> command_policy.PolicyDecision:
    return b if _ORDEM.get(b.action, 1) > _ORDEM.get(a.action, 1) else a


def avaliar_com_fallback(comando: str) -> tuple[command_policy.PolicyDecision, bool]:
    """(decisao, veio_do_fallback)."""
    decisao = command_policy.evaluate_command(comando)
    if decisao.action != "unknown":
        return decisao, False
    limpo = _sem_heredoc_nem_comentario(comando)
    decisao = command_policy.evaluate_command(limpo)
    if decisao.action != "unknown":
        return decisao, True
    resultado = command_policy.PolicyDecision("allow")
    for linha in _linhas(limpo):
        atual = command_policy.evaluate_command(linha)
        if atual.action == "unknown":
            atual = command_policy.evaluate_command(linha.replace('"', " ").replace("'", " "))
        if atual.action == "deny":
            return atual, True
        resultado = _pior(resultado, atual)
    return resultado, True


def _decidir(comando: str) -> int:
    try:
        decisao, do_fallback = avaliar_com_fallback(comando)
    except Exception:  # guarda nao pode cair calado
        decisao, do_fallback = command_policy.PolicyDecision("unknown", "guard exception"), True
    sufixo = " (lido pelo fallback: o parser nao leu o comando inteiro)" if do_fallback else ""
    if decisao.action == "deny":
        motivo = f"BLOCKED: {decisao.reason}{sufixo}"
        sys.stderr.write(json.dumps({"decision": "block", "reason": motivo}) + "\n")
        return EXIT_BLOQUEIA
    if decisao.action == "require_approval":
        motivo = f"APPROVAL REQUIRED: {decisao.reason}{sufixo}"
        sys.stderr.write(json.dumps({"decision": "block", "reason": motivo}) + "\n")
        return EXIT_BLOQUEIA
    if decisao.action == "warn":
        print(f"## Harness Warning: {decisao.reason}. Confirme com o usuario antes de prosseguir.")
        return EXIT_PASSA
    if decisao.action == "allow":
        return EXIT_PASSA
    print("## Harness Warning: command-policy nao conseguiu analisar o comando; trate como nao observado.")
    return EXIT_PASSA


def main() -> int:
    raiz = _harness_dir()  # o heartbeat e do involucro: mede a chamada mesmo sem Python
    try:
        status, comando = _extrair(sys.stdin.buffer.read())
    except Exception:  # excecao antes do comando nunca bloqueia
        status, comando = "SHAPE_UNKNOWN", ""
    if status != "OK":
        _avisar_cego(raiz, status)
        return EXIT_PASSA
    if not comando:
        return EXIT_PASSA
    return _decidir(comando)


if __name__ == "__main__":
    raise SystemExit(main())
