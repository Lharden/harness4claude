from __future__ import annotations

SHELL_TOOLS = {"bash", "shell", "powershell"}


def touch_target(tool_name: str, file_path: str) -> str | None:
    normalized_tool = tool_name.strip().casefold()
    if file_path.strip():
        return file_path.strip()
    if normalized_tool in SHELL_TOOLS:
        return "<shell-command>"
    return None


def counts_as_modified_file(tool_name: str, file_path: str) -> bool:
    return bool(file_path.strip()) and tool_name.strip().casefold() not in SHELL_TOOLS
