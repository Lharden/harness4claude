"""Testes do motor do compêndio.

O contrato é o produto. Uma página gerada errada se regenera; um registry que aceita
verbete incoerente contamina tudo que vier depois — e o que o `check` deixa passar vira
dívida silenciosa, que é o modo de falha que este vault já viveu uma vez.
"""

import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from tools.compendium import (
    MIN_OCORRENCIAS,
    build,
    find_candidates,
    known_tokens,
    load_registry,
    render_map,
    validate_registry,
    verify_code_refs,
)

BASE = """
schema_version = 1
title = "Compendio de teste"
updated = "2026-08-12"

[[categories]]
id = "recuperacao"
label = "Recuperacao"
order = 1

[[kinds]]
id = "tecnica"
label = "Tecnica"

[[terms]]
id = "chunking"
label = "Chunking"
category = "recuperacao"
kind = "tecnica"
status = "confirmado"
definition = "Divide o documento em trechos."
intuicao = "Um vetor por pagina vira centroide."
quando_nao_usar = "Documento curto e monotematico."
reviewed = "2026-08-12"
"""


def escrever_registry(tmp_path: Path, corpo: str = BASE) -> Path:
    caminho = tmp_path / "compendio" / "terms.toml"
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(textwrap.dedent(corpo).strip() + "\n", encoding="utf-8")
    return caminho


def carregar(tmp_path: Path, corpo: str = BASE) -> dict:
    return load_registry(escrever_registry(tmp_path, corpo))


# --- contrato -------------------------------------------------------------


def test_registry_minimo_e_valido(tmp_path: Path) -> None:
    assert validate_registry(carregar(tmp_path)) == []


def test_campo_didatico_e_obrigatorio(tmp_path: Path) -> None:
    """Sem intuicao e sem limite, o verbete vira dicionario comum."""
    sem_intuicao = BASE.replace('intuicao = "Um vetor por pagina vira centroide."\n', "")
    sem_limite = BASE.replace('quando_nao_usar = "Documento curto e monotematico."\n', "")

    assert any("intuicao" in e for e in validate_registry(carregar(tmp_path, sem_intuicao)))
    assert any(
        "quando_nao_usar" in e for e in validate_registry(carregar(tmp_path, sem_limite))
    )


def test_categoria_desconhecida_e_erro(tmp_path: Path) -> None:
    corpo = BASE.replace('category = "recuperacao"\nkind', 'category = "inexistente"\nkind')

    assert any("categoria desconhecida" in e for e in validate_registry(carregar(tmp_path, corpo)))


def test_id_duplicado_e_erro(tmp_path: Path) -> None:
    corpo = BASE + BASE.split("[[terms]]", 1)[1].join(["[[terms]]", ""])

    erros = validate_registry(carregar(tmp_path, corpo))

    assert any("duplicado" in e for e in erros)


def test_relacao_para_alvo_inexistente_e_erro(tmp_path: Path) -> None:
    corpo = BASE + '\nrelacoes = [{ target = "nao-existe", relation = "usa" }]\n'

    assert any("não existe no registry" in e for e in validate_registry(carregar(tmp_path, corpo)))


def test_label_com_caractere_que_quebra_ancora(tmp_path: Path) -> None:
    """`[[pagina#termo]]` deixa de resolver e o verbete fica inalcancavel."""
    corpo = BASE.replace('label = "Chunking"', 'label = "Chunking [beta]"')

    assert any("heading do Obsidian" in e for e in validate_registry(carregar(tmp_path, corpo)))


def test_formato_de_onde_no_codigo(tmp_path: Path) -> None:
    corpo = BASE + '\nonde_no_codigo = "sem_extensao_nem_simbolo"\n'

    assert any("onde_no_codigo" in e for e in validate_registry(carregar(tmp_path, corpo)))


# --- referência de código -------------------------------------------------


def test_referencia_de_codigo_valida_passa(tmp_path: Path) -> None:
    raiz = tmp_path / "repo"
    (raiz / "src").mkdir(parents=True)
    (raiz / "src" / "a.py").write_text("def alvo(): ...\n", encoding="utf-8")
    registry = {"terms": [{"id": "t", "onde_no_codigo": "repo/src/a.py:alvo"}]}

    assert verify_code_refs(registry, {"repo": raiz}) == []


def test_simbolo_renomeado_e_acusado(tmp_path: Path) -> None:
    """O campo so vale se apodrecer ruidosamente — senao aponta para funcao de 2024."""
    raiz = tmp_path / "repo"
    (raiz / "src").mkdir(parents=True)
    (raiz / "src" / "a.py").write_text("def outro_nome(): ...\n", encoding="utf-8")
    registry = {"terms": [{"id": "t", "onde_no_codigo": "repo/src/a.py:alvo"}]}

    problemas = verify_code_refs(registry, {"repo": raiz})

    assert len(problemas) == 1
    assert "não aparece" in problemas[0]


def test_arquivo_removido_e_acusado(tmp_path: Path) -> None:
    raiz = tmp_path / "repo"
    raiz.mkdir()
    registry = {"terms": [{"id": "t", "onde_no_codigo": "repo/sumiu.py:x"}]}

    assert any("inexistente" in p for p in verify_code_refs(registry, {"repo": raiz}))


def test_repo_ausente_na_maquina_nao_e_falso_alarme(tmp_path: Path) -> None:
    """O vault e multi-maquina: repo ausente aqui nao torna o verbete errado."""
    registry = {"terms": [{"id": "t", "onde_no_codigo": "outro-repo/x.py:y"}]}

    assert verify_code_refs(registry, {"repo": tmp_path}) == []


# --- render ---------------------------------------------------------------


def test_build_gera_hub_e_colecao(tmp_path: Path) -> None:
    escrever_registry(tmp_path)

    paginas = build(tmp_path, hoje="2026-08-12")

    assert "00 Compendio.md" in paginas
    assert "01 recuperacao.md" in paginas
    colecao = paginas["01 recuperacao.md"]
    assert "## Chunking" in colecao
    assert "**Intuição.**" in colecao
    assert "**Quando NÃO usar.**" in colecao


def test_build_e_idempotente(tmp_path: Path) -> None:
    escrever_registry(tmp_path)

    assert build(tmp_path, hoje="2026-08-12") == build(tmp_path, hoje="2026-08-12")


def test_mapa_so_existe_quando_ha_relacao(tmp_path: Path) -> None:
    """Mapa sem aresta e uma pagina vazia fingindo ser diagrama."""
    registry = carregar(tmp_path)
    categoria = registry["categories"][0]

    assert render_map(registry, categoria, "2026-08-12") is None


def test_mapa_carrega_o_verbo_da_relacao(tmp_path: Path) -> None:
    """E o que o graph view nativo do Obsidian nao faz: mostrar COMO se ligam."""
    corpo = BASE + textwrap.dedent("""
        relacoes = [{ target = "cosseno", relation = "é medido por" }]

        [[terms]]
        id = "cosseno"
        label = "Cosseno"
        category = "recuperacao"
        kind = "tecnica"
        status = "confirmado"
        definition = "Angulo entre vetores."
        intuicao = "Compara direcao, nao tamanho."
        quando_nao_usar = "Quando a magnitude importa."
        reviewed = "2026-08-12"
    """)
    registry = carregar(tmp_path, corpo)

    mapa = render_map(registry, registry["categories"][0], "2026-08-12")

    assert mapa is not None
    assert "flowchart LR" in mapa
    assert "-->|é medido por|" in mapa


# --- candidatos -----------------------------------------------------------


def test_candidato_precisa_de_repeticao(tmp_path: Path) -> None:
    fonte = tmp_path / "a.md"
    fonte.write_text("Usamos HNSW aqui.", encoding="utf-8")

    assert find_candidates([fonte], set(), set()) == []


def test_sigla_repetida_vira_candidato(tmp_path: Path) -> None:
    fonte = tmp_path / "a.md"
    fonte.write_text("HNSW. " * MIN_OCORRENCIAS, encoding="utf-8")

    tokens = [c["token"] for c in find_candidates([fonte], set(), set())]

    assert "HNSW" in tokens


def test_termo_ja_no_registry_nao_reaparece(tmp_path: Path) -> None:
    registry = carregar(tmp_path)
    fonte = tmp_path / "a.md"
    fonte.write_text("Chunking. " * (MIN_OCORRENCIAS + 2), encoding="utf-8")

    tokens = [c["token"] for c in find_candidates([fonte], known_tokens(registry), set())]

    assert "Chunking" not in tokens


def test_ignorado_nao_reaparece(tmp_path: Path) -> None:
    fonte = tmp_path / "a.md"
    fonte.write_text("HNSW. " * (MIN_OCORRENCIAS + 2), encoding="utf-8")

    tokens = [c["token"] for c in find_candidates([fonte], set(), {"hnsw"})]

    assert tokens == []


def test_identificador_e_caminho_nao_sao_candidatos(tmp_path: Path) -> None:
    """A primeira versao devolveu 1594 itens, quase todos assim."""
    fonte = tmp_path / "a.md"
    fonte.write_text(
        ("Rodar tmp_path e os.path.join com SKILL.md e state.json. " * (MIN_OCORRENCIAS + 1)),
        encoding="utf-8",
    )

    tokens = [c["token"] for c in find_candidates([fonte], set(), set())]

    assert not {"tmp_path", "SKILL.md", "state.json"} & set(tokens)


def test_maiuscula_de_inicio_de_frase_nao_conta(tmp_path: Path) -> None:
    """"Quando", "Sem", "Rodar" so estao maiusculas porque abrem a sentenca."""
    fonte = tmp_path / "a.md"
    fonte.write_text("Quando isso ocorre. " * (MIN_OCORRENCIAS + 2), encoding="utf-8")

    tokens = [c["token"] for c in find_candidates([fonte], set(), set())]

    assert "Quando" not in tokens


def test_nome_proprio_no_meio_da_frase_conta(tmp_path: Path) -> None:
    fonte = tmp_path / "a.md"
    fonte.write_text("O modelo Ollama roda local. " * (MIN_OCORRENCIAS + 1), encoding="utf-8")

    tokens = [c["token"] for c in find_candidates([fonte], set(), set())]

    assert "Ollama" in tokens


def test_bloco_de_codigo_nao_gera_candidato(tmp_path: Path) -> None:
    fonte = tmp_path / "a.md"
    fonte.write_text("```python\nHNSW = 1\nHNSW = 2\nHNSW = 3\n```\n", encoding="utf-8")

    assert find_candidates([fonte], set(), set()) == []
