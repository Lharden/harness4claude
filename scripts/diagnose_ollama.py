#!/usr/bin/env python
"""Smoke-test do Ollama para uso com Obsidian (Text Generator + karpathywiki).

Valida ponta a ponta:
- Servidor Ollama ativo e modelo presente
- Geracao via API NATIVA (/api/generate)         -> caminho do Text Generator (Langchain)
- Geracao via API OpenAI-compat (/v1/chat/...)    -> caminho do karpathywiki
- CORS para a origem do Obsidian (Origin: app://obsidian.md)
- Qualidade: extracao estruturada (JSON de entidades) + resumo
- Velocidade aproximada (tokens/s)

Uso: python diagnose_ollama.py [--model qwen3.5:9b]
Exit 0 = tudo OK; 1 = alguma falha.
"""

from __future__ import annotations

import argparse
import json
import re
import time
import urllib.error
import urllib.request

HOST = "http://127.0.0.1:11434"
ORIGIN = "app://obsidian.md"

EXTRACTION_TEXT = (
    "O Leonardo usa o Obsidian com o Ollama na maquina MAINFRAME (RTX 3080) "
    "para gerenciar conhecimento seguindo o padrao LLM Wiki do Karpathy."
)


def _request(path: str, payload: dict | None, *, origin: bool, timeout: int = 180):
    """Faz GET (payload None) ou POST JSON. Retorna (status, body_dict, headers)."""
    headers = {"Content-Type": "application/json"}
    if origin:
        headers["Origin"] = ORIGIN
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(HOST + path, data=data, headers=headers,
                                 method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8")), dict(resp.headers)


def check_server(model: str) -> bool:
    """Servidor responde e o modelo esta instalado?"""
    try:
        _, body, _ = _request("/api/tags", None, origin=False, timeout=10)
    except (urllib.error.URLError, OSError) as exc:
        print(f"[FAIL] servidor Ollama nao respondeu em {HOST} ({exc})")
        return False
    names = [m.get("name", "") for m in body.get("models", [])]
    ok = any(n == model or n.startswith(model.split(":")[0]) for n in names)
    print(f"[{'OK' if ok else 'FAIL'}]   servidor ativo; modelo '{model}' "
          f"{'presente' if ok else 'AUSENTE — rode: ollama pull ' + model}")
    return ok


def test_native(model: str) -> bool:
    """Caminho do Text Generator: /api/generate (+ CORS Origin)."""
    payload = {"model": model, "prompt": "Responda apenas: OK", "stream": False,
               "options": {"num_predict": 20}}
    try:
        t0 = time.perf_counter()
        _, body, _ = _request("/api/generate", payload, origin=True)
        dt = time.perf_counter() - t0
    except urllib.error.HTTPError as exc:
        hint = " (CORS? confira OLLAMA_ORIGINS e reinicie o Ollama)" if exc.code == 403 else ""
        print(f"[FAIL]   /api/generate HTTP {exc.code}{hint}")
        return False
    except (urllib.error.URLError, OSError) as exc:
        print(f"[FAIL]   /api/generate erro: {exc}")
        return False
    n = body.get("eval_count", 0)
    dur = body.get("eval_duration", 0) or 0
    tps = (n / (dur / 1e9)) if dur else 0
    print(f"[OK]     /api/generate (Text Generator) respondeu em {dt:.1f}s; ~{tps:.0f} tok/s")
    return True


def test_openai(model: str) -> bool:
    """Caminho do karpathywiki: /v1/chat/completions (+ CORS Origin)."""
    payload = {"model": model, "stream": False, "max_tokens": 20,
               "messages": [{"role": "user", "content": "Responda apenas: OK"}]}
    try:
        _, body, _ = _request("/v1/chat/completions", payload, origin=True)
    except urllib.error.HTTPError as exc:
        hint = " (CORS? confira OLLAMA_ORIGINS)" if exc.code == 403 else ""
        print(f"[FAIL]   /v1/chat/completions HTTP {exc.code}{hint}")
        return False
    except (urllib.error.URLError, OSError) as exc:
        print(f"[FAIL]   /v1/chat/completions erro: {exc}")
        return False
    content = body.get("choices", [{}])[0].get("message", {}).get("content", "")
    ok = bool(content)
    print(f"[{'OK' if ok else 'FAIL'}]   /v1/chat/completions (karpathywiki) "
          f"{'respondeu' if ok else 'sem conteudo'}")
    return ok


def _generate(model: str, prompt: str, num_predict: int = 1500) -> tuple[str, str]:
    """Gera via /api/generate. Retorna (response, thinking).

    num_predict alto para acomodar modelos 'thinking' (qwen3.x), que escrevem o
    raciocinio no campo separado 'thinking' ANTES do 'response' — com limite
    baixo, o 'response' (que os plugins leem) sai vazio.
    """
    payload = {"model": model, "prompt": prompt, "stream": False,
               "options": {"num_predict": num_predict, "temperature": 0.2}}
    _, body, _ = _request("/api/generate", payload, origin=True)
    return body.get("response", ""), body.get("thinking", "") or ""


def test_quality_extraction(model: str) -> bool:
    """Qualidade: extrair entidades/conceitos como JSON (uso real do karpathywiki)."""
    prompt = (
        "Extraia entidades e conceitos do texto e responda APENAS com JSON no formato "
        '{"entities": [...], "concepts": [...]}.\n\nTexto: ' + EXTRACTION_TEXT
    )
    try:
        out, thinking = _generate(model, prompt)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
        print(f"[FAIL]   extracao: erro {exc}")
        return False
    think_note = " [modelo THINKING — raciocinio em campo separado]" if thinking else ""
    m = re.search(r"\{.*\}", out, re.DOTALL)
    if not m:
        print(f"[WARN]   extracao: sem JSON no 'response'{think_note} "
              f"(aumente num_predict ou use think:false)")
        return False
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        print("[WARN]   extracao: JSON malformado")
        return False
    blob = json.dumps(data, ensure_ascii=False).lower()
    hits = [w for w in ("leonardo", "obsidian", "ollama", "karpathy", "mainframe") if w in blob]
    ok = "entities" in data and len(hits) >= 3
    print(f"[{'OK' if ok else 'WARN'}]   extracao estruturada: JSON valido, "
          f"{len(hits)}/5 entidades-chave reconhecidas {hits}")
    return ok


def test_quality_summary(model: str) -> bool:
    """Qualidade: resumo em uma frase."""
    try:
        out, _ = _generate(model, "Resuma em UMA frase: " + EXTRACTION_TEXT, num_predict=800)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
        print(f"[FAIL]   resumo: erro {exc}")
        return False
    # remove blocos de raciocinio <think>...</think> se houver
    clean = re.sub(r"<think>.*?</think>", "", out, flags=re.DOTALL).strip()
    ok = len(clean) > 15 and ("obsidian" in clean.lower() or "conhecimento" in clean.lower())
    preview = clean.replace("\n", " ")[:140]
    print(f"[{'OK' if ok else 'WARN'}]   resumo: \"{preview}\"")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test Ollama para Obsidian.")
    parser.add_argument("--model", default="qwen3.5:9b")
    args = parser.parse_args()
    m = args.model

    print(f"=== Diagnostico Ollama + Obsidian (modelo: {m}) ===\n")
    if not check_server(m):
        return 1
    print("\n--- Conectividade + CORS (origem Obsidian) ---")
    native = test_native(m)
    openai = test_openai(m)
    print("\n--- Qualidade (uso real) ---")
    extraction = test_quality_extraction(m)
    summary = test_quality_summary(m)

    print("\n=== Resumo ===")
    results = {"native /api/generate": native, "openai /v1": openai,
               "extracao JSON": extraction, "resumo": summary}
    for name, ok in results.items():
        print(f"  {'OK ' if ok else 'XX '} {name}")
    # criticos: conectividade dos dois caminhos. Qualidade e desejavel (WARN nao falha build).
    return 0 if (native and openai) else 1


if __name__ == "__main__":
    raise SystemExit(main())
