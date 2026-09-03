#!/usr/bin/env python
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from harness_paths import ensure_state_dir  # type: ignore[import-not-found]
from transactional_state import HarnessDatabase, StateTransitionError  # type: ignore[import-not-found]

VERIFICATION_PATTERNS = (
    r"^\s*(?:py|python(?:\.exe)?)\s+-m\s+(?:pytest|unittest)\b",
    r"^\s*pytest(?:\.exe)?\b",
    r"^\s*(?:npm|pnpm)\s+(?:run\s+)?test\b",
    r"^\s*yarn\s+test\b",
    r"^\s*cargo\s+test\b",
    r"^\s*go\s+test\b",
)
SHELL_TOOLS = {"bash", "powershell", "shell", "shell_command"}


def _event_name(payload: dict[str, Any], explicit: str | None = None) -> str:
    return str(explicit or payload.get("hook_event_name") or payload.get("hookEventName") or "")


def _tool_input(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("tool_input") or payload.get("toolInput") or {}
    return value if isinstance(value, dict) else {}


def _command(payload: dict[str, Any]) -> str:
    value = _tool_input(payload)
    return str(value.get("command") or value.get("cmd") or value.get("script") or "")


#: Operadores que compoem comandos. Montados com `chr()` porque este arquivo
#: ja foi corrompido uma vez por escaping de heredoc: `\r` chegou ao disco
#: como byte de retorno de carro e quebrou a sintaxe.
_OPERADORES = frozenset({';', '|', '&', '`', chr(13), chr(10)})


def _scan_composition(command: str) -> tuple[int, bool]:
    """Indice do primeiro operador fora de aspas (-1 se nao houver), e se
    a linha terminou com aspa aberta.

    Duas perguntas diferentes saem da mesma varredura, e e de proposito:
    `is_trusted_verification` recusa nos DOIS casos, enquanto `atomic_prefix`
    so pode cortar no primeiro. Manter duas varreduras separadas foi o que
    fez o corte cair dentro de um argumento entre aspas.
    """
    quote = None
    escaped = False
    for index, character in enumerate(command):
        if escaped:
            escaped = False
            continue
        if quote:
            if character == chr(92) and quote == '"':
                escaped = True
            elif character == quote:
                quote = None
            continue
        if character in {chr(39), '"'}:
            quote = character
            continue
        if character in _OPERADORES:
            return index, False
        if character == '$' and index + 1 < len(command) and command[index + 1] == '(':
            return index, False
    return -1, quote is not None


def _has_unquoted_shell_composition(command: str) -> bool:
    indice, aspa_aberta = _scan_composition(command)
    return indice >= 0 or aspa_aberta


def is_trusted_verification(command: str) -> bool:
    if not command or _has_unquoted_shell_composition(command):
        return False
    return any(re.search(pattern, command, re.IGNORECASE) for pattern in VERIFICATION_PATTERNS)


def looks_like_verification(command: str) -> bool:
    """Parece tentativa de verificar, mesmo que nao sirva como evidencia.

    Difere de `is_trusted_verification` em um ponto so: ignora composicao de
    shell. Serve para separar "voce tentou verificar e eu descartei" de "isto
    nao tinha nada a ver com teste" — sem essa distincao o aviso apareceria em
    `git log | head` e viraria ruido por turno.

    As ancoras `^` de VERIFICATION_PATTERNS continuam valendo: `echo "rode
    python -m pytest"` menciona pytest e nao roda teste nenhum.
    """
    if not command:
        return False
    return any(re.search(p, command, re.IGNORECASE) for p in VERIFICATION_PATTERNS)


def atomic_prefix(command: str) -> str:
    """O comando ate o primeiro operador de shell nao citado.

    E o que o aviso devolve para o leitor rodar. Reconstruir a intencao a
    partir do comando dele vale mais que uma receita generica:
    `python -m pytest -q | tail -20` vira `python -m pytest -q`, que e
    exatamente o que ele queria.

    Aspa aberta NAO e ponto de corte: ela torna o comando nao confiavel, mas
    nao ha prefixo bom a sugerir, entao devolve a linha inteira.
    """
    indice, _ = _scan_composition(command)
    return (command[:indice] if indice >= 0 else command).strip()


#: O aviso do descarte. Curto de proposito: ele aparece no meio do trabalho, e
#: um paragrafo aqui custa mais atencao do que o erro que ele evita.
AVISO_COMPOSICAO = (
    "[harness] evidencia de teste NAO gravada: o comando tem composicao de "
    "shell (pipe, `&&`, `;`, nova linha ou substituicao), e um comando composto "
    "pode fabricar saida de teste. Para que conte, rode sozinho: {sugestao}"
)

#: Background nao tem composicao nenhuma, entao passa por `is_trusted_verification`
#: — mas o PostToolUse chega antes de existir saida, e evidencia sem caso
#: coletado nao verifica (contrato Harness4Contract v1). O silencio aqui custou
#: dois runs de ~7 min repetidos em 2026-09-02.
AVISO_SEM_CASOS = (
    "[harness] evidencia de teste gravada SEM casos coletados, entao nao "
    "verifica. Causa provavel: o comando rodou em background e a saida ainda "
    "nao existia. Rode em primeiro plano para que conte."
)


#: O proprio CLI de estado do harness. Ver `is_state_management`.
STATE_CLI_PATTERNS = (
    r"\bstate_cli\.py\b",
    r"\bbranch_state\.py\b",
    r"\bconfirm_classification\.py\b",
)


def is_state_management(command: str) -> bool:
    """O comando so mexe no estado do harness, nao no codigo do projeto.

    `_handle_post_tool` trata todo comando de shell como possivel alteracao de
    codigo e chama `touch_file`, que zera `verified` e sobe `code_revision`. A
    heuristica e conservadora e correta para `sed -i`, `npm install`, `git
    checkout`. Mas ela criava um deadlock estrutural: `state_cli.py complete`
    so pode ser invocado por shell, e a propria invocacao invalidava, no mesmo
    PostToolUse, a evidencia que o `complete` exige. **Nenhuma task podia ser
    concluida pelo caminho previsto.**

    Medido em 2026-09-02: `code_revision` foi 501 -> 507 -> 511 entre gravar a
    evidencia e tentar fechar, sem uma linha de codigo mudar. `verified` foi
    para True tres vezes e voltou para False no comando seguinte, todas.

    A isencao e estreita de proposito: so o CLI do proprio harness, e so
    quando o comando nao tem composicao de shell — `state_cli.py ... && sed -i
    ...` continua contando como alteracao, porque a segunda metade e.
    """
    if not command or _has_unquoted_shell_composition(command):
        return False
    return any(re.search(p, command, re.IGNORECASE) for p in STATE_CLI_PATTERNS)



#: Destinos de redirecionamento que nao sao arquivo do projeto.
_DESTINOS_NULOS = frozenset({'/dev/null', 'nul', 'NUL', 'con', 'CON'})


def _tokenize(command: str) -> list[str]:
    """Tokens do comando, com os operadores fora de aspas separados.

    Conteudo entre aspas nunca vira operador: `echo 'a > b'` nao escreve
    em `b`. E por isso que uma regex sobre a linha crua nao serve aqui.
    """
    tokens: list[str] = []
    atual: list[str] = []
    quote = None
    escaped = False

    def fechar():
        if atual:
            tokens.append(''.join(atual))
            atual.clear()

    for character in command:
        if escaped:
            atual.append(character)
            escaped = False
            continue
        if quote:
            if character == chr(92) and quote == chr(34):
                escaped = True
                continue
            if character == quote:
                quote = None
                continue
            atual.append(character)
            continue
        if character in {chr(39), chr(34)}:
            quote = character
            continue
        if character.isspace():
            fechar()
            continue
        if character == '>':
            fechar()
            if tokens and tokens[-1] == '>':
                tokens[-1] = '>>'
            else:
                tokens.append('>')
            continue
        if character in {';', '|', '&'}:
            fechar()
            tokens.append(character)
            continue
        atual.append(character)
    fechar()
    return tokens


_OPERADORES_TOKEN = frozenset({'>', '>>', ';', '|', '&'})


#: Binarios cujo unico efeito e ler. Cada nome aqui e a afirmacao "este comando
#: nao escreve", e uma afirmacao errada apaga alteracao de codigo do contador —
#: por isso a lista e curta e nao inclui interpretador (`python`, `awk`) nem
#: comando que muda de efeito pelo argumento (`find -delete`, `git config`).
_SOMENTE_LEITURA = frozenset({
    'cat', 'head', 'tail', 'wc', 'grep', 'rg', 'ls', 'pwd', 'echo', 'printf',
    'sort', 'uniq', 'cut', 'nl', 'basename', 'dirname', 'stat', 'diff', 'cmp',
    'date', 'sed', 'true', 'false',
})

#: Subcomandos de git que so leem. `branch`, `remote` e `config` ficam de fora:
#: os tres escrevem dependendo da flag.
_GIT_SOMENTE_LEITURA = frozenset({
    'status', 'log', 'diff', 'show', 'ls-files', 'rev-parse', 'blame',
    'shortlog', 'describe', 'cat-file', 'grep',
})


def is_read_only(command: str) -> bool:
    """Sei que este comando nao escreve — nao apenas "nao consegui ver escrita".

    `shell_write_targets` distingue "escreve em X" de "nao da para saber", e o
    chamador guarda o placeholder no segundo caso. Isso e o certo para um
    programa arbitrario, mas `grep`, `cat` e `git log` nao sao caso duvidoso:
    inspecionar o repositorio subia `code_revision` e invalidava a evidencia da
    suite, o que obrigava a rodar a suite de novo para conseguir fechar.

    A porta e estreita: todo segmento da linha tem de comecar por um binario da
    lista, e qualquer redirecionamento ja tira o comando daqui pelo chamador.
    """
    if not command:
        return False
    if shell_write_targets(command):
        return False
    tokens = _tokenize(command)
    segmento: list[str] = []
    segmentos: list[list[str]] = []
    for token in tokens:
        if token in _OPERADORES_TOKEN:
            if segmento:
                segmentos.append(segmento)
            segmento = []
            continue
        segmento.append(token)
    if segmento:
        segmentos.append(segmento)
    if not segmentos:
        return False
    for partes in segmentos:
        binario = partes[0].replace(chr(92), '/').rsplit('/', 1)[-1]
        if binario.endswith('.exe'):
            binario = binario[:-4]
        if binario == 'git':
            resto = [p for p in partes[1:] if not p.startswith('-')]
            if not resto or resto[0] not in _GIT_SOMENTE_LEITURA:
                return False
            continue
        if binario not in _SOMENTE_LEITURA:
            return False
        if binario == 'sed' and any(p.startswith('-i') for p in partes[1:]):
            return False
    return True


def shell_write_targets(command: str) -> list[str]:
    """Arquivos que este comando de shell escreve, ate onde da para atribuir.

    Existe porque `_handle_post_tool` registrava todo comando como o caminho
    sintetico 'shell-command'. Com PRIMARY KEY(task_id, path) e INSERT OR
    IGNORE, mil comandos viravam UMA linha, e nenhuma nomeava um arquivo — o
    contador de arquivos so crescia por Edit/Write. Em 2026-09-03 uma task
    que alterou 2 arquivos por heredoc registrou `files=0` e virou L0, e
    `proxy_regex_vs_observado` e calculado sobre esse rotulo.

    Cobre redirecionamento, `tee` e `sed -i`. NAO cobre programa que escreve
    por dentro (`python - <<PY` com `write_text`), e nao ha como cobrir: e um
    programa. Por isso o chamador mantem o placeholder quando esta lista sai
    vazia — 'nao da para saber' e diferente de 'nao escreveu'.
    """
    if not command:
        return []
    tokens = _tokenize(command)
    alvos: list[str] = []

    def considerar(candidato: str) -> None:
        if not candidato or candidato in _OPERADORES_TOKEN:
            return
        if candidato.startswith('-') or candidato in _DESTINOS_NULOS:
            return
        if candidato not in alvos:
            alvos.append(candidato)

    for indice, token in enumerate(tokens):
        if token in {'>', '>>'} and indice + 1 < len(tokens):
            considerar(tokens[indice + 1])
        elif token == 'tee':
            for seguinte in tokens[indice + 1:]:
                if seguinte in _OPERADORES_TOKEN:
                    break
                if seguinte.startswith('-'):
                    continue
                considerar(seguinte)
                break
        elif token == 'sed':
            fatia = []
            for seguinte in tokens[indice + 1:]:
                if seguinte in _OPERADORES_TOKEN:
                    break
                fatia.append(seguinte)
            if any(f.startswith('-i') for f in fatia) and fatia:
                considerar(fatia[-1])
    return alvos

def _response(payload: dict[str, Any]) -> Any:
    return payload.get("tool_response") or payload.get("toolResponse") or payload.get("output") or ""


def _walk(value: Any):
    if isinstance(value, dict):
        for nested in value.values():
            yield from _walk(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            yield from _walk(nested)
    else:
        yield value


def _explicit_exit_code(value: Any) -> int | None:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = re.sub(r"[^a-z]", "", str(key).casefold())
            if normalized in {"exitcode", "returncode"}:
                if isinstance(nested, int) and not isinstance(nested, bool):
                    return nested
                if isinstance(nested, str) and re.fullmatch(r"-?\d+", nested.strip()):
                    return int(nested)
        for nested in value.values():
            result = _explicit_exit_code(nested)
            if result is not None:
                return result
    elif isinstance(value, (list, tuple)):
        for nested in value:
            result = _explicit_exit_code(nested)
            if result is not None:
                return result
    return None


def _exit_code(payload: dict[str, Any]) -> int | None:
    explicit = _explicit_exit_code(_response(payload))
    if explicit is not None:
        return explicit
    event = _event_name(payload)
    if event == "PostToolUse":
        return 0
    if event == "PostToolUseFailure":
        text = str(payload.get("error") or "")
        for pattern in (
            r"(?:status|exit)\s+code\s*[:=]?\s*(-?\d+)",
            r"exit(?:ed)?\s+with\s+(?:non-zero\s+)?(?:status\s+)?(?:code\s+)?(-?\d+)",
            r"exit\s+status\s+(-?\d+)",
        ):
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return int(match.group(1))
        return 1
    return None


def _response_text(payload: dict[str, Any]) -> str:
    values = [value for value in _walk(_response(payload)) if isinstance(value, str)]
    error = payload.get("error")
    if isinstance(error, str) and error:
        values.append(error)
    return "\n".join(values)


def _write_heartbeat(
    payload: dict[str, Any], event: str, harness_root: str | Path | None
) -> None:
    if not event:
        return
    root = Path(harness_root or os.environ.get("HARNESS_DIR") or Path.home() / ".claude" / "harness")
    try:
        heartbeats = root / "heartbeats"
        heartbeats.mkdir(parents=True, exist_ok=True)
        temporary = heartbeats / f".{event}.tmp"
        temporary.write_text(str(time.time()), encoding="utf-8")
        temporary.replace(heartbeats / event)
    except OSError:
        pass


def _test_counts(payload: dict[str, Any]) -> tuple[int | None, int | None, str | None]:
    text = _response_text(payload)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest() if text else None
    if re.search(r"\b(no tests ran|collected 0 items|0 tests? (?:run|passed|total))\b", text, re.IGNORECASE):
        return 0, 0, digest
    passed = [int(value) for value in re.findall(r"\b(\d+)\s+passed\b", text, re.IGNORECASE)]
    failed = [int(value) for value in re.findall(r"\b(\d+)\s+failed\b", text, re.IGNORECASE)]
    errors = [int(value) for value in re.findall(r"\b(\d+)\s+errors?\b", text, re.IGNORECASE)]
    if passed or failed or errors:
        passed_count = max(passed, default=0)
        return passed_count + max(failed, default=0) + max(errors, default=0), passed_count, digest
    if re.search(r"\btest result:\s*ok\b", text, re.IGNORECASE) or re.search(r"(?m)^ok\s+\S+", text):
        return 1, 1, digest
    return None, None, digest


def _projection(bucket: Path) -> dict[str, Any]:
    try:
        value = json.loads((bucket / "state.json").read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _sync_projection(bucket: Path, projection: dict[str, Any], task: dict[str, Any]) -> None:
    projection.update(
        {
            "task_id": task["task_id"],
            "status": task["status"],
            "pipeline": task["pipeline"],
            "current_step": task["phase"],
            "revision": task["revision"],
            "code_revision": task["code_revision"],
            "verified": task["verified"],
            "stop_continuations": task["stop_continuations"],
            "pending_gate": task["pending_gate"],
            "scope_id": task["scope_id"],
            # A projecao do hook e a que sobrescreve o state com mais
            # frequencia. Sem esta linha ela reintroduzia a lista vazia a cada
            # PostToolUse, desfazendo o que o `state_cli` tivesse acabado de
            # projetar — as duas metades precisam existir juntas.
            "artifacts_so_far": [a["path"] for a in task.get("artifacts", [])],
        }
    )
    temporary = bucket / "state.json.transactional.tmp"
    temporary.write_text(json.dumps(projection, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(bucket / "state.json")


def _database_for_payload(
    payload: dict[str, Any], harness_root: str | Path | None
) -> tuple[Path, HarnessDatabase, dict[str, Any], dict[str, Any]] | None:
    root = Path(harness_root or os.environ.get("HARNESS_DIR") or Path.home() / ".claude" / "harness")
    bucket = ensure_state_dir(
        root,
        payload.get("cwd") or None,
        session_id=payload.get("session_id") or payload.get("sessionId") or None,
    )
    projection = _projection(bucket)
    task_id = projection.get("task_id")
    if not task_id or not (bucket / "harness.db").is_file():
        return None
    database = HarnessDatabase(bucket)
    try:
        task = database.task(str(task_id))
    except StateTransitionError:
        return None
    return bucket, database, projection, task


def _handle_post_tool(payload: dict[str, Any], context) -> str:
    bucket, database, projection, task = context
    tool_name = str(payload.get("tool_name") or payload.get("toolName") or "").casefold()
    command = _command(payload)
    if tool_name in SHELL_TOOLS and not is_state_management(command):
        # Atribuir o caminho real quando da; manter o placeholder quando nao da.
        # As duas metades importam: sem a primeira o contador de arquivos e cego
        # a escrita por shell; sem a segunda, um programa que escreve por dentro
        # passaria por "nao alterou nada".
        alvos = shell_write_targets(command)
        if alvos or not is_read_only(command):
            task = database.touch_files(task["task_id"], alvos or ["shell-command"])
    aviso = ""
    if is_trusted_verification(command):
        collected, passed, output_hash = _test_counts(payload)
        task = database.record_evidence(
            task["task_id"],
            evidence_type="test",
            command=command,
            exit_code=_exit_code(payload),
            tests_collected=collected,
            tests_passed=passed,
            output_hash=output_hash,
        )
        if collected is None:
            aviso = AVISO_SEM_CASOS
    elif looks_like_verification(command):
        aviso = AVISO_COMPOSICAO.format(sugestao=atomic_prefix(command) or command)
    _sync_projection(bucket, projection, task)
    return aviso


def _handle_stop(payload: dict[str, Any], context) -> str:
    if payload.get("stop_hook_active") or payload.get("stopHookActive"):
        return ""
    bucket, database, projection, task = context
    if task["status"] != "active" or not task["pipeline"] or task["verified"]:
        return ""
    task = database.register_stop_continuation(task["task_id"], limit=2)
    _sync_projection(bucket, projection, task)
    if task["pending_gate"] == "escalation":
        reason = (
            "HARNESS v3 escalation gate: verification remains incomplete after two continuations. "
            "Ask the user for direction with the concrete blocker and evidence."
        )
    else:
        reason = (
            "HARNESS v3 verification gate: continue the active harness-workflow pipeline and attach "
            "fresh test evidence before the final response."
        )
    return json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False)


def handle_payload(
    payload: dict[str, Any], *, harness_root: str | Path | None = None, event: str | None = None
) -> str:
    name = _event_name(payload, event)
    _write_heartbeat(payload, name, harness_root)
    if name == "Stop" and (payload.get("stop_hook_active") or payload.get("stopHookActive")):
        return ""
    context = _database_for_payload(payload, harness_root)
    if context is None:
        return ""
    if name in {"PostToolUse", "PostToolUseFailure"}:
        return _handle_post_tool(payload, context)
    if name == "Stop":
        return _handle_stop(payload, context)
    return ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event")
    args = parser.parse_args()
    try:
        payload = json.load(sys.stdin)
    except (OSError, ValueError):
        payload = {}
    output = handle_payload(payload, event=args.event)
    if not output:
        return 0
    if _event_name(payload, args.event) == "Stop":
        # O gate do Stop e `decision: block`, o unico canal que interrompe de
        # verdade. Ele sai cru: passar por `emit.py` o silenciaria, porque la o
        # Stop e `silent` por projeto.
        print(output)
        return 0
    _emitir(payload, args.event, output)
    return 0


def _emitir(payload: dict[str, Any], event: str | None, texto: str) -> None:
    """Avisos do PostToolUse pelo emissor unico, e nao por `print` solto.

    O aviso de composicao de shell existe para dizer "a evidencia deste teste
    NAO foi gravada". Ele saia por `print`, entao nao passava pelo extrato de
    `emissions.jsonl` e `check_hook_liveness.py --delivery` nao tinha como
    medir se chegava. Em 2026-09-03 uma suite verde de 551s foi descartada por
    um `| tail -12` e o aviso nao apareceu — invisivel para quem devia ler e
    invisivel para a auditoria, que e a combinacao que originou esta auditoria
    toda.
    """
    nome = _event_name(payload, event)
    try:
        import importlib.util

        caminho = os.path.join(os.path.dirname(os.path.abspath(__file__)), "emit.py")
        spec = importlib.util.spec_from_file_location("harness_emit", caminho)
        if spec is None or spec.loader is None:
            raise ImportError
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.Emitter(
            nome,
            hook="transactional",
            session_id=payload.get("session_id"),
            cwd=payload.get("cwd"),
        ).add("evidence_warning", texto).flush()
    except Exception:
        print(texto)


if __name__ == "__main__":
    raise SystemExit(main())
