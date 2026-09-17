"""Regressao de banco: a regua nova reprocessa o corpus real e nao perde codigo.

`tests/data/files-corpus-2026-09-17.json` e a tabela `files` inteira da sessao
de coordenacao — 468 linhas, seis tasks, 2 611 subidas de `code_revision`, lida
em modo somente-leitura. E corpus real, rotulavel, e por isso vale mais que
qualquer caso inventado: a regua de R3 e um filtro, e o risco de um filtro e
sempre o mesmo, jogar fora o que importava.

A afirmacao que este arquivo trava: **todo caminho que e codigo sob teste
sobrevive**. Se um unico cair, a regua rejeitou uma escrita real e o conserto
virou cegueira.

[superado: 467 linhas] — o plano contou 467 e o autor 466, mais cedo no mesmo
dia. O banco cresceu entre as leituras; a fixture congela 468 para que a
proxima leitura compare com um numero e nao com uma lembranca.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])


def _load(nome: str, relativo: str):
    spec = importlib.util.spec_from_file_location(nome, ROOT / relativo)
    modulo = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(modulo)
    return modulo


hook = _load("corpus_hook", "hooks/harness-transactional.py")
politica = _load("corpus_politica", "scripts/post_tool_policy.py")

CORPUS = json.loads(
    (ROOT / "tests" / "data" / "files-corpus-2026-09-17.json").read_text(encoding="utf-8")
)
CAMINHOS: list[str] = CORPUS["caminhos"]
RAIZ: str = CORPUS["raiz_do_projeto"]

#: O que muda o resultado da suite daquele projeto. `.md` fica de FORA de
#: proposito: o mapa o classificou como classe C, "dentro da raiz e nao e
#: codigo". Num repositorio onde um teste le um `.md` versionado, ele seria
#: codigo — e por isso a classe C existe separada em vez de virar D por comodo.
EXTENSOES_DE_CODIGO = {".py", ".sh", ".ps1", ".toml", ".cfg", ".ini", ".yaml", ".yml", ".sql"}


def _classe(caminho: str) -> str:
    if hook.nao_pode_ser_caminho(caminho):
        return "A"
    if not politica.inside_root(caminho, RAIZ):
        return "B"
    if Path(caminho).suffix.lower() in EXTENSOES_DE_CODIGO:
        return "D"
    return "C"


CLASSES = {caminho: _classe(caminho) for caminho in CAMINHOS}
CODIGO_SOB_TESTE = [c for c, classe in CLASSES.items() if classe == "D"]


def test_o_corpus_e_o_que_diz_ser():
    """Censo antes do veredito: fixture truncada faria tudo abaixo passar vazio."""
    assert len(CAMINHOS) == 468
    assert CORPUS["subidas_de_code_revision"] == 2611
    assert RAIZ.endswith("science-harness")


def test_todo_codigo_sob_teste_sobrevive_a_regua():
    """A metade que mais vale: nenhuma escrita real pode sumir da atribuicao."""
    assert CODIGO_SOB_TESTE, "sem classe D o teste passaria vazio"
    perdidos = [c for c in CODIGO_SOB_TESTE if hook.nao_pode_ser_caminho(c)]
    assert perdidos == [], (
        f"{len(perdidos)} caminho(s) de codigo sob teste rejeitados pela regua — "
        f"cada um e uma escrita real que some da atribuicao:\n  "
        + "\n  ".join(f"{c!r} -> {hook.nao_pode_ser_caminho(c)}" for c in perdidos[:10])
    )


def test_todo_caminho_valido_fora_da_raiz_tambem_sobrevive_a_regua():
    """Classe B e escrita REAL, so que fora do repositorio.

    Quem a exclui e R2, pela raiz — nao a regua de forma. Se a regua a pegasse,
    estaria rejeitando pelo motivo errado, e o motivo errado e o que nao se
    consegue auditar depois.
    """
    fora = [c for c, classe in CLASSES.items() if classe == "B"]
    assert fora, "sem classe B o teste passaria vazio"
    assert [c for c in fora if hook.nao_pode_ser_caminho(c)] == []


def test_a_regua_pega_o_que_nao_pode_ser_arquivo():
    """A outra metade: a regua nao pode ser uma funcao que devolve None sempre."""
    impossiveis = [c for c, classe in CLASSES.items() if classe == "A"]
    assert len(impossiveis) >= 100, (
        f"so {len(impossiveis)} de {len(CAMINHOS)} recusados; o mapa mediu 233 "
        "pela regua da §2 e esta e mais estreita de proposito — mas uma queda "
        "para perto de zero significa que a regua parou de reger"
    )
    motivos = {hook.nao_pode_ser_caminho(c) for c in impossiveis}
    assert "variavel-nao-expandida" in motivos
    assert None not in motivos


def test_a_regua_nao_rejeita_caminho_comum():
    """Controle sintetico, fora do corpus: o que TEM de passar, passa."""
    validos = [
        "scripts/x.py",
        "docs/nota final.md",
        "../saida.txt",
        "./out.log",
        "C:" + chr(92) + "Users" + chr(92) + "me" + chr(92) + "repo" + chr(92) + "x.py",
        "Makefile",
        "MSGEOF",          # nome valido: quem o elimina e a etapa 1, nao a regua
        "0.75",            # o erro de instrumento do mapa: isto E um nome valido
        "F4.0",
        chr(92) + chr(92) + "servidor" + chr(92) + "share" + chr(92) + "a.txt",
    ]
    assert {c: hook.nao_pode_ser_caminho(c) for c in validos if hook.nao_pode_ser_caminho(c)} == {}


def test_a_regua_pega_os_casos_nomeados_no_mapa():
    impossiveis = {
        "$TEMP" + chr(92) + "claude" + chr(92) + "orfaos.py": "variavel-nao-expandida",
        "$LOCK" + chr(92) + "dono": "variavel-nao-expandida",
        "$P" + chr(92) + "$f": "variavel-nao-expandida",
        "0.75:": "dois-pontos-fora-de-letra-de-drive",
        "b:": "so-letra-de-drive",
        "a" + chr(10) + "b": "caractere-ilegal",
        "x?y": "caractere-ilegal",
        "docs" + chr(92): "termina-em-separador",
        "...": "sem-alfanumerico",
        "--": "sem-alfanumerico",
    }
    assert {c: hook.nao_pode_ser_caminho(c) for c in impossiveis} == impossiveis
