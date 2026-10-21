"""Testes para hooks/emit.py — o emissor unico dos hooks.

O que esta travado aqui, e por que:

- **`systemMessage` nunca mais.** O bug que originou este modulo nao era um
  erro de logica: era um canal que existe, aceita a escrita, nao devolve erro,
  e simplesmente nao chega ao modelo. 81 sinais em 47 sessoes, 0 acoes. Um
  teste que garanta que nenhum caminho aqui produz essa chave e o unico jeito
  de a regressao ser barulhenta em vez de invisivel. Desde 2026-10-19 a chave
  tem um uso legitimo, o tipo `tela` (a tela do usuario, nunca o modelo), so em
  UserPromptSubmit e SessionStart; nenhum outro tipo a produz.
- **Um bloco por turno.** O host aceita um `hookSpecificOutput` por saida.
  Dois emissores escrevendo a chave direto se sobrescreveriam em silencio —
  era o risco concreto de juntar BRANCH SIGNAL e `<harness-parked>`. Por isso
  a acumulacao tem teste proprio.
- **O Stop nao fala.** Nao e preferencia de estilo: no Stop o turno acabou, e
  "faca X antes de responder" chega tarde por construcao. Foi assim que os 4
  sinais de ramo da historia morreram.
- **Falha do extrato nao pode custar a emissao.** A telemetria existe para
  auditar; se ela derrubar o hook, ela virou o problema que veio medir.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
EMIT_PATH = ROOT / "hooks" / "emit.py"


@pytest.fixture(scope="module")
def emit():
    spec = importlib.util.spec_from_file_location("harness_emit", EMIT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["harness_emit"] = mod
    spec.loader.exec_module(mod)
    return mod


class TestMatrizDeCanais:
    @pytest.mark.parametrize(
        "evento,canal",
        [
            ("UserPromptSubmit", "additionalContext"),
            ("userpromptsubmit", "additionalContext"),
            ("SessionStart", "additionalContext"),
            ("PostToolUse", "additionalContext"),
            ("PreCompact", "stdout"),
            ("Stop", "silent"),
            ("SubagentStop", "silent"),
            ("SessionEnd", "silent"),
        ],
    )
    def test_canal_por_evento(self, emit, evento, canal):
        assert emit.resolve_channel(evento) == canal

    def test_evento_desconhecido_cai_no_canal_conservador(self, emit):
        """stdout e o unico canal provado em todo evento que ja emitiu."""
        assert emit.resolve_channel("EventoQueAindaNaoExiste") == "stdout"
        assert emit.resolve_channel(None) == "stdout"

    def test_posttooluse_usa_additional_context(self, emit):
        """https://code.claude.com/docs/en/hooks: PostToolUse nao esta entre as

        excecoes que recebem stdout cru como contexto (so UserPromptSubmit,
        UserPromptExpansion, SessionStart e PostModelSwitch estao). O canal que
        a doc da para "Add context for Claude" nesse evento e
        hookSpecificOutput.additionalContext. Ate 2026-09-24 este teste travava
        o oposto, apoiado em observacao de transcript que confundia "aparece na
        UI" com "chega ao modelo" — o mesmo erro que o systemMessage cometia.
        """
        assert emit.resolve_channel("PostToolUse") == "additionalContext"


class TestSystemMessageNuncaMais:
    def test_nenhum_payload_produz_system_message(self, emit, tmp_path):
        for evento in ("UserPromptSubmit", "SessionStart", "PostToolUse",
                       "PreCompact", "Stop", "Qualquer"):
            em = emit.Emitter(evento, hook="t", root=tmp_path)
            em.add("k", "texto qualquer")
            assert "systemMessage" not in json.dumps(em.payload())

    def test_a_chave_so_existe_na_constante_do_tipo_tela(self):
        """A chave sai por um lugar so: a constante que o tipo `tela` usa.

        Ate 2026-10-19 a chave nao podia aparecer no codigo. Com o tipo `tela`
        (design do monitor entre as maquinas, ASSUMPTION-009), ela aparece uma
        vez, e qualquer outro caminho que a escreva reprova aqui; o teste de
        cima continua provando que nenhum tipo comum a produz.
        """
        fonte = EMIT_PATH.read_text(encoding="utf-8")
        codigo = fonte.split('"""', 2)[-1]
        assert codigo.count('"systemMessage"') == 1
        assert 'SYSTEM_MESSAGE = "systemMessage"' in codigo
        assert "'systemMessage'" not in codigo


class TestTela:
    """O tipo `tela`: `systemMessage` de topo, so em UserPromptSubmit e SessionStart (design do monitor, comp. 8)."""

    @pytest.mark.parametrize("evento", ["UserPromptSubmit", "SessionStart", "userpromptsubmit"])
    def test_tela_vira_system_message_e_nao_entra_no_contexto(self, emit, tmp_path, evento):
        em = emit.Emitter(evento, hook="t", root=tmp_path)
        em.add("maquina", "MAQUINA: x").add("tela", "MAQUINA: x")
        out = em.payload()
        assert out["systemMessage"] == "MAQUINA: x"
        assert out["hookSpecificOutput"]["additionalContext"] == "MAQUINA: x"
        assert out["hookSpecificOutput"]["hookEventName"] == emit.canonical_event(evento)

    def test_tela_sozinha_nao_vai_ao_modelo(self, emit, tmp_path):
        em = emit.Emitter("UserPromptSubmit", hook="t", root=tmp_path)
        em.add("tela", "so para o usuario")
        assert em.payload() == {"systemMessage": "so para o usuario"}
        assert em.text() == ""

    @pytest.mark.parametrize("evento", ["PostToolUse", "PreToolUse", "Stop", "SubagentStop", "SessionEnd",
                                        "PreCompact", "PostCompact", "SubagentStart", "Qualquer", None])
    def test_tela_fora_dos_dois_eventos_e_recusada(self, emit, tmp_path, evento):
        em = emit.Emitter(evento, hook="t", root=tmp_path)
        with pytest.raises(ValueError, match="tela"):
            em.add("tela", "nao pode")
        assert em.payload() == {}

    def test_um_json_so_com_os_dois_campos(self, emit, tmp_path):
        buf = io.StringIO()
        em = emit.Emitter("UserPromptSubmit", hook="t", root=tmp_path)
        em.add("classified", "HARNESS v3 CLASSIFIED").add("maquina", "MAQUINA: y").add("tela", "MAQUINA: y")
        em.flush(stream=buf)
        out = json.loads(buf.getvalue())  # falha se houver dois objetos
        assert set(out) == {"hookSpecificOutput", "systemMessage"}
        assert "HARNESS v3 CLASSIFIED" in out["hookSpecificOutput"]["additionalContext"]
        assert "MAQUINA: y" in out["hookSpecificOutput"]["additionalContext"]

    def test_extrato_registra_a_tela_pelo_canal_dela(self, emit, tmp_path):
        emit.Emitter("SessionStart", hook="t", root=tmp_path).add("tela", "abc").flush(stream=io.StringIO())
        row = json.loads(emit.log_path(tmp_path).read_text(encoding="utf-8").strip())
        assert row["kind"] == "tela" and row["channel"] == "systemMessage"

    def test_cli_tela_no_session_start(self, emit, tmp_path, capsys, monkeypatch):
        monkeypatch.setenv("HARNESS_DIR", str(tmp_path))
        emit.main(["--event", "SessionStart", "--kind", "digest", "--text", "bloco\n\nMAQUINA: z",
                   "--tela", "MAQUINA: z"])
        out = json.loads(capsys.readouterr().out)
        assert out["systemMessage"] == "MAQUINA: z"
        assert out["hookSpecificOutput"]["additionalContext"] == "bloco\n\nMAQUINA: z"

    def test_cli_tela_em_evento_sem_tela_vai_ao_stderr_e_o_texto_sai(self, emit, tmp_path, capsys, monkeypatch):
        monkeypatch.setenv("HARNESS_DIR", str(tmp_path))
        emit.main(["--event", "PostToolUse", "--kind", "k", "--text", "contexto", "--tela", "nao"])
        cap = capsys.readouterr()
        out = json.loads(cap.out)
        assert "systemMessage" not in out and out["hookSpecificOutput"]["additionalContext"] == "contexto"
        assert "tela" in cap.err


class TestUmBlocoPorTurno:
    def test_dois_add_viram_um_additional_context(self, emit, tmp_path):
        em = emit.Emitter("UserPromptSubmit", hook="t", root=tmp_path)
        em.add("branch", "BRANCH SIGNAL: ramo").add("parked", "<harness-parked>x")
        out = em.payload()
        assert list(out) == ["hookSpecificOutput"]
        ctx = out["hookSpecificOutput"]["additionalContext"]
        assert "BRANCH SIGNAL: ramo" in ctx and "<harness-parked>x" in ctx

    def test_ordem_dos_blocos_e_preservada(self, emit, tmp_path):
        em = emit.Emitter("UserPromptSubmit", hook="t", root=tmp_path)
        em.add("a", "primeiro").add("b", "segundo")
        ctx = em.payload()["hookSpecificOutput"]["additionalContext"]
        assert ctx.index("primeiro") < ctx.index("segundo")

    def test_bloco_vazio_nao_entra(self, emit, tmp_path):
        em = emit.Emitter("UserPromptSubmit", hook="t", root=tmp_path)
        em.add("a", "").add("b", "   ").add("c", None)
        assert em.payload() == {}


class TestStopNaoFala:
    def test_stop_nao_escreve_nada_no_stdout(self, emit, tmp_path):
        buf = io.StringIO()
        em = emit.Emitter("Stop", hook="t", root=tmp_path)
        em.add("branch", "BRANCH SIGNAL: ramo — marcador 'alias'")
        em.flush(stream=buf)
        assert buf.getvalue() == ""

    def test_mas_o_silencio_fica_registrado(self, emit, tmp_path):
        """Silencio nao pode ser indistinguivel de 'nao havia sinal'.

        Foi essa ambiguidade que escondeu o bug por uma semana inteira.
        """
        em = emit.Emitter("Stop", hook="t", root=tmp_path)
        em.add("branch", "sinal suprimido")
        em.flush(stream=io.StringIO())
        linhas = emit.log_path(tmp_path).read_text(encoding="utf-8").strip().splitlines()
        assert json.loads(linhas[-1])["channel"] == "silent"


class TestExtrato:
    def test_uma_linha_por_bloco(self, emit, tmp_path):
        em = emit.Emitter("UserPromptSubmit", hook="h", session_id="s1", root=tmp_path)
        em.add("classify", "aaa").add("branch", "bbb")
        em.flush(stream=io.StringIO())
        linhas = emit.log_path(tmp_path).read_text(encoding="utf-8").strip().splitlines()
        assert len(linhas) == 2
        kinds = [json.loads(x)["kind"] for x in linhas]
        assert kinds == ["classify", "branch"]

    def test_registra_o_custo_em_chars(self, emit, tmp_path):
        em = emit.Emitter("UserPromptSubmit", hook="h", root=tmp_path)
        em.add("classify", "12345")
        em.flush(stream=io.StringIO())
        row = json.loads(emit.log_path(tmp_path).read_text(encoding="utf-8").strip())
        assert row["chars"] == 5 and row["session_id"] == ""

    def test_agregado_conta_por_kind_e_canal(self, emit, tmp_path):
        for _ in range(3):
            emit.Emitter("UserPromptSubmit", hook="h", root=tmp_path) \
                .add("classify", "xx").flush(stream=io.StringIO())
        emit.Emitter("PreCompact", hook="h", root=tmp_path) \
            .add("reclassify", "yyy").flush(stream=io.StringIO())
        agg = emit.aggregate(tmp_path)
        assert agg["classify"]["n"] == 3
        assert agg["classify"]["channels"]["additionalContext"] == 3
        assert agg["reclassify"]["channels"]["stdout"] == 1

    def test_agregado_de_extrato_inexistente_e_vazio(self, emit, tmp_path):
        assert emit.aggregate(tmp_path / "nao-existe") == {}


class TestNuncaDerrubaOHook:
    def test_flush_e_idempotente(self, emit, tmp_path):
        buf = io.StringIO()
        em = emit.Emitter("UserPromptSubmit", hook="h", root=tmp_path)
        em.add("k", "uma vez so")
        em.flush(stream=buf)
        em.flush(stream=buf)
        assert buf.getvalue().count("uma vez so") == 1

    def test_extrato_ilegivel_nao_impede_a_emissao(self, emit, tmp_path):
        """Telemetria que derruba o hook virou o problema que veio medir."""
        bloqueio = tmp_path / "emissions.jsonl"
        bloqueio.mkdir(parents=True)  # diretorio no lugar do arquivo: append falha
        buf = io.StringIO()
        em = emit.Emitter("UserPromptSubmit", hook="h", root=tmp_path)
        em.add("k", "a mensagem ainda tem que sair")
        em.flush(stream=buf)
        assert "a mensagem ainda tem que sair" in buf.getvalue()

    def test_linha_corrompida_no_extrato_nao_quebra_o_agregado(self, emit, tmp_path):
        emit.Emitter("UserPromptSubmit", hook="h", root=tmp_path) \
            .add("classify", "ok").flush(stream=io.StringIO())
        with open(emit.log_path(tmp_path), "a", encoding="utf-8") as fh:
            fh.write("{isto nao e json}\n\n")
        assert emit.aggregate(tmp_path)["classify"]["n"] == 1


class TestCli:
    def test_channel_of_imprime_o_canal(self, emit, capsys):
        emit.main(["--channel-of", "Stop"])
        assert capsys.readouterr().out.strip() == "silent"

    def test_sem_texto_nao_emite(self, emit, capsys):
        assert emit.main(["--event", "UserPromptSubmit", "--kind", "k"]) == 0
        assert capsys.readouterr().out == ""

    def test_texto_vira_additional_context(self, emit, tmp_path, capsys, monkeypatch):
        monkeypatch.setenv("HARNESS_DIR", str(tmp_path))
        emit.main(["--event", "UserPromptSubmit", "--kind", "classify",
                   "--text", "confirme a classificacao"])
        out = json.loads(capsys.readouterr().out)
        assert out["hookSpecificOutput"]["additionalContext"] == "confirme a classificacao"
        assert out["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
