"""Read-only readiness checks for the Obsidian vault sync setup."""

from __future__ import annotations

import argparse
import json
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REQUIRED_COMMUNITY_PLUGINS = {
    "dataview",
    "obsidian-local-rest-api",
    "smart-connections",
}

SYNC_PLUGIN_OPTIONS = {
    "github-sync-multi-platform",
    "remotely-save",
    "remotely-secure",
}

REQUIRED_GITIGNORE_ENTRIES = (
    ".obsidian/workspace.json",
    ".obsidian/workspace-mobile.json",
    ".obsidian/plugins/obsidian-local-rest-api/data.json",
    ".obsidian/plugins/remotely-save/data.json",
    ".obsidian/plugins/remotely-secure/data.json",
    ".smart-env/",
    ".ruff_cache/",
    ".pytest_cache/",
    "__pycache__/",
    "*.py[cod]",
    "*.tar.gz",
    "*.rar",
    ".git/",
    "*/.git/",
)

LOCAL_REST_HEALTH_URL = "https://127.0.0.1:27124/"


@dataclass(frozen=True)
class Finding:
    """A single readiness finding."""

    level: str
    message: str


def load_json(path: Path) -> Any:
    """Load a JSON file using UTF-8."""
    return json.loads(path.read_text(encoding="utf-8"))


def load_json_list(path: Path) -> set[str]:
    """Load a JSON list of strings, returning an empty set when absent."""
    if not path.exists():
        return set()
    data = load_json(path)
    if not isinstance(data, list):
        raise ValueError(f"Expected a JSON list in {path}")
    return {str(item) for item in data}


def gitignore_entries(root: Path) -> set[str]:
    """Return meaningful entries from the root .gitignore."""
    gitignore = root / ".gitignore"
    if not gitignore.exists():
        return set()
    entries: set[str] = set()
    for line in gitignore.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            entries.add(stripped)
    return entries


def installed_plugins(root: Path) -> set[str]:
    """Return plugin directory names installed under .obsidian/plugins."""
    plugins_dir = root / ".obsidian" / "plugins"
    if not plugins_dir.exists():
        return set()
    return {path.name for path in plugins_dir.iterdir() if path.is_dir()}


def core_plugins(root: Path) -> dict[str, bool]:
    """Return Obsidian core plugin states."""
    path = root / ".obsidian" / "core-plugins.json"
    if not path.exists():
        return {}
    data = load_json(path)
    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return {str(key): bool(value) for key, value in data.items()}


def nested_git_repositories(root: Path) -> list[str]:
    """Find nested .git directories below the vault root."""
    nested: list[str] = []
    for path in root.rglob(".git"):
        if not path.is_dir():
            continue
        if path.parent == root:
            continue
        nested.append(path.parent.relative_to(root).as_posix())
    return sorted(nested)


def local_rest_status(url: str = LOCAL_REST_HEALTH_URL) -> dict[str, Any]:
    """Check the Local REST API status endpoint without requiring auth."""
    context = ssl._create_unverified_context()
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, context=context, timeout=5) as response:
            body = response.read().decode("utf-8")
    except urllib.error.URLError as error:
        return {
            "available": False,
            "error": str(error.reason),
        }
    data = json.loads(body)
    return {
        "available": response.status == 200 and data.get("status") == "OK",
        "status_code": response.status,
        "plugin_version": data.get("manifest", {}).get("version"),
        "authenticated": data.get("authenticated"),
    }


def analyze_vault(root: Path, *, check_rest: bool = False) -> dict[str, Any]:
    """Analyze sync readiness and return structured results."""
    root = root.resolve()
    findings: list[Finding] = []

    obsidian_dir = root / ".obsidian"
    if not obsidian_dir.exists():
        findings.append(Finding("error", "Missing .obsidian directory."))

    enabled = load_json_list(obsidian_dir / "community-plugins.json")
    installed = installed_plugins(root)
    core = core_plugins(root)
    ignores = gitignore_entries(root)
    missing_ignores = [
        entry for entry in REQUIRED_GITIGNORE_ENTRIES if entry not in ignores
    ]
    missing_required_plugins = sorted(REQUIRED_COMMUNITY_PLUGINS - installed)
    disabled_required_plugins = sorted(REQUIRED_COMMUNITY_PLUGINS - enabled)
    enabled_sync_plugins = sorted(SYNC_PLUGIN_OPTIONS & enabled)
    nested_repos = nested_git_repositories(root)

    if not core.get("sync", False):
        findings.append(Finding("warning", "Obsidian core Sync plugin is not enabled."))
    if missing_required_plugins:
        findings.append(
            Finding(
                "error",
                "Missing required plugins: " + ", ".join(missing_required_plugins),
            )
        )
    if disabled_required_plugins:
        findings.append(
            Finding(
                "warning",
                "Required plugins installed but disabled: "
                + ", ".join(disabled_required_plugins),
            )
        )
    if len(enabled_sync_plugins) > 1:
        findings.append(
            Finding(
                "warning",
                "Multiple third-party sync plugins are enabled: "
                + ", ".join(enabled_sync_plugins)
                + ". Use only one active sync engine besides Obsidian Sync.",
            )
        )
    if missing_ignores:
        findings.append(
            Finding(
                "error",
                "Root .gitignore is missing required entries: "
                + ", ".join(missing_ignores),
            )
        )
    if nested_repos:
        findings.append(
            Finding(
                "warning",
                "Nested Git repositories found: "
                + ", ".join(nested_repos)
                + ". Keep them ignored from the root backup repo.",
            )
        )

    rest: dict[str, Any] | None = None
    if check_rest:
        rest = local_rest_status()
        if not rest.get("available"):
            findings.append(Finding("error", "Local REST API is not reachable."))

    errors = [finding for finding in findings if finding.level == "error"]
    warnings = [finding for finding in findings if finding.level == "warning"]
    return {
        "root": root.as_posix(),
        "ready": not errors,
        "summary": {
            "enabled_community_plugins": sorted(enabled),
            "installed_community_plugins": sorted(installed),
            "sync_core_enabled": core.get("sync", False),
            "enabled_third_party_sync_plugins": enabled_sync_plugins,
            "nested_git_repositories": nested_repos,
            "missing_gitignore_entries": missing_ignores,
            "error_count": len(errors),
            "warning_count": len(warnings),
        },
        "local_rest_api": rest,
        "findings": [finding.__dict__ for finding in findings],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--check-rest",
        action="store_true",
        help="Also check https://127.0.0.1:27124/ for the Obsidian REST plugin.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    result = analyze_vault(args.root, check_rest=args.check_rest)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["ready"]:
        raise SystemExit(1)


if __name__ == "__main__":
    # `tools/` e sys.path[0] quando o arquivo roda como script; em modo importado
    # este bloco nao executa, e o stdout do chamador fica intacto.
    from console import usar_utf8

    usar_utf8()
    main()
