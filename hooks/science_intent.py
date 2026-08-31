#!/usr/bin/env python
from __future__ import annotations

import json
import re
import sys


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    prompt = str(payload.get("prompt") or payload.get("user_prompt") or "")
    if re.search(r"\b(scientific|science|evidence|paper|papers|claim|claims|estudo|evid[eê]ncia|artigo)\b", prompt, re.I):
        print(json.dumps({"systemMessage": "SCIENCE HARNESS: invoke skill='science-evidence'; use science_harness read-only and preserve corpus provenance."}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
