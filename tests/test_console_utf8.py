"""Saida de terminal que nao morre no code page do host.

O defeito (2026-08-24): `python tools/wiki_query.py "..."` num console Windows
levantava `UnicodeEncodeError: 'charmap' codec can't encode character '\\u2192'`.
O mesmo em `wiki_index.py` e `wiki_moc.py`. Nenhum dos tres tem seta no
codigo-fonte — a seta vem do CONTEUDO do vault, que as tres ferramentas ecoam.

Por isso a correcao nao pode ser por string: qualquer pagina pode ganhar um
caractere fora do cp1252 amanha, e a ferramenta que a le quebra. O ponto certo e
o stream de saida, uma vez, na entrada do processo.

A falha era total, nao parcial: o traceback substitui a resposta inteira. Uma
consulta que achou a pagina certa devolvia stack trace.
"""

from __future__ import annotations

import ast
import io
import os
import subprocess
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "tools"))

from tools.console import usar_utf8

# U+2192 e U+2190 nao existem no cp1252 — sao os dois que quebraram de verdade.
SETAS = "spec → design ← verificacao"

FM = """---
type: concept
created: 2026-01-01
updated: 2026-01-01
status: active
tags: [t]
---

"""


def entrypoints() -> list[Path]:
    """Todo `tools/*.py` que roda como script."""
    return sorted(
        p
        for p in (RAIZ / "tools").glob("*.py")
        if p.name != "__init__.py" and "__main__" in p.read_text(encoding="utf-8")
    )


# --- o helper -------------------------------------------------------------


def test_usar_utf8_troca_o_encoding_do_stdout(monkeypatch) -> None:
    fake = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", fake)
    monkeypatch.setattr(sys, "stderr", fake)

    usar_utf8()

    assert sys.stdout.encoding.lower().replace("-", "") == "utf8"


def test_usar_utf8_nao_levanta_em_stream_sem_reconfigure(monkeypatch) -> None:
    """Stdout capturado por pytest/subprocess nem sempre e um TextIOWrapper."""

    class SemReconfigure:
        encoding = "cp1252"

    monkeypatch.setattr(sys, "stdout", SemReconfigure())
    monkeypatch.setattr(sys, "stderr", SemReconfigure())

    usar_utf8()  # nao levanta


# --- o contrato, varrido -------------------------------------------------


@pytest.mark.parametrize("script", entrypoints(), ids=lambda p: p.name)
def test_todo_entrypoint_de_tools_chama_usar_utf8(script: Path) -> None:
    """Ferramenta nova nao pode nascer sem a chamada.

    Varredura em vez de lista fixa: lista fixa envelhece em silencio, e o proximo
    `tools/*.py` chegaria quebrado sem nenhum teste vermelho.
    """
    arvore = ast.parse(script.read_text(encoding="utf-8"))
    chamadas = {
        no.func.id
        for no in ast.walk(arvore)
        if isinstance(no, ast.Call) and isinstance(no.func, ast.Name)
    }
    assert "usar_utf8" in chamadas, (
        f"{script.name} roda como script e nao chama usar_utf8() — "
        "vai quebrar no primeiro caractere fora do code page do host"
    )


# --- o defeito, ponta a ponta --------------------------------------------


def _vault_com_setas(tmp_path: Path) -> Path:
    for rel, tipo in (("concepts/sdd.md", "concept"), ("projects/frente.md", "project")):
        p = tmp_path / "wiki" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            FM.replace("concept", tipo) + f"# Pagina\n\n{SETAS}\n", encoding="utf-8"
        )
    return tmp_path


@pytest.mark.parametrize("ferramenta", ["wiki_index.py", "wiki_moc.py"])
def test_render_no_stdout_sobrevive_a_console_cp1252(tmp_path: Path, ferramenta: str) -> None:
    """Sem --write as duas imprimem o documento inteiro; e ali que a seta passa."""
    raiz = _vault_com_setas(tmp_path)
    env = {**os.environ, "PYTHONIOENCODING": "cp1252"}

    proc = subprocess.run(
        [sys.executable, str(RAIZ / "tools" / ferramenta), "--root", str(raiz)],
        capture_output=True,
        env=env,
        timeout=120,
    )
    erro = proc.stderr.decode("utf-8", "replace")

    assert "UnicodeEncodeError" not in erro, erro[-600:]
    assert proc.returncode == 0, erro[-600:]
