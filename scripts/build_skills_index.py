#!/usr/bin/env python3
"""Constroi o indice do skill-router (harness4claude v3.3).

Varre SKILL.md de todos os plugins instalados (habilitados E desabilitados) +
skills pessoais, junta uso (skillUsage) e embeda "name. description" via Ollama.
stdlib-only; saidas atomicas em ~/.claude/harness/skills-index/.

Uso: python build_skills_index.py [--no-embed] [--check-stale] [--out DIR]
--check-stale: exit 0 = fresco, exit 1 = stale/ausente.
"""
import argparse
import hashlib
import json
import math
import os
import re
import struct
import sys
import tempfile
import time
import urllib.request

HOME = os.path.expanduser("~")
CLAUDE_DIR = os.path.join(HOME, ".claude")
INSTALLED_JSON = os.path.join(CLAUDE_DIR, "plugins", "installed_plugins.json")
SETTINGS_JSON = os.path.join(CLAUDE_DIR, "settings.json")
CLAUDE_JSON = os.path.join(HOME, ".claude.json")
PERSONAL_SKILLS_DIR = os.path.join(CLAUDE_DIR, "skills")
DEFAULT_OUT = os.path.join(CLAUDE_DIR, "harness", "skills-index")
ALIASES_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skill-aliases.json")
OLLAMA_URL = os.environ.get("HARNESS_OLLAMA_URL", "http://localhost:11434")
EMBED_MODEL = "nomic-embed-text-v2-moe"

_FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)


def parse_frontmatter(text):
    """Extrai name/description do frontmatter sem depender de PyYAML."""
    m = _FM_RE.match(text)
    if not m:
        return {}
    lines = m.group(1).splitlines()
    out, i = {}, 0
    while i < len(lines):
        km = re.match(r"^(name|description):\s*(.*)$", lines[i])
        if not km:
            i += 1
            continue
        key, val = km.group(1), km.group(2).strip()
        if val in (">", ">-", "|", "|-", ""):
            block, i = [], i + 1
            while i < len(lines) and (lines[i].startswith((" ", "\t")) or not lines[i].strip()):
                block.append(lines[i].strip())
                i += 1
            out[key] = " ".join(b for b in block if b)
        else:
            if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
                val = val[1:-1]
            out[key] = val
            i += 1
    return out


def _load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def scan_skills(installed_json=INSTALLED_JSON, settings_json=SETTINGS_JSON,
                claude_json=CLAUDE_JSON, personal_dir=PERSONAL_SKILLS_DIR):
    """Lista skills de plugins instalados (via installPath) + pessoais, sem embeddings."""
    enabled_map = _load_json(settings_json, {}).get("enabledPlugins", {})
    usage = _load_json(claude_json, {}).get("skillUsage", {})
    aliases = _load_json(ALIASES_JSON, {})
    skills = []

    def add(plugin_label, source, enabled, skill_dir):
        md = os.path.join(skill_dir, "SKILL.md")
        if not os.path.isfile(md):
            return
        try:
            with open(md, encoding="utf-8", errors="replace") as f:
                fm = parse_frontmatter(f.read(16384))
        except OSError:
            return
        name = fm.get("name") or os.path.basename(skill_dir)
        desc = (fm.get("description") or "").strip()
        short = plugin_label.split("@")[0]
        sid = name if source == "personal" else f"{short}:{name}"
        u = usage.get(sid) or usage.get(name) or {}
        skills.append({
            "id": sid, "name": name, "plugin": plugin_label, "source": source,
            "enabled": enabled, "path": md, "description": desc,
            "desc_chars": len(desc), "aliases": list(aliases.get(sid, [])),
            "usage_count": int(u.get("usageCount", 0)),
            "last_used_at": u.get("lastUsedAt"), "vec_row": -1,
        })

    for pid, entries in _load_json(installed_json, {}).get("plugins", {}).items():
        for entry in entries or []:
            root = entry.get("installPath")
            if not root or not os.path.isdir(root):
                continue
            sroot = os.path.join(root, "skills")
            if not os.path.isdir(sroot):
                continue
            enabled = bool(enabled_map.get(pid, False))
            for d in sorted(os.listdir(sroot)):
                add(pid, "marketplace", enabled, os.path.join(sroot, d))

    if os.path.isdir(personal_dir):
        for d in sorted(os.listdir(personal_dir)):
            add(d, "personal", True, os.path.join(personal_dir, d))

    skills.sort(key=lambda s: s["id"])
    return skills
