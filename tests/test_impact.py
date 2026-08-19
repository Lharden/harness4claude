"""Testes do tools/impact.py — raio de impacto de mudança não commitada.

Metade destes casos testa o que a ferramenta se RECUSA a dizer, e é de propósito.
Uma análise de impacto que devolve lista plausível sempre é pior que nenhuma:
quem lê passa a confiar, e o dia em que ela erra por omissão não tem sintoma.

Os três silêncios perigosos, cada um com teste:

  arquivo fora do grafo   -> "não sei", nunca "sem impacto"
  grafo não direcionado   -> "vizinhança", nunca "depende de"
  hub atingido            -> "a busca parou", nunca a lista do repo inteiro
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
def imp():
    spec = importlib.util.spec_from_file_location("impact", ROOT / "tools" / "impact.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["impact"] = mod
    spec.loader.exec_module(mod)
    return mod


def _grafo(tmp_path, nos, arestas, directed=False, manifest=None):
    d = tmp_path / "graphify-out"
    d.mkdir(exist_ok=True)
    (d / "graph.json").write_text(json.dumps({
        "directed": directed, "multigraph": False,
        "nodes": nos, "links": arestas,
    }), encoding="utf-8")
    (d / "manifest.json").write_text(json.dumps(manifest or {}), encoding="utf-8")
    return d


def _no(ident, arquivo):
    return {"id": ident, "label": ident, "source_file": arquivo, "file_type": "code"}


def _aresta(a, b, relation="calls", confidence="EXTRACTED"):
    return {"source": a, "target": b, "relation": relation, "confidence": confidence}


class TestSilenciosPerigosos:
    def test_arquivo_fora_do_grafo_e_desconhecido_nao_zero(self, imp, tmp_path):
        """O pior resultado possivel: devolver "nenhum impacto" para arquivo que
        a ferramenta nao conhece. E silencioso e tem a forma de boa noticia."""
        g = _grafo(tmp_path, [_no("a", "a.py")], [])
        res = imp.command_impact(g, None, ["novo.py"], 2, tmp_path)
        assert res["detalhe"]["fora_do_grafo"] == ["novo.py"]
        assert res["resumo"]["fora_do_grafo"] == 1
        assert any("DESCONHECIDO" in w for w in res["warnings"])
        # A mensagem precisa negar explicitamente a leitura errada, nao so
        # informar: "desconhecido" sozinho ainda e lido como "nada demais".
        assert any("'sem impacto'" in w and "sei" in w for w in res["warnings"])

    def test_grafo_nao_direcionado_diz_vizinhanca_nao_dependencia(self, imp, tmp_path):
        """Sem direcao nao existe "chamador", existe "conectado". Trocar um pelo
        outro e inventar causalidade a partir de adjacencia."""
        g = _grafo(tmp_path, [_no("a", "a.py"), _no("b", "b.py")],
                   [_aresta("a", "b")], directed=False)
        res = imp.command_impact(g, None, ["a.py"], 2, tmp_path)
        assert res["resumo"]["direcionado"] is False
        assert any("vizinhan" in w and "depend" in w for w in res["warnings"])

    def test_hub_para_a_busca_em_vez_de_devolver_tudo(self, imp, tmp_path):
        """Passar por um hub a dois saltos devolve o repositorio inteiro:
        verdadeiro, e inutil."""
        nos = [_no("semente", "s.py"), _no("hub", "hub.py")]
        arestas = [_aresta("semente", "hub")]
        for i in range(imp.GRAU_DE_HUB + 10):          # infla o grau do hub
            nos.append(_no(f"n{i}", f"n{i}.py"))
            arestas.append(_aresta("hub", f"n{i}"))
        g = _grafo(tmp_path, nos, arestas)
        res = imp.command_impact(g, None, ["s.py"], 3, tmp_path)
        assert res["detalhe"]["hubs_atingidos"], "hub nao foi reportado"
        assert res["resumo"]["arquivos_afetados"] < 10, \
            "atravessou o hub e devolveu o repo inteiro"
        assert any("hub" in w for w in res["warnings"])


class TestTravessia:
    def test_alcanca_por_calls_e_marca_saltos(self, imp, tmp_path):
        g = _grafo(tmp_path, [_no("a", "a.py"), _no("b", "b.py"), _no("c", "c.py")],
                   [_aresta("a", "b"), _aresta("b", "c")])
        res = imp.command_impact(g, None, ["a.py"], 2, tmp_path)
        por = {x["arquivo"]: x["saltos"] for x in res["detalhe"]["afetados"]}
        assert por == {"b.py": 1, "c.py": 2}

    def test_contains_nao_e_dependencia(self, imp, tmp_path):
        """`contains` e estrutural — arquivo contem simbolo. Num grafo nao
        direcionado, atravessa-la diz "tudo no mesmo arquivo e afetado", que e
        trivialmente certo e nao informa."""
        g = _grafo(tmp_path, [_no("a", "a.py"), _no("b", "b.py")],
                   [_aresta("a", "b", relation="contains")])
        assert imp.command_impact(g, None, ["a.py"], 2, tmp_path)["detalhe"]["afetados"] == []

    def test_confianca_inferred_contamina_o_caminho(self, imp, tmp_path):
        """Caminho que passa por uma aresta INFERRED nao pode ser reportado como
        certo, mesmo que o ultimo salto seja EXTRACTED."""
        g = _grafo(tmp_path, [_no("a", "a.py"), _no("b", "b.py"), _no("c", "c.py")],
                   [_aresta("a", "b", confidence="INFERRED"), _aresta("b", "c")])
        conf = {x["arquivo"]: x["confianca"] for x in
                imp.command_impact(g, None, ["a.py"], 2, tmp_path)["detalhe"]["afetados"]}
        assert conf == {"b.py": "INFERRED", "c.py": "INFERRED"}

    def test_profundidade_truncada_e_reportada(self, imp, tmp_path):
        g = _grafo(tmp_path, [_no("a", "a.py"), _no("b", "b.py"), _no("c", "c.py")],
                   [_aresta("a", "b"), _aresta("b", "c")])
        res = imp.command_impact(g, None, ["a.py"], 1, tmp_path)
        assert res["detalhe"]["truncou_na_profundidade"] is True
        assert any("truncada" in w for w in res["warnings"])

    def test_arquivo_alterado_nao_aparece_como_afetado_de_si_mesmo(self, imp, tmp_path):
        g = _grafo(tmp_path, [_no("a1", "a.py"), _no("a2", "a.py")], [_aresta("a1", "a2")])
        assert imp.command_impact(g, None, ["a.py"], 2, tmp_path)["detalhe"]["afetados"] == []

    def test_testes_afetados_saem_separados(self, imp, tmp_path):
        g = _grafo(tmp_path, [_no("a", "src/a.py"), _no("t", "tests/test_a.py")],
                   [_aresta("a", "t")])
        res = imp.command_impact(g, None, ["src/a.py"], 2, tmp_path)
        assert [t["arquivo"] for t in res["detalhe"]["testes_afetados"]] == ["tests/test_a.py"]


class TestDegradacao:
    def test_sem_grafo_diz_o_que_fazer(self, imp, tmp_path):
        res = imp.command_impact(tmp_path / "nao-existe", None, ["a.py"], 2, tmp_path)
        assert res["ready"] is False
        assert any("graphify update" in e for e in res["errors"])

    def test_grafo_ilegivel_nao_levanta(self, imp, tmp_path):
        d = tmp_path / "graphify-out"
        d.mkdir()
        (d / "graph.json").write_text("{ nao e json", encoding="utf-8")
        res = imp.command_impact(d, None, ["a.py"], 2, tmp_path)
        assert res["ready"] is False and "ilegível" in res["errors"][0]

    def test_sem_alteracao_e_resposta_valida(self, imp, tmp_path):
        g = _grafo(tmp_path, [_no("a", "a.py")], [])
        res = imp.command_impact(g, None, [], 2, tmp_path)
        assert res["ready"] is True and res["resumo"]["alterados"] == 0

    def test_grafo_velho_avisa(self, imp, tmp_path):
        """Arquivo que mudou ANTES do diff e depois do grafo: a vizinhanca
        descreve codigo que ja nao existe, e nada mais avisaria."""
        antigo = tmp_path / "velho.py"
        antigo.write_text("x", encoding="utf-8")
        g = _grafo(tmp_path, [_no("a", "a.py")], [],
                   manifest={"velho.py": {"mtime": 1.0}, "a.py": {"mtime": 9e9}})
        res = imp.command_impact(g, None, ["a.py"], 2, tmp_path)
        assert any("DEPOIS da constru" in w for w in res["warnings"])

    def test_grafo_atual_declara_status(self, imp, tmp_path):
        """Status de frescor e campo do resumo, nao aviso no meio da lista."""
        g = _grafo(tmp_path, [_no("a", "a.py")], [], manifest={"a.py": {"mtime": 9e9}})
        res = imp.command_impact(g, None, ["a.py"], 2, tmp_path)
        assert res["resumo"]["grafo_status"] == "atual"
        assert not any("CONFERIDA" in w for w in res["warnings"])

    def test_grafo_velho_marca_vizinhanca_como_nao_conferida(self, imp, tmp_path):
        """Grafo desatualizado responde, mas nao passa por medicao.

        Nuance absorvida do `isCurrentGraph` do open-science (2026-08-19): o
        estado do grafo e resposta de primeira classe, ao lado da resposta.
        Antes disso o mesmo fato existia so como o 5o aviso de uma lista.
        """
        antigo_arq = tmp_path / "velho.py"
        antigo_arq.write_text("x", encoding="utf-8")
        g = _grafo(tmp_path, [_no("a", "a.py"), _no("b", "b.py")], [_aresta("a", "b")],
                   manifest={"velho.py": {"mtime": 1.0}, "a.py": {"mtime": 9e9}})
        res = imp.command_impact(g, None, ["a.py"], 2, tmp_path)
        assert res["resumo"]["grafo_status"] == "desatualizado"
        assert "CONFERIDA" in res["warnings"][0]
        assert res["ready"] is True   # frescor e ressalva, nao defeito

    def test_sem_manifest_o_frescor_e_desconhecido_nao_atual(self, imp, tmp_path):
        """Ausencia de prova nao vira prova de frescor."""
        g = _grafo(tmp_path, [_no("a", "a.py")], [])
        (g / "manifest.json").unlink()
        res = imp.command_impact(g, None, ["a.py"], 2, tmp_path)
        assert res["resumo"]["grafo_status"] == "frescor-desconhecido"
        assert "CONFERIDA" in res["warnings"][0]

    def test_impacto_nao_e_defeito(self, imp, tmp_path):
        """`ready` segue True mesmo com muitos afetados: e informacao para
        revisar, nao falha. Se reprovasse, viveria vermelho e ninguem leria."""
        g = _grafo(tmp_path, [_no("a", "a.py"), _no("b", "b.py")], [_aresta("a", "b")])
        assert imp.command_impact(g, None, ["a.py"], 2, tmp_path)["ready"] is True
