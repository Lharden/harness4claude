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

D-059 (2026-10-14): o comando era cortado nos operadores e cada trecho ia direto ao `shlex`, sem ler o que o shell
executa por dentro. `echo $(git push --force)`, crases, `(...)`, `{ ...; }`, `eval`, `sudo`, `env -S`, `xargs`,
`pwsh -EncodedCommand`, `if`/`then`/`do` e a continuacao de linha (`&& \\` + quebra) passavam como `allow`, e o push
forcado por refspec (`+main`), por `-uf` e por `--force-with-lease=<ref>` so avisava. Agora um lexer (`_Lexer`) le
o comando como o shell: substituicoes e subshells viram comandos aninhados, o corpo de heredoc e dado (e script
quando quem le o stdin e um shell), e a continuacao de linha some.

Stdlib pura: os hooks nao podem depender do pacote `mh` estar importavel, e o guard do Claude roda no prazo do host.
"""

from __future__ import annotations

import base64
import binascii
import re
import shlex
from dataclasses import dataclass, field
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
    except (ValueError, RecursionError) as exc:
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
    for segment in _Lexer(command).run():
        tokens = _program_tokens(shlex.split(segment.lexable, posix=True))
        if not tokens:
            continue
        program = PurePath(tokens[0].replace("\\", "/")).name.lower()
        program = program.removesuffix(".exe")
        invocation = CommandInvocation(program, tuple(tokens[1:]), segment.raw)
        invocations.append(invocation)
        nested = _nested_command(invocation)
        if nested is not None:
            invocations.extend(parse_invocations(nested))
        if program in _STDIN_SCRIPT_READERS:
            # `bash <<EOF ... EOF`: o corpo do heredoc e o script que o shell roda.
            for script in segment.stdin_scripts:
                invocations.extend(parse_invocations(script))
    return invocations


# ------------------------------------------------------------------------------------------------- o lexer


#: O que entra no lugar de uma substituicao no texto que vai ao `shlex`: o comando de dentro e julgado a parte, e o
#: valor dele so existe quando o shell roda (o mesmo limite de `$G`).
_SUBSTITUICAO = "__subst__"
_OPERATORS = ("&&", "||", "\r\n", "\n", ";", "|")
#: Palavras do shell que vem antes do comando e nao sao o programa.
_SHELL_KEYWORDS = frozenset({".", "{", "!", "if", "then", "else", "elif", "do", "while", "until"})
#: Quem le o stdin como script: o corpo de um heredoc entregue a eles e comando, nao dado.
_STDIN_SCRIPT_READERS = frozenset({"bash", "sh", "zsh", "dash", "ksh", "pwsh", "powershell", "cmd"})


@dataclass
class _Segment:
    raw: str
    lexable: str
    stdin_scripts: list[str] = field(default_factory=list)


class _Lexer:
    """Le o comando como o shell le, para achar todo comando que ele executa.

    Trechos separados por `&&`, `||`, `;`, `|`, `&` e quebra de linha; `$(...)`, crases, `<(...)` e `>(...)` (fora
    de aspas simples, inclusive dentro de aspas duplas) e `(...)` na posicao de comando viram trechos proprios; o
    corpo de heredoc sai do texto (com `<<EOF` sem aspas, as substituicoes dentro dele ainda sao lidas); `\\` +
    quebra de linha some. Crase sem par e literal: no bash e erro de sintaxe (nada roda) e no PowerShell e escape.
    """

    def __init__(self, text: str, out: list[_Segment] | None = None) -> None:
        self.text = text
        self.i = 0
        self.out: list[_Segment] = [] if out is None else out

    def run(self) -> list[_Segment]:
        self.commands(closing=None)
        return self.out

    def commands(self, closing: str | None) -> None:
        """Le comandos ate o `)` que fecha (deixa `self.i` nele) ou ate o fim do texto."""
        text = self.text
        raw: list[str] = []
        lex: list[str] = []
        quote: str | None = None
        depth = 0
        pending: list[tuple[str, bool, bool]] = []
        owned: list[tuple[str, bool, bool, _Segment]] = []

        def flush() -> None:
            segment = _Segment("".join(raw).strip(), "".join(lex).strip())
            owned.extend((delim, quoted, strip, segment) for delim, quoted, strip in pending)
            pending.clear()
            if segment.lexable:
                self.out.append(segment)
            raw.clear()
            lex.clear()

        def keep(chunk: str, lexed: str | None = None) -> None:
            raw.append(chunk)
            lex.append(chunk if lexed is None else lexed)

        while self.i < len(text):
            char = text[self.i]
            if quote == "'":
                keep(char)
                self.i += 1
                if char == "'":
                    quote = None
                continue
            if char == "\\":
                after = text[self.i + 1 : self.i + 3]
                if after.startswith("\n") or after == "\r\n":
                    width = 1 + (1 if after.startswith("\n") else 2)
                    keep(text[self.i : self.i + width], "")
                    self.i += width
                    continue
                keep(text[self.i : self.i + 2])
                self.i += 2
                continue
            if text.startswith("$(", self.i) or (quote is None and text[self.i : self.i + 2] in {"<(", ">("}):
                start = self.i
                self.i += 2
                self.commands(closing=")")
                self.i = min(self.i + 1, len(text))
                keep(text[start : self.i], _SUBSTITUICAO)
                continue
            if char == "`":
                fim = self._backtick_end(self.i + 1)
                if fim is not None:
                    inner = _unescape_backtick(text[self.i + 1 : fim - 1])
                    _Lexer(inner, self.out).commands(closing=None)
                    keep(text[self.i : fim], _SUBSTITUICAO)
                    self.i = fim
                    continue
            if char == '"':
                quote = None if quote == '"' else '"'
                keep(char)
                self.i += 1
                continue
            if quote == '"':
                keep(char)
                self.i += 1
                continue
            # ---- fora de aspas
            if char == "'":
                quote = "'"
                keep(char)
                self.i += 1
                continue
            if text.startswith("<<<", self.i):
                keep("<<<")
                self.i += 3
                continue
            if text.startswith("<<", self.i) and self._heredoc_operator(keep, pending):
                continue
            if char == "(":
                if all(word in _SHELL_KEYWORDS for word in "".join(lex).split()):
                    start = self.i
                    self.i += 1
                    self.commands(closing=")")
                    self.i = min(self.i + 1, len(text))
                    keep(text[start : self.i], " ")
                    continue
                depth += 1
            elif char == ")":
                if depth:
                    depth -= 1
                elif closing == ")":
                    flush()
                    return
            operator = None
            for candidate in _OPERATORS:
                if text.startswith(candidate, self.i):
                    operator = candidate
                    break
            if operator is None and char == "&" and not _is_redirection_ampersand(text, self.i):
                operator = "&"
            if operator is not None:
                flush()
                self.i += len(operator)
                if operator.endswith("\n"):
                    for delim, quoted, strip, segment in owned:
                        body = self._heredoc_body(delim, strip, closing)
                        segment.stdin_scripts.append(body)
                        if not quoted:
                            _Lexer(body, self.out).expansions()
                    owned.clear()
                continue
            keep(char)
            self.i += 1
        if quote is not None:
            raise ValueError("unclosed quote")
        flush()

    def expansions(self) -> None:
        """Corpo de heredoc com delimitador sem aspas: aspas sao literais, mas `$(...)` e crases rodam."""
        text = self.text
        while self.i < len(text):
            if text[self.i] == "\\":
                self.i += 2
            elif text.startswith("$(", self.i):
                self.i += 2
                self.commands(closing=")")
                self.i += 1
            elif text[self.i] == "`" and (fim := self._backtick_end(self.i + 1)) is not None:
                _Lexer(_unescape_backtick(text[self.i + 1 : fim - 1]), self.out).commands(closing=None)
                self.i = fim
            else:
                self.i += 1

    def _backtick_end(self, start: int) -> int | None:
        """O indice depois da crase que fecha, ou None se ela nao fecha."""
        index = start
        while index < len(self.text):
            if self.text[index] == "\\":
                index += 2
                continue
            if self.text[index] == "`":
                return index + 1
            index += 1
        return None

    def _heredoc_operator(self, keep, pending: list[tuple[str, bool, bool]]) -> bool:
        """`<<EOF`, `<<-EOF`, `<< 'EOF'`: guarda o delimitador para a proxima quebra de linha."""
        text = self.text
        index = self.i + 2
        strip = text.startswith("-", index)
        index += strip
        while index < len(text) and text[index] in " \t":
            index += 1
        word: list[str] = []
        quoted = False
        inner_quote: str | None = None
        while index < len(text):
            char = text[index]
            if inner_quote is not None:
                if char == inner_quote:
                    inner_quote = None
                else:
                    word.append(char)
                index += 1
                continue
            if char in {"'", '"'}:
                inner_quote = char
                quoted = True
                index += 1
                continue
            if char == "\\":
                quoted = True
                word.append(text[index + 1 : index + 2])
                index += 2
                continue
            if char in " \t\r\n;&|<>()":
                break
            word.append(char)
            index += 1
        if inner_quote is not None:
            raise ValueError("unclosed quote")
        delimiter = "".join(word)
        if not delimiter:
            return False
        keep(text[self.i : index])
        self.i = index
        pending.append((delimiter, quoted, strip))
        return True

    def _heredoc_body(self, delimiter: str, strip: bool, closing: str | None) -> str:
        """Consome as linhas do corpo ate a linha do delimitador (ou o fim do texto)."""
        text = self.text
        lines: list[str] = []
        while self.i < len(text):
            newline = text.find("\n", self.i)
            end = len(text) if newline < 0 else newline
            line = text[self.i : end].removesuffix("\r")
            candidate = line.lstrip("\t") if strip else line
            if candidate == delimiter:
                self.i = end + 1 if newline >= 0 else end
                return "\n".join(lines)
            if closing == ")" and candidate.startswith(delimiter):
                rest = candidate[len(delimiter) :]
                if rest.lstrip().startswith(")"):
                    # `EOF)` na mesma linha, dentro de `$(...)`: o `)` fecha a substituicao.
                    self.i += len(line) - len(rest.lstrip())
                    return "\n".join(lines)
            lines.append(line)
            self.i = end + 1 if newline >= 0 else end
        return "\n".join(lines)


def _unescape_backtick(inner: str) -> str:
    """Dentro de crases, `\\` so escapa `$`, crase e `\\`."""
    return re.sub(r"\\([$`\\])", r"\1", inner)


def _is_redirection_ampersand(command: str, index: int) -> bool:
    """O `&` de `2>&1`, `>&2`, `<&3` e `&>` faz parte de um redirecionamento; nao separa comandos."""
    before = command[index - 1] if index > 0 else ""
    after = command[index + 1] if index + 1 < len(command) else ""
    return before in {">", "<"} or after == ">"


# --------------------------------------------------------------------------------------- o programa que roda


# Atribuicao de variavel antes do programa (`GIT_TRACE=1 git push`): o programa vem depois.
_ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# Definicao de funcao (`f() { git push --force; }`): o corpo vem depois do nome.
_FUNCTION_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*\(\)$")
# Prefixos que executam o comando seguinte, com as opcoes deles que consomem o token seguinte.
_EXEC_PREFIXES: dict[str, frozenset[str]] = {
    "env": frozenset({"-u", "--unset", "-C", "--chdir"}),
    "command": frozenset(),
    "exec": frozenset({"-a"}),
    "nohup": frozenset(),
    "time": frozenset(),
    "sudo": frozenset({
        "-u", "--user", "-g", "--group", "-p", "--prompt", "-C", "--close-from", "-D", "--chdir",
        "-r", "--role", "-t", "--type", "-T", "--command-timeout", "-U", "--other-user", "-R", "--chroot",
    }),
    "xargs": frozenset({
        "-a", "--arg-file", "-d", "--delimiter", "-E", "-I", "-L", "-n", "--max-args", "-P", "--max-procs",
        "-s", "--max-chars", "--process-slot-var",
    }),
}


def _env_split_string(tokens: list[str], index: int) -> list[str] | None:
    """`env -S 'git push --force'`: o argumento de `-S` e partido em palavras e vira o comando."""
    option = tokens[index]
    if option in {"-S", "--split-string"} and index + 1 < len(tokens):
        value, rest = tokens[index + 1], tokens[index + 2 :]
    elif option.startswith("--split-string="):
        value, rest = option.split("=", 1)[1], tokens[index + 1 :]
    elif option.startswith("-S") and len(option) > 2:
        value, rest = option[2:], tokens[index + 1 :]
    else:
        return None
    return shlex.split(value, posix=True) + rest


def _program_tokens(tokens: list[str]) -> list[str]:
    """Os tokens a partir do programa que de fato roda.

    Ate 2026-10-14 o programa era sempre o primeiro token: `& git push --force` (operador de chamada do
    PowerShell), `. git ...` (dot-source), `GIT_TRACE=1 git ...` e `env|command|exec|nohup|time git ...`
    eram lidos como outro programa e passavam sem a politica. `command -v` so consulta; nao executa.
    D-059: `sudo`, `xargs`, `env -S '...'`, as palavras do shell (`{`, `!`, `if`, `then`, `do`...) e a
    definicao de funcao (`f() {`, `function f {`) tambem.
    """
    index = 0
    while index < len(tokens) - 1:
        token = tokens[index]
        name = token.lower()
        if token in _SHELL_KEYWORDS or _ENV_ASSIGNMENT.match(token) or _FUNCTION_NAME.match(token):
            index += 1
            continue
        if name == "function":  # `function f { ...; }`: o corpo e o comando que roda quando `f` e chamada
            index += 2
            continue
        if name not in _EXEC_PREFIXES or (name == "command" and tokens[index + 1] in {"-v", "-V"}):
            break
        index += 1
        while index < len(tokens) and tokens[index].startswith("-"):
            split = _env_split_string(tokens, index) if name == "env" else None
            if split is not None:
                return _program_tokens(split)
            if tokens[index] == "--":
                index += 1
                break
            index += 2 if tokens[index] in _EXEC_PREFIXES[name] else 1
    return tokens[index:]


# Shells em que o script vem no primeiro argumento depois de um grupo de opcoes com `c` (`-c`, `-lc`, `-ec`).
_SH_FAMILY = frozenset({"bash", "sh", "zsh", "dash", "ksh"})
_SH_SCRIPT_FLAG = re.compile(r"^-[A-Za-z]*c[A-Za-z]*$")


def _nested_command(invocation: CommandInvocation) -> str | None:
    """O script que um shell aninhado vai rodar, pelas regras de cada shell.

    Ate 2026-10-14 so `-c`/`/c` exatos como primeiro argumento abriam o script, e do `cmd` e do PowerShell
    so o token seguinte era lido. `bash -lc '...'`, `cmd /c git push --force` e `pwsh -Command git push
    --force` passavam sem a politica (master-harness, plano-desktop §13.26). D-059: `eval`, `Invoke-Expression`
    e `pwsh -EncodedCommand` (base64 de UTF-16LE) tambem.
    """
    args = list(invocation.args)
    lowered = [arg.lower() for arg in args]
    if invocation.program in {"pwsh", "powershell"}:
        for index, arg in enumerate(lowered):
            # O PowerShell aceita qualquer prefixo de `-Command`, e tudo o que vem depois e a linha do comando.
            if len(arg) >= 2 and "-command".startswith(arg):
                return " ".join(args[index + 1 :]) or None
            # `-e`, `-ec`, `-enc`... `-EncodedCommand`: o valor e o comando em base64 de UTF-16LE.
            if (arg == "-ec" or (len(arg) >= 2 and "-encodedcommand".startswith(arg))) and index + 1 < len(args):
                return _decode_powershell(args[index + 1])
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
    if invocation.program == "eval":
        return " ".join(args) or None
    if invocation.program in {"iex", "invoke-expression"}:
        if lowered and len(lowered[0]) >= 2 and "-command".startswith(lowered[0]):
            args = args[1:]
        return " ".join(args) or None
    return None


def _decode_powershell(value: str) -> str | None:
    """O comando de `-EncodedCommand`; None se o valor nao decodifica (o PowerShell recusa e nada roda)."""
    try:
        return base64.b64decode(value, validate=True).decode("utf-16-le") or None
    except (binascii.Error, ValueError):
        return None


# --------------------------------------------------------------------------------------------- a decisao


# Opcoes globais do git que consomem o token seguinte. A forma `--opcao=valor`
# ocupa um token so e cai no caso geral.
_GIT_GLOBAL_WITH_VALUE = frozenset({
    "-c", "-C", "--git-dir", "--work-tree", "--namespace",
    "--config-env", "--super-prefix", "--exec-path",
})
# Opcoes do `git push` que consomem o token seguinte: o valor delas nao e refspec.
_PUSH_WITH_VALUE = frozenset({"-o", "--push-option", "--repo", "--receive-pack", "--exec"})


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


def _force_push(args: list[str]) -> str | None:
    """Por que este `git push` e forcado, ou None.

    `--force`, `--force-with-lease[=<ref>]`, `-f` sozinho ou num grupo (`-uf`) e o refspec com `+` (`+main`,
    `+HEAD:main`). Ate o D-059 so `--force`, `-f` e `--force-with-lease` exatos contavam. `--force-if-includes`
    sozinho nao forca nada. Apagar ramo remoto (`--delete`, `-d`, `:ramo`) e push comum: avisa.
    """
    positional = False
    skip = False
    for arg in args:
        if skip:
            skip = False
            continue
        if positional or not arg.startswith("-"):
            if arg.startswith("+"):
                return "force push by +refspec"
            continue
        if arg == "--":
            positional = True
        elif arg in {"--force", "--force-with-lease"} or arg.startswith("--force-with-lease="):
            return "force push"
        elif arg in _PUSH_WITH_VALUE:
            skip = True
        elif not arg.startswith("--"):
            for position, letter in enumerate(arg[1:], start=1):
                if letter == "f":
                    return "force push"
                if letter == "o":  # `-o<valor>` ou `-uo <valor>`: o resto e o valor de push-option
                    skip = position == len(arg) - 1
                    break
    return None


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
            forced = _force_push(args[1:])
            if forced is not None:
                return PolicyDecision("deny", forced, invocation)
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
