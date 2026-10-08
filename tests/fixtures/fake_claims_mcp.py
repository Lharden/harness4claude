"""Servidor MCP stdio minimo no formato do `shs.exe claims-mcp`.

A sonda de `integration.science-harness` sobe este processo a partir do registro
que o hook `science_intent` aceitou, e chama `server_info` como o modelo chamaria.
Responde so o que a sonda usa: `initialize`, `tools/list` e `tools/call` de
`server_info`. O formato de `server_info` copia o do servidor real (medido em
2026-10-07: chaves `corpora`, `retrieval`, `refused`).
"""
from __future__ import annotations

import json
import sys

SERVER_INFO = {"corpora": ["probe"], "retrieval": "bm25", "refused": {}}


def _responder(mensagem: dict) -> dict | None:
    if "id" not in mensagem:
        return None
    metodo = mensagem.get("method")
    if metodo == "initialize":
        resultado = {
            "protocolVersion": mensagem.get("params", {}).get("protocolVersion", "2025-06-18"),
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "claims", "version": "fake"},
        }
    elif metodo == "tools/list":
        resultado = {"tools": [{"name": "server_info", "inputSchema": {"type": "object"}}]}
    elif metodo == "tools/call" and mensagem.get("params", {}).get("name") == "server_info":
        resultado = {"content": [{"type": "text", "text": json.dumps(SERVER_INFO)}], "isError": False}
    else:
        return {"jsonrpc": "2.0", "id": mensagem["id"],
                "error": {"code": -32601, "message": f"metodo nao suportado: {metodo}"}}
    return {"jsonrpc": "2.0", "id": mensagem["id"], "result": resultado}


def main() -> int:
    for linha in sys.stdin:
        if not linha.strip():
            continue
        resposta = _responder(json.loads(linha))
        if resposta is not None:
            sys.stdout.write(json.dumps(resposta) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
