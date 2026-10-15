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

Item GS (2026-10-15): a regra do git so olhava `reset --hard`, `clean -f`, `branch -D`, `checkout|restore .` e o push
forcado, e so pela grafia exata. `git stash drop|clear`, `git reflog expire|delete`, `git gc --prune=now`, `git prune`,
`git branch --delete --force`, `git update-ref -d`, `git filter-branch`, `git push --mirror` e as abreviacoes que o git
aceita (`--force-w`, `--mir`, `--ha`) passavam, e `timeout 5 git push --force` tambem: `timeout`, `nice`, `doas`,
`setsid` e os outros involucros de `_EXEC_PREFIXES` nao estavam na tabela. Agora cada subcomando tem a lista de opcoes
longas do git (`_GIT_OPCOES`) e a politica resolve o prefixo contra ela, como o git faz.

Stdlib pura: os hooks nao podem depender do pacote `mh` estar importavel, e o guard do Claude roda no prazo do host.
"""

from __future__ import annotations

import base64
import binascii
import re
import shlex
from collections.abc import Sequence
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


@dataclass(frozen=True)
class _Prefixo:
    """Como um involucro chega ao comando que ele executa.

    `valor`: opcoes que consomem o token seguinte. `posicionais`: argumentos obrigatorios antes do comando
    (`timeout DURACAO`, `flock ARQUIVO`, `chroot RAIZ`); opcoes entre eles tambem sao lidas. `numerico`: um posicional
    opcional, consumido so quando e numero (`chrt [prioridade]`). `script`: opcoes cujo valor e um script de shell
    (`su -c`, `flock ARQUIVO -c`). `permuta`: o involucro aceita opcoes depois do primeiro posicional (getopt do GNU
    sem `+`, como `su usuario -c '...'`), entao `script` vale em qualquer posicao antes de `--`. `junta`: o resto da
    linha e juntado e entregue a `sh -c` (`watch`), salvo com uma das opcoes de `exato`.
    """

    valor: frozenset[str] = frozenset()
    posicionais: int = 0
    numerico: bool = False
    script: frozenset[str] = frozenset()
    permuta: bool = False
    junta: bool = False
    exato: frozenset[str] = frozenset()


_SU_VALOR = frozenset({"-g", "--group", "-G", "--supp-group", "-s", "--shell", "-w", "--whitelist-environment"})
_SU_SCRIPT = frozenset({"-c", "--command", "--session-command"})

#: Prefixos que executam o comando seguinte. Criterio (item GS): programas cuja funcao documentada e executar o argv
#: que vem depois trocando so um atributo do processo (privilegio, prazo, prioridade, CPU, sessao, trava, buffer,
#: raiz, namespace, repeticao), dos pacotes base dos hosts: coreutils (`env`, `nice`, `nohup`, `stdbuf`, `timeout`,
#: `chroot`), util-linux (`setsid`, `ionice`, `chrt`, `taskset`, `flock`, `runuser`, `su`, `nsenter`, `unshare`,
#: `setpriv`, `setarch`, `prlimit`, `script`), procps (`watch`), a familia do sudo (`sudo`, `doas`, `pkexec`), expect
#: (`unbuffer`), findutils (`xargs`) e os do shell (`command`, `exec`, `time`). Fora, por nao serem involucros: os
#: interpretadores (`python -c`, `node -e`), `find -exec`, `parallel`, `make`, `ssh` (outra maquina) e o
#: `Start-Process` do PowerShell (outra gramatica). Censo de 15/10 nos 81 796 comandos dos transcripts do Claude:
#: `timeout` aparece 1 849 vezes na posicao de comando; dos novos, nenhum outro aparece.
_EXEC_PREFIXES: dict[str, _Prefixo] = {
    "env": _Prefixo(frozenset({"-u", "--unset", "-C", "--chdir"})),
    "command": _Prefixo(),
    "exec": _Prefixo(frozenset({"-a"})),
    "nohup": _Prefixo(),
    "time": _Prefixo(frozenset({"-f", "--format", "-o", "--output"})),
    "sudo": _Prefixo(frozenset({
        "-u", "--user", "-g", "--group", "-p", "--prompt", "-C", "--close-from", "-D", "--chdir",
        "-r", "--role", "-t", "--type", "-T", "--command-timeout", "-U", "--other-user", "-R", "--chroot",
    })),
    "xargs": _Prefixo(frozenset({
        "-a", "--arg-file", "-d", "--delimiter", "-E", "-I", "-L", "-n", "--max-args", "-P", "--max-procs",
        "-s", "--max-chars", "--process-slot-var",
    })),
    "doas": _Prefixo(frozenset({"-C", "-u"})),
    "pkexec": _Prefixo(frozenset({"--user"})),
    "runuser": _Prefixo(_SU_VALOR | {"-u", "--user"}, script=_SU_SCRIPT, permuta=True),
    "su": _Prefixo(_SU_VALOR, script=_SU_SCRIPT, permuta=True),
    "timeout": _Prefixo(frozenset({"-k", "--kill-after", "-s", "--signal"}), posicionais=1),
    "nice": _Prefixo(frozenset({"-n", "--adjustment"})),
    "ionice": _Prefixo(frozenset({"-c", "--class", "-n", "--classdata", "-p", "--pid", "-P", "--pgid", "-u", "--uid"})),
    "chrt": _Prefixo(frozenset({"-T", "--sched-runtime", "-P", "--sched-period", "-D", "--sched-deadline"}),
                     numerico=True),
    "taskset": _Prefixo(posicionais=1),
    "setsid": _Prefixo(),
    "stdbuf": _Prefixo(frozenset({"-i", "-o", "-e", "--input", "--output", "--error"})),
    "flock": _Prefixo(frozenset({"-w", "--wait", "--timeout", "-E", "--conflict-exit-code"}), posicionais=1,
                      script=frozenset({"-c", "--command"})),
    "chroot": _Prefixo(frozenset({"--userspec", "--groups"}), posicionais=1),
    "unbuffer": _Prefixo(),
    "watch": _Prefixo(frozenset({"-n", "--interval", "-q", "--equexit"}), junta=True,
                      exato=frozenset({"-x", "--exec"})),
    "nsenter": _Prefixo(frozenset({"-t", "--target", "-S", "--setuid", "-G", "--setgid"})),
    "unshare": _Prefixo(frozenset({
        "--map-user", "--map-group", "--map-users", "--map-groups", "-S", "--setuid", "-G", "--setgid",
        "--propagation", "-R", "--root", "-w", "--wd", "--monotonic", "--boottime",
    })),
    "setpriv": _Prefixo(frozenset({
        "--reuid", "--regid", "--ruid", "--euid", "--rgid", "--egid", "--groups", "--inh-caps", "--ambient-caps",
        "--bounding-set", "--securebits", "--pdeathsig", "--selinux-label", "--apparmor-profile",
        "--landlock-access", "--landlock-rule",
    })),
    "setarch": _Prefixo(posicionais=1),
    "prlimit": _Prefixo(frozenset({"-p", "--pid", "-o", "--output"})),
    "script": _Prefixo(frozenset({
        "-I", "--log-in", "-O", "--log-out", "-B", "--log-io", "-T", "--log-timing", "-m", "--logging-format",
        "-o", "--output-limit", "-E", "--echo",
    }), script=frozenset({"-c", "--command"}), permuta=True),
}


def _valor_de_script(tokens: Sequence[str], index: int, prefixo: _Prefixo) -> str | None:
    """O script de shell que esta opcao entrega (`-c '...'`, `--command=...`, `-lc '...'`, `-c'...'`), ou None."""
    token = tokens[index]
    proximo = tokens[index + 1] if index + 1 < len(tokens) else None
    if token in prefixo.script:
        return proximo
    for opcao in prefixo.script:
        if opcao.startswith("--") and token.startswith(opcao + "="):
            return token.split("=", 1)[1]
    if "-c" in prefixo.script and not token.startswith("--"):
        if re.fullmatch(r"-[A-Za-z]+c", token):  # `su -lc '...'`: o `c` fecha o grupo e o valor vem depois
            return proximo
        if token.startswith("-c") and len(token) > 2:  # `su -c'git push -f'`
            return token[2:]
    return None


def _script_permutado(tokens: Sequence[str], prefixo: _Prefixo) -> str | None:
    """Num involucro que permuta (`su usuario -c '...'`), o script de `-c` em qualquer posicao antes de `--`."""
    index = 0
    while index < len(tokens) and tokens[index] != "--":
        if tokens[index].startswith("-"):
            script = _valor_de_script(tokens, index, prefixo)
            if script is not None:
                return script
            index += 2 if tokens[index] in prefixo.valor else 1
        else:
            index += 1
    return None


def _depois_do_prefixo(nome: str, prefixo: _Prefixo, tokens: list[str], index: int) -> tuple[int, list[str] | None]:
    """Onde comeca o comando que o prefixo executa; ou, no segundo item, o argv que ele executa no lugar do resto."""
    posicionais = prefixo.posicionais
    numerico = prefixo.numerico
    exato = False
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            index += 1
            break
        if token.startswith("-"):
            split = _env_split_string(tokens, index) if nome == "env" else None
            if split is not None:
                return index, split
            script = _valor_de_script(tokens, index, prefixo)
            if script is not None:
                return index, ["sh", "-c", script]
            exato = exato or token in prefixo.exato
            index += 2 if token in prefixo.valor else 1
            continue
        if posicionais:
            posicionais -= 1
            index += 1
            continue
        if numerico and token.isdigit():
            numerico = False
            index += 1
            continue
        break
    if prefixo.junta and not exato:
        # `watch git push --force`: o watch junta o argv com espacos e entrega a `sh -c`.
        return index, (["sh", "-c", " ".join(tokens[index:])] if index < len(tokens) else [])
    return index, None


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
    definicao de funcao (`f() {`, `function f {`) tambem. Item GS: os involucros novos de `_EXEC_PREFIXES`, com os
    posicionais antes do comando (`timeout 5 git ...`) e o script de `su -c`, `flock ARQUIVO -c` e `watch`.
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
        prefixo = _EXEC_PREFIXES.get(name)
        if prefixo is None or (name == "command" and tokens[index + 1] in {"-v", "-V"}):
            break
        if prefixo.permuta and (script := _script_permutado(tokens[index + 1 :], prefixo)) is not None:
            return _program_tokens(["sh", "-c", script])
        index, desvio = _depois_do_prefixo(name, prefixo, tokens, index + 1)
        if desvio is not None:
            return _program_tokens(desvio)
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
@dataclass(frozen=True)
class _Opcoes:
    """As opcoes longas que um subcomando do git conhece, as que exigem valor e as letras curtas com valor."""

    longas: frozenset[str]
    com_valor: frozenset[str]
    curtas_com_valor: str


def _opcoes(listagem: str, ocultas: str = "", curtas_com_valor: str = "") -> _Opcoes:
    """Le a saida de `git <sub> --git-completion-helper` (o `=` final marca valor obrigatorio) e as ocultas dela."""
    nomes = [n for n in f"{listagem} {ocultas}".split() if n != "--"]
    return _Opcoes(
        frozenset(n.removeprefix("--").removesuffix("=") for n in nomes),
        frozenset(n.removeprefix("--").removesuffix("=") for n in nomes if n.endswith("=")),
        curtas_com_valor,
    )


#: As listas sao a saida literal de `git <sub> --git-completion-helper` do git 2.56.0. Ela omite as opcoes marcadas
#: NOCOMPLETE, que o git aceita igual: essas vao no segundo argumento, pelo manual (`branch --force`, `push
#: --branches`, `push --thin`). Uma opcao que falte aqui so pode fazer a politica bloquear a mais, nunca a menos:
#: o prefixo ambiguo conta todas as candidatas (`_candidatas`).
_GIT_OPCOES: dict[str, _Opcoes] = {
    "push": _opcoes(
        "--verbose --quiet --repo= --all --mirror --delete --tags --dry-run --porcelain --force --force-with-lease "
        "--force-if-includes --recurse-submodules= --receive-pack= --exec= --set-upstream --progress --prune "
        "--no-verify --follow-tags --signed --atomic --push-option= --ipv4 --ipv6 --verify -- --no-verbose "
        "--no-quiet --no-repo --no-all --no-branches --no-mirror --no-delete --no-tags --no-dry-run --no-porcelain "
        "--no-force --no-force-with-lease --no-force-if-includes --no-recurse-submodules --no-receive-pack "
        "--no-exec --no-set-upstream --no-progress --no-prune --no-follow-tags --no-signed --no-atomic "
        "--no-push-option",
        "--branches --thin --no-thin",
        curtas_com_valor="o",
    ),
    "branch": _opcoes(
        "--verbose --quiet --track --set-upstream-to= --unset-upstream --color --remotes --contains --no-contains "
        "--abbrev --all --delete --move --omit-empty --copy --list --show-current --create-reflog "
        "--edit-description --delete-merged= --dry-run --merged --no-merged --forked= --column --sort= "
        "--points-at= --ignore-case --recurse-submodules --format= -- --no-verbose --no-quiet --no-track "
        "--no-set-upstream-to --no-unset-upstream --no-color --no-abbrev --no-delete --no-move --no-omit-empty "
        "--no-copy --no-list --no-show-current --no-create-reflog --no-edit-description --no-dry-run --no-column "
        "--no-sort --no-points-at --no-ignore-case --no-recurse-submodules --no-format",
        "--force --no-force",
        curtas_com_valor="u",
    ),
    "reset": _opcoes(
        "--quiet --no-refresh --mixed --soft --hard --merge --keep --recurse-submodules --patch --auto-advance "
        "--unified= --inter-hunk-context= --intent-to-add --pathspec-from-file= --pathspec-file-nul --stdin "
        "--refresh -- --no-quiet --no-recurse-submodules --no-patch --no-auto-advance --no-intent-to-add "
        "--no-pathspec-from-file --no-pathspec-file-nul --no-stdin",
    ),
    "gc": _opcoes(
        "--quiet --prune --cruft --max-cruft-size= --aggressive --detach --keep-largest-pack --expire-to= "
        "--no-quiet -- --no-prune --no-cruft --no-aggressive --no-detach --no-keep-largest-pack --no-expire-to",
    ),
    "prune": _opcoes(
        "--dry-run --verbose --progress --expire= --exclude-promisor-objects --no-dry-run -- --no-verbose "
        "--no-progress --no-expire --no-exclude-promisor-objects",
    ),
    "reflog expire": _opcoes(
        "--dry-run --rewrite --updateref --verbose --expire= --expire-unreachable= --stale-fix --all "
        "--single-worktree --no-dry-run -- --no-rewrite --no-updateref --no-verbose --no-stale-fix --no-all "
        "--no-single-worktree",
    ),
    "reflog delete": _opcoes(
        "--dry-run --rewrite --updateref --verbose --no-dry-run -- --no-rewrite --no-updateref --no-verbose",
    ),
    "update-ref": _opcoes(
        "--no-deref --stdin --create-reflog --batch-updates --deref -- --no-stdin --no-create-reflog "
        "--no-batch-updates",
        curtas_com_valor="m",
    ),
}


@dataclass
class _Lidos:
    """As opcoes de um subcomando como o git as le: cada longa com as candidatas e o valor, as curtas e o resto."""

    longas: list[tuple[frozenset[str], str | None]] = field(default_factory=list)
    curtas: str = ""
    posicionais: list[str] = field(default_factory=list)

    def tem(self, nome: str) -> bool:
        return any(nome in candidatas for candidatas, _ in self.longas)

    def valores(self, nome: str) -> list[str | None]:
        return [valor for candidatas, valor in self.longas if nome in candidatas]

    def ensaio(self) -> bool:
        """`-n` ou `--dry-run` sem ambiguidade, e nenhum `--no-dry-run`."""
        longas = [candidatas for candidatas, _ in self.longas]
        return ("n" in self.curtas or frozenset({"dry-run"}) in longas) and frozenset({"no-dry-run"}) not in longas


def _candidatas(nome: str, opcoes: _Opcoes) -> frozenset[str]:
    """As opcoes longas que `--nome` pode ser: a exata, ou toda a que comeca com ele.

    O git aceita o prefixo unico de uma opcao longa (`--force-w` e `--force-with-lease`, `--mir` e `--mirror`). Se o
    prefixo e ambiguo, o git recusa e nada roda; a politica fica com todas as candidatas, para que o git de outra
    versao, em que o mesmo prefixo seja unico, nao passe.
    """
    if nome in opcoes.longas:
        return frozenset({nome})
    return frozenset(o for o in opcoes.longas if o.startswith(nome)) if nome else frozenset()


def _ler_opcoes(args: Sequence[str], opcoes: _Opcoes) -> _Lidos:
    """As opcoes de `args` pelas regras do parse-options do git: em qualquer posicao ate `--`, curtas agrupaveis."""
    lidos = _Lidos()
    index = 0
    while index < len(args):
        arg = args[index]
        index += 1
        if arg == "--":
            lidos.posicionais.extend(args[index:])
            break
        if arg.startswith("--"):
            nome, igual, valor = arg[2:].partition("=")
            candidatas = _candidatas(nome.lower(), opcoes)
            if not igual and candidatas and candidatas <= opcoes.com_valor:
                valor, igual = (args[index] if index < len(args) else ""), "="
                index += 1
            lidos.longas.append((candidatas, valor if igual else None))
        elif arg.startswith("-") and len(arg) > 1:
            for posicao, letra in enumerate(arg[1:], start=1):
                lidos.curtas += letra
                if letra in opcoes.curtas_com_valor:  # `-o<valor>`, ou `-uo <valor>`: o resto e o valor
                    index += posicao == len(arg) - 1
                    break
        else:
            lidos.posicionais.append(arg)
    return lidos


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

    `--force`, `--force-with-lease[=<ref>]`, `-f` sozinho ou num grupo (`-uf`), o refspec com `+` (`+main`,
    `+HEAD:main`) e `--mirror` (forca e apaga no remoto o que nao existe aqui), em grafia inteira ou abreviada.
    Ate o D-059 so `--force`, `-f` e `--force-with-lease` exatos contavam; ate o item GS, nem `--mirror` nem as
    abreviacoes. `--force-if-includes` sozinho nao forca nada. Apagar ramo remoto (`--delete`, `-d`, `:ramo`,
    `--prune`) e push comum: avisa.
    """
    lidos = _ler_opcoes(args, _GIT_OPCOES["push"])
    if lidos.tem("force") or lidos.tem("force-with-lease") or "f" in lidos.curtas:
        return "force push"
    if lidos.tem("mirror"):
        return "force push by --mirror"
    if any(arg.startswith("+") for arg in lidos.posicionais):
        return "force push by +refspec"
    return None


def _git_destrutivo(original: Sequence[str], args: Sequence[str]) -> str | None:
    """Por que este subcomando do git destroi trabalho ou o caminho de recupera-lo, ou None (o push fica a parte).

    `original` preserva a caixa (`-d` e `-D` sao opcoes diferentes); `args` e o mesmo em minusculas.
    """
    subcommand = args[0]
    flags = set(args[1:])
    if subcommand == "reset" and _ler_opcoes(original[1:], _GIT_OPCOES["reset"]).tem("hard"):
        return "destructive git reset --hard"
    if subcommand == "clean" and any(flag.startswith("-") and "f" in flag[1:] for flag in flags):
        return "destructive git clean force"
    if subcommand == "branch":
        lidos = _ler_opcoes(original[1:], _GIT_OPCOES["branch"])
        apaga = "d" in lidos.curtas or lidos.tem("delete") or lidos.tem("delete-merged")
        forca = "f" in lidos.curtas or lidos.tem("force")
        if any("D" in flag[1:] for flag in original[1:] if flag.startswith("-")) or (apaga and forca):
            return "forced branch deletion"
    if subcommand in {"checkout", "restore"} and "." in args[1:]:
        return "broad workspace restore"
    if subcommand == "stash" and len(args) > 1 and args[1] in {"drop", "clear"}:
        # O subcomando do stash e sempre o primeiro argumento; `git stash -q drop` e `push` com o caminho `drop`.
        return f"destructive git stash {args[1]}"
    reflog = f"reflog {args[1]}" if subcommand == "reflog" and len(args) > 1 else ""
    if reflog in _GIT_OPCOES and not _ler_opcoes(original[2:], _GIT_OPCOES[reflog]).ensaio():
        return f"destructive git {reflog}"
    if subcommand == "gc":
        for valor in _ler_opcoes(original[1:], _GIT_OPCOES["gc"]).valores("prune"):
            if valor is not None and valor.lower() != "never":  # `--prune` sem valor e o prazo padrao (2 semanas)
                return f"destructive git gc --prune={valor}"
    if subcommand == "prune" and not _ler_opcoes(original[1:], _GIT_OPCOES["prune"]).ensaio():
        return "destructive git prune"
    if subcommand == "update-ref" and "d" in _ler_opcoes(original[1:], _GIT_OPCOES["update-ref"]).curtas:
        return "destructive git update-ref -d"
    if subcommand == "filter-branch":
        return "history rewrite by git filter-branch"
    if subcommand == "filter-repo" and not ({"--analyze", "--dry-run"} & flags):
        return "history rewrite by git filter-repo"
    return None


def _evaluate_invocation(invocation: CommandInvocation) -> PolicyDecision:
    original = _git_subcommand_args(invocation.args) if invocation.program == "git" else invocation.args
    args = [arg.lower() for arg in original]
    if invocation.program == "git" and args:
        destrutivo = _git_destrutivo(original, args)
        if destrutivo is not None:
            return PolicyDecision("deny", destrutivo, invocation)
        if args[0] == "push":
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
