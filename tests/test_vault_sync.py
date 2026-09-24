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

    counts = vs.sync(vault, tmp_path / "harness", cwd, raiz=tmp_path / "harness")

    espelhada = vault / "wiki" / "specs" / "feature-spec.md"
    assert counts["specs"] == 1
    assert espelhada.read_text(encoding="utf-8").startswith("---\ntype: spec\n")
    assert "REQ-001: algo." in espelhada.read_text(encoding="utf-8")


def test_context_vira_pagina_de_decisao_nomeada_pelo_projeto(tmp_path: Path) -> None:
    cwd = _projeto(tmp_path, context="# CONTEXT\n\n## Locked Decisions\n- L-01: usar SQLite.")
    vault = tmp_path / "ai-brain"

    counts = vs.sync(vault, tmp_path / "harness", cwd, raiz=tmp_path / "harness")

    decisao = vault / "wiki" / "decisions" / "projeto-x-context.md"
    assert counts["decisions"] == 1
    assert decisao.is_file()
    conteudo = decisao.read_text(encoding="utf-8")
    assert conteudo.startswith("---\ntype: decision\n")
    assert "L-01: usar SQLite." in conteudo


def test_sync_permanece_idempotente(tmp_path: Path) -> None:
    cwd = _projeto(tmp_path, spec="# Feature\n\ncorpo", context="# CONTEXT\n\ncorpo")
    vault = tmp_path / "ai-brain"

    primeira = vs.sync(vault, tmp_path / "harness", cwd, raiz=tmp_path / "harness")
    segunda = vs.sync(vault, tmp_path / "harness", cwd, raiz=tmp_path / "harness")

    assert primeira == {"sessions": 0, "specs": 1, "decisions": 1, "inbox": 0, "branches": 0}
    assert segunda == {"sessions": 0, "specs": 0, "decisions": 0, "inbox": 0, "branches": 0}


def test_projeto_sem_context_nao_cria_decisions(tmp_path: Path) -> None:
    cwd = _projeto(tmp_path, spec="# Feature\n\ncorpo")
    vault = tmp_path / "ai-brain"

    counts = vs.sync(vault, tmp_path / "harness", cwd, raiz=tmp_path / "harness")

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
    raiz = tmp_path / "harness"
    # Balde de SESSAO, como o hook passa. Ate 2026-09-24 este teste usava o mesmo
    # diretorio como raiz e como balde, e por isso ficava verde com o hook quebrado.
    balde = harness_paths.state_dir(root=raiz, cwd=cwd, session_id="sessao-de-teste")
    assert balde != harness_paths.state_dir(root=raiz, cwd=cwd)
    destino = harness_paths.state_dir(root=raiz, cwd=cwd) / "branches"
    destino.mkdir(parents=True, exist_ok=True)
    (destino / "sensor-de-deriva.seed.md").write_text(
        "# Sensor de Deriva\n\ncorpo da semente", encoding="utf-8"
    )

    counts = vs.sync(tmp_path / "ai-brain", balde, cwd, raiz=raiz)

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
    vs.sync(vault, harness, cwd, raiz=harness)
    pagina = _pagina(vault)
    pagina.write_text(pagina.read_text(encoding="utf-8") + "\nNOTA DO AUTOR\n", encoding="utf-8")
    _fonte(cwd).write_text("# Feature\n\nversao 2\n", encoding="utf-8")
    _no_futuro(_fonte(cwd))
    eventos: list[str] = []

    counts = vs.sync(vault, harness, cwd, raiz=harness, eventos=eventos)

    assert "NOTA DO AUTOR" in pagina.read_text(encoding="utf-8")
    assert "versao 2" not in pagina.read_text(encoding="utf-8")
    assert counts["specs"] == 0
    assert any("recusada" in e and "feature-spec.md" in e for e in eventos), eventos


def test_pagina_intocada_e_atualizada_quando_a_fonte_muda(tmp_path: Path) -> None:
    cwd = _projeto(tmp_path, spec="# Feature\n\nversao 1\n")
    vault, harness = tmp_path / "ai-brain", tmp_path / "harness"
    vs.sync(vault, harness, cwd, raiz=harness)
    _fonte(cwd).write_text("# Feature\n\nversao 2\n", encoding="utf-8")
    _no_futuro(_fonte(cwd))
    eventos: list[str] = []

    counts = vs.sync(vault, harness, cwd, raiz=harness, eventos=eventos)

    assert counts["specs"] == 1
    assert "versao 2" in _pagina(vault).read_text(encoding="utf-8")
    assert eventos == []


def test_mtime_novo_sem_mudanca_de_conteudo_nao_reescreve(tmp_path: Path) -> None:
    """Checkout, Obsidian Sync e `touch` mexem no mtime sem mexer no texto."""
    cwd = _projeto(tmp_path, spec="# Feature\n\ncorpo\n")
    vault, harness = tmp_path / "ai-brain", tmp_path / "harness"
    vs.sync(vault, harness, cwd, raiz=harness)
    _no_passado(_pagina(vault))
    antes = _pagina(vault).stat().st_mtime
    _no_futuro(_fonte(cwd))

    counts = vs.sync(vault, harness, cwd, raiz=harness)

    assert counts["specs"] == 0
    assert _pagina(vault).stat().st_mtime == antes


def test_pagina_com_mtime_mexido_mas_intocada_ainda_atualiza(tmp_path: Path) -> None:
    """O inverso: mtime da pagina mais novo que a fonte nao prova edicao humana."""
    cwd = _projeto(tmp_path, spec="# Feature\n\nversao 1\n")
    vault, harness = tmp_path / "ai-brain", tmp_path / "harness"
    vs.sync(vault, harness, cwd, raiz=harness)
    _no_futuro(_pagina(vault), 5000)
    _fonte(cwd).write_text("# Feature\n\nversao 2\n", encoding="utf-8")

    counts = vs.sync(vault, harness, cwd, raiz=harness)

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

    primeira = vs.sync(vault, harness, cwd, raiz=harness, eventos=eventos)

    assert primeira["specs"] == 0, "adotar nao e reescrever"
    assert _pagina(vault).read_bytes() == bytes_antes
    assert eventos == []

    _fonte(cwd).write_text("# Feature\n\nversao 2\n", encoding="utf-8")
    segunda = vs.sync(vault, harness, cwd, raiz=harness)

    assert segunda["specs"] == 1, "a adocao registrou a pagina: a mudanca da fonte chega"
    assert "versao 2" in _pagina(vault).read_text(encoding="utf-8")


def test_primeiro_contato_recusa_pagina_diferente(tmp_path: Path) -> None:
    cwd = _projeto(tmp_path, spec="# Feature\n\ncorpo\n")
    vault, harness = tmp_path / "ai-brain", tmp_path / "harness"
    _pagina(vault).parent.mkdir(parents=True)
    _pagina(vault).write_text("# Feature\n\nTEXTO ESCRITO NO OBSIDIAN\n", encoding="utf-8")
    _no_passado(_pagina(vault))
    eventos: list[str] = []

    counts = vs.sync(vault, harness, cwd, raiz=harness, eventos=eventos)

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
    [superado no inbox, 2026-09-24: as notas diarias ganharam o rotulo do repo no nome e
    nao colidem mais; o caso continua valendo para specs de mesmo nome em repos diferentes.]
    """
    cwd = _projeto(tmp_path, spec="# Feature\n\ncorpo\n")
    vault, harness = tmp_path / "ai-brain", tmp_path / "harness"
    _no_passado(_fonte(cwd))
    _pagina(vault).parent.mkdir(parents=True)
    _pagina(vault).write_text("# Feature\n\nconteudo de outra fonte\n", encoding="utf-8")
    eventos: list[str] = []

    counts = vs.sync(vault, harness, cwd, raiz=harness, eventos=eventos)

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

    counts = vs.sync(vault, harness, cwd, raiz=harness, eventos=eventos)

    assert (vault / "wiki" / "specs" / "b-spec.md").is_file()
    assert (vault / "wiki" / "decisions" / "projeto-x-context.md").is_file()
    assert counts["specs"] == 1
    assert any("falha" in e and "a-spec.md" in e for e in eventos), eventos


def test_manifesto_fica_fora_do_vault(tmp_path: Path) -> None:
    cwd = _projeto(tmp_path, spec="# Feature\n\ncorpo\n")
    vault, harness = tmp_path / "ai-brain", tmp_path / "harness"
    manifesto = tmp_path / "raiz-do-harness" / vs.MANIFESTO

    vs.sync(vault, harness, cwd, raiz=harness, manifesto=manifesto)

    assert manifesto.is_file()
    assert not (harness / vs.MANIFESTO).exists()
    assert [p.name for p in vault.rglob("*") if p.is_file() and p.suffix != ".md"] == []


def test_manifesto_padrao_fica_no_harness_dir(tmp_path: Path) -> None:
    cwd = _projeto(tmp_path, spec="# Feature\n\ncorpo\n")
    harness = tmp_path / "harness"

    vs.sync(tmp_path / "ai-brain", harness, cwd, raiz=harness)

    assert (harness / vs.MANIFESTO).is_file()


def test_manifesto_corrompido_vale_como_vazio_e_avisa(tmp_path: Path) -> None:
    cwd = _projeto(tmp_path, spec="# Feature\n\ncorpo\n")
    vault, harness = tmp_path / "ai-brain", tmp_path / "harness"
    vs.sync(vault, harness, cwd, raiz=harness)
    (harness / vs.MANIFESTO).write_text("{isto nao e json", encoding="utf-8")
    eventos: list[str] = []

    counts = vs.sync(vault, harness, cwd, raiz=harness, eventos=eventos)

    assert counts["specs"] == 0, "a pagina igual a fonte e adotada, nao reescrita"
    assert any("manifesto" in e for e in eventos), eventos
    assert json.loads((harness / vs.MANIFESTO).read_text(encoding="utf-8"))["paginas"]

    _fonte(cwd).write_text("# Feature\n\nversao 2\n", encoding="utf-8")
    assert vs.sync(vault, harness, cwd, raiz=harness)["specs"] == 1


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

    vs.sync(vault, harness, a, raiz=harness)
    assert vs.sync(vault, harness, b, raiz=harness)["specs"] == 0, "a fonte mais velha nao assume a pagina"
    assert vs.sync(vault, harness, a, raiz=harness)["specs"] == 0
    assert "do projeto A" in _pagina(vault).read_text(encoding="utf-8")

    _fonte(b).write_text("# Feature\n\ndo projeto B, editado\n", encoding="utf-8")
    _no_futuro(_fonte(b))
    assert vs.sync(vault, harness, b, raiz=harness)["specs"] == 1, "a fonte mais nova assume"
    assert vs.sync(vault, harness, a, raiz=harness)["specs"] == 0
    assert "do projeto B, editado" in _pagina(vault).read_text(encoding="utf-8")


# --- o slug e do repositorio, nao da pasta -------------------------------------
#
# Ate 2026-09-24 `project_slug` usava o nome do diretorio. Sessao num worktree
# (harness4claude/.claude/worktrees/portao-fd-e-pin) nomeava a decisao pelo worktree.
# Medido no AI-Brain real: 11 paginas `*-context.md` com 4 corpos distintos, 8 delas
# com o mesmo CONTEXT.md do harness4claude, e a wiki-query devolvendo o mesmo
# documento ate 8 vezes. O mesmo slug ia no campo `project:` de specs e sementes.


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True,
        env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"},
    )


def _repo_com_worktree(tmp_path: Path) -> tuple[Path, Path]:
    """Repositorio git de verdade com CONTEXT.md e um worktree em pasta de outro nome."""
    principal = tmp_path / "repo-real"
    (principal / "docs" / "specs").mkdir(parents=True)
    (principal / "docs" / "CONTEXT.md").write_text("# CONTEXT\n\ndo principal\n", encoding="utf-8")
    (principal / "docs" / "specs" / "feature-spec.md").write_text("# Feature\n", encoding="utf-8")
    _git(principal, "init", "-q", "-b", "main")
    _git(principal, "add", "-A")
    _git(principal, "commit", "-q", "-m", "base", "--no-verify")
    worktree = tmp_path / "repo-real" / ".claude" / "worktrees" / "ramo-com-outro-nome"
    _git(principal, "worktree", "add", "-q", "-b", "ramo", str(worktree))
    return principal, worktree


def test_worktree_e_checkout_principal_escrevem_a_mesma_pagina_de_decisao(tmp_path: Path) -> None:
    principal, worktree = _repo_com_worktree(tmp_path)
    vault, harness = tmp_path / "ai-brain", tmp_path / "harness"
    decisoes = vault / "wiki" / "decisions"

    vs.sync(vault, harness, principal, raiz=harness)
    (worktree / "docs" / "CONTEXT.md").write_text("# CONTEXT\n\ndo worktree\n", encoding="utf-8")
    _no_futuro(worktree / "docs" / "CONTEXT.md")
    counts = vs.sync(vault, harness, worktree, raiz=harness)

    assert sorted(p.name for p in decisoes.iterdir()) == ["repo-real-context.md"]
    assert counts["decisions"] == 1, "a fonte mais nova, do worktree, assume a pagina do repo"
    pagina = (decisoes / "repo-real-context.md").read_text(encoding="utf-8")
    assert "do worktree" in pagina
    assert re.search(r"^project: repo-real$", pagina, re.M)
    spec = (vault / "wiki" / "specs" / "feature-spec.md").read_text(encoding="utf-8")
    assert re.search(r"^project: repo-real$", spec, re.M)

    # A fonte mais velha nao retoma a pagina: e a regra de colisao do manifesto.
    _no_passado(principal / "docs" / "CONTEXT.md")
    assert vs.sync(vault, harness, principal, raiz=harness)["decisions"] == 0
    assert "do worktree" in (decisoes / "repo-real-context.md").read_text(encoding="utf-8")


def test_nota_do_remember_de_um_worktree_conserva_o_rotulo_da_pasta(tmp_path: Path) -> None:
    """O rotulo do inbox nomeia a PASTA do `.remember`, nao o projeto.

    Colapsa-lo no repositorio poria a nota do worktree e a do principal, de mesmo nome,
    na mesma pagina.
    """
    principal, worktree = _repo_com_worktree(tmp_path)
    (principal / ".remember").mkdir()
    (principal / ".remember" / NOTA).write_text("# do principal\n", encoding="utf-8")
    (worktree / ".remember").mkdir()
    (worktree / ".remember" / NOTA).write_text("# do worktree\n", encoding="utf-8")

    _sync_inbox(tmp_path, worktree)

    assert _inbox(tmp_path) == {
        f"ramo-com-outro-nome--{NOTA}": "# do worktree\n",
        f"repo-real--{NOTA}": "# do principal\n",
    }


def test_is_mirrored_cobre_todo_destino_que_o_sync_escreve(tmp_path: Path) -> None:
    """`is_mirrored` e o que vault_maintenance e wiki_accents consultam antes de editar.

    Os destinos sao derivados RODANDO o sync com uma fonte de cada tipo, para a lista
    nao envelhecer calada quando um destino novo for acrescentado.
    """
    import harness_paths

    harness = tmp_path / "harness"
    (harness / "traces").mkdir(parents=True)
    (harness / "traces" / "sessao.md").write_text("# s\n", encoding="utf-8")
    cwd = _projeto(tmp_path, spec="# x\n", context="# c\n")
    (cwd / ".remember").mkdir()
    (cwd / ".remember" / "today-2026-01-01.md").write_text("# r\n", encoding="utf-8")
    sementes = harness_paths.state_dir(root=harness, cwd=cwd) / "branches"
    sementes.mkdir(parents=True)
    (sementes / "ramo.seed.md").write_text("# semente\n", encoding="utf-8")
    vault = tmp_path / "ai-brain"

    contagens = vs.sync(vault, harness, cwd, raiz=harness)

    assert all(contagens.values()), f"cada destino precisa receber uma pagina: {contagens}"
    escritas = [p.relative_to(vault).as_posix() for p in vault.rglob("*.md")]
    fora = [r for r in escritas if r != "wiki/log.md" and not vs.is_mirrored(r)]
    assert fora == [], f"destino do sync sem cobertura em is_mirrored: {fora}"


def test_is_mirrored_nao_pega_pagina_humana_das_mesmas_pastas() -> None:
    assert not vs.is_mirrored("wiki/decisions/estado-duravel-do-pipeline.md")
    assert not vs.is_mirrored("raw/inbox/Bem-vindo ao Obsidian.md")
    assert not vs.is_mirrored("wiki/log.md")
    assert vs.is_mirrored("wiki/decisions/harness4claude-context.md")
    assert vs.is_mirrored("raw/inbox/slb-mestrado-projeto--today-2026-08-06.done.md")
    # Nome sem rotulo: as paginas legadas, de antes de 2026-09-24, continuam protegidas.
    assert vs.is_mirrored("raw/inbox/today-2026-08-06.done.md")
    assert not vs.is_mirrored("raw/inbox/_processed/slb-mestrado-projeto--today-2026-08-06.done.md")


# --- notas diarias: um nome por repositorio, e nota consumida nao volta --------
#
# Ate 2026-09-24 toda `.remember/today-*.md` ia para `raw/inbox/` com o nome da fonte.
# Medido no AI-Brain real: 22 nomes tinham mais de uma fonte, e 33 versoes de 7 repos
# estavam fora do vault porque a nota mais nova de outro repo ocupava o nome. E uma nota
# tirada do inbox voltava no PreCompact seguinte: 11 nomes estavam em `_processed/` e no
# inbox ao mesmo tempo.

NOTA = "today-2026-08-06.done.md"


def _repo(base: Path, nome: str, notas: dict[str, str]) -> Path:
    """Repositorio falso (`.git` diretorio) com `.remember/` e as notas dadas."""
    raiz = base / nome
    (raiz / ".git").mkdir(parents=True)
    (raiz / ".remember").mkdir()
    for arquivo, texto in notas.items():
        (raiz / ".remember" / arquivo).write_text(texto, encoding="utf-8")
    return raiz


def _sync_inbox(tmp_path: Path, cwd: Path, remember_global: Path | None = None) -> dict[str, int]:
    """Sync hermetico: nunca le o `C:/.remember` real."""
    harness = tmp_path / "harness"
    global_ = remember_global or tmp_path / "sem-remember-global"
    return vs.sync(tmp_path / "ai-brain", harness, cwd, raiz=harness, remember_global=global_)


def _inbox(tmp_path: Path) -> dict[str, str]:
    pasta = tmp_path / "ai-brain" / "raw" / "inbox"
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(pasta.glob("*.md"))}


def test_notas_de_repos_diferentes_com_o_mesmo_nome_nao_colidem(tmp_path: Path) -> None:
    a = _repo(tmp_path, "repo-a", {NOTA: "# do repo A\n"})
    b = _repo(tmp_path, "repo-b", {NOTA: "# do repo B\n"})

    _sync_inbox(tmp_path, a)
    _sync_inbox(tmp_path, b)

    assert _inbox(tmp_path) == {f"repo-a--{NOTA}": "# do repo A\n", f"repo-b--{NOTA}": "# do repo B\n"}


def test_sessao_em_worktree_espelha_as_notas_do_checkout_dono(tmp_path: Path) -> None:
    """O plugin remember escreve no checkout principal; nenhum dos 9 worktrees medidos
    tinha `.remember`. Pelo `cwd` do worktree, a sessao nao espelhava nota nenhuma."""
    principal = _repo(tmp_path, "repo-principal", {NOTA: "# nota\n"})
    gitdir = principal / ".git" / "worktrees" / "wt"
    gitdir.mkdir(parents=True)
    worktree = tmp_path / "pasta-do-worktree"
    worktree.mkdir()
    (worktree / ".git").write_text(f"gitdir: {gitdir}\n", encoding="utf-8")

    _sync_inbox(tmp_path, worktree)

    assert list(_inbox(tmp_path)) == [f"repo-principal--{NOTA}"]


def test_remember_global_tem_rotulo_fixo_e_uma_pagina_so(tmp_path: Path) -> None:
    """Com o rotulo do `cwd`, cada repo copiaria a mesma nota global com o proprio prefixo."""
    global_ = tmp_path / "remember-global"
    global_.mkdir()
    (global_ / NOTA).write_text("# global\n", encoding="utf-8")
    a = _repo(tmp_path, "repo-a", {})
    b = _repo(tmp_path, "repo-b", {})

    assert _sync_inbox(tmp_path, a, global_)["inbox"] == 1
    assert _sync_inbox(tmp_path, b, global_)["inbox"] == 0
    assert list(_inbox(tmp_path)) == [f"{vs.ROTULO_REMEMBER_GLOBAL}--{NOTA}"]


def test_nota_apagada_do_inbox_nao_volta_enquanto_a_fonte_nao_muda(tmp_path: Path) -> None:
    a = _repo(tmp_path, "repo-a", {NOTA: "# v1\n"})
    _sync_inbox(tmp_path, a)
    pagina = tmp_path / "ai-brain" / "raw" / "inbox" / f"repo-a--{NOTA}"
    pagina.unlink()

    assert _sync_inbox(tmp_path, a)["inbox"] == 0
    assert not pagina.exists()

    (a / ".remember" / NOTA).write_text("# v2, conteudo novo\n", encoding="utf-8")
    assert _sync_inbox(tmp_path, a)["inbox"] == 1, "conteudo novo e trabalho novo: volta"
    assert pagina.read_text(encoding="utf-8") == "# v2, conteudo novo\n"


def test_nota_movida_para_processed_nao_volta_mesmo_sem_manifesto(tmp_path: Path) -> None:
    """`_processed/` vive no vault: vale nas duas maquinas e sobrevive a perda do
    manifesto, que fica em `~/.claude/harness`, um por maquina."""
    a = _repo(tmp_path, "repo-a", {NOTA: "# v1\n"})
    _sync_inbox(tmp_path, a)
    inbox = tmp_path / "ai-brain" / "raw" / "inbox"
    (inbox / "_processed").mkdir()
    (inbox / f"repo-a--{NOTA}").rename(inbox / "_processed" / f"repo-a--{NOTA}")
    (tmp_path / "harness" / vs.MANIFESTO).unlink()

    assert _sync_inbox(tmp_path, a)["inbox"] == 0
    assert not (inbox / f"repo-a--{NOTA}").exists()


def test_nota_consumida_com_rotulo_repetido_so_volta_pela_fonte_mais_nova(tmp_path: Path) -> None:
    """Dois repos com a mesma pasta dao o mesmo rotulo e caem no mesmo destino.

    A fonte que nao escreveu a pagina nao a recria so por ter sha diferente do
    registrado: vale a regra de sempre, a mais nova vence.
    """
    a = _repo(tmp_path / "x", "repo", {NOTA: "# do x\n"})
    b = _repo(tmp_path / "y", "repo", {NOTA: "# do y, mais velho\n"})
    _no_passado(b / ".remember" / NOTA)
    _sync_inbox(tmp_path, a)
    pagina = tmp_path / "ai-brain" / "raw" / "inbox" / f"repo--{NOTA}"
    pagina.unlink()

    assert _sync_inbox(tmp_path, b)["inbox"] == 0
    assert not pagina.exists()

    (b / ".remember" / NOTA).write_text("# do y, editado\n", encoding="utf-8")
    _no_futuro(b / ".remember" / NOTA)
    assert _sync_inbox(tmp_path, b)["inbox"] == 1
    assert pagina.read_text(encoding="utf-8") == "# do y, editado\n"


def test_nota_processada_com_nome_antigo_nao_volta_com_nome_novo(tmp_path: Path) -> None:
    """As notas de `_processed/` anteriores a 2026-09-24 tem o nome sem rotulo."""
    a = _repo(tmp_path, "repo-a", {NOTA: "# v1\n"})
    processadas = tmp_path / "ai-brain" / "raw" / "inbox" / "_processed"
    processadas.mkdir(parents=True)
    (processadas / NOTA).write_bytes(b"# v1\r\n")

    assert _sync_inbox(tmp_path, a)["inbox"] == 0
    assert _inbox(tmp_path) == {}


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
