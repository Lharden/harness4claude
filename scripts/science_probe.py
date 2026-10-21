#!/usr/bin/env python
"""Sonda de `integration.science-harness` no harness4claude: lê o registro do `science_harness` no `.claude.json`,
sobe o servidor registrado, faz `initialize` e `tools/list` e responde em uma linha JSON (D-136, desenho
`master-harness/docs/specs/d136-sonda-science-design.md` §3).

Uso (comando de produção):

    python scripts/science_probe.py --json [--cwd DIR] [--prazo SEGUNDOS]

Saída 0 (passa), 1 (reprova) ou 2 (não verificado), a mesma convenção do `mh paridade` e do `mh pronto`. O vocabulário
de motivos e o handshake moram em `scripts/sonda_mcp.py`, cópia derivada do master-harness; aqui mora só o que é do
Claude: onde está o registro e qual escopo vale.

## O leitor do registro

É a lógica que viveu em `hooks/science_intent.py` até o D-136: `CLAUDE_CONFIG_DIR` (ou `~`) e o `.claude.json`; o
escopo local do projeto (o ancestral mais próximo do `--cwd` em `projects`) vale antes do escopo do usuário; e o
`science_harness` em `disabledMcpServers` do projeto desliga o servidor (é o que o `/mcp` grava). A diferença é que a
leitura devolve o motivo e não mais `None`: "não registrado", "desligado" e "config ilegível" são não verificado (a
máquina não declarou o serviço), e executável ausente deixa de contar como "ausente" e passa a reprovar (registrado e
quebrado), que é o defeito do D-136.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sonda_mcp

SERVIDOR = "science_harness"
HOST = "claude"


def _config_path() -> str:
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    return os.path.join(base or os.path.expanduser("~"), ".claude.json")


def _norm(caminho: str) -> str:
    return os.path.normcase(os.path.abspath(caminho))


def _ancestrais(cwd: str) -> list[str]:
    atual, saida = _norm(cwd), []
    while True:
        saida.append(atual)
        pai = os.path.dirname(atual)
        if pai == atual:
            return saida
        atual = pai


def registro_science(cwd: str) -> tuple[Any, str | None, str]:
    """`(spec, motivo, detalhe)` do `science_harness` que o Claude carrega neste cwd.

    `motivo` é `None` quando há um registro a sondar; senão é um motivo `nao_verificado` do núcleo. O `spec` devolvido
    é o registro como está no arquivo (a validação do formato é do núcleo), com `$VAR` do comando já expandido.
    """
    caminho = _config_path()
    try:
        with open(caminho, encoding="utf-8") as arquivo:
            config = json.load(arquivo)
    except FileNotFoundError:
        return None, "config_ausente", f"{caminho} nao existe"
    except (OSError, ValueError) as erro:
        return None, "config_ilegivel", f"{caminho}: {erro.__class__.__name__}"
    try:
        if not isinstance(config, dict):
            raise TypeError("a raiz do .claude.json nao e um objeto")
        bruto = config.get("projects") or {}
        if not isinstance(bruto, dict):
            raise TypeError("projects nao e um objeto")
        projetos = {_norm(k): v for k, v in bruto.items() if isinstance(v, dict)}
        projeto = next((projetos[d] for d in _ancestrais(cwd) if d in projetos), {})
        if SERVIDOR in (projeto.get("disabledMcpServers") or []):
            return None, "desligado", f"{SERVIDOR} esta em disabledMcpServers do projeto"
        for escopo in (projeto, config):
            servidores = escopo.get("mcpServers") or {}
            if not isinstance(servidores, dict):
                raise TypeError("mcpServers nao e um objeto")
            if SERVIDOR in servidores:
                spec = servidores[SERVIDOR]
                if isinstance(spec, dict) and isinstance(spec.get("command"), str):
                    spec = dict(spec, command=os.path.expandvars(spec["command"]))
                return spec, None, ""
    except (AttributeError, TypeError) as erro:
        return None, "config_ilegivel", f"{caminho}: {erro}"
    return None, "nao_registrado", f"{SERVIDOR} nao esta em mcpServers"


def sonda(cwd: str, prazo_s: float) -> dict[str, Any]:
    """O relato da sonda para o `cwd`: lê o registro e, havendo um, sobe o servidor."""
    spec, motivo, detalhe = registro_science(cwd)
    if motivo is not None:
        return sonda_mcp.relato_sem_servidor(HOST, motivo, spec=spec, detalhe=detalhe)
    return sonda_mcp.sondar(spec, host=HOST, cwd=cwd, prazo_s=prazo_s)


def main(argv: list[str] | None = None) -> int:
    analisador = argparse.ArgumentParser(description="Sonda de integration.science-harness (initialize e tools/list).")
    analisador.add_argument("--json", action="store_true", help="a saida e sempre uma linha JSON; aceito por simetria")
    analisador.add_argument("--cwd", default=None, help="o diretorio do projeto (padrao: o cwd)")
    analisador.add_argument("--prazo", type=float, default=sonda_mcp.PRAZO_S, help="prazo da conversa, em segundos")
    args = analisador.parse_args(argv)
    cwd = args.cwd if args.cwd is not None else os.getcwd()
    return sonda_mcp.emitir(sonda(cwd, args.prazo))


if __name__ == "__main__":
    raise SystemExit(main())
