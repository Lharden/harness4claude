"""Testes do tools/graph_lint.py — a validacao que o knowledge graph nao tinha.

Este repositorio valida quase tudo: wiki_lint a wiki, arsenal check o registry,
compendium check os verbetes, smoke e liveness os hooks. O grafo era a excecao, e
tres coisas dependem dele (graph-context, impact.py, o export para o vault).

O que estes testes fixam e a SEVERIDADE, que e onde este lint pode se estragar:
integridade referencial e ERRO, caracteristica de uso e AVISO. Tratar as duas
como a mesma coisa faria o relatorio viver vermelho por caracteristica, e o
defeito de verdade passaria despercebido no meio.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])


@pytest.fixture(scope="module")
def gl():
    spec = importlib.util.spec_from_file_location("graph_lint", ROOT / "tools" / "graph_lint.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["graph_lint"] = mod
    spec.loader.exec_module(mod)
    return mod


def _g(tmp_path, nos, arestas):
    d = tmp_path / "graphify-out"
    d.mkdir(exist_ok=True)
    (d / "graph.json").write_text(json.dumps({"directed": False, "nodes": nos, "links": arestas}),
                                  encoding="utf-8")
    return d


def _n(i, arquivo="a.py", comunidade=1):
    return {"id": i, "source_file": arquivo, "community": comunidade}


def _a(x, y, conf="EXTRACTED"):
    return {"source": x, "target": y, "relation": "calls", "confidence": conf}


class TestSeveridade:
    def test_aresta_para_no_inexistente_e_ERRO(self, gl, tmp_path):
        """Defeito do artefato: alguma coisa foi escrita errada ou se perdeu."""
        res = gl.command_graph_lint(_g(tmp_path, [_n("a")], [_a("a", "fantasma")]))
        assert res["ready"] is False
        assert "integridade referencial" in res["errors"][0]

    def test_no_isolado_e_AVISO_nao_erro(self, gl, tmp_path):
        """O grafo esta integro; o que esta ruim e o que da para fazer com ele."""
        res = gl.command_graph_lint(_g(tmp_path, [_n("a"), _n("b")], [_a("a", "a")]))
        assert res["ready"] is True
        assert any("isolados" in w for w in res["warnings"])

    def test_comunidade_gorda_e_AVISO(self, gl, tmp_path):
        """Dizer que um quarto dos nos pertence ao mesmo agrupamento e o mesmo
        que nao agrupar."""
        nos = [_n(f"n{i}", comunidade=1) for i in range(10)]
        ar = [_a(f"n{i}", f"n{i+1}") for i in range(9)]
        res = gl.command_graph_lint(_g(tmp_path, nos, ar))
        assert res["ready"] is True
        assert any("nao explica nada" in w.replace("ã", "a").replace("ç", "c")
                   for w in res["warnings"])

    def test_grafo_saudavel_passa_limpo(self, gl, tmp_path):
        nos = [_n(f"n{i}", comunidade=i % 5) for i in range(20)]
        ar = [_a(f"n{i}", f"n{(i+1) % 20}") for i in range(20)]
        res = gl.command_graph_lint(_g(tmp_path, nos, ar))
        assert res["ready"] is True and res["errors"] == []


class TestSinais:
    def test_conta_fracao_inferida(self, gl, tmp_path):
        """Quem le o impact.py precisa saber se olha fato ou palpite."""
        nos = [_n("a"), _n("b"), _n("c")]
        ar = [_a("a", "b", "INFERRED"), _a("b", "c")]
        res = gl.command_graph_lint(_g(tmp_path, nos, ar))
        assert res["resumo"]["fracao_inferida"] == 0.5
        assert any("palpite" in w for w in res["warnings"])

    def test_hub_e_informacao_nao_defeito(self, gl, tmp_path):
        nos = [_n("hub")] + [_n(f"n{i}") for i in range(gl.GRAU_DE_HUB + 5)]
        ar = [_a("hub", f"n{i}") for i in range(gl.GRAU_DE_HUB + 5)]
        res = gl.command_graph_lint(_g(tmp_path, nos, ar))
        assert res["ready"] is True
        assert res["detalhe"]["hubs"][0]["no"] == "hub"

    def test_no_sem_source_file_avisa(self, gl, tmp_path):
        res = gl.command_graph_lint(_g(tmp_path, [_n("a"), _n("b", arquivo="")], [_a("a", "b")]))
        assert any("source_file" in w for w in res["warnings"])


class TestDegradacao:
    def test_grafo_ausente_diz_o_que_fazer(self, gl, tmp_path):
        res = gl.command_graph_lint(tmp_path / "nao-existe")
        assert res["ready"] is False and "graphify update" in res["errors"][0]

    def test_grafo_ilegivel_nao_levanta(self, gl, tmp_path):
        d = tmp_path / "graphify-out"
        d.mkdir()
        (d / "graph.json").write_text("{ quebrado", encoding="utf-8")
        res = gl.command_graph_lint(d)
        assert res["ready"] is False and "ilegível" in res["errors"][0]
