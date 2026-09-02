"""Testes para scripts/branch_seed.py — semente e launcher do Branch Keeper.

Um ramo so vale se nascer sabendo de onde veio. A semente e o prompt inicial
que conecta a conversa nova a conversa pai; o launcher e o `.ps1` que abre a
janela. Estes testes travam as duas categorias de falha previsiveis aqui:

1. **Semente incompleta.** Se faltar origem, motivo, contexto ou primeira acao,
   o ramo abre e trava — voce cai numa conversa limpa demais, sem saber por que
   esta ali. Um teste por secao obrigatoria.

2. **Quoting aninhado.** A cadeia e `wt -> pwsh -> claude -> prompt multilinha`,
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


def _render(seed, branch, **kw):
    kw.setdefault("parent_name", "Branch Keeper")
    kw.setdefault("parent_session", "99999999-8888-7777-6666-555555555555")
    kw.setdefault("project", "harness4claude")
    kw.setdefault("summary", "Separar deriva de ramo usando a mesma ancora.")
    kw.setdefault("why_split", "O tema virou subsistema proprio e ia engolir a conversa pai.")
    kw.setdefault("context_items", ["scripts/branch_sensor.py", "docs/specs/branch-keeper-spec.md"])
    kw.setdefault("first_action", "Ler branch_sensor.py e listar os pisos atuais.")
    return seed.render_seed(branch=branch, **kw)


class TestSemente:
    def test_tem_as_seis_secoes_obrigatorias(self, seed, branch):
        texto = _render(seed, branch)
        for titulo in seed.REQUIRED_SECTIONS:
            assert titulo in texto, f"secao ausente: {titulo}"

    def test_carrega_origem_do_pai(self, seed, branch):
        texto = _render(seed, branch)
        assert "99999999-8888-7777-6666-555555555555" in texto
        assert "Branch Keeper" in texto

    def test_carrega_identidade_do_ramo(self, seed, branch):
        texto = _render(seed, branch)
        assert branch["name"] in texto
        assert branch["session_id"] in texto

    def test_diz_o_que_nao_desenvolver_no_pai(self, seed, branch):
        texto = _render(seed, branch)
        assert "engolir a conversa pai" in texto

    def test_contexto_vem_como_path_nao_como_conteudo(self, seed, branch):
        texto = _render(seed, branch)
        assert "scripts/branch_sensor.py" in texto
        assert "docs/specs/branch-keeper-spec.md" in texto

    def test_ensina_a_reportar_de_volta(self, seed, branch):
        assert f"/branch close {branch['slug']}" in _render(seed, branch)

    def test_falta_de_primeira_acao_e_recusada(self, seed, branch):
        """Ramo sem primeira acao concreta e ramo que nao comeca."""
        with pytest.raises(ValueError):
            _render(seed, branch, first_action="")

    def test_semente_cabe_num_prompt(self, seed, branch):
        texto = _render(seed, branch, context_items=[f"arquivo_{i}.py" for i in range(40)])
        assert len(texto) < seed.MAX_SEED_CHARS


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
            seed_text=_render(seed, branch),
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


class TestSementeSabeVoltar:
    """O ramo nasce com contexto limpo — mas nao pode nascer sem endereco.

    Ate 2026-09-02 a semente dizia de qual sessao o ramo veio e nada mais. Na
    pratica isso deixava duas coisas quebradas:

    - para consultar o pai, o unico caminho documentado era `--resume`, que
      carrega a sessao inteira e substitui a atual: exatamente o custo que
      ramificar existe para nao pagar;
    - as irmas ja estavam no registro desde 2026-08-27 e nada as renderizava,
      entao cada ramo nascia achando-se filho unico e dois irmaos podiam
      refazer o mesmo trabalho sem se ver.
    """

    def test_semente_ensina_a_consultar_o_pai_sem_carregar(self, seed, branch):
        texto = _render(seed, branch)
        assert "session_query.py" in texto
        assert "--session 99999999-8888-7777-6666-555555555555" in texto

    def test_semente_lista_as_irmas(self, seed, branch):
        irmas = [
            {"slug": "calibrar-piso", "status": "open"},
            {"slug": "indice-sessoes", "status": "pending"},
        ]
        texto = _render(seed, branch, siblings=irmas)
        assert "calibrar-piso" in texto and "indice-sessoes" in texto

    def test_o_proprio_ramo_nao_aparece_como_irma(self, seed, branch):
        branch["slug"] = "sensor-de-deriva"
        texto = _render(seed, branch, siblings=[{"slug": "sensor-de-deriva", "status": "open"}])
        assert "Irmas deste pai" not in texto

    def test_sem_irmas_nao_deixa_secao_vazia(self, seed, branch):
        assert "Irmas deste pai" not in _render(seed, branch)

    def test_semente_longa_nao_perde_as_irmas(self, seed, branch):
        """O re-render por excesso de tamanho descartava `siblings` em silencio."""
        texto = _render(
            seed, branch,
            siblings=[{"slug": "calibrar-piso", "status": "open"}],
            context_items=[f"caminho/muito/longo/numero/{i}.py" for i in range(40)],
            summary="x " * 3000,
        )
        assert "calibrar-piso" in texto

    def test_close_explica_que_a_conclusao_volta_sozinha(self, seed, branch):
        texto = _render(seed, branch)
        assert "harness-parked" in texto


class TestSementeNaoRecursionaAteEstourar:
    """Excesso fora do contexto travava a recursao.

    `render_seed` encolhia `context_items` pela metade ate caber. Mas
    `max(1, len // 2)` estabiliza em 1, e cortar contexto nao encolhe texto que
    nao esta no contexto: um `summary` longo fazia a funcao recursionar ate
    estourar a pilha. Abrir ramo com resumo grande derrubava a skill inteira
    com RecursionError, sem dizer por que — e nenhum teste cobria o caminho.

    Bug pre-existente, exposto em 2026-09-02 ao testar que a semente longa nao
    perde as irmas.
    """

    def test_resumo_longo_nao_estoura_a_pilha(self, seed, branch):
        import sys as _sys

        limite = _sys.getrecursionlimit()
        _sys.setrecursionlimit(200)
        try:
            texto = _render(seed, branch, summary="x " * 3000, context_items=["a.py"])
        finally:
            _sys.setrecursionlimit(limite)
        assert texto and len(texto) <= seed.MAX_SEED_CHARS

    def test_semente_truncada_avisa_que_foi(self, seed, branch):
        texto = _render(seed, branch, summary="x " * 3000, context_items=["a.py"])
        assert "truncada" in texto.lower()

    def test_semente_normal_nao_e_truncada(self, seed, branch):
        assert "truncada" not in _render(seed, branch).lower()
