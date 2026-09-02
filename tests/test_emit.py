"""Testes para hooks/emit.py — o emissor unico dos hooks.

O que esta travado aqui, e por que:

- **`systemMessage` nunca mais.** O bug que originou este modulo nao era um
  erro de logica: era um canal que existe, aceita a escrita, nao devolve erro,
  e simplesmente nao chega ao modelo. 81 sinais em 47 sessoes, 0 acoes. Um
  teste que garanta que nenhum caminho aqui produz essa chave e o unico jeito
  de a regressao ser barulhenta em vez de invisivel.
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
            ("PostToolUse", "stdout"),
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

    def test_posttooluse_nunca_usa_additional_context(self, emit):
        """additionalContext em PostToolUse nunca foi observado chegando.

        Usa-lo seria repetir o erro do systemMessage: escrever num canal que
        aceita a escrita e talvez nao entregue.
        """
        assert emit.resolve_channel("PostToolUse") != "additionalContext"


class TestSystemMessageNuncaMais:
    def test_nenhum_payload_produz_system_message(self, emit, tmp_path):
        for evento in ("UserPromptSubmit", "SessionStart", "PostToolUse",
                       "PreCompact", "Stop", "Qualquer"):
            em = emit.Emitter(evento, hook="t", root=tmp_path)
            em.add("k", "texto qualquer")
            assert "systemMessage" not in json.dumps(em.payload())

    def test_o_modulo_nao_menciona_a_chave_como_saida(self):
        fonte = EMIT_PATH.read_text(encoding="utf-8")
        codigo = fonte.split('"""', 2)[-1]
        assert '"systemMessage"' not in codigo


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
        emit.Emitter("PostToolUse", hook="h", root=tmp_path) \
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
