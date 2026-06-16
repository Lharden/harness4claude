"""Tests for tools.export_plugins."""

from __future__ import annotations

import json
from pathlib import Path

from tools.export_plugins import build_lock, collect_plugins


def _make_plugin(plugins_dir: Path, plugin_id: str, version: str, **extra: object) -> None:
    """Cria .obsidian/plugins/<id>/manifest.json com os campos dados."""
    pdir = plugins_dir / plugin_id
    pdir.mkdir(parents=True)
    manifest: dict[str, object] = {
        "id": plugin_id,
        "name": plugin_id.title(),
        "version": version,
    }
    manifest.update(extra)
    (pdir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    # binario que NAO deve ser lido nem versionado
    (pdir / "main.js").write_text("// big bundle", encoding="utf-8")


def _seed_vault(root: Path, enabled: list[str]) -> None:
    obsidian = root / ".obsidian"
    plugins = obsidian / "plugins"
    plugins.mkdir(parents=True)
    (obsidian / "community-plugins.json").write_text(
        json.dumps(enabled), encoding="utf-8"
    )


def test_collect_is_sorted_and_marks_enabled(tmp_path: Path) -> None:
    _seed_vault(tmp_path, enabled=["zeta", "alpha"])
    _make_plugin(tmp_path / ".obsidian" / "plugins", "zeta", "1.2.0")
    _make_plugin(tmp_path / ".obsidian" / "plugins", "alpha", "0.1.0")
    _make_plugin(tmp_path / ".obsidian" / "plugins", "beta", "3.0.0")  # instalado, off

    entries = collect_plugins(tmp_path)

    assert [e.id for e in entries] == ["alpha", "beta", "zeta"]  # deterministico
    enabled = {e.id: e.is_enabled for e in entries}
    assert enabled == {"alpha": True, "beta": False, "zeta": True}


def test_build_lock_counts_and_schema(tmp_path: Path) -> None:
    _seed_vault(tmp_path, enabled=["alpha"])
    _make_plugin(
        tmp_path / ".obsidian" / "plugins",
        "alpha",
        "0.1.0",
        author="Leo",
        authorUrl="https://example.com",
        isDesktopOnly=True,
    )

    lock = build_lock(tmp_path)

    assert lock["schema"] == "vault-plugins.lock/v1"
    assert lock["plugin_count"] == 1
    assert lock["enabled_count"] == 1
    only = lock["plugins"][0]
    assert only["version"] == "0.1.0"
    assert only["author_url"] == "https://example.com"
    assert only["is_desktop_only"] is True


def test_missing_plugins_dir_is_empty(tmp_path: Path) -> None:
    lock = build_lock(tmp_path)
    assert lock["plugin_count"] == 0
    assert lock["plugins"] == []
