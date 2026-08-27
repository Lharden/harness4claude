"""Testes de regressão do vault_sync — bug VAULT_PATH (2026-06-12).

Após a migração MCP, VAULT_PATH passou a apontar para a RAIZ do vault
Obsidian (consumida por NODE_EXTRA_CA_CERTS/MCP). O sync usava essa env
como override e duplicou a árvore wiki/ na raiz. O override correto é
AI_BRAIN_PATH.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import vault_sync as vs

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "vault_sync.py"
TRACE = "trace-regressao.md"


def _run_sync(env_extra: dict[str, str], tmp_path: Path) -> None:
    """Executa o vault_sync em subprocess com env controlado e 1 trace semeado."""
    harness_dir = tmp_path / "harness"
    (harness_dir / "traces").mkdir(parents=True, exist_ok=True)
    (harness_dir / "traces" / TRACE).write_text("# trace de teste\n", encoding="utf-8")
    env = {**os.environ, **env_extra}
    subprocess.run(
        [sys.executable, str(SCRIPT), "--quiet", "--harness-dir", str(harness_dir)],
        check=True,
        env=env,
        cwd=tmp_path,
        timeout=60,
    )


def test_ai_brain_path_define_destino(tmp_path: Path) -> None:
    """AI_BRAIN_PATH é o override válido: o trace é espelhado dentro dele."""
    alvo = tmp_path / "ai-brain"
    alvo.mkdir()
    _run_sync({"AI_BRAIN_PATH": str(alvo)}, tmp_path)
    assert (alvo / "wiki" / "sessions" / TRACE).exists()


def test_vault_path_nao_e_consumido(tmp_path: Path) -> None:
    """VAULT_PATH (raiz do vault, semântica MCP) NUNCA pode virar destino do sync."""
    raiz_vault = tmp_path / "vault-root"
    raiz_vault.mkdir()
    alvo = tmp_path / "ai-brain"
    alvo.mkdir()
    _run_sync({"VAULT_PATH": str(raiz_vault), "AI_BRAIN_PATH": str(alvo)}, tmp_path)
    assert not (raiz_vault / "wiki").exists(), "regressão: sync escreveu na raiz do vault"
    assert (alvo / "wiki" / "sessions" / TRACE).exists()


# --- carimbo de frontmatter -----------------------------------------------


def test_stamp_frontmatter_prefixa_quando_ausente() -> None:
    saida = vs.stamp_frontmatter("# Spec\n\ncorpo", "spec", source="a.md", today="2026-08-11")

    assert saida.startswith("---\ntype: spec\n")
    assert saida.endswith("# Spec\n\ncorpo")
    assert "source: a.md" in saida


def test_stamp_frontmatter_nao_reescreve_campo_que_ja_existe() -> None:
    """Valor declarado na origem manda — o carimbo completa, nunca sobrescreve."""
    original = "---\ntype: design\nupdated: 2026-01-01\n---\n\ncorpo"

    saida = vs.stamp_frontmatter(original, "spec", source="a.md", today="2026-08-11")

    assert re.search(r"^type: design$", saida, re.M), "type da origem preservado"
    assert re.search(r"^updated: 2026-01-01$", saida, re.M), "updated da origem preservado"
    assert "type: spec" not in saida
    assert "updated: 2026-08-11" not in saida


def test_stamp_frontmatter_completa_bloco_incompleto() -> None:
    """Frontmatter parcial nao e frontmatter valido.

    Regressao real (2026-08-19): as specs ganharam `applies_to` na origem, o bloco passou
    a comecar com `---`, e o carimbo desistiu inteiro. Quatro paginas chegaram ao vault
    sem `type` nem `updated` — invisiveis para o Dataview e erro de lint. Presenca de
    bloco nao e presenca de contrato.
    """
    original = "---\napplies_to:\n  - src/**\n---\n\ncorpo"

    saida = vs.stamp_frontmatter(original, "spec", source="a.md", today="2026-08-11")

    assert "applies_to:" in saida, "campo da origem preservado"
    assert "  - src/**" in saida, "valor multilinha preservado"
    assert re.search(r"^type: spec$", saida, re.M)
    assert re.search(r"^updated: 2026-08-11$", saida, re.M)
    assert saida.rstrip().endswith("corpo")
    assert saida.count("---") == 2, "um unico bloco, nao dois empilhados"


def _projeto(tmp_path: Path, *, spec: str | None = None, context: str | None = None) -> Path:
    """Monta um projeto com docs/specs e/ou docs/CONTEXT.md."""
    docs = tmp_path / "projeto-x" / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    if spec is not None:
        (docs / "specs").mkdir(exist_ok=True)
        (docs / "specs" / "feature-spec.md").write_text(spec, encoding="utf-8")
    if context is not None:
        (docs / "CONTEXT.md").write_text(context, encoding="utf-8")
    return docs.parent


def test_spec_crua_chega_ao_vault_com_frontmatter(tmp_path: Path) -> None:
    cwd = _projeto(tmp_path, spec="# Feature\n\nREQ-001: algo.")
    vault = tmp_path / "ai-brain"

    counts = vs.sync(vault, tmp_path / "harness", cwd)

    espelhada = vault / "wiki" / "specs" / "feature-spec.md"
    assert counts["specs"] == 1
    assert espelhada.read_text(encoding="utf-8").startswith("---\ntype: spec\n")
    assert "REQ-001: algo." in espelhada.read_text(encoding="utf-8")


def test_context_vira_pagina_de_decisao_nomeada_pelo_projeto(tmp_path: Path) -> None:
    cwd = _projeto(tmp_path, context="# CONTEXT\n\n## Locked Decisions\n- L-01: usar SQLite.")
    vault = tmp_path / "ai-brain"

    counts = vs.sync(vault, tmp_path / "harness", cwd)

    decisao = vault / "wiki" / "decisions" / "projeto-x-context.md"
    assert counts["decisions"] == 1
    assert decisao.is_file()
    conteudo = decisao.read_text(encoding="utf-8")
    assert conteudo.startswith("---\ntype: decision\n")
    assert "L-01: usar SQLite." in conteudo


def test_sync_permanece_idempotente(tmp_path: Path) -> None:
    cwd = _projeto(tmp_path, spec="# Feature\n\ncorpo", context="# CONTEXT\n\ncorpo")
    vault = tmp_path / "ai-brain"

    primeira = vs.sync(vault, tmp_path / "harness", cwd)
    segunda = vs.sync(vault, tmp_path / "harness", cwd)

    assert primeira == {"sessions": 0, "specs": 1, "decisions": 1, "inbox": 0, "branches": 0}
    assert segunda == {"sessions": 0, "specs": 0, "decisions": 0, "inbox": 0, "branches": 0}


def test_projeto_sem_context_nao_cria_decisions(tmp_path: Path) -> None:
    cwd = _projeto(tmp_path, spec="# Feature\n\ncorpo")
    vault = tmp_path / "ai-brain"

    counts = vs.sync(vault, tmp_path / "harness", cwd)

    assert counts["decisions"] == 0
    assert not (vault / "wiki" / "decisions").exists()


def test_semente_de_ramo_vai_para_o_vault(tmp_path: Path) -> None:
    """A semente e o unico registro de por que um ramo existe.

    Ela nasce no bucket do harness, que e volatil. Sem o espelho no vault, um
    ramo que voce lembra vagamente daqui a dois meses so existiria como um uuid
    perdido no /resume — nao como algo que a busca encontra pelo tema.
    """
    import sys

    sys.path.insert(0, str(Path(os.environ["HARNESS_PLUGIN_ROOT"]) / "scripts"))
    import harness_paths

    cwd = _projeto(tmp_path, spec="# Feature\n\ncorpo")
    harness = tmp_path / "harness"
    destino = harness_paths.state_dir(root=harness, cwd=cwd) / "branches"
    destino.mkdir(parents=True, exist_ok=True)
    (destino / "sensor-de-deriva.seed.md").write_text(
        "# Sensor de Deriva\n\ncorpo da semente", encoding="utf-8"
    )

    counts = vs.sync(tmp_path / "ai-brain", harness, cwd)

    espelho = tmp_path / "ai-brain" / "wiki" / "branches" / "sensor-de-deriva.seed.md"
    assert counts["branches"] == 1
    assert espelho.is_file()
    assert espelho.read_text(encoding="utf-8").startswith("---\ntype: branch\n")
