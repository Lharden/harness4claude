"""Testes do tools/design_scope.py — a direcao que faltava na rastreabilidade.

O design-doc ancora por feature-slug e o trace corre num sentido so. Da para ir
do requisito ao codigo; nao da para ir do codigo ao requisito. Este modulo casa
caminho contra `applies_to` declarado e responde a pergunta inversa.

O que estes testes fixam, em ordem de importancia:

1. **`*` nao atravessa `/`.** E a unica coisa que separa este resolvedor de um
   `fnmatch` ingenuo. Se `src/*.py` casar com `src/a/b.py`, todo padrao vira
   padrao largo em silencio e o relatorio fica verdadeiro e inutil.
2. **Doc sem `applies_to` e AVISO, nao erro.** Toda spec escrita antes desta
   convencao existir esta nesse estado. Reprovar por isso faria a ferramenta
   nascer vermelha e ninguem a rodaria uma segunda vez.
3. **Arquivo nao governado nao e aviso.** Teste, config e script legitimamente
   nao tem design doc. Vira ruido, nao sinal.
4. **Caminho inexistente casa mesmo assim.** O uso principal e saber qual norma
   governa um arquivo ANTES de cria-lo.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])


@pytest.fixture(scope="module")
def ds():
    spec = importlib.util.spec_from_file_location(
        "design_scope", ROOT / "tools" / "design_scope.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["design_scope"] = mod
    spec.loader.exec_module(mod)
    return mod


def escrever(base: Path, rel: str, texto: str) -> Path:
    p = base / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(texto, encoding="utf-8")
    return p


def doc(*padroes: str, corpo: str = "# Design\n") -> str:
    linhas = ["---", "applies_to:"] + [f"  - {p}" for p in padroes] + ["---", "", corpo]
    return "\n".join(linhas)


# ---------------------------------------------------------------------------
# Glob — o nucleo
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("pattern,alvo,esperado", [
    # `*` fica dentro de um segmento.
    ("src/*.py", "src/a.py", True),
    ("src/*.py", "src/sub/a.py", False),
    # `**` atravessa.
    ("src/**", "src/a.py", True),
    ("src/**", "src/sub/deep/a.py", True),
    ("src/auth/**", "src/authz/a.py", False),
    # `**/` casa com zero ou mais diretorios.
    ("**/*.py", "a.py", True),
    ("**/*.py", "x/y/a.py", True),
    ("**/test_*.py", "tests/unit/test_a.py", True),
    ("**/test_*.py", "tests/unit/a.py", False),
    # Literal exato.
    ("src/middleware/session.py", "src/middleware/session.py", True),
    ("src/middleware/session.py", "src/middleware/session.pyc", False),
    # `?` e um caractere, nao uma barra.
    ("src/a?.py", "src/ab.py", True),
    ("src/a?.py", "src/a/.py", False),
    # Ancoragem: nao casa prefixo solto.
    ("src/**", "other/src/a.py", False),
    # Diretorio como alvo (expandir() acrescenta a barra final).
    ("src/auth/**", "src/auth/", True),
])
def test_glob_segmento_vs_travessia(ds, pattern, alvo, esperado):
    assert bool(ds.glob_para_regex(pattern).match(alvo)) is esperado


def test_ponto_regex_nao_vira_curinga(ds):
    """`.` no glob e literal. Sem escape, `a.py` casaria com `axpy`."""
    assert not ds.glob_para_regex("src/a.py").match("src/axpy")


# ---------------------------------------------------------------------------
# Front matter
# ---------------------------------------------------------------------------


def test_extrai_lista_yaml(ds):
    assert ds.extrair_applies_to(doc("src/**", "docs/x.md")) == ["src/**", "docs/x.md"]


def test_extrai_lista_inline(ds):
    texto = "---\napplies_to: [src/**, docs/x.md]\n---\n\n# D\n"
    assert ds.extrair_applies_to(texto) == ["src/**", "docs/x.md"]


def test_aspas_sao_removidas(ds):
    texto = '---\napplies_to:\n  - "src/**"\n  - \'docs/x.md\'\n---\n'
    assert ds.extrair_applies_to(texto) == ["src/**", "docs/x.md"]


def test_sem_front_matter_devolve_none(ds):
    assert ds.extrair_applies_to("# Design\n\nsem front matter\n") is None


def test_front_matter_sem_applies_to_devolve_none(ds):
    assert ds.extrair_applies_to("---\ntitle: X\nstatus: draft\n---\n\n# D\n") is None


def test_outra_chave_encerra_a_lista(ds):
    """Chave seguinte no mesmo nivel nao pode ser engolida como padrao."""
    texto = "---\napplies_to:\n  - src/**\nstatus: draft\n---\n"
    assert ds.extrair_applies_to(texto) == ["src/**"]


# ---------------------------------------------------------------------------
# Validacao de padrao
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ruim", ["/abs/x.py", "C:/x.py", "../fora/x.py", "src\\win.py", ""])
def test_padrao_invalido_levanta(ds, ruim):
    with pytest.raises(ValueError):
        ds.validar_padrao("d.md", 0, ruim)


def test_padrao_invalido_vira_erro_nao_excecao(ds, tmp_path):
    escrever(tmp_path, "docs/specs/a-design.md", doc("/abs/x.py"))
    res = ds.command_design_scope(tmp_path, ["src/a.py"])
    assert res["ready"] is False
    assert any("relativo" in e for e in res["errors"])


# ---------------------------------------------------------------------------
# Casamento ponta a ponta
# ---------------------------------------------------------------------------


def test_caminho_encontra_seu_doc(ds, tmp_path):
    escrever(tmp_path, "docs/specs/auth-design.md", doc("src/auth/**"))
    escrever(tmp_path, "docs/specs/billing-design.md", doc("src/billing/**"))
    res = ds.command_design_scope(tmp_path, ["src/auth/token.py"])
    assert res["ready"] is True
    assert [m["doc"] for m in res["detalhe"]["matches"]] == ["docs/specs/auth-design.md"]


def test_dois_docs_podem_governar_o_mesmo_caminho(ds, tmp_path):
    """Sobreposicao e esperada onde as preocupacoes se cruzam."""
    escrever(tmp_path, "docs/specs/auth-design.md", doc("src/auth/**"))
    escrever(tmp_path, "docs/specs/seguranca-design.md", doc("src/**/*.py"))
    res = ds.command_design_scope(tmp_path, ["src/auth/token.py"])
    assert len(res["detalhe"]["matches"]) == 2


def test_caminho_inexistente_casa_mesmo_assim(ds, tmp_path):
    """O uso principal e saber a norma ANTES de criar o arquivo."""
    escrever(tmp_path, "docs/specs/auth-design.md", doc("src/auth/**"))
    res = ds.command_design_scope(tmp_path, ["src/auth/ainda-nao-existe.py"])
    assert len(res["detalhe"]["matches"]) == 1


def test_explain_mostra_qual_padrao_casou(ds, tmp_path):
    escrever(tmp_path, "docs/specs/auth-design.md", doc("src/auth/**", "src/x.py"))
    res = ds.command_design_scope(tmp_path, ["src/auth/token.py"])
    casos = res["detalhe"]["matches"][0]["matches"]
    assert casos == [{"alvo": "src/auth/token.py", "pattern": "src/auth/**"}]


def test_alvo_fora_da_raiz_e_erro(ds, tmp_path):
    escrever(tmp_path, "docs/specs/a-design.md", doc("src/**"))
    res = ds.command_design_scope(tmp_path, ["../fora.py"])
    assert res["ready"] is False
    assert any("fora da raiz" in e for e in res["errors"])


# ---------------------------------------------------------------------------
# Severidade — onde esta ferramenta pode se estragar
# ---------------------------------------------------------------------------


def test_doc_sem_applies_to_e_aviso_nao_erro(ds, tmp_path):
    """Toda spec anterior a esta convencao esta nesse estado."""
    escrever(tmp_path, "docs/specs/velha-design.md", "# Design\n\nsem front matter\n")
    res = ds.command_design_scope(tmp_path, ["src/a.py"])
    assert res["ready"] is True
    assert res["errors"] == []
    assert any("applies_to" in w for w in res["warnings"])
    assert res["detalhe"]["sem_applies_to"] == ["docs/specs/velha-design.md"]


def test_strict_promove_o_aviso_a_erro(ds, tmp_path):
    escrever(tmp_path, "docs/specs/velha-design.md", "# Design\n")
    res = ds.command_design_scope(tmp_path, ["src/a.py"], estrito=True)
    assert res["ready"] is False


def test_arquivo_sem_doc_nao_gera_aviso(ds, tmp_path):
    """Teste e config legitimamente nao tem design doc. Viraria ruido."""
    escrever(tmp_path, "docs/specs/auth-design.md", doc("src/auth/**"))
    res = ds.command_design_scope(tmp_path, ["pyproject.toml"])
    assert res["ready"] is True
    assert res["warnings"] == []
    assert res["detalhe"]["sem_doc"] == ["pyproject.toml"]


def test_sem_diretorio_de_specs_degrada_em_silencio(ds, tmp_path):
    """Repo sem docs/specs nao e defeito — e repo que ainda nao usa o mecanismo."""
    res = ds.command_design_scope(tmp_path, ["src/a.py"])
    assert res["ready"] is True
    assert res["errors"] == [] and res["warnings"] == []
    assert res["resumo"]["docs_governantes"] == 0


# ---------------------------------------------------------------------------
# --all e --changed
# ---------------------------------------------------------------------------


def test_all_lista_todo_doc_que_declara_escopo(ds, tmp_path):
    escrever(tmp_path, "docs/specs/a-design.md", doc("src/a/**"))
    escrever(tmp_path, "docs/specs/b-design.md", doc("src/b/**"))
    escrever(tmp_path, "docs/specs/c-design.md", "# sem front matter\n")
    res = ds.command_design_scope(tmp_path, [], todos=True)
    assert res["resumo"]["docs_governantes"] == 2
    assert res["resumo"]["sem_applies_to"] == 1


def test_changed_le_o_diff_do_git(ds, tmp_path):
    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, capture_output=True, check=True)

    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    escrever(tmp_path, "docs/specs/auth-design.md", doc("src/auth/**"))
    git("add", "-A")
    git("commit", "-qm", "base")

    escrever(tmp_path, "src/auth/novo.py", "x = 1\n")  # nao rastreado
    res = ds.command_design_scope(tmp_path, [], changed=True)
    assert "src/auth/novo.py" in res["detalhe"]["matches"][0]["matches"][0]["alvo"]


def test_changed_sem_git_nao_levanta(ds, tmp_path):
    escrever(tmp_path, "docs/specs/a-design.md", doc("src/**"))
    res = ds.command_design_scope(tmp_path, [], changed=True)
    assert res["ready"] is True
    assert res["resumo"]["alvos"] == 0


# ---------------------------------------------------------------------------
# Contrato de saida
# ---------------------------------------------------------------------------


def test_contrato_de_saida(ds, tmp_path):
    escrever(tmp_path, "docs/specs/a-design.md", doc("src/**"))
    res = ds.command_design_scope(tmp_path, ["src/a.py"])
    assert res["comando"] == "design-scope"
    assert set(res) == {"comando", "ready", "errors", "warnings", "resumo", "detalhe"}
    assert isinstance(res["ready"], bool)


def test_render_markdown_nao_levanta(ds, tmp_path):
    escrever(tmp_path, "docs/specs/a-design.md", doc("src/**"))
    res = ds.command_design_scope(tmp_path, ["src/a.py"])
    assert "design-scope" in ds.render(res, explicar=True)
