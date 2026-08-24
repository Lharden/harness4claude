"""Export a lightweight, reproducible lock of the vault's community plugins.

Em vez de versionar os binarios (`main.js`, ~52 MB), capturamos apenas a LISTA +
os manifests (id, nome, versao, autor, estado habilitado). Isso mantem o backup
Git leve (~KB) e permite reinstalar o MESMO conjunto de plugins numa maquina nova
a partir da community store do Obsidian.

Saida: um JSON ordenado e deterministico (`vault-plugins.lock.json` por padrao),
adequado para versionar e fazer diff entre maquinas.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PluginEntry:
    """Manifest minimo e reproduzivel de um community plugin."""

    id: str
    name: str
    version: str
    author: str
    author_url: str
    is_enabled: bool
    is_desktop_only: bool


def _load_json(path: Path) -> Any:
    """Carrega um JSON em UTF-8."""
    return json.loads(path.read_text(encoding="utf-8"))


def _enabled_ids(obsidian_dir: Path) -> set[str]:
    """Ids habilitados em community-plugins.json (lista de strings)."""
    path = obsidian_dir / "community-plugins.json"
    if not path.exists():
        return set()
    data = _load_json(path)
    if not isinstance(data, list):
        raise ValueError(f"Esperava uma lista JSON em {path}")
    return {str(item) for item in data}


def collect_plugins(root: Path) -> list[PluginEntry]:
    """Le cada .obsidian/plugins/*/manifest.json e devolve entradas ordenadas."""
    obsidian_dir = root / ".obsidian"
    plugins_dir = obsidian_dir / "plugins"
    if not plugins_dir.exists():
        return []
    enabled = _enabled_ids(obsidian_dir)

    entries: list[PluginEntry] = []
    for manifest_path in sorted(plugins_dir.glob("*/manifest.json")):
        manifest = _load_json(manifest_path)
        if not isinstance(manifest, dict):
            continue
        plugin_id = str(manifest.get("id") or manifest_path.parent.name)
        entries.append(
            PluginEntry(
                id=plugin_id,
                name=str(manifest.get("name", plugin_id)),
                version=str(manifest.get("version", "")),
                author=str(manifest.get("author", "")),
                author_url=str(manifest.get("authorUrl", "")),
                is_enabled=plugin_id in enabled,
                is_desktop_only=bool(manifest.get("isDesktopOnly", False)),
            )
        )
    return sorted(entries, key=lambda e: e.id)


def build_lock(root: Path) -> dict[str, Any]:
    """Monta o documento de lock deterministico (sem timestamps voláteis)."""
    plugins = collect_plugins(root.resolve())
    return {
        "schema": "vault-plugins.lock/v1",
        "plugin_count": len(plugins),
        "enabled_count": sum(1 for p in plugins if p.is_enabled),
        "plugins": [asdict(p) for p in plugins],
    }


def build_parser() -> argparse.ArgumentParser:
    """CLI: --root, --out, --stdout."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("vault-plugins.lock.json"),
        help="Arquivo de saida (relativo a --root salvo se absoluto).",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Imprime o lock em vez de escrever em arquivo.",
    )
    return parser


def main() -> None:
    """Gera o lock e escreve em disco (ou stdout)."""
    args = build_parser().parse_args()
    lock = build_lock(args.root)
    payload = json.dumps(lock, ensure_ascii=False, indent=2, sort_keys=False)
    if args.stdout:
        print(payload)
        return
    out = args.out if args.out.is_absolute() else args.root / args.out
    out.write_text(payload + "\n", encoding="utf-8")
    print(
        f"[ok] {lock['plugin_count']} plugins ({lock['enabled_count']} ativos) -> {out}"
    )


if __name__ == "__main__":
    # `tools/` e sys.path[0] quando o arquivo roda como script; em modo importado
    # este bloco nao executa, e o stdout do chamador fica intacto.
    from console import usar_utf8

    usar_utf8()
    main()
