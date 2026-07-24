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
