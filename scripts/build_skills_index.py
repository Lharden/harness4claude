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
_HARNESS_DIR = os.environ.get("HARNESS_DIR") or os.path.join(CLAUDE_DIR, "harness")
DEFAULT_OUT = os.path.join(_HARNESS_DIR, "skills-index")
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


def fingerprint(skills, settings_json=SETTINGS_JSON):
    enabled_map = _load_json(settings_json, {}).get("enabledPlugins", {})
    h1 = hashlib.sha1()
    for s in skills:
        try:
            st = os.stat(s["path"])
            h1.update(f"{s['path']}|{int(st.st_mtime)}|{st.st_size}\n".encode())
        except OSError:
            h1.update(f"{s['path']}|gone\n".encode())
    h2 = hashlib.sha1(
        "".join(sorted(f"{k}={v}" for k, v in enabled_map.items())).encode())
    return {"skill_files_hash": h1.hexdigest(), "enabled_plugins_hash": h2.hexdigest()}


def atomic_write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def l2norm(v):
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def pack_f16(rows):
    buf = bytearray()
    for row in rows:
        buf += struct.pack(f"<{len(row)}e", *row)
    return bytes(buf)


def ollama_embed(texts, timeout=180):
    out = []
    for i in range(0, len(texts), 64):
        req = urllib.request.Request(
            OLLAMA_URL.rstrip("/") + "/api/embed",
            data=json.dumps({"model": EMBED_MODEL, "input": texts[i:i + 64],
                             "keep_alive": "30m"}).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out.extend(json.load(r)["embeddings"])
    return out


def build(out_dir=DEFAULT_OUT, no_embed=False, skills=None):
    if skills is None:
        skills = scan_skills()
    dim, blob = 0, b""
    if not no_embed and skills:
        texts = [f"search_document: {s['name']}. {s['description'][:1000]}" for s in skills]
        vecs = [l2norm(v) for v in ollama_embed(texts)]
        dim = len(vecs[0])
        for row, s in enumerate(skills):
            s["vec_row"] = row
        blob = pack_f16(vecs)
    index = {
        "schema_version": 1,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "model": None if no_embed else EMBED_MODEL,
        "dim": dim,
        "fingerprint": fingerprint(skills),
        "skills": skills,
    }
    atomic_write(os.path.join(out_dir, "embeddings.f16.bin"), blob)
    atomic_write(os.path.join(out_dir, "skills-index.json"),
                 json.dumps(index, ensure_ascii=False, indent=1).encode("utf-8"))
    meta = {k: index[k] for k in ("schema_version", "built_at", "model", "dim", "fingerprint")}
    meta["count"] = len(skills)
    atomic_write(os.path.join(out_dir, "meta.json"), json.dumps(meta).encode("utf-8"))
    stale = os.path.join(out_dir, ".stale")
    if os.path.exists(stale):
        os.remove(stale)
    return len(skills)


def check_stale(out_dir=DEFAULT_OUT, skills=None):
    meta = _load_json(os.path.join(out_dir, "meta.json"), None)
    if not meta:
        return True
    if skills is None:
        skills = scan_skills()
    return fingerprint(skills) != meta.get("fingerprint")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--no-embed", action="store_true")
    ap.add_argument("--check-stale", action="store_true")
    ap.add_argument("--out", default=DEFAULT_OUT)
    a = ap.parse_args(argv)
    if a.check_stale:
        return 1 if check_stale(a.out) else 0
    n = build(a.out, a.no_embed)
    print(f"skills-index: {n} skills -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
