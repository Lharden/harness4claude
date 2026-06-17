from pathlib import Path

from tools.vault_sync_doctor import (
    REQUIRED_GITIGNORE_ENTRIES,
    analyze_vault,
    gitignore_entries,
    nested_git_repositories,
)


def write_json(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def create_minimal_vault(root: Path) -> None:
    write_json(
        root / ".obsidian" / "community-plugins.json",
        '["dataview","obsidian-local-rest-api","smart-connections"]',
    )
    write_json(root / ".obsidian" / "core-plugins.json", '{"sync": true}')
    for plugin in ("dataview", "obsidian-local-rest-api", "smart-connections"):
        (root / ".obsidian" / "plugins" / plugin).mkdir(parents=True)
    (root / ".gitignore").write_text(
        "\n".join(REQUIRED_GITIGNORE_ENTRIES) + "\n",
        encoding="utf-8",
    )


def test_gitignore_entries_ignores_comments_and_blank_lines(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text(
        "# comment\n\n.smart-env/\n*.tar.gz\n",
        encoding="utf-8",
    )

    assert gitignore_entries(tmp_path) == {".smart-env/", "*.tar.gz"}


def test_analyze_vault_accepts_minimal_safe_setup(tmp_path: Path) -> None:
    create_minimal_vault(tmp_path)

    result = analyze_vault(tmp_path)

    assert result["ready"] is True
    assert result["summary"]["sync_core_enabled"] is True
    assert result["summary"]["error_count"] == 0


def test_analyze_vault_blocks_missing_secret_ignores(tmp_path: Path) -> None:
    create_minimal_vault(tmp_path)
    (tmp_path / ".gitignore").write_text(".smart-env/\n", encoding="utf-8")

    result = analyze_vault(tmp_path)

    assert result["ready"] is False
    assert (
        ".obsidian/plugins/obsidian-local-rest-api/data.json"
        in result["summary"]["missing_gitignore_entries"]
    )


def test_analyze_vault_warns_when_multiple_sync_plugins_enabled(
    tmp_path: Path,
) -> None:
    create_minimal_vault(tmp_path)
    write_json(
        tmp_path / ".obsidian" / "community-plugins.json",
        (
            '["dataview","obsidian-local-rest-api","smart-connections",'
            '"remotely-save","github-sync-multi-platform"]'
        ),
    )

    result = analyze_vault(tmp_path)

    messages = [finding["message"] for finding in result["findings"]]
    assert any("Multiple third-party sync plugins" in message for message in messages)


def test_nested_git_repositories_finds_child_repos_only(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "SLB" / ".git").mkdir(parents=True)
    (tmp_path / "Projeto" / ".git").mkdir(parents=True)

    assert nested_git_repositories(tmp_path) == ["Projeto", "SLB"]
