"""Servidor MCP stdio minimo no formato do `shs.exe claims-mcp`.

A sonda de `integration.science-harness` (`scripts/science_probe.py`) sobe este processo a partir do registro do
`science_harness` e faz `initialize` e `tools/list`. As ferramentas sao as nove do servidor real (medido em
2026-10-21: `claims 1.30.0`). Cada flag tira ou estraga um pedaco de um servidor bom:

    --sem NOME        omite a ferramenta NOME do tools/list (repetivel)
    --mudo            nunca responde (le e descarta)
    --morre           sai antes de responder ao initialize
    --invalido        responde ao initialize com uma linha que nao e JSON
    --erro            responde ao initialize com `error`
    --eco-env NOME    escreve o valor da variavel NOME no stderr (para provar que o valor de `env` nao vaza)
    --pid ARQUIVO     grava o pid do proprio servidor em ARQUIVO (para provar que ele nao sobrevive)

Sem flag, e um servidor bom. Nenhuma ferramenta e executavel: a sonda nao chama `tools/call`.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

FERRAMENTAS = [
    "get_claim", "get_document", "list_corpora", "map_entry", "navigate", "neighbors", "search_claims",
    "search_documents", "server_info",
]


def _escrever(mensagem: dict) -> None:
    sys.stdout.write(json.dumps(mensagem) + "\n")
    sys.stdout.flush()


def main() -> int:
    analisador = argparse.ArgumentParser()
    analisador.add_argument("--sem", action="append", default=[])
    for flag in ("--mudo", "--morre", "--invalido", "--erro"):
        analisador.add_argument(flag, action="store_true")
    analisador.add_argument("--eco-env", default=None)
    analisador.add_argument("--pid", default=None)
    a = analisador.parse_args()

    if a.pid:
        with open(a.pid, "w", encoding="utf-8") as arquivo:
            arquivo.write(str(os.getpid()))
    if a.eco_env:
        sys.stderr.write(f"env {a.eco_env}={os.environ.get(a.eco_env, '')}\n")
        sys.stderr.flush()
    ferramentas = [{"name": n, "inputSchema": {"type": "object"}} for n in FERRAMENTAS if n not in a.sem]
    for linha in sys.stdin:
        if a.mudo or not linha.strip():
            continue
        mensagem = json.loads(linha)
        if "id" not in mensagem:
            continue
        metodo = mensagem.get("method")
        if metodo == "initialize":
            if a.morre:
                return 3
            if a.invalido:
                sys.stdout.write("isto nao e json\n")
                sys.stdout.flush()
                continue
            if a.erro:
                _escrever({"jsonrpc": "2.0", "id": mensagem["id"], "error": {"code": -32603, "message": "falhou"}})
                continue
            _escrever({"jsonrpc": "2.0", "id": mensagem["id"], "result": {
                "protocolVersion": "2025-06-18", "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "claims", "version": "fake"}}})
        elif metodo == "tools/list":
            _escrever({"jsonrpc": "2.0", "id": mensagem["id"], "result": {"tools": ferramentas}})
        else:
            _escrever({"jsonrpc": "2.0", "id": mensagem["id"],
                       "error": {"code": -32601, "message": f"metodo nao suportado: {metodo}"}})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
