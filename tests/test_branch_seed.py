"""Testes para scripts/branch_seed.py — semente e launcher do Branch Keeper.

Um ramo so vale se nascer sabendo de onde veio. A semente e o prompt inicial
que conecta a conversa nova a conversa pai; o launcher e o `.ps1` que abre a
janela. A semente e escrita pelo modelo e gravada como veio: o modulo nao a
monta nem confere as secoes (o `render_seed` que fazia isso nunca teve chamador
e foi apagado em 2026-10-07, decisao 7 de
master-harness/docs/decisoes-capacidades-orfas.md). Estes testes travam a
categoria de falha previsivel que sobra:

**Quoting aninhado.** A cadeia e `wt -> pwsh -> claude -> prompt multilinha`,
   e o caminho real da maquina tem `Program Files` no meio. Uma aspa simples no
   nome do ramo, um apostrofo no tema, um espaco no path do projeto: cada um
   quebra a janela de um jeito diferente e so na hora de abrir. Por isso o
   launcher e um arquivo, nunca uma string inline — e por isso o escape tem
   teste proprio.

A semente carrega PATHS e DECISOES, nunca conteudo colado de arquivo. Ramificar
para escapar do desperdicio de contexto e reinjetar o contexto inteiro na
semente seria trocar o problema de lugar.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
SEED_PATH = ROOT / "scripts" / "branch_seed.py"


@pytest.fixture(scope="module")
def seed():
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("branch_seed", SEED_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["branch_seed"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def branch():
    return {
        "slug": "sensor-de-deriva",
        "name": "Sensor de Deriva",
        "topic": "medir escorregamento da conversa contra a ancora",
        "session_id": "11111111-2222-3333-4444-555555555555",
        "status": "pending",
    }


class TestEscapePowerShell:
    def test_aspa_simples_e_duplicada(self, seed):
        assert seed.ps_quote("d'agua") == "'d''agua'"

    def test_path_com_espaco_sobrevive(self, seed):
        assert seed.ps_quote(r"C:\Program Files\x") == r"'C:\Program Files\x'"

    def test_quebra_de_linha_nao_escapa_do_literal(self, seed):
        out = seed.ps_quote("a\nb")
        assert out.startswith("'") and out.endswith("'")


class TestLauncher:
    def test_launcher_usa_literalpath_e_uuid(self, seed, branch, tmp_path):
        ps1 = seed.render_launcher(
            branch=branch, cwd=str(tmp_path / "meu projeto"), seed_path=str(tmp_path / "s.md")
        )
        assert "-LiteralPath" in ps1
        assert branch["session_id"] in ps1
        assert "--session-id" in ps1

    def test_nome_com_apostrofo_nao_quebra_o_script(self, seed, tmp_path):
        b = {
            "slug": "ramo",
            "name": "Ramo d'Agua",
            "topic": "x",
            "session_id": "11111111-2222-3333-4444-555555555555",
        }
        ps1 = seed.render_launcher(branch=b, cwd=str(tmp_path), seed_path=str(tmp_path / "s.md"))
        assert "'Ramo d''Agua'" in ps1

    def test_escreve_semente_e_launcher_no_bucket_do_projeto(self, seed, branch, tmp_path):
        paths = seed.write_branch_files(
            cwd=str(tmp_path),
            branch=branch,
            seed_text="# Sensor de Deriva\n\n## Origem\n- Conversa pai: Branch Keeper\n",
        )
        assert Path(paths["seed_path"]).read_text(encoding="utf-8").startswith("#")
        assert Path(paths["launcher_path"]).suffix == ".ps1"
        assert Path(paths["seed_path"]).parent == Path(paths["launcher_path"]).parent


class TestComandoDeAbertura:
    def test_comando_abre_janela_nova_no_diretorio_certo(self, seed, branch, tmp_path):
        proj = tmp_path / "meu projeto"
        argv = seed.launch_command(
            branch=branch, cwd=str(proj), launcher_path=str(tmp_path / "l.ps1")
        )
        assert argv[0].lower().endswith("wt.exe")
        assert "-w" in argv and "-1" in argv
        assert str(proj) in argv  # um argumento inteiro, espaco incluso
        assert argv[-1].endswith("l.ps1")
        assert "-File" in argv

    def test_host_none_nao_abre_janela(self, seed, branch, tmp_path, monkeypatch):
        monkeypatch.setenv("HARNESS_BRANCH_HOST", "none")
        assert seed.launch_command(
            branch=branch, cwd=str(tmp_path), launcher_path=str(tmp_path / "l.ps1")
        ) == []


class TestLauncherNaoHerdaMarcadorDeFilho:
    """O ramo e sessao de primeira classe, nao subprocesso do pai.

    A janela do ramo nasce da arvore de processos da sessao mae, entao herda
    `CLAUDE_CODE_CHILD_SESSION` — e com esse marcador o CLI desliga a gravacao
    do transcript. Medido em 2026-09-04, no primeiro ramo real aberto por esta
    skill: a janela subiu com "Transcript saving is off".

    O dano e exatamente o que ramificar existe para evitar: sem transcript, o
    ramo nao entra no `sessions-index`, `session_query` nao o acha, e a mae nao
    tem como consultar o filho. O ramo viraria a sessao orfa que o plano
    inteiro nomeia como o problema.
    """

    def test_launcher_limpa_o_marcador_e_forca_persistencia(self, seed, branch, tmp_path):
        ps1 = seed.render_launcher(
            branch=branch, cwd=str(tmp_path), seed_path=str(tmp_path / "s.md")
        )
        assert "CLAUDE_CODE_CHILD_SESSION" in ps1, "o marcador herdado precisa ser tratado"
        assert "CLAUDE_CODE_FORCE_SESSION_PERSISTENCE" in ps1

    def test_tratamento_vem_antes_de_chamar_o_cli(self, seed, branch, tmp_path):
        """Limpar depois de subir o CLI nao adianta: a decisao e na inicializacao."""
        ps1 = seed.render_launcher(
            branch=branch, cwd=str(tmp_path), seed_path=str(tmp_path / "s.md")
        )
        assert ps1.index("CLAUDE_CODE_FORCE_SESSION_PERSISTENCE") < ps1.index("claude --session-id")
