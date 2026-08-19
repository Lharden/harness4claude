"""O endpoint padrao do Ollama nao pode ser um hostname.

`localhost` resolve `::1` antes de `127.0.0.1` nesta maquina, e o Ollama escuta
so em IPv4. `urllib` nao tem happy-eyeballs: ele espera o SYN em `::1` estourar
antes de cair para o IPv4. Medido em 2026-08-19, com o Ollama no ar e o modelo
quente:

    embed_query("http://localhost:11434")   2283 ms
    embed_query("http://127.0.0.1:11434")    220 ms

Sao ~2,06s pagos em todo prompt que chega na Camada B, contra um
`EMBED_TIMEOUT` de 3,0s — margem de 700ms para o embed inteiro. Era o gerador
das "88 falhas consecutivas, 100% TimeoutError" que puseram o router atras de
`HARNESS_ROUTER=1`.

Por que o teste existente nao pegou: `test_router_reachability.py` monta seus
sockets em `127.0.0.1` literal. Ele exercita o relogio, nunca a resolucao de
nome — e o defeito mora exatamente na resolucao.
"""

import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "hooks"))

# Codigo vivo. `docs/` guarda planos e registros historicos, que citam o valor
# antigo de proposito e nao configuram nada.
AREAS = ("hooks", "scripts", "tools")
HOSTNAME_PROIBIDO = re.compile(r"https?://localhost[:/]")


def fontes_vivas():
    for area in AREAS:
        for caminho in sorted((RAIZ / area).rglob("*")):
            if caminho.suffix not in (".py", ".sh", ".json"):
                continue
            if "__pycache__" in caminho.parts:
                continue
            yield caminho


def test_default_do_router_e_ipv4_literal():
    """O default que o router usa em producao resolve para um endereco so."""
    import skill_router

    host = urlparse(skill_router.OLLAMA_URL).hostname
    assert host == "127.0.0.1", (
        f"OLLAMA_URL default aponta para {host!r}; um hostname deixa a ordem de "
        "resolucao decidir, e ::1 antes de 127.0.0.1 custa ~2s por embed"
    )


def test_nenhuma_fonte_viva_usa_localhost():
    """Um site esquecido paga o mesmo pedagio sem ninguem perceber."""
    culpados = []
    for caminho in fontes_vivas():
        try:
            texto = caminho.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for n, linha in enumerate(texto.splitlines(), 1):
            if HOSTNAME_PROIBIDO.search(linha):
                culpados.append(f"{caminho.relative_to(RAIZ).as_posix()}:{n}")
    assert not culpados, (
        "endpoint com hostname em codigo vivo (use 127.0.0.1): " + ", ".join(culpados)
    )
