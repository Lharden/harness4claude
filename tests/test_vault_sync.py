"""Testes de regressão do vault_sync — bug VAULT_PATH (2026-06-12).

Após a migração MCP, VAULT_PATH passou a apontar para a RAIZ do vault
Obsidian (consumida por NODE_EXTRA_CA_CERTS/MCP). O sync usava essa env
como override e duplicou a árvore wiki/ na raiz. O override correto é
AI_BRAIN_PATH.
"""

import json
import os
import re
import subprocess
import sys
import time
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


# --- recusa por hash: a pagina do vault so e reescrita se ninguem a editou ----
#
# Ate 2026-09-24 o espelho decidia por mtime: fonte mais nova que a pagina =
# copiar. Dois defeitos reproduzidos com a funcao de producao: a edicao humana
# feita no Obsidian sumia na primeira mudanca da fonte, sem backup; e um erro de
# escrita num arquivo derrubava o lote inteiro, com a saida descartada pelo hook.


def _no_futuro(caminho: Path, segundos: int = 1000) -> None:
    """Empurra o mtime para frente sem mexer no conteudo."""
    t = time.time() + segundos
    os.utime(caminho, (t, t))


def _no_passado(caminho: Path, segundos: int = 1000) -> None:
    t = time.time() - segundos
    os.utime(caminho, (t, t))


def _fonte(cwd: Path) -> Path:
    return cwd / "docs" / "specs" / "feature-spec.md"


def _pagina(vault: Path) -> Path:
    return vault / "wiki" / "specs" / "feature-spec.md"


def test_edicao_humana_sobrevive_a_mudanca_da_fonte(tmp_path: Path) -> None:
    cwd = _projeto(tmp_path, spec="# Feature\n\nversao 1\n")
    vault, harness = tmp_path / "ai-brain", tmp_path / "harness"
    vs.sync(vault, harness, cwd)
    pagina = _pagina(vault)
    pagina.write_text(pagina.read_text(encoding="utf-8") + "\nNOTA DO AUTOR\n", encoding="utf-8")
    _fonte(cwd).write_text("# Feature\n\nversao 2\n", encoding="utf-8")
    _no_futuro(_fonte(cwd))
    eventos: list[str] = []

    counts = vs.sync(vault, harness, cwd, eventos=eventos)

    assert "NOTA DO AUTOR" in pagina.read_text(encoding="utf-8")
    assert "versao 2" not in pagina.read_text(encoding="utf-8")
    assert counts["specs"] == 0
    assert any("recusada" in e and "feature-spec.md" in e for e in eventos), eventos


def test_pagina_intocada_e_atualizada_quando_a_fonte_muda(tmp_path: Path) -> None:
    cwd = _projeto(tmp_path, spec="# Feature\n\nversao 1\n")
    vault, harness = tmp_path / "ai-brain", tmp_path / "harness"
    vs.sync(vault, harness, cwd)
    _fonte(cwd).write_text("# Feature\n\nversao 2\n", encoding="utf-8")
    _no_futuro(_fonte(cwd))
    eventos: list[str] = []

    counts = vs.sync(vault, harness, cwd, eventos=eventos)

    assert counts["specs"] == 1
    assert "versao 2" in _pagina(vault).read_text(encoding="utf-8")
    assert eventos == []


def test_mtime_novo_sem_mudanca_de_conteudo_nao_reescreve(tmp_path: Path) -> None:
    """Checkout, Obsidian Sync e `touch` mexem no mtime sem mexer no texto."""
    cwd = _projeto(tmp_path, spec="# Feature\n\ncorpo\n")
    vault, harness = tmp_path / "ai-brain", tmp_path / "harness"
    vs.sync(vault, harness, cwd)
    _no_passado(_pagina(vault))
    antes = _pagina(vault).stat().st_mtime
    _no_futuro(_fonte(cwd))

    counts = vs.sync(vault, harness, cwd)

    assert counts["specs"] == 0
    assert _pagina(vault).stat().st_mtime == antes


def test_pagina_com_mtime_mexido_mas_intocada_ainda_atualiza(tmp_path: Path) -> None:
    """O inverso: mtime da pagina mais novo que a fonte nao prova edicao humana."""
    cwd = _projeto(tmp_path, spec="# Feature\n\nversao 1\n")
    vault, harness = tmp_path / "ai-brain", tmp_path / "harness"
    vs.sync(vault, harness, cwd)
    _no_futuro(_pagina(vault), 5000)
    _fonte(cwd).write_text("# Feature\n\nversao 2\n", encoding="utf-8")

    counts = vs.sync(vault, harness, cwd)

    assert counts["specs"] == 1
    assert "versao 2" in _pagina(vault).read_text(encoding="utf-8")


def test_primeiro_contato_adota_pagina_igual_a_menos_de_datas_slug_e_fim_de_linha(
    tmp_path: Path,
) -> None:
    """Vault que existia antes do manifesto: a pagina que o sync antigo escreveu e adotada.

    Datas carimbadas mudam a cada dia, o slug muda entre worktree e checkout, e o
    Windows grava CRLF: nada disso e edicao humana.
    """
    texto = "# Feature\n\ncorpo\n"
    cwd = _projeto(tmp_path, spec=texto)
    vault, harness = tmp_path / "ai-brain", tmp_path / "harness"
    antiga = vs.stamp_frontmatter(
        texto, "spec", source="feature-spec.md", today="2020-01-01", project="slug-de-worktree"
    )
    _pagina(vault).parent.mkdir(parents=True)
    _pagina(vault).write_bytes(antiga.replace("\n", "\r\n").encode("utf-8"))
    _no_passado(_pagina(vault))
    bytes_antes = _pagina(vault).read_bytes()
    eventos: list[str] = []

    primeira = vs.sync(vault, harness, cwd, eventos=eventos)

    assert primeira["specs"] == 0, "adotar nao e reescrever"
    assert _pagina(vault).read_bytes() == bytes_antes
    assert eventos == []

    _fonte(cwd).write_text("# Feature\n\nversao 2\n", encoding="utf-8")
    segunda = vs.sync(vault, harness, cwd)

    assert segunda["specs"] == 1, "a adocao registrou a pagina: a mudanca da fonte chega"
    assert "versao 2" in _pagina(vault).read_text(encoding="utf-8")


def test_primeiro_contato_recusa_pagina_diferente(tmp_path: Path) -> None:
    cwd = _projeto(tmp_path, spec="# Feature\n\ncorpo\n")
    vault, harness = tmp_path / "ai-brain", tmp_path / "harness"
    _pagina(vault).parent.mkdir(parents=True)
    _pagina(vault).write_text("# Feature\n\nTEXTO ESCRITO NO OBSIDIAN\n", encoding="utf-8")
    _no_passado(_pagina(vault))
    eventos: list[str] = []

    counts = vs.sync(vault, harness, cwd, eventos=eventos)

    assert counts["specs"] == 0
    assert "TEXTO ESCRITO NO OBSIDIAN" in _pagina(vault).read_text(encoding="utf-8")
    assert any("recusada" in e and "feature-spec.md" in e for e in eventos), eventos


def test_primeiro_contato_com_pagina_mais_nova_que_a_fonte_nao_escreve_nem_avisa(
    tmp_path: Path,
) -> None:
    """O espelho por mtime tambem nao tocaria esta pagina, entao nada se perde.

    Medido na copia do AI-Brain real (2026-09-24): 5 paginas de `raw/inbox` eram de
    outro repositorio com `.remember/today-*.md` de mesmo nome, mais novas que o arquivo
    do SLB. Avisar ali repetiria o mesmo aviso a cada PreCompact, sem acao possivel.
    """
    cwd = _projeto(tmp_path, spec="# Feature\n\ncorpo\n")
    vault, harness = tmp_path / "ai-brain", tmp_path / "harness"
    _no_passado(_fonte(cwd))
    _pagina(vault).parent.mkdir(parents=True)
    _pagina(vault).write_text("# Feature\n\nconteudo de outra fonte\n", encoding="utf-8")
    eventos: list[str] = []

    counts = vs.sync(vault, harness, cwd, eventos=eventos)

    assert counts["specs"] == 0
    assert "conteudo de outra fonte" in _pagina(vault).read_text(encoding="utf-8")
    assert eventos == []


def test_erro_num_arquivo_nao_derruba_o_lote(tmp_path: Path) -> None:
    cwd = _projeto(tmp_path, context="# CONTEXT\n\ncorpo\n")
    specs = cwd / "docs" / "specs"
    specs.mkdir()
    (specs / "a-spec.md").write_text("# A\n", encoding="utf-8")
    (specs / "b-spec.md").write_text("# B\n", encoding="utf-8")
    vault, harness = tmp_path / "ai-brain", tmp_path / "harness"
    (vault / "wiki" / "specs" / "a-spec.md").mkdir(parents=True)  # destino ocupado: a escrita falha
    eventos: list[str] = []

    counts = vs.sync(vault, harness, cwd, eventos=eventos)

    assert (vault / "wiki" / "specs" / "b-spec.md").is_file()
    assert (vault / "wiki" / "decisions" / "projeto-x-context.md").is_file()
    assert counts["specs"] == 1
    assert any("falha" in e and "a-spec.md" in e for e in eventos), eventos


def test_manifesto_fica_fora_do_vault(tmp_path: Path) -> None:
    cwd = _projeto(tmp_path, spec="# Feature\n\ncorpo\n")
    vault, harness = tmp_path / "ai-brain", tmp_path / "harness"
    manifesto = tmp_path / "raiz-do-harness" / vs.MANIFESTO

    vs.sync(vault, harness, cwd, manifesto=manifesto)

    assert manifesto.is_file()
    assert not (harness / vs.MANIFESTO).exists()
    assert [p.name for p in vault.rglob("*") if p.is_file() and p.suffix != ".md"] == []


def test_manifesto_padrao_fica_no_harness_dir(tmp_path: Path) -> None:
    cwd = _projeto(tmp_path, spec="# Feature\n\ncorpo\n")
    harness = tmp_path / "harness"

    vs.sync(tmp_path / "ai-brain", harness, cwd)

    assert (harness / vs.MANIFESTO).is_file()


def test_manifesto_corrompido_vale_como_vazio_e_avisa(tmp_path: Path) -> None:
    cwd = _projeto(tmp_path, spec="# Feature\n\ncorpo\n")
    vault, harness = tmp_path / "ai-brain", tmp_path / "harness"
    vs.sync(vault, harness, cwd)
    (harness / vs.MANIFESTO).write_text("{isto nao e json", encoding="utf-8")
    eventos: list[str] = []

    counts = vs.sync(vault, harness, cwd, eventos=eventos)

    assert counts["specs"] == 0, "a pagina igual a fonte e adotada, nao reescrita"
    assert any("manifesto" in e for e in eventos), eventos
    assert json.loads((harness / vs.MANIFESTO).read_text(encoding="utf-8"))["paginas"]

    _fonte(cwd).write_text("# Feature\n\nversao 2\n", encoding="utf-8")
    assert vs.sync(vault, harness, cwd)["specs"] == 1


def test_duas_fontes_para_a_mesma_pagina_nao_se_alternam(tmp_path: Path) -> None:
    """Dois projetos com `docs/specs/feature-spec.md` caem na mesma pagina.

    Medido na primeira execucao sobre a copia do AI-Brain real (2026-09-24): sem esta
    regra, a segunda passagem SEM mudanca nenhuma reescrevia 30 paginas — cada projeto
    sobrescrevia a do outro a cada PreCompact. Vence a fonte mais nova, como no espelho
    por mtime; a mais velha nao retoma a pagina.
    """
    vault, harness = tmp_path / "ai-brain", tmp_path / "harness"
    a = _projeto(tmp_path / "a", spec="# Feature\n\ndo projeto A\n")
    b = _projeto(tmp_path / "b", spec="# Feature\n\ndo projeto B\n")
    _no_passado(_fonte(b))

    vs.sync(vault, harness, a)
    assert vs.sync(vault, harness, b)["specs"] == 0, "a fonte mais velha nao assume a pagina"
    assert vs.sync(vault, harness, a)["specs"] == 0
    assert "do projeto A" in _pagina(vault).read_text(encoding="utf-8")

    _fonte(b).write_text("# Feature\n\ndo projeto B, editado\n", encoding="utf-8")
    _no_futuro(_fonte(b))
    assert vs.sync(vault, harness, b)["specs"] == 1, "a fonte mais nova assume"
    assert vs.sync(vault, harness, a)["specs"] == 0
    assert "do projeto B, editado" in _pagina(vault).read_text(encoding="utf-8")


def test_cli_imprime_recusa_no_stderr_e_grava_manifesto_onde_mandado(tmp_path: Path) -> None:
    """E o stderr que o hook do PreCompact redireciona para o log."""
    cwd = _projeto(tmp_path, spec="# Feature\n\ncorpo\n")
    vault = tmp_path / "ai-brain"
    _pagina(vault).parent.mkdir(parents=True)
    _pagina(vault).write_text("# Feature\n\nTEXTO ESCRITO NO OBSIDIAN\n", encoding="utf-8")
    _no_passado(_pagina(vault))
    manifesto = tmp_path / "raiz" / vs.MANIFESTO

    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--quiet", "--vault", str(vault),
         "--harness-dir", str(tmp_path / "harness"), "--manifesto", str(manifesto)],
        capture_output=True, text=True, encoding="utf-8", env={**os.environ, "PYTHONUTF8": "1"},
        cwd=cwd, timeout=60,
    )

    assert proc.returncode == 0
    assert "recusada" in proc.stderr and "feature-spec.md" in proc.stderr, proc.stderr
    assert "TEXTO ESCRITO NO OBSIDIAN" in _pagina(vault).read_text(encoding="utf-8")
    assert not manifesto.exists() or "feature-spec.md" not in manifesto.read_text(encoding="utf-8")
