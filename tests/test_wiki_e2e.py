"""Ponta a ponta do ciclo de memória de decisão.

Percorre a cadeia inteira, como ela roda em produção:

    discuss escreve docs/CONTEXT.md
      -> vault_sync espelha para wiki/decisions/{projeto}-context.md
      -> build_wiki_index indexa a página nova
      -> wiki_prior_art acha a decisão na tarefa seguinte
      -> wiki_query cita a página
      -> wiki_index poe a página no index.md e wiki_lint aprova a cobertura

E o gate que fecha o projeto: cada peca já tem teste unitário, mas so este prova que a
decisão escrita numa sessão chega a sessão seguinte. Roda sem Ollama (índice --no-embed,
Camada A), então vale em CI.
"""

import json
import sys
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_wiki_index as bwi
import vault_sync as vs

from tools import wiki_index as wi
from tools import wiki_lint as wl
from tools import wiki_prior_art as pa
from tools import wiki_query as wq


def _ollama_up() -> bool:
    try:
        with urllib.request.urlopen(wq.OLLAMA_URL + "/api/tags", timeout=2) as response:
            return wq.EMBED_MODEL in response.read().decode()
    except Exception:
        return False


needs_ollama = pytest.mark.skipif(not _ollama_up(), reason="Ollama indisponivel")

CONTEXT_MD = """# CONTEXT — indexacao vetorial do corpus

> Gerado pela fase discuss do Harness v3.

## Locked Decisions

- **L-01**: Usar retrieval exato com PPR para o corpus atual.

## Deferred (fora do escopo)

- Indice aproximado HNSW: corpus pequeno demais para o indice aproximado compensar.

## Discretion (Claude decide)

- Formato de serializacao do indice.
"""

SEMENTE = (
    "---\ntype: concept\ncreated: 2026-01-01\nupdated: 2026-01-01\n"
    "status: active\ntags: [seed]\n---\n\n# Semente\n\n## Contexto\n"
    + "conteudo de apoio para o vault nao ficar com uma unica pagina. " * 3
)


def montar_projeto(tmp_path: Path) -> Path:
    """Projeto como o discuss o deixa: docs/CONTEXT.md escrito."""
    projeto = tmp_path / "indexacao-vetorial"
    docs = projeto / "docs"
    docs.mkdir(parents=True)
    (docs / "CONTEXT.md").write_text(CONTEXT_MD, encoding="utf-8")
    return projeto


def montar_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "AI-Brain"
    semente = vault / "wiki" / "concepts" / "semente.md"
    semente.parent.mkdir(parents=True)
    semente.write_text(SEMENTE, encoding="utf-8")
    return vault


def test_decisao_de_uma_sessao_chega_na_seguinte(tmp_path: Path) -> None:
    projeto = montar_projeto(tmp_path)
    vault = montar_vault(tmp_path)
    indice = tmp_path / "wiki-index"

    # --- sessão 1: o pipeline termina e o sync colhe o CONTEXT ------------
    contagens = vs.sync(vault, tmp_path / "harness", projeto)

    pagina = vault / "wiki" / "decisions" / "indexacao-vetorial-context.md"
    assert contagens["decisions"] == 1
    assert pagina.is_file(), "vault_sync nao colheu docs/CONTEXT.md"
    conteudo = pagina.read_text(encoding="utf-8")
    assert conteudo.startswith("---\ntype: decision\n"), "chegou sem frontmatter"
    assert "HNSW" in conteudo, "o tier Deferred (a recusa) se perdeu no caminho"

    # --- indexação (sem Ollama: Camada A) ---------------------------------
    total = bwi.build(str(vault), str(indice), no_embed=True)
    assert total > 0
    assert bwi.check_stale(str(vault), str(indice)) is False

    # --- sessão 2: tarefa nova propoe justamente o que foi recusado -------
    dados = pa.collect("vamos adotar HNSW para acelerar o retrieval", index_dir=indice)
    achados = [h["id"] for h in dados["literal"]]

    assert "decisions/indexacao-vetorial-context" in achados, (
        "prior-art nao encontrou a recusa registrada na sessao anterior"
    )
    bloco = pa.render(dados)
    assert "HNSW" in bloco
    assert "[[decisions/indexacao-vetorial-context]]" in bloco

    # --- consulta direta pela Camada A (sem embeddings) -------------------
    resultado = wq.query("indexacao-vetorial-context", index_dir=indice, top_k=3)
    citadas = [h["wikilink"] for h in resultado["hits"]]
    assert "[[decisions/indexacao-vetorial-context]]" in citadas
    assert resultado["hits"][0]["layer"] == "A"

    # --- a página nova entra no index e o lint aprova a cobertura ---------
    (vault / "wiki" / "index.md").write_text(wi.build_index(vault), encoding="utf-8")
    lint = wl.analyze_wiki(vault)

    assert lint["summary"]["pages_missing_from_index"] == []
    assert lint["summary"]["index_phantom_entries"] == []
    assert lint["summary"]["missing_frontmatter"] == []

    # --- o digest do SessionStart anuncia a decisão nova -------------------
    digest = wi.build_digest(vault)
    assert "[[decisions/indexacao-vetorial-context]]" in digest


@needs_ollama
def test_consulta_semantica_acha_a_decisao_sem_alias(tmp_path: Path) -> None:
    """Pergunta parafraseada so chega na página pela Camada B — exige embeddings.

    Separado do e2e principal de proposito: com índice --no-embed a Camada A cobre
    apenas título e slug, e mascarar isso escondería justamente o que os embeddings
    entregam.
    """
    projeto = montar_projeto(tmp_path)
    vault = montar_vault(tmp_path)
    indice = tmp_path / "wiki-index"

    vs.sync(vault, tmp_path / "harness", projeto)
    bwi.build(str(vault), str(indice))

    resultado = wq.query("qual indice usamos para o corpus vetorial", index_dir=indice, top_k=3)

    citadas = [h["wikilink"] for h in resultado["hits"]]
    assert "[[decisions/indexacao-vetorial-context]]" in citadas
    assert any(h["layer"] == "B" for h in resultado["hits"])


def test_segunda_passagem_do_sync_nao_reescreve(tmp_path: Path) -> None:
    """Idempotência ponta a ponta: rodar o ciclo duas vezes não duplica nem re-copia."""
    projeto = montar_projeto(tmp_path)
    vault = montar_vault(tmp_path)

    primeira = vs.sync(vault, tmp_path / "harness", projeto)
    pagina = vault / "wiki" / "decisions" / "indexacao-vetorial-context.md"
    carimbo = pagina.stat().st_mtime
    segunda = vs.sync(vault, tmp_path / "harness", projeto)

    assert primeira["decisions"] == 1
    assert segunda["decisions"] == 0
    assert pagina.stat().st_mtime == carimbo
    assert len(list((vault / "wiki" / "decisions").glob("*.md"))) == 1


def test_log_do_vault_registra_a_colheita(tmp_path: Path) -> None:
    """O log e append-only e e a trilha de auditoria da operação."""
    projeto = montar_projeto(tmp_path)
    vault = montar_vault(tmp_path)

    vs.sync(vault, tmp_path / "harness", projeto)

    log = (vault / "wiki" / "log.md").read_text(encoding="utf-8")
    assert "decisions:1" in log


def test_ciclo_sobrevive_a_projeto_sem_context(tmp_path: Path) -> None:
    """Projeto que nunca rodou discuss não deve criar decisions/ vazio."""
    projeto = tmp_path / "sem-context"
    (projeto / "docs" / "specs").mkdir(parents=True)
    (projeto / "docs" / "specs" / "x-spec.md").write_text("# X\n\nREQ-001.", encoding="utf-8")
    vault = montar_vault(tmp_path)

    contagens = vs.sync(vault, tmp_path / "harness", projeto)

    assert contagens["specs"] == 1
    assert contagens["decisions"] == 0
    assert not (vault / "wiki" / "decisions").exists()


def test_indice_fica_stale_quando_uma_decisao_e_adicionada(tmp_path: Path) -> None:
    """Sem isto, a sessão seguinte consultaria um índice sem a decisão nova."""
    projeto = montar_projeto(tmp_path)
    vault = montar_vault(tmp_path)
    indice = tmp_path / "wiki-index"

    bwi.build(str(vault), str(indice), no_embed=True)
    assert bwi.check_stale(str(vault), str(indice)) is False

    vs.sync(vault, tmp_path / "harness", projeto)

    assert bwi.check_stale(str(vault), str(indice)) is True

    # O índice velho ainda responde (não trava a fase), mas sem a decisão nova.
    dados = pa.collect("adotar HNSW", index_dir=indice)
    assert dados["literal"] == []
    assert json.dumps(dados)  # serializável: o --json não quebra

    bwi.build(str(vault), str(indice), no_embed=True)
    dados = pa.collect("adotar HNSW", index_dir=indice)
    assert [h["id"] for h in dados["literal"]] == ["decisions/indexacao-vetorial-context"]
