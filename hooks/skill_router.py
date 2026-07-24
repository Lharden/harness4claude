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
