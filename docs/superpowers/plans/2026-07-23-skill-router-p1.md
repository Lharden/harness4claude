# Skill Router P1 (índice + router MVP) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Construir o índice de skills + roteador híbrido (regex/alias + embeddings locais via Ollama) do harness4claude v3.3, injetando dicas top-3 de skills via hook UserPromptSubmit.

**Architecture:** Um builder offline (`scripts/build_skills_index.py`) varre SKILL.md de todos os plugins (habilitados e desabilitados) + skills pessoais e gera índice JSON + embeddings f16 em `~/.claude/harness/skills-index/`. Um hook novo (`hooks/harness-skill-router.sh` → `skill_router.py`), paralelo ao classify e sem state lock, aplica guards, Camada A (nome/alias) e Camada B (embedding da query via Ollama, dot product), e injeta `[skill-hint]` via `hookSpecificOutput.additionalContext`. Spec: `docs/specs/skill-router-design.md` (seções 3–4).

**Tech Stack:** Python 3 stdlib-only (sem numpy/PyYAML no hot path), bash (git-bash/Windows), Ollama HTTP `/api/embed` com `nomic-embed-text-v2-moe` (já instalado), pytest (suíte existente em `tests/`).

## Global Constraints

- Repo de trabalho: `C:\Users\LHarden2\.claude\plugins\local\harness4claude` (clone local; a cópia ativa no Claude Code é o cache 3.2.0 — teste ao vivo só no final, ver Task 10).
- Python stdlib-only em hooks e builder. `PYTHONUTF8=1` em todo shim bash.
- O router NUNCA bloqueia um prompt: todo caminho de falha = `exit 0` sem stdout; erros vão para `~/.claude/harness/router/debug-router.log`.
- O router NUNCA adquire o state lock; leitura de `~/.claude/harness/state.json` é lock-free e falha de parse = "sem pipeline ativo".
- `hooks/hooks.json`: timeouts em **milissegundos** (padrão do arquivo existente).
- Constantes do design (não inventar outras): TOP_K=3, MIN_COS=0.45, MIN_MARGIN=0.05, DISABLED_MIN=0.60, DISABLED_LEAD=0.08, MAX_OFFERS_PER_SKILL=2, EMBED_TIMEOUT=1.2s, guards 20≤len≤30000, modelo `nomic-embed-text-v2-moe`, prefixos `search_document: ` / `search_query: `, `keep_alive: "30m"`.
- Arquivos de saída do builder: escrita atômica via `os.replace` (mesmo padrão do classify).
- Textos user-facing das dicas em PT (padrão do plugin); código/comentários seguem o estilo do repo.
- NÃO tocar: `harness-classify.sh`, `harness-reclassify.sh`, `state-lock.sh`, `schemas/`, `record_signal.py`, `~/.claude.json` (read-only), `settings.json`.
- Commits frequentes, um por task, mensagens em PT no estilo do repo (`feat:`, `chore:`, `test:`, `docs:`).

---

### Task 1: Housekeeping — commit pendente + esqueleto de arquivos

**Files:**
- Commit (pré-existente, de outra frente): `hooks/hooks.json` (modificado) + `hooks/harness-graphify-autosetup.sh` (untracked)
- Create: `scripts/skill-aliases.json`

**Interfaces:**
- Produces: `scripts/skill-aliases.json` — mapa `{skill_id: [aliases]}` lido pelo builder (Task 3).

- [ ] **Step 1: Commitar o trabalho pendente de outra frente (graphify-autosetup)**

O working tree tem `M hooks/hooks.json` + `?? hooks/harness-graphify-autosetup.sh` — é o registro do hook graphify-autosetup (já ativo na cópia de cache), feito no clone mas nunca commitado. Commitar separado ANTES de começar, para que os commits deste plano fiquem limpos:

```bash
cd /c/Users/LHarden2/.claude/plugins/local/harness4claude
git add hooks/hooks.json hooks/harness-graphify-autosetup.sh
git commit -m "chore: registrar hook harness-graphify-autosetup pendente (ja ativo no cache 3.2.0)"
git status --short   # esperado: limpo (fora .pytest_tmp_analysis/, que é lixo local — ignorar)
```

- [ ] **Step 2: Criar o arquivo de aliases curados (semente pequena)**

Criar `scripts/skill-aliases.json`:

```json
{
  "harness4claude:write-spec": ["spec formal", "user stories", "acceptance criteria"],
  "harness4claude:design-doc": ["design tecnico", "design doc"],
  "harness4claude:grill-me": ["perguntas adversariais"],
  "harness4claude:security-scan-python": ["bandit", "pip-audit", "scan de seguranca", "security scan"],
  "slb-presentations": ["deck", "pptx", "powerpoint", "apresentacao"],
  "graphify": ["knowledge graph", "grafo de conhecimento", "god nodes"],
  "superpowers:brainstorming": ["brainstorm", "brainstorming"],
  "superpowers:test-driven-development": ["tdd"]
}
```

- [ ] **Step 3: Commit**

```bash
git add scripts/skill-aliases.json
git commit -m "feat(router): semente de aliases curados para a Camada A"
```

---

### Task 2: Parser de frontmatter (builder, parte 1)

**Files:**
- Create: `scripts/build_skills_index.py`
- Create: `tests/test_build_skills_index.py`

**Interfaces:**
- Produces: `parse_frontmatter(text: str) -> dict` — retorna `{"name": str, "description": str}` (chaves ausentes se não encontradas). Suporta valor simples, aspas simples/duplas e blocos dobrados (`>`, `>-`, `|`, `|-`).

- [ ] **Step 1: Escrever os testes que falham**

Criar `tests/test_build_skills_index.py`:

```python
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import build_skills_index as bsi


def test_frontmatter_simple():
    text = "---\nname: graphify\ndescription: builds knowledge graphs\n---\n# corpo"
    fm = bsi.parse_frontmatter(text)
    assert fm["name"] == "graphify"
    assert fm["description"] == "builds knowledge graphs"


def test_frontmatter_quoted():
    text = '---\nname: "write-spec"\ndescription: \'Gera specs: formais\'\n---\n'
    fm = bsi.parse_frontmatter(text)
    assert fm["name"] == "write-spec"
    assert fm["description"] == "Gera specs: formais"


def test_frontmatter_folded_block():
    text = (
        "---\nname: dataviz\ndescription: >-\n"
        "  Use this skill whenever you create charts.\n"
        "  Read it BEFORE writing chart code.\n---\n"
    )
    fm = bsi.parse_frontmatter(text)
    assert fm["description"] == (
        "Use this skill whenever you create charts. Read it BEFORE writing chart code."
    )


def test_frontmatter_missing():
    assert bsi.parse_frontmatter("# sem frontmatter") == {}
    assert bsi.parse_frontmatter("---\nmetadata:\n  x: 1\n---\n") == {}
```

- [ ] **Step 2: Rodar e ver falhar**

```bash
cd /c/Users/LHarden2/.claude/plugins/local/harness4claude
python -m pytest tests/test_build_skills_index.py -v
```
Esperado: FAIL/ERROR com `ModuleNotFoundError: build_skills_index`.

- [ ] **Step 3: Implementar o módulo com o parser**

Criar `scripts/build_skills_index.py`:

```python
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
```

- [ ] **Step 4: Rodar e ver passar**

```bash
python -m pytest tests/test_build_skills_index.py -v
```
Esperado: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/build_skills_index.py tests/test_build_skills_index.py
git commit -m "feat(router): parser de frontmatter stdlib-only do builder"
```

---

### Task 3: Scanner de skills (builder, parte 2)

**Files:**
- Modify: `scripts/build_skills_index.py` (acrescentar ao final)
- Modify: `tests/test_build_skills_index.py` (acrescentar)

**Interfaces:**
- Consumes: `parse_frontmatter` (Task 2).
- Produces: `scan_skills(installed_json, settings_json, claude_json, personal_dir) -> list[dict]` — cada dict com chaves `id, name, plugin, source, enabled, path, description, desc_chars, aliases, usage_count, last_used_at, vec_row` (vec_row=-1 nesta fase), ordenada por `id`. Formato de `id`: `"{plugin_curto}:{name}"` para plugins, `"{name}"` para pessoais. `installed_plugins.json` é chaveado por `"nome@marketplace"` com entries contendo `installPath` (autoritativo — ignora dirs stale).

- [ ] **Step 1: Escrever o teste que falha**

Acrescentar em `tests/test_build_skills_index.py`:

```python
import json


def _mk_skill(root, plugin_dirname, skill, desc):
    d = os.path.join(root, plugin_dirname, "skills", skill)
    os.makedirs(d)
    with open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write(f"---\nname: {skill}\ndescription: {desc}\n---\n# x\n")
    return os.path.join(root, plugin_dirname)


def test_scan_skills(tmp_path, monkeypatch):
    root = str(tmp_path)
    p1 = _mk_skill(root, "alpha", "alpha-skill", "faz coisas alpha")
    p2 = _mk_skill(root, "beta", "beta-skill", "faz coisas beta")
    personal = os.path.join(root, "personal")
    os.makedirs(os.path.join(personal, "meu-skill"))
    with open(os.path.join(personal, "meu-skill", "SKILL.md"), "w", encoding="utf-8") as f:
        f.write("---\nname: meu-skill\ndescription: pessoal\n---\n")

    installed = os.path.join(root, "installed_plugins.json")
    with open(installed, "w", encoding="utf-8") as f:
        json.dump({"plugins": {
            "alpha@mkt": [{"installPath": p1}],
            "beta@mkt": [{"installPath": p2}],
            "gone@mkt": [{"installPath": os.path.join(root, "nao-existe")}],
        }}, f)
    settings = os.path.join(root, "settings.json")
    with open(settings, "w", encoding="utf-8") as f:
        json.dump({"enabledPlugins": {"alpha@mkt": True, "beta@mkt": False}}, f)
    cjson = os.path.join(root, "claude.json")
    with open(cjson, "w", encoding="utf-8") as f:
        json.dump({"skillUsage": {"alpha:alpha-skill": {"usageCount": 7, "lastUsedAt": 123},
                                  "meu-skill": {"usageCount": 2, "lastUsedAt": 456}}}, f)
    monkeypatch.setattr(bsi, "ALIASES_JSON", os.path.join(root, "aliases.json"))
    with open(os.path.join(root, "aliases.json"), "w", encoding="utf-8") as f:
        json.dump({"alpha:alpha-skill": ["apelido"]}, f)

    skills = bsi.scan_skills(installed, settings, cjson, personal)
    by_id = {s["id"]: s for s in skills}
    assert set(by_id) == {"alpha:alpha-skill", "beta:beta-skill", "meu-skill"}
    assert by_id["alpha:alpha-skill"]["enabled"] is True
    assert by_id["alpha:alpha-skill"]["usage_count"] == 7
    assert by_id["alpha:alpha-skill"]["aliases"] == ["apelido"]
    assert by_id["beta:beta-skill"]["enabled"] is False
    assert by_id["meu-skill"]["source"] == "personal"
    assert by_id["meu-skill"]["enabled"] is True
    assert by_id["meu-skill"]["usage_count"] == 2
    assert all(s["vec_row"] == -1 for s in skills)
    assert [s["id"] for s in skills] == sorted(s["id"] for s in skills)
```

- [ ] **Step 2: Rodar e ver falhar**

```bash
python -m pytest tests/test_build_skills_index.py::test_scan_skills -v
```
Esperado: FAIL com `AttributeError: ... has no attribute 'scan_skills'`.

- [ ] **Step 3: Implementar**

Acrescentar em `scripts/build_skills_index.py`:

```python
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
```

- [ ] **Step 4: Rodar e ver passar**

```bash
python -m pytest tests/test_build_skills_index.py -v
```
Esperado: 5 passed.

- [ ] **Step 5: Smoke test contra o sistema real (read-only)**

```bash
python -c "import sys; sys.path.insert(0,'scripts'); import build_skills_index as b; s=b.scan_skills(); print(len(s), 'skills'); print(sum(1 for x in s if x['enabled']), 'habilitadas')"
```
Esperado: ~180-190 skills no total (pós-poda P0), com contagem de habilitadas menor que o total (os 6 plugins podados aparecem como enabled=False).

- [ ] **Step 6: Commit**

```bash
git add scripts/build_skills_index.py tests/test_build_skills_index.py
git commit -m "feat(router): scanner de skills (plugins habilitados+desabilitados+pessoais)"
```

---

### Task 4: Fingerprint, escrita atômica, `--no-embed` e `--check-stale` (builder, parte 3)

**Files:**
- Modify: `scripts/build_skills_index.py` (acrescentar)
- Modify: `tests/test_build_skills_index.py` (acrescentar)

**Interfaces:**
- Consumes: `scan_skills` (Task 3).
- Produces: `fingerprint(skills, settings_json) -> dict` com `skill_files_hash`/`enabled_plugins_hash`; `atomic_write(path, data: bytes)`; `build(out_dir, no_embed, skills=None) -> int` (nº de skills; com `no_embed=True` não chama Ollama, `dim=0`, `vec_row=-1`); `check_stale(out_dir, skills=None) -> bool`; CLI `main(argv)` com `--no-embed/--check-stale/--out`. Saídas: `skills-index.json`, `embeddings.f16.bin`, `meta.json` (meta = schema_version, built_at, model, dim, fingerprint, count).

- [ ] **Step 1: Escrever os testes que falham**

Acrescentar em `tests/test_build_skills_index.py`:

```python
def _fake_skills(tmp_path):
    md = tmp_path / "SKILL.md"
    md.write_text("---\nname: x\ndescription: d\n---\n", encoding="utf-8")
    return [{"id": "p:x", "name": "x", "plugin": "p@m", "source": "marketplace",
             "enabled": True, "path": str(md), "description": "d", "desc_chars": 1,
             "aliases": [], "usage_count": 0, "last_used_at": None, "vec_row": -1}]


def test_build_no_embed_and_check_stale(tmp_path):
    skills = _fake_skills(tmp_path)
    out = str(tmp_path / "idx")
    n = bsi.build(out_dir=out, no_embed=True, skills=skills)
    assert n == 1
    idx = json.load(open(os.path.join(out, "skills-index.json"), encoding="utf-8"))
    meta = json.load(open(os.path.join(out, "meta.json"), encoding="utf-8"))
    assert idx["dim"] == 0 and idx["skills"][0]["vec_row"] == -1
    assert meta["count"] == 1
    assert os.path.getsize(os.path.join(out, "embeddings.f16.bin")) == 0
    # mesmo conteudo => fresco
    assert bsi.check_stale(out, skills=skills) is False
    # mudou o SKILL.md => stale
    p = skills[0]["path"]
    with open(p, "a", encoding="utf-8") as f:
        f.write("mudou\n")
    os.utime(p, (os.path.getmtime(p) + 5, os.path.getmtime(p) + 5))
    assert bsi.check_stale(out, skills=skills) is True


def test_check_stale_missing_index(tmp_path):
    assert bsi.check_stale(str(tmp_path / "nao-existe"), skills=[]) is True
```

- [ ] **Step 2: Rodar e ver falhar**

```bash
python -m pytest tests/test_build_skills_index.py -v
```
Esperado: 2 novos FAIL (`no attribute 'build'`).

- [ ] **Step 3: Implementar**

Acrescentar em `scripts/build_skills_index.py`:

```python
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
```

Nota: `ollama_embed` só existe na Task 5 — como `build(no_embed=True)` não o chama, os testes desta task passam; NÃO rodar o CLI sem `--no-embed` ainda.

- [ ] **Step 4: Rodar e ver passar**

```bash
python -m pytest tests/test_build_skills_index.py -v
```
Esperado: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/build_skills_index.py tests/test_build_skills_index.py
git commit -m "feat(router): fingerprint, escrita atomica, --no-embed e --check-stale"
```

---

### Task 5: Cliente de embedding Ollama + build completo (builder, parte 4)

**Files:**
- Modify: `scripts/build_skills_index.py` (acrescentar `ollama_embed`)
- Modify: `tests/test_build_skills_index.py` (acrescentar teste com servidor HTTP fake)

**Interfaces:**
- Consumes: `build`, `l2norm`, `pack_f16` (Task 4).
- Produces: `ollama_embed(texts: list[str], timeout=180) -> list[list[float]]` — POST `{OLLAMA_URL}/api/embed` com `{"model": EMBED_MODEL, "input": [...], "keep_alive": "30m"}` em lotes de 64; lê `OLLAMA_URL` do módulo (testes fazem monkeypatch).

- [ ] **Step 1: Escrever o teste que falha (fake Ollama em thread)**

Acrescentar em `tests/test_build_skills_index.py`:

```python
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


class _FakeOllama(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        vecs = [[1.0, 2.0, 2.0] for _ in body["input"]]  # norma 3 -> [1/3,2/3,2/3]
        out = json.dumps({"embeddings": vecs}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


def test_build_with_fake_embeddings(tmp_path, monkeypatch):
    srv = HTTPServer(("127.0.0.1", 0), _FakeOllama)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(bsi, "OLLAMA_URL", f"http://127.0.0.1:{srv.server_port}")
    try:
        skills = _fake_skills(tmp_path)
        out = str(tmp_path / "idx")
        assert bsi.build(out_dir=out, skills=skills) == 1
        idx = json.load(open(os.path.join(out, "skills-index.json"), encoding="utf-8"))
        assert idx["dim"] == 3 and idx["skills"][0]["vec_row"] == 0
        import struct as st
        raw = open(os.path.join(out, "embeddings.f16.bin"), "rb").read()
        v = st.unpack("<3e", raw)
        assert abs(v[0] - 1 / 3) < 1e-2 and abs(sum(x * x for x in v) - 1.0) < 1e-2
    finally:
        srv.shutdown()
```

- [ ] **Step 2: Rodar e ver falhar**

```bash
python -m pytest tests/test_build_skills_index.py::test_build_with_fake_embeddings -v
```
Esperado: FAIL com `NameError: name 'ollama_embed' is not defined`.

- [ ] **Step 3: Implementar (inserir ANTES de `build` no arquivo)**

```python
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
```

- [ ] **Step 4: Rodar e ver passar**

```bash
python -m pytest tests/test_build_skills_index.py -v
```
Esperado: 8 passed.

- [ ] **Step 5: Build real de integração (Ollama de verdade)**

```bash
python scripts/build_skills_index.py
ls -la ~/.claude/harness/skills-index/
python -c "import json,os;m=json.load(open(os.path.expanduser('~/.claude/harness/skills-index/meta.json')));print(m['count'],'skills, dim',m['dim'])"
python scripts/build_skills_index.py --check-stale && echo FRESCO
```
Esperado: `skills-index: ~185 skills -> ...`, `dim 768`, `.bin` com ~185×768×2 ≈ 284KB, e `FRESCO`. Se o Ollama estiver parado, iniciar (`ollama serve` já roda como serviço; verificar com `curl -s localhost:11434/api/tags | head -c 200`).

- [ ] **Step 6: Commit**

```bash
git add scripts/build_skills_index.py tests/test_build_skills_index.py
git commit -m "feat(router): cliente /api/embed com lotes e build completo do indice"
```

---

### Task 6: Núcleo de scoring do router (Camadas A/B, pick)

**Files:**
- Create: `hooks/skill_router.py`
- Create: `tests/test_skill_router.py`

**Interfaces:**
- Consumes: formato do índice (Tasks 3-5).
- Produces: em `hooks/skill_router.py` — `layer_a(prompt_low: str, skills) -> list[hit]`; `layer_b(qvec, skills, vecs) -> list[hit]` ordenada por score desc (`score = cos + min(0.1, 0.03*log1p(usage_count))`); `pick(a_hits, b_scored) -> list[hit]` (A pinada; B completa até TOP_K com `cos >= MIN_COS` e `cos >= mediana + MIN_MARGIN`; skill desabilitada exige `cos >= DISABLED_MIN` e `cos >= melhor_habilitada + DISABLED_LEAD`, máx. 1). Um `hit` é `{"id", "score", "cos"?, "layer", "skill"}`.

- [ ] **Step 1: Escrever os testes que falham**

Criar `tests/test_skill_router.py`:

```python
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hooks"))
import skill_router as sr


def _skill(sid, enabled=True, usage=0, aliases=None):
    return {"id": sid, "name": sid.split(":")[-1], "plugin": f"{sid.split(':')[0]}@m",
            "source": "marketplace", "enabled": enabled, "path": "x", "description": f"desc {sid}",
            "desc_chars": 9, "aliases": aliases or [], "usage_count": usage,
            "last_used_at": None, "vec_row": -1}


def test_layer_a_name_and_alias():
    skills = [_skill("p:write-spec"), _skill("q:deck-maker", aliases=["deck"])]
    hits = sr.layer_a("quero um write-spec e um deck bonito", skills)
    assert {h["id"] for h in hits} == {"p:write-spec", "q:deck-maker"}
    assert all(h["score"] == 1.0 and h["layer"] == "A" for h in hits)
    # sem match parcial dentro de palavra
    assert sr.layer_a("undeckable", [_skill("q:deck-maker", aliases=["deck"])]) == []


def test_layer_b_scores_and_boost():
    skills = [_skill("a:x", usage=0), _skill("b:y", usage=50)]
    skills[0]["vec_row"], skills[1]["vec_row"] = 0, 1
    vecs = [(1.0, 0.0), (1.0, 0.0)]
    out = sr.layer_b((1.0, 0.0), skills, vecs)
    assert out[0]["id"] == "b:y"          # empate em cos, boost de uso decide
    assert abs(out[0]["cos"] - 1.0) < 1e-9
    assert out[0]["score"] > out[1]["score"]


def test_pick_thresholds_and_disabled_bar():
    def hit(sid, cos, enabled=True):
        return {"id": sid, "cos": cos, "score": cos, "layer": "B",
                "skill": _skill(sid, enabled=enabled)}
    # mediana dos 5 = 0.30; corte = max(MIN_COS, mediana+margem) na prática
    b = [hit("a:1", 0.80), hit("a:2", 0.50), hit("a:3", 0.30),
         hit("a:4", 0.20), hit("a:5", 0.10)]
    got = [h["id"] for h in sr.pick([], b)]
    assert got == ["a:1", "a:2"]          # 0.30 falha margem; 0.20/0.10 falham MIN_COS
    # desabilitada precisa de 0.60 E lead de 0.08 sobre a melhor habilitada
    b2 = [hit("on:1", 0.55), hit("off:1", 0.61, enabled=False), hit("off:2", 0.70, enabled=False)]
    got2 = [h["id"] for h in sr.pick([], b2)]
    assert "off:2" in got2 and "off:1" not in got2   # so 1 desabilitada, a melhor
    # camada A pinada na frente e sem duplicar
    a = [{"id": "a:1", "score": 1.0, "layer": "A", "skill": _skill("a:1")}]
    got3 = [h["id"] for h in sr.pick(a, b)]
    assert got3[0] == "a:1" and got3.count("a:1") == 1 and len(got3) <= sr.TOP_K
```

- [ ] **Step 2: Rodar e ver falhar**

```bash
python -m pytest tests/test_skill_router.py -v
```
Esperado: ERROR `ModuleNotFoundError: skill_router`.

- [ ] **Step 3: Implementar**

Criar `hooks/skill_router.py`:

```python
#!/usr/bin/env python3
"""Skill-router hibrido (harness4claude v3.3) — hook UserPromptSubmit.

Camada A: match exato de nome/alias. Camada B: embeddings via Ollama local.
Contrato: NUNCA bloqueia um prompt — qualquer falha => exit 0 sem output.
Read-only sobre state.json (sem lock). stdlib-only.
"""
import json
import math
import os
import re
import struct
import sys
import time
import urllib.request

HOME = os.path.expanduser("~")
HARNESS_DIR = os.path.join(HOME, ".claude", "harness")
IDX_DIR = os.environ.get("HARNESS_SKILLS_INDEX",
                         os.path.join(HARNESS_DIR, "skills-index"))
ROUTER_DIR = os.path.join(HARNESS_DIR, "router")
STATE_JSON = os.path.join(HARNESS_DIR, "state.json")
OLLAMA_URL = os.environ.get("HARNESS_OLLAMA_URL", "http://localhost:11434")
EMBED_MODEL = "nomic-embed-text-v2-moe"

TOP_K = 3
MIN_COS = 0.45
MIN_MARGIN = 0.05
DISABLED_MIN = 0.60
DISABLED_LEAD = 0.08
MAX_OFFERS_PER_SKILL = 2
EMBED_TIMEOUT = 1.2
MIN_LEN, MAX_LEN = 20, 30000

AUTOMATION_SIGS = (
    "you are summarizing a claude code session",
    "harness v3 classified",
    "<harness-classification>",
)


def _dbg(msg):
    try:
        os.makedirs(ROUTER_DIR, exist_ok=True)
        with open(os.path.join(ROUTER_DIR, "debug-router.log"), "a",
                  encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}\n")
    except OSError:
        pass


def layer_a(prompt_low, skills):
    hits = []
    for s in skills:
        for term in [s["name"]] + list(s.get("aliases", [])):
            t = term.strip().lower()
            if len(t) < 3:
                continue
            if re.search(r"(?<![\w-])" + re.escape(t) + r"(?![\w-])", prompt_low):
                hits.append({"id": s["id"], "score": 1.0, "layer": "A", "skill": s})
                break
    return hits


def layer_b(qvec, skills, vecs):
    scored = []
    for s in skills:
        row = s.get("vec_row", -1)
        if row < 0 or row >= len(vecs):
            continue
        cos = sum(a * b for a, b in zip(qvec, vecs[row]))
        boost = min(0.1, 0.03 * math.log1p(s.get("usage_count", 0)))
        scored.append({"id": s["id"], "cos": cos, "score": cos + boost,
                       "layer": "B", "skill": s})
    scored.sort(key=lambda h: h["score"], reverse=True)
    return scored


def pick(a_hits, b_scored):
    """Camada A pinada; B completa ate TOP_K com threshold + bar p/ desabilitadas."""
    chosen, seen = [], set()
    for h in a_hits:
        if h["id"] not in seen:
            chosen.append(h)
            seen.add(h["id"])
    if b_scored:
        cosines = sorted(h["cos"] for h in b_scored)
        median = cosines[len(cosines) // 2]
        best_enabled = max((h["cos"] for h in b_scored if h["skill"]["enabled"]),
                           default=0.0)
        disabled_used = any(not h["skill"]["enabled"] for h in chosen)
        for h in b_scored:
            if len(chosen) >= TOP_K:
                break
            if h["id"] in seen:
                continue
            if h["cos"] < MIN_COS or h["cos"] < median + MIN_MARGIN:
                continue
            if not h["skill"]["enabled"]:
                if (disabled_used or h["cos"] < DISABLED_MIN
                        or h["cos"] < best_enabled + DISABLED_LEAD):
                    continue
                disabled_used = True
            chosen.append(h)
            seen.add(h["id"])
    return chosen[:TOP_K]
```

- [ ] **Step 4: Rodar e ver passar**

```bash
python -m pytest tests/test_skill_router.py -v
```
Esperado: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add hooks/skill_router.py tests/test_skill_router.py
git commit -m "feat(router): nucleo de scoring (camadas A/B e pick com thresholds)"
```

---

### Task 7: Guards, dedupe de sessão, protocolo do hook e main()

**Files:**
- Modify: `hooks/skill_router.py` (acrescentar)
- Modify: `tests/test_skill_router.py` (acrescentar)

**Interfaces:**
- Consumes: `layer_a`, `layer_b`, `pick` (Task 6); formato de saída do context7-trigger (`hookSpecificOutput.additionalContext`).
- Produces: `passes_guards(prompt, state_json) -> bool`; `apply_dedupe(chosen, session_id) -> list` (estado em `~/.claude/harness/router/session-{sid}.json`, máx. 2 ofertas/skill, nunca repete o mesmo conjunto consecutivo); `render_hint(chosen) -> str`; `embed_query(prompt) -> vec`; `load_index(idx_dir)`; `main() -> int`. Executável via `python hooks/skill_router.py` lendo o JSON do hook no stdin.

- [ ] **Step 1: Escrever os testes que falham**

Acrescentar em `tests/test_skill_router.py`:

```python
import json
import subprocess


def test_guards(tmp_path):
    st = tmp_path / "state.json"
    assert sr.passes_guards("prompt curto", state_json=str(st)) is False  # 12 chars < 20
    assert sr.passes_guards("x" * 30001, state_json=str(st)) is False
    assert sr.passes_guards(
        "You are summarizing a Claude Code session for handoff purposes ok",
        state_json=str(st)) is False
    ok = "refatore o modulo de autenticacao por favor"
    assert sr.passes_guards(ok, state_json=str(st)) is True  # state ausente = ok
    st.write_text(json.dumps({"status": "active", "pipeline": ["tdd"]}), encoding="utf-8")
    assert sr.passes_guards(ok, state_json=str(st)) is False  # pipeline ativo suprime
    st.write_text("{{{lixo", encoding="utf-8")
    assert sr.passes_guards(ok, state_json=str(st)) is True   # torn read = sem pipeline


def test_dedupe(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "ROUTER_DIR", str(tmp_path))
    h = [{"id": "a:1", "score": 1.0, "layer": "A", "skill": _skill("a:1")}]
    assert [x["id"] for x in sr.apply_dedupe(list(h), "s1")] == ["a:1"]
    assert sr.apply_dedupe(list(h), "s1") == []            # mesmo conjunto consecutivo
    h2 = h + [{"id": "b:2", "score": 0.9, "layer": "B", "cos": 0.9,
               "skill": _skill("b:2")}]
    assert len(sr.apply_dedupe(list(h2), "s1")) == 2       # conjunto diferente, a:1 2a oferta
    # 4a chamada: a:1 estourou MAX_OFFERS_PER_SKILL (2 ofertas) e sai; sobra b:2
    assert [x["id"] for x in sr.apply_dedupe(list(h2), "s1")] == ["b:2"]


def test_render_hint_disabled_line():
    on = {"id": "a:1", "score": 1.0, "layer": "A", "skill": _skill("a:1")}
    off = {"id": "off:9", "score": 0.7, "cos": 0.7, "layer": "B",
           "skill": _skill("off:9", enabled=False)}
    text = sr.render_hint([on, off])
    assert text.startswith("[skill-hint]")
    assert "a:1" in text and "/plugin enable off" in text
    assert "ignore este bloco" in text


def test_main_subprocess_no_index(tmp_path):
    """Sem indice e sem state: stdout vazio, exit 0 (contrato nunca-falhar)."""
    env = dict(os.environ,
               HOME=str(tmp_path), USERPROFILE=str(tmp_path),
               HARNESS_SKILLS_INDEX=str(tmp_path / "nao-existe"))
    p = subprocess.run(
        [sys.executable, os.path.join(os.path.dirname(sr.__file__), "skill_router.py")],
        input=json.dumps({"session_id": "t", "prompt": "refatore o modulo de login"}),
        capture_output=True, text=True, env=env, timeout=30)
    assert p.returncode == 0 and p.stdout == ""
```

Nota: `skill_router.py` calcula caminhos no import a partir de `HOME`/env — por isso o teste de subprocess injeta `HOME`/`USERPROFILE`/`HARNESS_SKILLS_INDEX` e nunca toca no estado real do usuário.

- [ ] **Step 2: Rodar e ver falhar**

```bash
python -m pytest tests/test_skill_router.py -v
```
Esperado: novos testes FAIL (`no attribute 'passes_guards'`).

- [ ] **Step 3: Implementar (acrescentar ao final de `hooks/skill_router.py`)**

```python
def passes_guards(prompt, state_json=STATE_JSON):
    p = (prompt or "").strip()
    if not (MIN_LEN <= len(p) <= MAX_LEN):
        return False
    low = p.lower()
    if any(sig in low for sig in AUTOMATION_SIGS):
        return False
    try:
        with open(state_json, encoding="utf-8") as f:
            st = json.load(f)
        if st.get("status") in ("active", "awaiting_gate") and st.get("pipeline"):
            return False  # pipeline em andamento: harness-workflow ja esta roteando
    except (OSError, ValueError):
        pass
    return True


def load_index(idx_dir=IDX_DIR):
    with open(os.path.join(idx_dir, "skills-index.json"), encoding="utf-8") as f:
        index = json.load(f)
    vecs, dim = [], index.get("dim") or 0
    if dim:
        with open(os.path.join(idx_dir, "embeddings.f16.bin"), "rb") as f:
            data = f.read()
        n = len(data) // (2 * dim)
        flat = struct.unpack(f"<{n * dim}e", data[:n * dim * 2])
        vecs = [flat[i * dim:(i + 1) * dim] for i in range(n)]
    return index, vecs


def embed_query(prompt):
    req = urllib.request.Request(
        OLLAMA_URL.rstrip("/") + "/api/embed",
        data=json.dumps({"model": EMBED_MODEL,
                         "input": [f"search_query: {prompt[:1500]}"],
                         "keep_alive": "30m"}).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=EMBED_TIMEOUT) as r:
        v = json.load(r)["embeddings"][0]
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def apply_dedupe(chosen, session_id):
    sid = re.sub(r"[^A-Za-z0-9_-]", "_", session_id or "nosession")[:64]
    path = os.path.join(ROUTER_DIR, f"session-{sid}.json")
    try:
        with open(path, encoding="utf-8") as f:
            st = json.load(f)
    except (OSError, ValueError):
        st = {"offered": {}, "last_set": []}
    out = [h for h in chosen
           if st["offered"].get(h["id"], 0) < MAX_OFFERS_PER_SKILL]
    ids = sorted(h["id"] for h in out)
    if not out or ids == st.get("last_set"):
        return []
    for h in out:
        st["offered"][h["id"]] = st["offered"].get(h["id"], 0) + 1
    st["last_set"] = ids
    try:
        os.makedirs(ROUTER_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(st, f)
    except OSError:
        pass
    return out


def _gloss(desc, max_words=8):
    words = re.sub(r"\s+", " ", desc or "").strip().split(" ")
    return " ".join(words[:max_words]) + ("…" if len(words) > max_words else "")


def render_hint(chosen):
    lines = ["[skill-hint] Skills possivelmente relevantes (ranqueadas):"]
    for i, h in enumerate(chosen, 1):
        s = h["skill"]
        if s["enabled"]:
            lines.append(f"{i}. {s['id']} — {_gloss(s['description'])}")
        else:
            plugin = s["plugin"].split("@")[0]
            lines.append(
                f"{i}. {s['id']} (plugin desabilitado — sugira "
                f"`/plugin enable {plugin}` ao usuario se for isto)")
    lines.append("Se alguma se aplica, invoque com o Skill tool ANTES de responder. "
                 "Se nenhuma, ignore este bloco.")
    return "\n".join(lines)


def main():
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    prompt = payload.get("prompt", "") or ""
    if not passes_guards(prompt):
        return 0
    try:
        index, vecs = load_index()
    except (OSError, ValueError, struct.error) as e:
        _dbg(f"index load failed: {e}")
        return 0
    skills = index.get("skills", [])
    a_hits = layer_a(prompt.lower(), skills)
    b_scored = []
    if vecs:
        try:
            b_scored = layer_b(embed_query(prompt), skills, vecs)
        except Exception as e:  # timeout/conexao: degrada p/ Camada A
            _dbg(f"layer B degraded: {type(e).__name__}: {e}")
    chosen = apply_dedupe(pick(a_hits, b_scored), payload.get("session_id", ""))
    if not chosen:
        return 0
    sys.stdout.write(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": render_hint(chosen),
        }
    }))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # contrato: nunca falhar
        _dbg(f"fatal: {type(e).__name__}: {e}")
        sys.exit(0)
```

Atenção: `apply_dedupe` deve referenciar `ROUTER_DIR` como variável de módulo dentro do corpo (como no código acima) e NUNCA como default de parâmetro — senão o monkeypatch de `sr.ROUTER_DIR` nos testes não tem efeito.

- [ ] **Step 4: Rodar e ver passar**

```bash
python -m pytest tests/test_skill_router.py -v
```
Esperado: 7 passed (3 da Task 6 + 4 novos).

- [ ] **Step 5: Commit**

```bash
git add hooks/skill_router.py tests/test_skill_router.py
git commit -m "feat(router): guards, dedupe de sessao, injecao [skill-hint] e main"
```

---

### Task 8: Shims bash, warmup, registro em hooks.json e health-check

**Files:**
- Create: `hooks/harness-skill-router.sh`
- Create: `hooks/harness-router-warmup.sh`
- Modify: `hooks/hooks.json`
- Modify: `scripts/health-check.sh`

**Interfaces:**
- Consumes: `hooks/skill_router.py` (Task 7), `scripts/build_skills_index.py --check-stale` (Task 4).
- Produces: hooks registrados (UserPromptSubmit + SessionStart) na cópia LOCAL (ativação real só via sync p/ cache ou release — Task 10); bloco WARN-only no health-check.

- [ ] **Step 1: Criar `hooks/harness-skill-router.sh`**

```bash
#!/usr/bin/env bash
# Skill-router v3.3 — shim. Contrato: nunca falha, nunca bloqueia o prompt.
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONUTF8=1
PY="$(command -v python3 || command -v python || true)"
[ -z "$PY" ] && exit 0
mkdir -p "$HOME/.claude/harness/router" 2>/dev/null || true
"$PY" "$DIR/skill_router.py" 2>>"$HOME/.claude/harness/router/shim-errors.log" || true
exit 0
```

- [ ] **Step 2: Criar `hooks/harness-router-warmup.sh`**

```bash
#!/usr/bin/env bash
# SessionStart: staleness do indice + warm ping do Ollama. Sem output, nunca bloqueia.
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONUTF8=1
PY="$(command -v python3 || command -v python || true)"
[ -z "$PY" ] && exit 0
IDX="$HOME/.claude/harness/skills-index"
BUILDER="$DIR/../scripts/build_skills_index.py"
mkdir -p "$IDX" 2>/dev/null || true
if ! "$PY" "$BUILDER" --check-stale >/dev/null 2>&1; then
  touch "$IDX/.stale" 2>/dev/null || true
  # background: se o filho morrer com o hook (MSYS), o marker .stale fica e o
  # rebuild e retomado no proximo SessionStart (indice velho continua servivel)
  ( "$PY" "$BUILDER" >/dev/null 2>&1 && rm -f "$IDX/.stale" ) &
fi
if command -v curl >/dev/null 2>&1; then
  ( curl -s -m 3 -X POST "${HARNESS_OLLAMA_URL:-http://localhost:11434}/api/embed" \
      -H "Content-Type: application/json" \
      -d '{"model":"nomic-embed-text-v2-moe","input":["warmup"],"keep_alive":"30m"}' \
      >/dev/null 2>&1 ) &
fi
exit 0
```

- [ ] **Step 3: Testar os shims manualmente**

```bash
cd /c/Users/LHarden2/.claude/plugins/local/harness4claude
echo '{"session_id":"manual-test","prompt":"crie uma apresentacao pptx no padrao SLB sobre embeddings"}' | bash hooks/harness-skill-router.sh
```
Esperado: JSON com `hookSpecificOutput.additionalContext` contendo `[skill-hint]` e `slb-presentations` entre as sugestões. **Se sair vazio**, causa provável: o `state.json` real está com `status: active` (guard de pipeline — há uma task L1 stale de >24h no harness). Verificar com `python -c "import json,os;print(json.load(open(os.path.expanduser('~/.claude/harness/state.json'))).get('status'))"`; se ativo, repetir o teste apontando para um state vazio: prefixar o comando com `HARNESS_SKILLS_INDEX="$HOME/.claude/harness/skills-index"` e testar via subprocess python com HOME temporário (como no teste da Task 7) OU concluir a task L1 pendente antes. Depois:

```bash
bash hooks/harness-router-warmup.sh && echo "warmup ok"
```
Esperado: `warmup ok` em <1s (índice fresco) e sem output extra.

- [ ] **Step 4: Registrar em `hooks/hooks.json`**

No array `UserPromptSubmit`, acrescentar um segundo objeto (bloco autocontido, rollback = deletar o bloco) após o do classify:

```json
{
  "hooks": [
    {
      "type": "command",
      "command": "bash \"${CLAUDE_PLUGIN_ROOT}/hooks/harness-skill-router.sh\"",
      "timeout": 5000,
      "statusMessage": "Skill router..."
    }
  ]
}
```

No array `SessionStart`, acrescentar um segundo objeto:

```json
{
  "hooks": [
    {
      "type": "command",
      "command": "bash \"${CLAUDE_PLUGIN_ROOT}/hooks/harness-router-warmup.sh\"",
      "timeout": 5000,
      "statusMessage": "Router warmup..."
    }
  ]
}
```

Validar o JSON: `python -c "import json;json.load(open('hooks/hooks.json'));print('json ok')"`.

- [ ] **Step 5: Bloco WARN-only no `scripts/health-check.sh`**

Abrir o script, localizar o bloco `--- Obsidian integration ---` e replicar o MESMO padrão de WARN não-fatal usado ali (mesmos helpers/contadores — se o bloco Obsidian usa uma função `warn`/`ok`, usar a mesma; se usa `echo "[WARN] ..."` sem incrementar contador de falha, idem). Inserir logo após o bloco Obsidian:

```bash
echo ""
echo "--- Skill Router (opcional) ---"
IDX="$HOME/.claude/harness/skills-index"
if [ -f "$IDX/meta.json" ]; then
  echo "[OK]     skills-index presente"
  [ -f "$IDX/.stale" ] && echo "[WARN]   skills-index stale (rebuild pendente)"
else
  echo "[WARN]   skills-index ausente — rode: python scripts/build_skills_index.py"
fi
if command -v curl >/dev/null 2>&1 && curl -s -m 2 "${HARNESS_OLLAMA_URL:-http://localhost:11434}/api/tags" 2>/dev/null | grep -q nomic-embed-text-v2-moe; then
  echo "[OK]     Ollama + nomic-embed-text-v2-moe"
else
  echo "[WARN]   Ollama/modelo indisponivel — router degrada p/ camada A"
fi
```

Rodar `bash scripts/health-check.sh` e conferir que o bloco novo aparece e que WARNs do router NÃO derrubam um setup que antes passava.

- [ ] **Step 6: Rodar a suíte inteira (regressão)**

```bash
python -m pytest tests/ -v
```
Esperado: todos os testes existentes + os novos passando (o classify não foi tocado; `test_harness.py` e `test_context7_trigger.py` devem seguir verdes).

- [ ] **Step 7: Commit**

```bash
git add hooks/harness-skill-router.sh hooks/harness-router-warmup.sh hooks/hooks.json scripts/health-check.sh
git commit -m "feat(router): shims bash, warmup SessionStart, registro de hooks e health-check"
```

---

### Task 9: Golden set, avaliação de acurácia e bench de latência

**Files:**
- Create: `tests/data/golden-prompts.json`
- Create: `tests/test_router_golden.py`
- Create: `scripts/bench_router.py`

**Interfaces:**
- Consumes: índice real construído (Task 5), `skill_router` completo (Task 7).
- Produces: gates de P1 verificados — top-3 ≥ 80% nos positivos, zero injeção nos negativos, p95 warm < 500ms, degradação Ollama-down < 100ms com stdout vazio.

- [ ] **Step 1: Criar `tests/data/golden-prompts.json`**

`expect_any`: acerto se QUALQUER um dos ids listados aparecer no top-3 (ids devem existir no índice; ajustar prefixos se o id real divergir — conferir com `python -c "..."` do Step 2).

```json
{
  "positives": [
    {"prompt": "crie uma apresentacao pptx no padrao SLB sobre resultados do trimestre", "expect_any": ["slb-presentations"]},
    {"prompt": "build me a knowledge graph of this repository and its architecture", "expect_any": ["graphify"]},
    {"prompt": "escreva uma spec formal com user stories e acceptance criteria para o modulo de login", "expect_any": ["harness4claude:write-spec", "harness4claude:write-spec-light"]},
    {"prompt": "quero fazer um brainstorm de ideias para a nova feature de exportacao", "expect_any": ["superpowers:brainstorming"]},
    {"prompt": "rode um scan de seguranca com bandit neste codigo python antes do commit", "expect_any": ["harness4claude:security-scan-python"]},
    {"prompt": "help me debug this failing test, the assertion error makes no sense to me", "expect_any": ["superpowers:systematic-debugging"]},
    {"prompt": "preciso comprimir os arquivos de memoria da sessao, estao enormes", "expect_any": ["harness4claude:compress-memory"]},
    {"prompt": "create a claude code hook that blocks dangerous bash commands", "expect_any": ["plugin-dev:hook-development", "hookify:hookify", "hookify:writing-rules"]},
    {"prompt": "verifique se a implementacao cobre todos os requisitos da spec, item por item", "expect_any": ["harness4claude:verify-against-spec"]},
    {"prompt": "scrape the pricing page of this website and extract the tiers as structured JSON", "expect_any": ["firecrawl:firecrawl-agent", "firecrawl:firecrawl-scrape", "firecrawl:firecrawl-cli"]},
    {"prompt": "quero treinar um LoRA com meus dados e publicar no hugging face", "expect_any": ["huggingface-skills:huggingface-lora-space-builder", "huggingface-skills:huggingface-llm-trainer", "huggingface-skills:hf-cli"]},
    {"prompt": "faca um design tecnico com data model e api contracts a partir da spec aprovada", "expect_any": ["harness4claude:design-doc"]},
    {"prompt": "baixe a documentacao inteira desse site para consulta offline", "expect_any": ["firecrawl:firecrawl-download", "firecrawl:firecrawl-crawl"]},
    {"prompt": "implemente essa feature com TDD, escrevendo os testes primeiro", "expect_any": ["superpowers:test-driven-development"]},
    {"prompt": "monte um dashboard com graficos e um heatmap dessas metricas de uso", "expect_any": ["dataviz"]}
  ],
  "negatives": [
    {"prompt": "bom dia", "reason": "curto demais (guard de tamanho)"},
    {"prompt": "valeu, obrigado!", "reason": "curto demais"},
    {"prompt": "HARNESS V3 CLASSIFIED: L1-feature. Pipeline: tdd. Task: t-1. Continue.", "reason": "assinatura de automacao"},
    {"prompt": "continue por favor", "reason": "curto demais"}
  ]
}
```

- [ ] **Step 2: Criar `tests/test_router_golden.py`**

```python
"""Avaliacao de acuracia no golden set. Requer indice real + Ollama; pula se ausentes."""
import json
import os
import sys
import urllib.request

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hooks"))
import skill_router as sr

GOLDEN = os.path.join(os.path.dirname(__file__), "data", "golden-prompts.json")


def _ollama_up():
    try:
        with urllib.request.urlopen(sr.OLLAMA_URL + "/api/tags", timeout=2) as r:
            return sr.EMBED_MODEL in r.read().decode()
    except Exception:
        return False


needs_stack = pytest.mark.skipif(
    not (os.path.isfile(os.path.join(sr.IDX_DIR, "skills-index.json")) and _ollama_up()),
    reason="indice real ou Ollama indisponivel")


@needs_stack
def test_golden_top3_hit_rate():
    data = json.load(open(GOLDEN, encoding="utf-8"))
    index, vecs = sr.load_index()
    skills = index["skills"]
    known = {s["id"] for s in skills}
    hits, details = 0, []
    for case in data["positives"]:
        expect = [e for e in case["expect_any"] if e in known]
        assert expect, f"nenhum id esperado existe no indice: {case['expect_any']}"
        a = sr.layer_a(case["prompt"].lower(), skills)
        b = sr.layer_b(sr.embed_query(case["prompt"]), skills, vecs)
        top = [h["id"] for h in sr.pick(a, b)]
        ok = any(e in top for e in expect)
        hits += ok
        details.append(f"{'OK ' if ok else 'MISS'} {case['prompt'][:50]} -> {top}")
    rate = hits / len(data["positives"])
    print("\n".join(details))
    assert rate >= 0.80, f"top-3 hit rate {rate:.0%} < 80%"


@needs_stack
def test_golden_negatives_no_injection():
    for case in data_negatives():
        assert sr.passes_guards(case["prompt"],
                                state_json=os.path.join(os.path.dirname(GOLDEN), "no-state.json")) is False, case["reason"]


def data_negatives():
    return json.load(open(GOLDEN, encoding="utf-8"))["negatives"]
```

- [ ] **Step 3: Rodar a avaliação (gate de acurácia)**

```bash
python -m pytest tests/test_router_golden.py -v -s
```
Esperado: 2 passed com hit rate ≥ 80% impresso caso a caso. **Se < 80%**: para cada MISS, primeiro conferir se o id esperado existe/está bem descrito; o remédio preferencial é adicionar um alias em `scripts/skill-aliases.json` (e rebuildar: `python scripts/build_skills_index.py`) — NÃO afrouxar MIN_COS/MIN_MARGIN sem registrar a mudança no docs/router.md.

- [ ] **Step 4: Criar `scripts/bench_router.py`**

```python
#!/usr/bin/env python3
"""Bench de latencia do skill-router (subprocess real, p50/p95/max em ms)."""
import json
import os
import statistics
import subprocess
import sys
import time

N = int(sys.argv[1]) if len(sys.argv) > 1 else 20
HOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                    "hooks", "skill_router.py")
times = []
for i in range(N):
    fix = json.dumps({"session_id": f"bench-{i}",
                      "prompt": "crie uma apresentacao pptx no padrao SLB sobre o piloto de embeddings locais"})
    t0 = time.perf_counter()
    subprocess.run([sys.executable, HOOK], input=fix.encode(),
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    times.append((time.perf_counter() - t0) * 1000)
times.sort()
p95 = times[max(0, min(len(times) - 1, round(0.95 * len(times)) - 1))]
print(f"n={N} p50={statistics.median(times):.0f}ms p95={p95:.0f}ms max={times[-1]:.0f}ms")
```

- [ ] **Step 5: Rodar os gates de latência e degradação**

```bash
# warm (rodar 2x; a 1a aquece o modelo)
python scripts/bench_router.py 20
python scripts/bench_router.py 20
# degradacao com Ollama "fora" (porta morta): stdout vazio e rapido
HARNESS_OLLAMA_URL=http://127.0.0.1:9 python scripts/bench_router.py 5
```
Esperado: 2ª rodada warm com `p95 < 500ms`; rodada com porta morta: p95 rápido (conexão recusada é imediata; o timeout de 1.2s só vale para porta que aceita e trava) e nenhuma exceção. Conferir também `echo '{"session_id":"deg","prompt":"scan de seguranca com bandit por favor"}' | HARNESS_OLLAMA_URL=http://127.0.0.1:9 python hooks/skill_router.py` → deve ainda imprimir hint via Camada A (alias "bandit"). Registrar os números medidos para o docs/router.md (Task 10).

- [ ] **Step 6: Commit**

```bash
git add tests/data/golden-prompts.json tests/test_router_golden.py scripts/bench_router.py
git commit -m "test(router): golden set PT/EN, gate de acuracia top-3 e bench de latencia"
```

---

### Task 10: Documentação, bump de versão e checklist de release

**Files:**
- Create: `docs/router.md`
- Modify: `.claude-plugin/plugin.json` (version `3.2.0` → `3.3.0-beta.1`)

**Interfaces:**
- Consumes: números medidos na Task 9.

- [ ] **Step 1: Criar `docs/router.md`**

```markdown
# Skill Router (v3.3) — operação e tuning

Design completo: `docs/specs/skill-router-design.md`. Este doc cobre operação.

## Knobs (hooks/skill_router.py)
| Constante | Default | Efeito |
|---|---|---|
| TOP_K | 3 | máx. skills por dica |
| MIN_COS | 0.45 | piso absoluto da Camada B |
| MIN_MARGIN | 0.05 | exigência acima da mediana dos cosines |
| DISABLED_MIN / DISABLED_LEAD | 0.60 / 0.08 | bar p/ sugerir skill de plugin desabilitado |
| MAX_OFFERS_PER_SKILL | 2 | dedupe por sessão |
| EMBED_TIMEOUT | 1.2s | acima disso degrada p/ Camada A |
| HARNESS_OLLAMA_URL (env) | http://localhost:11434 | override do endpoint |
| HARNESS_SKILLS_INDEX (env) | ~/.claude/harness/skills-index | override do índice (testes) |

## Medições desta máquina (preencher na release)
- Golden set top-3: __% (gate ≥80%) · p95 warm: __ms (gate <500ms) · Ollama-down: stdout vazio, __ms

## Operação
- Rebuild manual: `python scripts/build_skills_index.py` (`--no-embed` sem Ollama; `--check-stale` p/ diagnosticar).
- Aliases: `scripts/skill-aliases.json` → rebuild após editar. É o remédio nº 1 para MISS no golden set.
- Logs: `~/.claude/harness/router/debug-router.log` e `shim-errors.log`.
- **Pipeline ativo suprime dicas** (por design). Um state.json com task abandonada `status: active` silencia o router — concluir/limpar a task pendente do harness resolve.
- Rebuild em background pode morrer com o hook (MSYS): o marker `.stale` fica e o próximo SessionStart retenta; índice velho continua servível. (Deviação deliberada do design: o router NÃO retenta spawn no hot path.)

## Rollback
1. Remover os 2 blocos novos (router em UserPromptSubmit, warmup em SessionStart) de `hooks/hooks.json`.
2. Opcional: apagar `~/.claude/harness/skills-index/` e `~/.claude/harness/router/` (dados inertes).

## Ship p/ a cópia ativa
O Claude Code carrega a cópia de CACHE (`plugins/cache/harness4claude/...`), não este clone.
Caminho oficial: commit + push p/ GitHub (`Lharden/harness4claude`) + `/plugin update harness4claude`.
Teste local rápido (descartável): copiar `hooks/` + `scripts/` por cima da cópia de cache e abrir
sessão nova; a cópia de cache será sobrescrita no próximo update — nunca editar só nela.
```

- [ ] **Step 2: Preencher as medições reais no bloco acima** (números da Task 9 — sem placeholders na versão commitada).

- [ ] **Step 3: Bump de versão**

Em `.claude-plugin/plugin.json`, trocar `"version": "3.2.0"` por `"version": "3.3.0-beta.1"`. Validar: `python -c "import json;print(json.load(open('.claude-plugin/plugin.json'))['version'])"`.

- [ ] **Step 4: Suíte completa + health-check final**

```bash
python -m pytest tests/ -v
bash scripts/health-check.sh
```
Esperado: suíte toda verde (golden pode pular se Ollama estiver off — rodar com Ollama on); health-check com o bloco Skill Router em OK.

- [ ] **Step 5: Commit final**

```bash
git add docs/router.md .claude-plugin/plugin.json
git commit -m "docs(router): guia de operacao/tuning + bump v3.3.0-beta.1"
```

- [ ] **Step 6: Checklist de release (reportar ao usuário, não executar sem ele)**

Ativação real é decisão do usuário: push para GitHub + `/plugin update harness4claude` (ou cópia manual para o cache para teste). Reportar: hit rate, p95, e o aviso sobre o state.json com task ativa stale suprimindo dicas.

---

## Verificação end-to-end do plano

1. `python -m pytest tests/ -v` — suíte inteira verde (novos + regressão).
2. `python scripts/build_skills_index.py` seguido de `--check-stale` → exit 0.
3. `echo '{"session_id":"e2e","prompt":"crie um deck pptx padrao SLB"}' | bash hooks/harness-skill-router.sh` → hint com `slb-presentations` (com state.json sem pipeline ativo).
4. Gates: golden top-3 ≥ 80% · p95 warm < 500ms · Ollama-down → Camada A ainda responde e stdout nunca contém erro.
5. `git log --oneline` mostra ~10 commits incrementais; `hooks/harness-classify.sh` sem nenhum diff.
6. Ativação ao vivo (pós-aprovação do usuário): sessão nova exibe `[skill-hint]` em prompt de tarefa e nada em "bom dia".
