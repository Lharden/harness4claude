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
