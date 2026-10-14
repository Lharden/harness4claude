# DERIVADO - nao edite este arquivo.
#
# A fonte e `master-harness/mh/command_policy.py`, e ela e a unica
# dona da politica de comando do git guard. O repositorio do host `claude`
# nao importa o master-harness: recebe o arquivo inteiro. O que cada host faz
# com a decisao (bloquear, avisar, o fallback do Claude) mora no hook dele.
#
# Para mudar o que esta aqui: edite a fonte e rode `mh identidade semear`.
# Para saber se derivou: `mh identidade check` (sai 1 se divergir).
#
# ---- fim do cabecalho vendorizado; daqui para baixo e copia exata ----
"""A politica de comando do git guard dos dois hosts: uma fonte, duas copias geradas.

Le um comando de shell e decide `deny`, `require_approval`, `warn`, `allow` ou `unknown`. Quem decide o que fazer
com a decisao e o hook de cada host: `harness4claude/hooks/harness_git_guard.py` (que ainda tenta um fallback linha
a linha no `unknown`) e `harness4codex/harness4codex/git_guard.py`.

Ate 2026-10-14 havia duas versoes da mesma linhagem, mantidas a mao, e o conserto do harness4codex (`afb37ff`:
operador `&`, prefixos e shells aninhados) nao chegou ao harness4claude: 11 de 23 formas de comando git destrutivo
passavam no guard do Claude (master-harness, plano-desktop §13.38). Agora este arquivo e a fonte, e
`mh identidade semear --artefato command_policy` entrega as duas copias, com marca; `mh identidade check` confere.

Stdlib pura: os hooks nao podem depender do pacote `mh` estar importavel, e o guard do Claude roda no prazo do host.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import PurePath


@dataclass(frozen=True)
class CommandInvocation:
    program: str
    args: tuple[str, ...]
    raw: str


@dataclass(frozen=True)
class PolicyDecision:
    action: str
    reason: str = ""
    invocation: CommandInvocation | None = None


def evaluate_command(command: str) -> PolicyDecision:
    try:
        invocations = parse_invocations(command or "")
    except ValueError as exc:
        return PolicyDecision("unknown", f"command parse failed: {exc}")
    decision = PolicyDecision("allow")
    for invocation in invocations:
        current = _evaluate_invocation(invocation)
        if current.action == "deny":
            return current
        if (current.action == "require_approval" and decision.action not in {"deny"}) or (
            current.action == "warn" and decision.action == "allow"
        ):
            decision = current
    return decision


def parse_invocations(command: str) -> list[CommandInvocation]:
    invocations: list[CommandInvocation] = []
    for segment in _split_shell(command):
        try:
            tokens = shlex.split(segment, posix=True)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        tokens = _program_tokens(tokens)
        if not tokens:
            continue
        program = PurePath(tokens[0].replace("\\", "/")).name.lower()
        program = program.removesuffix(".exe")
        invocation = CommandInvocation(program, tuple(tokens[1:]), segment.strip())
        invocations.append(invocation)
        nested = _nested_command(invocation)
        if nested is not None:
            invocations.extend(parse_invocations(nested))
    return invocations


def _split_shell(command: str) -> list[str]:
    result: list[str] = []
    buffer: list[str] = []
    quote: str | None = None
    escaped = False
    index = 0
    while index < len(command):
        char = command[index]
        if escaped:
            buffer.append(char)
            escaped = False
            index += 1
            continue
        if char == "\\" and quote != "'":
            buffer.append(char)
            escaped = True
            index += 1
            continue
        if char in {'"', "'"}:
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
            buffer.append(char)
            index += 1
            continue
        if quote is None:
            operator = None
            for candidate in ("&&", "||", "\r\n", "\n", ";", "|"):
                if command.startswith(candidate, index):
                    operator = candidate
                    break
            if operator is None and char == "&" and not _is_redirection_ampersand(command, index):
                operator = "&"
            if operator is not None:
                value = "".join(buffer).strip()
                if value:
                    result.append(value)
                buffer = []
                index += len(operator)
                continue
        buffer.append(char)
        index += 1
    if quote is not None:
        raise ValueError("unclosed quote")
    value = "".join(buffer).strip()
    if value:
        result.append(value)
    return result


def _is_redirection_ampersand(command: str, index: int) -> bool:
    """O `&` de `2>&1`, `>&2`, `<&3` e `&>` faz parte de um redirecionamento; nao separa comandos."""
    before = command[index - 1] if index > 0 else ""
    after = command[index + 1] if index + 1 < len(command) else ""
    return before in {">", "<"} or after == ">"


# Atribuicao de variavel antes do programa (`GIT_TRACE=1 git push`): o programa vem depois.
_ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# Prefixos que executam o comando seguinte, com as opcoes deles que consomem o token seguinte.
_EXEC_PREFIXES: dict[str, frozenset[str]] = {
    "env": frozenset({"-u", "--unset", "-C", "--chdir"}),
    "command": frozenset(),
    "exec": frozenset({"-a"}),
    "nohup": frozenset(),
    "time": frozenset(),
}


def _program_tokens(tokens: list[str]) -> list[str]:
    """Os tokens a partir do programa que de fato roda.

    Ate 2026-10-14 o programa era sempre o primeiro token: `& git push --force` (operador de chamada do
    PowerShell), `. git ...` (dot-source), `GIT_TRACE=1 git ...` e `env|command|exec|nohup|time git ...`
    eram lidos como outro programa e passavam sem a politica. `command -v` so consulta; nao executa.
    """
    index = 0
    while index < len(tokens) - 1:
        token = tokens[index]
        name = token.lower()
        if token == "." or _ENV_ASSIGNMENT.match(token):
            index += 1
            continue
        if name not in _EXEC_PREFIXES or (name == "command" and tokens[index + 1] in {"-v", "-V"}):
            break
        index += 1
        while index < len(tokens) - 1 and tokens[index].startswith("-"):
            index += 2 if tokens[index] in _EXEC_PREFIXES[name] else 1
    return tokens[index:]


# Shells em que o script vem no primeiro argumento depois de um grupo de opcoes com `c` (`-c`, `-lc`, `-ec`).
_SH_FAMILY = frozenset({"bash", "sh", "zsh", "dash", "ksh"})
_SH_SCRIPT_FLAG = re.compile(r"^-[A-Za-z]*c[A-Za-z]*$")


def _nested_command(invocation: CommandInvocation) -> str | None:
    """O script que um shell aninhado vai rodar, pelas regras de cada shell.

    Ate 2026-10-14 so `-c`/`/c` exatos como primeiro argumento abriam o script, e do `cmd` e do PowerShell
    so o token seguinte era lido. `bash -lc '...'`, `cmd /c git push --force` e `pwsh -Command git push
    --force` passavam sem a politica (master-harness, plano-desktop §13.26).
    """
    args = list(invocation.args)
    lowered = [arg.lower() for arg in args]
    if invocation.program in {"pwsh", "powershell"}:
        # O PowerShell aceita qualquer prefixo de `-Command`, e tudo o que vem depois e a linha do comando.
        for index, arg in enumerate(lowered):
            if len(arg) >= 2 and "-command".startswith(arg):
                return " ".join(args[index + 1 :]) or None
        return None
    if invocation.program == "cmd":
        # Depois de `/c` ou `/k`, o resto da linha e o comando; `/s`, `/q` e `/d` podem vir antes.
        for index, arg in enumerate(lowered):
            if arg in {"/c", "/k"}:
                return " ".join(args[index + 1 :]) or None
        return None
    if invocation.program in _SH_FAMILY:
        for index, arg in enumerate(args):
            if _SH_SCRIPT_FLAG.match(arg):
                return args[index + 1] if index + 1 < len(args) else None
    return None


# Opcoes globais do git que consomem o token seguinte. A forma `--opcao=valor`
# ocupa um token so e cai no caso geral.
_GIT_GLOBAL_WITH_VALUE = frozenset({
    "-c", "-C", "--git-dir", "--work-tree", "--namespace",
    "--config-env", "--super-prefix", "--exec-path",
})


def _git_subcommand_args(args: tuple[str, ...]) -> tuple[str, ...]:
    """Os argumentos a partir do subcomando, pulando as opcoes globais.

    Ate 2026-10-07 o subcomando era `args[0]`, e `git -C <dir> branch -D x` era
    lido como o subcomando `-c`: 12 `branch -D` reais passaram como allow nos
    transcripts da maquina, e 41 pushes sem o aviso.
    """
    index = 0
    while index < len(args) and args[index].startswith("-"):
        index += 2 if args[index] in _GIT_GLOBAL_WITH_VALUE else 1
    return args[index:]


def _evaluate_invocation(invocation: CommandInvocation) -> PolicyDecision:
    original = _git_subcommand_args(invocation.args) if invocation.program == "git" else invocation.args
    args = [arg.lower() for arg in original]
    if invocation.program == "git" and args:
        subcommand = args[0]
        flags = set(args[1:])
        if subcommand == "reset" and "--hard" in flags:
            return PolicyDecision("deny", "destructive git reset --hard", invocation)
        if subcommand == "clean" and any(flag.startswith("-") and "f" in flag[1:] for flag in flags):
            return PolicyDecision("deny", "destructive git clean force", invocation)
        if subcommand == "branch" and any("D" in flag[1:] for flag in original[1:] if flag.startswith("-")):
            return PolicyDecision("deny", "forced branch deletion", invocation)
        if subcommand in {"checkout", "restore"} and "." in args[1:]:
            return PolicyDecision("deny", "broad workspace restore", invocation)
        if subcommand == "push":
            if any(flag in {"--force", "-f", "--force-with-lease"} for flag in flags):
                return PolicyDecision("deny", "force push", invocation)
            return PolicyDecision("warn", "confirm the reviewed git push target and diff", invocation)
    # Os dois hosts mudam o proprio registro de plugins pela CLI: `codex plugin add` e `claude plugin install`.
    if (
        invocation.program in {"codex", "claude"}
        and len(args) >= 2
        and args[0] in {"plugin", "plugins"}
        and args[1] in {"add", "install", "remove", "uninstall"}
    ):
        return PolicyDecision("require_approval", f"{invocation.program} plugin registry mutation", invocation)
    return PolicyDecision("allow", invocation=invocation)
