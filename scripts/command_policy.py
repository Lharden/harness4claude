from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePath
import argparse
import json
import shlex


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
        if current.action == "require_approval" and decision.action not in {"deny"}:
            decision = current
        elif current.action == "warn" and decision.action == "allow":
            decision = current
    return decision


def parse_invocations(command: str) -> list[CommandInvocation]:
    invocations: list[CommandInvocation] = []
    for segment in _split_shell(command):
        try:
            tokens = shlex.split(segment, posix=True)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        if not tokens:
            continue
        program = PurePath(tokens[0].replace("\\", "/")).name.lower()
        if program.endswith(".exe"):
            program = program[:-4]
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


def _nested_command(invocation: CommandInvocation) -> str | None:
    args = list(invocation.args)
    if invocation.program in {"pwsh", "powershell"}:
        for marker in ("-command", "-c"):
            if marker in [arg.lower() for arg in args]:
                return args[[arg.lower() for arg in args].index(marker) + 1]
    if invocation.program in {"bash", "sh", "cmd"} and args:
        if args[0].lower() in {"-c", "/c"} and len(args) > 1:
            return args[1]
    return None


def _evaluate_invocation(invocation: CommandInvocation) -> PolicyDecision:
    args = [arg.lower() for arg in invocation.args]
    if invocation.program == "git" and args:
        subcommand = args[0]
        flags = set(args[1:])
        if subcommand == "reset" and "--hard" in flags:
            return PolicyDecision("deny", "destructive git reset --hard", invocation)
        if subcommand == "clean" and any(flag.startswith("-") and "f" in flag[1:] for flag in flags):
            return PolicyDecision("deny", "destructive git clean force", invocation)
        if subcommand == "branch" and any("D" in flag[1:] for flag in invocation.args[1:] if flag.startswith("-")):
            return PolicyDecision("deny", "forced branch deletion", invocation)
        if subcommand in {"checkout", "restore"} and "." in args[1:]:
            return PolicyDecision("deny", "broad workspace restore", invocation)
        if subcommand == "push":
            if any(flag in {"--force", "-f", "--force-with-lease"} for flag in flags):
                return PolicyDecision("deny", "force push", invocation)
            return PolicyDecision("warn", "confirm the reviewed git push target and diff", invocation)
    if invocation.program in {"codex", "claude"} and len(args) >= 2:
        if args[0] in {"plugin", "plugins"} and args[1] in {"add", "install", "remove", "uninstall"}:
            return PolicyDecision("require_approval", f"{invocation.program} plugin registry mutation", invocation)
    return PolicyDecision("allow", invocation=invocation)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--command", required=True)
    args = parser.parse_args(argv)
    decision = evaluate_command(args.command)
    print(json.dumps({
        "action": decision.action,
        "reason": decision.reason,
        "program": decision.invocation.program if decision.invocation else None,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
