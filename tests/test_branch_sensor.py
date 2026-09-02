"""Testes para scripts/branch_sensor.py — o sensor passivo do Branch Keeper.

O sensor e uma rede, nao um juiz. Ele existe porque a deteccao que depende de
eu lembrar de olhar e exatamente a que falha em conversa longa. Tres camadas
independentes: eu (na skill), regex (sempre) e embedding (quando o Ollama
responde). Estes testes cobrem as duas ultimas.

O que esta travado aqui:

- **Falso positivo e o proprio problema.** Uma pergunta que oferece ramo no
  meio do trabalho quebra o foco tanto quanto a tangente que ela tentava
  evitar. Por isso o orcamento (2 ofertas por sessao, cooldown, dedupe) tem
  mais testes que a deteccao em si.
- **Ollama fora do ar nao pode calar o sensor.** A decisao do usuario foi
  "regex e ollama, para caso eu esqueca" — se a camada B cair, a A sozinha
  ainda oferece, marcada como degradada. Silencio seria a pior falha possivel:
  invisivel e indistinguivel de "nao havia ramo".
- **Deriva nao e ramo.** Escorregar do objetivo pede um aviso, nao uma janela
  nova. Sao veredictos diferentes com acoes diferentes, e um teste garante que
  deriva sozinha nunca vira oferta de ramo.
- **`stop_hook_active` corta a recursao.** O hook de Stop dispara ao fim de
  cada resposta; sem esse corte ele reentraria na propria saida.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
SENSOR_PATH = ROOT / "scripts" / "branch_sensor.py"


@pytest.fixture(scope="module")
def sensor():
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("branch_sensor", SENSOR_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["branch_sensor"] = mod
    spec.loader.exec_module(mod)
    return mod


class TestCamadaA:
    @pytest.mark.parametrize(
        "texto",
        [
            "e se a gente tambem indexasse os traces?",
            "Outra ideia: dava pra medir isso por embedding",
            "seria interessante ter um dashboard disso",
            "alias, o launcher podia virar atalho",
            "por outro lado, poderiamos cachear o resultado",
            "what if we also tracked the drift score?",
            "we could also ship this as a plugin",
            "side note: the vault sync is slow",
        ],
    )
    def test_marcadores_pt_e_en(self, sensor, texto):
        assert sensor.layer_a(texto) is not None

    @pytest.mark.parametrize(
        "texto",
        [
            "roda os testes de branch_state e me diz o resultado",
            "o teste falhou na linha 42, corrige",
            "commita isso na feat/branch-keeper",
        ],
    )
    def test_trabalho_normal_nao_dispara(self, sensor, texto):
        assert sensor.layer_a(texto) is None

    def test_texto_vazio_nao_dispara(self, sensor):
        assert sensor.layer_a("") is None


class TestAncora:
    def test_ancora_persiste_e_e_relida(self, sensor, tmp_path):
        sensor.set_anchor(cwd=str(tmp_path), text="construir o Branch Keeper",
                          source="first-prompt", session_id="s1", embedding=[1.0, 0.0])
        a = sensor.load_anchor(cwd=str(tmp_path))
        assert a["text"] == "construir o Branch Keeper"
        assert a["source"] == "first-prompt"

    def test_sem_ancora_devolve_none(self, sensor, tmp_path):
        assert sensor.load_anchor(cwd=str(tmp_path)) is None

    def test_ancora_de_outra_sessao_e_descartada(self, sensor, tmp_path):
        """Sessao nova, objetivo novo: herdar a ancora antiga mediria deriva errada."""
        sensor.set_anchor(cwd=str(tmp_path), text="objetivo antigo", source="first-prompt",
                          session_id="s1", embedding=[1.0, 0.0])
        assert sensor.load_anchor(cwd=str(tmp_path), session_id="s2") is None
        assert sensor.load_anchor(cwd=str(tmp_path), session_id="s1") is not None

    def test_cosseno(self, sensor):
        assert sensor.cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
        assert sensor.cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
        assert sensor.cosine([], [1.0]) is None


class TestVeredicto:
    def test_regex_mais_similaridade_baixa_vira_ramo(self, sensor):
        v = sensor.verdict(hit_a="e se", sim=0.20, drift_streak=0)
        assert v["kind"] == "ramo"

    def test_regex_com_similaridade_alta_e_so_o_mesmo_assunto(self, sensor):
        assert sensor.verdict(hit_a="e se", sim=0.90, drift_streak=0)["kind"] is None

    def test_similaridade_baixa_sustentada_vira_deriva(self, sensor):
        assert sensor.verdict(hit_a=None, sim=0.10, drift_streak=3)["kind"] == "deriva"

    def test_deriva_isolada_nao_vira_ramo(self, sensor):
        assert sensor.verdict(hit_a=None, sim=0.10, drift_streak=1)["kind"] is None

    def test_sem_ollama_a_regex_ainda_oferece_marcada_como_degradada(self, sensor):
        v = sensor.verdict(hit_a="outra ideia", sim=None, drift_streak=0)
        assert v["kind"] == "ramo" and v["degraded"] is True

    def test_sem_ollama_e_sem_regex_nao_ha_veredicto(self, sensor):
        assert sensor.verdict(hit_a=None, sim=None, drift_streak=9)["kind"] is None


class TestOrcamento:
    def test_conta_ofertas_por_sessao(self, sensor, tmp_path, monkeypatch):
        """Teto e cooldown sao limites distintos: aqui so o teto esta em jogo."""
        monkeypatch.setenv("HARNESS_BRANCH_MAX_OFFERS", "2")
        monkeypatch.setenv("HARNESS_BRANCH_COOLDOWN_TURNS", "1")
        for turno in (10, 20):
            assert sensor.budget_allows(cwd=str(tmp_path), turn=turno) is True
            sensor.record_offer(cwd=str(tmp_path), turn=turno)
        assert sensor.budget_allows(cwd=str(tmp_path), turn=30) is False

    def test_cooldown_bloqueia_oferta_seguida(self, sensor, tmp_path, monkeypatch):
        monkeypatch.setenv("HARNESS_BRANCH_MAX_OFFERS", "9")
        monkeypatch.setenv("HARNESS_BRANCH_COOLDOWN_TURNS", "5")
        sensor.record_offer(cwd=str(tmp_path), turn=10)
        assert sensor.budget_allows(cwd=str(tmp_path), turn=12) is False
        assert sensor.budget_allows(cwd=str(tmp_path), turn=16) is True

    def test_streak_de_deriva_sobe_e_zera(self, sensor, tmp_path, monkeypatch):
        monkeypatch.setenv("HARNESS_BRANCH_DRIFT_FLOOR", "0.35")
        assert sensor.bump_drift(cwd=str(tmp_path), sim=0.10) == 1
        assert sensor.bump_drift(cwd=str(tmp_path), sim=0.10) == 2
        assert sensor.bump_drift(cwd=str(tmp_path), sim=0.80) == 0

    def test_sim_ausente_nao_mexe_no_streak(self, sensor, tmp_path):
        sensor.bump_drift(cwd=str(tmp_path), sim=0.10)
        assert sensor.bump_drift(cwd=str(tmp_path), sim=None) == 1


class TestAvaliacaoCompleta:
    def _fake_embed(self, vec):
        return lambda _texto: vec

    def test_sinal_pede_julgamento_e_nao_manda_oferecer(self, sensor, tmp_path, monkeypatch):
        """O sinal mudou de ordem para pergunta, e o motivo esta medido.

        A camada A foi calibrada em 2026-09-02 contra 703 pares reais: precisao
        ~0.10, e 12 dos 16 padroes nunca dispararam. Um sinal que erra 9 em 10
        nao pode mandar oferecer — mandaria interromper o foco nove vezes para
        acertar uma, que e exatamente o dano que o Branch Keeper existe para
        evitar. Ele pode, no maximo, pedir que o modelo OLHE.
        """
        monkeypatch.setattr(sensor, "embed", self._fake_embed([0.0, 1.0]))
        sensor.set_anchor(cwd=str(tmp_path), text="ancora", source="first-prompt",
                          session_id="s1", embedding=[1.0, 0.0])
        out = sensor.evaluate(cwd=str(tmp_path), text="e se a gente indexasse os traces?",
                              session_id="s1", turn=3)
        assert "BRANCH SIGNAL" in out
        assert "NAO MANDA OFERECER" in out
        assert "may-offer" in out, "tem que mandar consultar o portao antes"
        assert "EM SILENCIO" in out, "falso positivo nao pode virar comentario"

    def test_camada_b_desligada_nao_paga_embed(self, sensor, tmp_path, monkeypatch):
        """Desligada por default: melhor F1 medido 0.209 contra 0.108 do acaso.

        Com ela fora, `sim` e sempre None e o veredicto de `ramo` sai marcado
        `degradado` — que passa a ser o estado NORMAL, nao a excecao.
        """
        chamadas = []
        monkeypatch.setattr(sensor, "embed",
                            lambda t: chamadas.append(t) or [0.0, 1.0])
        sensor.set_anchor(cwd=str(tmp_path), text="ancora", source="first-prompt",
                          session_id="s1", embedding=[1.0, 0.0])
        sensor.evaluate(cwd=str(tmp_path), text="e se a gente indexasse os traces?",
                        session_id="s1", turn=3)
        assert chamadas == [], "camada B rodou mesmo desligada"

    def test_camada_b_ligada_volta_a_filtrar(self, sensor, tmp_path, monkeypatch):
        """Ligar de volta e uma variavel de ambiente, nao um patch.

        Quando ligada, ela volta a suprimir marcador em texto do MESMO assunto —
        o unico trabalho util que ela fazia. Isto fica testado para que religar
        depois de uma calibracao melhor nao exija arqueologia.
        """
        monkeypatch.setenv("HARNESS_BRANCH_LAYER_B", "1")
        monkeypatch.setattr(sensor, "embed", self._fake_embed([1.0, 0.0]))
        sensor.set_anchor(cwd=str(tmp_path), text="ancora", source="first-prompt",
                          session_id="s1", embedding=[1.0, 0.0])
        assert sensor.evaluate(cwd=str(tmp_path), text="e se a gente rodasse o teste?",
                               session_id="s1", turn=3) == ""

    def test_ollama_fora_do_ar_nao_cala_o_sensor(self, sensor, tmp_path, monkeypatch):
        def _explode(_):
            raise OSError("ollama fora")

        monkeypatch.setattr(sensor, "embed", _explode)
        out = sensor.evaluate(cwd=str(tmp_path), text="outra ideia: exportar isso",
                              session_id="s1", turn=3)
        assert "BRANCH SIGNAL" in out

    def test_tema_ja_registrado_nao_reoferece(self, sensor, tmp_path, monkeypatch):
        import branch_state

        monkeypatch.setattr(sensor, "embed", self._fake_embed([0.0, 1.0]))
        branch_state.add(cwd=str(tmp_path), name="Ja Existe",
                         topic="e se a gente indexasse os traces")
        out = sensor.evaluate(cwd=str(tmp_path), text="e se a gente indexasse os traces?",
                              session_id="s1", turn=3)
        assert out == ""

    def test_orcamento_estourado_silencia(self, sensor, tmp_path, monkeypatch):
        monkeypatch.setenv("HARNESS_BRANCH_MAX_OFFERS", "0")
        monkeypatch.setattr(sensor, "embed", self._fake_embed([0.0, 1.0]))
        assert sensor.evaluate(cwd=str(tmp_path), text="e se a gente exportasse isso?",
                               session_id="s1", turn=3) == ""

    def test_turno_comum_nao_paga_embed(self, sensor, tmp_path, monkeypatch):
        """Camada B custa ~1s. Cobrar isso todo prompt seria taxar o foco.

        Sem marcador e fora da amostragem nao ha o que decidir: a deriva exige
        streak, entao medir turno sim, turno nao atrasa o alarme em alguns
        turnos — nao o impede.
        """
        chamadas = []
        monkeypatch.setattr(sensor, "embed", lambda t: chamadas.append(t) or [0.0, 1.0])
        monkeypatch.setenv("HARNESS_BRANCH_DRIFT_SAMPLE", "2")
        sensor.set_anchor(cwd=str(tmp_path), text="ancora", source="first-prompt",
                          session_id="s1", embedding=[1.0, 0.0])
        sensor.evaluate(cwd=str(tmp_path), text="roda os testes e me diz o resultado",
                        session_id="s1", turn=3)
        assert chamadas == []

    def test_marcador_paga_embed_em_qualquer_turno(self, sensor, tmp_path, monkeypatch):
        """Com a camada B LIGADA, marcador fura a amostragem.

        Precisa do `HARNESS_BRANCH_LAYER_B=1` explicito desde 2026-09-02: com
        ela desligada por default, nenhum turno paga embed. A regra continua
        valendo para quem religar apos uma calibracao melhor.
        """
        chamadas = []
        monkeypatch.setenv("HARNESS_BRANCH_LAYER_B", "1")
        monkeypatch.setattr(sensor, "embed", lambda t: chamadas.append(t) or [0.0, 1.0])
        monkeypatch.setenv("HARNESS_BRANCH_DRIFT_SAMPLE", "2")
        sensor.set_anchor(cwd=str(tmp_path), text="ancora", source="first-prompt",
                          session_id="s1", embedding=[1.0, 0.0])
        sensor.evaluate(cwd=str(tmp_path), text="e se a gente exportasse isso tudo?",
                        session_id="s1", turn=3)
        assert len(chamadas) == 1

    def test_desligado_por_env_nao_avalia(self, sensor, tmp_path, monkeypatch):
        monkeypatch.setenv("HARNESS_BRANCH", "0")
        assert sensor.evaluate(cwd=str(tmp_path), text="outra ideia qualquer",
                               session_id="s1", turn=3) == ""


class TestPayloadDoHook:
    def test_prompt_do_usuario_e_extraido(self, sensor):
        payload = {"prompt": "e se a gente medisse isso?", "cwd": "/x", "session_id": "s1"}
        assert sensor.text_from_payload(payload) == "e se a gente medisse isso?"

    def test_stop_le_a_ultima_fala_do_assistente(self, sensor, tmp_path):
        tr = tmp_path / "t.jsonl"
        tr.write_text(
            "\n".join(
                [
                    json.dumps({"type": "user", "message": {"content": "faz X"}}),
                    json.dumps(
                        {
                            "type": "assistant",
                            "message": {"content": [{"type": "text", "text": "outra ideia: Y"}]},
                        }
                    ),
                ]
            ),
            encoding="utf-8",
        )
        payload = {"transcript_path": str(tr), "hook_event_name": "Stop", "cwd": "/x"}
        assert "outra ideia" in sensor.text_from_payload(payload)

    def test_stop_reentrante_e_ignorado(self, sensor):
        payload = {"hook_event_name": "Stop", "stop_hook_active": True, "transcript_path": "x"}
        assert sensor.text_from_payload(payload) == ""

    def test_transcript_ausente_nao_derruba(self, sensor):
        payload = {"hook_event_name": "Stop", "transcript_path": "/nao/existe.jsonl"}
        assert sensor.text_from_payload(payload) == ""


class TestAncoraCega:
    """Ancora que nasce sem embedding tem que ser recuperada depois.

    O `set_anchor` so roda quando `load_anchor` devolve None. Uma ancora
    gravada com o Ollama fora tem texto e `embedding: null` — e um dict, nao
    e None — entao ela nunca era recriada e `cosine(vec, None)` devolvia None
    pelo resto da vida daquele projeto. A camada B ficava cega em silencio, e
    todo ramo saia marcado `degradado` sem que nada no disco explicasse por
    que. Medido em 2026-09-01: 2 das 5 ancoras reais estavam nesse estado.

    O backfill repara o vetor e **nao** toca no texto: mover a ancora seria
    mover o zero da regua no meio da medicao.
    """

    def _payload(self, tmp_path, prompt, session_id="s1"):
        return {
            "hook_event_name": "UserPromptSubmit",
            "cwd": str(tmp_path),
            "session_id": session_id,
            "prompt": prompt,
        }

    def _run_main(self, sensor, monkeypatch, payload):
        import io

        monkeypatch.setattr(sensor.sys, "stdin", io.StringIO(json.dumps(payload)))
        return sensor.main()

    def test_ancora_sem_embedding_e_recuperada(self, sensor, tmp_path, monkeypatch):
        sensor.set_anchor(cwd=str(tmp_path), text="objetivo da sessao",
                          source="first-prompt", session_id="s1", embedding=None)
        assert sensor.load_anchor(cwd=str(tmp_path), session_id="s1")["embedding"] is None

        monkeypatch.setattr(sensor, "embed", lambda _t: [1.0, 0.0])
        assert self._run_main(sensor, monkeypatch,
                              self._payload(tmp_path, "seguindo o trabalho normal")) == 0

        a = sensor.load_anchor(cwd=str(tmp_path), session_id="s1")
        assert a["embedding"] == [1.0, 0.0]
        assert a["text"] == "objetivo da sessao"

    def test_backfill_nao_desloca_a_ancora_existente(self, sensor, tmp_path, monkeypatch):
        sensor.set_anchor(cwd=str(tmp_path), text="objetivo original",
                          source="first-prompt", session_id="s1", embedding=[1.0, 0.0])
        monkeypatch.setattr(sensor, "embed", lambda _t: [0.0, 1.0])
        self._run_main(sensor, monkeypatch,
                       self._payload(tmp_path, "outro texto qualquer no turno"))

        a = sensor.load_anchor(cwd=str(tmp_path), session_id="s1")
        assert a["embedding"] == [1.0, 0.0]
        assert a["text"] == "objetivo original"

    def test_ollama_ainda_fora_nao_quebra_o_hook(self, sensor, tmp_path, monkeypatch):
        sensor.set_anchor(cwd=str(tmp_path), text="objetivo da sessao",
                          source="first-prompt", session_id="s1", embedding=None)

        def _explode(_texto):
            raise OSError("ollama fora")

        monkeypatch.setattr(sensor, "embed", _explode)
        assert self._run_main(sensor, monkeypatch,
                              self._payload(tmp_path, "seguindo o trabalho normal")) == 0
        assert sensor.load_anchor(cwd=str(tmp_path), session_id="s1")["embedding"] is None


class TestCanalVivo:
    """O sinal precisa sair por um canal que chega ao modelo.

    Ate 2026-09-01 o sensor emitia por `systemMessage`, que e canal de UI: os
    4 BRANCH SIGNAL da historia inteira foram escritos, nao deram erro, e nao
    chegaram. Nenhuma oferta foi feita, `branches.json` nunca nasceu, e o
    silencio ficou indistinguivel de "nao havia ramo". Estes testes existem
    para que a volta desse canal seja barulhenta.
    """

    class _Stdin:
        """stdin falso que serve aos dois caminhos.

        Precisa de `.read()` e de `.buffer` porque o teste tem que rodar
        identico contra o codigo velho e o novo — um duplo que so aceita o
        caminho novo prova que o duplo mudou, nao que o canal mudou. E o
        `.read()` decodifica em cp1252 de proposito: e o que o Windows fazia.
        """

        def __init__(self, raw: bytes):
            import io

            self.buffer = io.BytesIO(raw)
            self._texto = raw.decode("cp1252", "replace")

        def read(self, *_a):
            return self._texto

    def _rodar(self, sensor, monkeypatch, tmp_path, prompt, evento="UserPromptSubmit",
               session_id="s1"):
        import io

        payload = {
            "hook_event_name": evento,
            "cwd": str(tmp_path),
            "session_id": session_id,
            "prompt": prompt,
        }
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        monkeypatch.setattr(sensor.sys, "stdin", self._Stdin(raw))
        buf = io.StringIO()
        monkeypatch.setattr(sensor.sys, "stdout", buf)
        sensor.main()
        return buf.getvalue()

    def test_ramo_sai_por_additional_context(self, sensor, tmp_path, monkeypatch):
        monkeypatch.setattr(sensor, "embed", lambda _t: [0.0, 1.0])
        sensor.set_anchor(cwd=str(tmp_path), text="ancora", source="first-prompt",
                          session_id="s1", embedding=[1.0, 0.0])
        out = self._rodar(sensor, monkeypatch, tmp_path,
                          "e se a gente montasse um dashboard separado?")
        assert out, "o sensor ficou mudo com marcador presente"
        data = json.loads(out)
        assert "systemMessage" not in data
        assert "BRANCH SIGNAL" in data["hookSpecificOutput"]["additionalContext"]
        assert data["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"

    def test_stop_nao_fala(self, sensor, tmp_path, monkeypatch):
        """No Stop o turno acabou: 'invoque AGORA' chegaria tarde por construcao.

        Foi assim que os 4 sinais da historia morreram — todos vieram do Stop.
        `text_from_payload` e substituido de proposito: no Stop real o texto sai
        do transcript, e sem isso o hook retornaria cedo e o teste passaria sem
        nunca alcancar o ponto de emissao.
        """
        monkeypatch.setattr(sensor, "embed", lambda _t: [0.0, 1.0])
        monkeypatch.setattr(sensor, "text_from_payload",
                            lambda p: p.get("prompt", ""))
        sensor.set_anchor(cwd=str(tmp_path), text="ancora", source="first-prompt",
                          session_id="s1", embedding=[1.0, 0.0])
        out = self._rodar(sensor, monkeypatch, tmp_path,
                          "e se a gente montasse um dashboard separado?", evento="Stop")
        assert "BRANCH SIGNAL" not in out

    def test_o_mesmo_prompt_no_userpromptsubmit_fala(self, sensor, tmp_path, monkeypatch):
        """Controle do teste acima: o silencio do Stop e do evento, nao do texto."""
        monkeypatch.setattr(sensor, "embed", lambda _t: [0.0, 1.0])
        monkeypatch.setattr(sensor, "text_from_payload",
                            lambda p: p.get("prompt", ""))
        sensor.set_anchor(cwd=str(tmp_path), text="ancora", source="first-prompt",
                          session_id="s1", embedding=[1.0, 0.0])
        out = self._rodar(sensor, monkeypatch, tmp_path,
                          "e se a gente montasse um dashboard separado?")
        assert "BRANCH SIGNAL" in out

    def test_acento_no_prompt_sobrevive_a_ida_e_volta(self, sensor, tmp_path, monkeypatch):
        """stdin em cp1252 transformava 'alias' acentuado em 'aliÃ¡s'.

        A camada A compara texto dobrado (sem acento); um prompt mal decodificado
        muda os bytes e o marcador escapa. O hook nao pode depender de o
        chamador exportar PYTHONIOENCODING.
        """
        monkeypatch.setattr(sensor, "embed", lambda _t: [0.0, 1.0])
        sensor.set_anchor(cwd=str(tmp_path), text="ancora", source="first-prompt",
                          session_id="s1", embedding=[1.0, 0.0])
        out = self._rodar(sensor, monkeypatch, tmp_path,
                          "aliás, e se fizéssemos a visualização em outra ferramenta?")
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        assert "aliás" in ctx and "visualização" in ctx
        assert "Ã" not in ctx


class TestSinalDiferido:
    """Sinal nascido no Stop e guardado, nao descartado.

    Os 4 BRANCH SIGNAL da historia inteira vieram do evento Stop, onde a
    instrucao "invoque a skill AGORA, antes de responder" chega depois da
    resposta — inexecutavel por construcao. Mas descartar tambem custa:
    `evaluate` chama `record_offer` ANTES de saber se a entrega e possivel,
    entao um sinal perdido no Stop ainda queima uma das 2 ofertas da sessao.
    Observado ao vivo em 2026-09-01: `offers: 1` gasto por um sinal que
    ninguem leu. Guardar fecha os dois buracos com o mesmo arquivo.
    """

    def _rodar(self, sensor, monkeypatch, tmp_path, prompt, evento, session_id="s1"):
        monkeypatch.setattr(sensor, "embed", lambda _t: [0.0, 1.0])
        monkeypatch.setattr(sensor, "text_from_payload", lambda p: p.get("prompt", ""))
        return TestCanalVivo()._rodar(sensor, monkeypatch, tmp_path, prompt,
                                      evento=evento, session_id=session_id)

    def _ancora(self, sensor, tmp_path, session_id="s1"):
        sensor.set_anchor(cwd=str(tmp_path), text="ancora", source="first-prompt",
                          session_id=session_id, embedding=[1.0, 0.0])

    def test_stop_guarda_em_vez_de_perder(self, sensor, tmp_path, monkeypatch):
        self._ancora(sensor, tmp_path)
        out = self._rodar(sensor, monkeypatch, tmp_path,
                          "e se a gente exportasse tudo pra outro formato?", "Stop")
        assert "BRANCH SIGNAL" not in out
        assert sensor._pending_path(str(tmp_path)).exists()

    def test_o_prompt_seguinte_entrega(self, sensor, tmp_path, monkeypatch):
        self._ancora(sensor, tmp_path)
        self._rodar(sensor, monkeypatch, tmp_path,
                    "e se a gente exportasse tudo pra outro formato?", "Stop")
        out = self._rodar(sensor, monkeypatch, tmp_path,
                          "voltando ao trabalho normal agora", "UserPromptSubmit")
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        assert "BRANCH SIGNAL" in ctx and "outro formato" in ctx

    def test_entrega_uma_vez_so(self, sensor, tmp_path, monkeypatch):
        self._ancora(sensor, tmp_path)
        self._rodar(sensor, monkeypatch, tmp_path,
                    "e se a gente exportasse tudo pra outro formato?", "Stop")
        self._rodar(sensor, monkeypatch, tmp_path, "primeiro prompt depois",
                    "UserPromptSubmit")
        out2 = self._rodar(sensor, monkeypatch, tmp_path, "segundo prompt depois",
                           "UserPromptSubmit")
        assert "outro formato" not in out2
        assert not sensor._pending_path(str(tmp_path)).exists()

    def test_sinal_de_outra_sessao_nao_e_entregue(self, sensor, tmp_path, monkeypatch):
        """Objetivo novo, ancora nova: oferecer um ramo sobre a conversa de
        ontem seria ruido com cara de memoria."""
        sensor.stash_pending(cwd=str(tmp_path), session_id="sessao-velha",
                             text="BRANCH SIGNAL: ramo de ontem", turn=3)
        self._ancora(sensor, tmp_path, session_id="s1")
        out = self._rodar(sensor, monkeypatch, tmp_path, "trabalho de hoje seguindo",
                          "UserPromptSubmit", session_id="s1")
        assert "ramo de ontem" not in out

    def test_take_sem_arquivo_e_string_vazia(self, sensor, tmp_path):
        assert sensor.take_pending(cwd=str(tmp_path), session_id="s1") == ""


class TestAnelDeSims:
    """Cada medicao de cosseno fica registrada com o turno.

    Nao muda decisao nenhuma — e materia-prima de calibracao coletada de graca
    em producao. Os pisos 0.55/0.35 sao chutes admitidos no proprio design doc,
    e a unica medicao avulsa que existe saiu anticorrelacionada (mesmo assunto
    0.33, tangente 0.44). Escolher numero melhor exige dados de uso real.
    """

    def test_medicao_entra_no_anel_com_o_turno(self, sensor, tmp_path):
        sensor.bump_drift(cwd=str(tmp_path), sim=0.31, turn=14)
        sensor.bump_drift(cwd=str(tmp_path), sim=0.72, turn=16)
        anel = sensor._budget(str(tmp_path))["sims"]
        assert anel == [{"turn": 14, "sim": 0.31}, {"turn": 16, "sim": 0.72}]

    def test_turno_sem_medicao_nao_polui_o_anel(self, sensor, tmp_path):
        sensor.bump_drift(cwd=str(tmp_path), sim=0.31, turn=2)
        sensor.bump_drift(cwd=str(tmp_path), sim=None, turn=3)
        assert len(sensor._budget(str(tmp_path))["sims"]) == 1

    def test_anel_tem_teto(self, sensor, tmp_path):
        for t in range(sensor.SIMS_RING + 5):
            sensor.bump_drift(cwd=str(tmp_path), sim=0.5, turn=t)
        anel = sensor._budget(str(tmp_path))["sims"]
        assert len(anel) == sensor.SIMS_RING
        assert anel[-1]["turn"] == sensor.SIMS_RING + 4  # guarda os mais recentes


class TestStreakSobreviveAAmostragem:
    """O streak NAO congela em turno fora da amostragem.

    O diagnostico de 2026-09-01 acusou `bump_drift` de impedir `deriva` de
    fechar quando `sim is None`. A acusacao esta errada e este teste e a prova:
    com DRIFT_SAMPLE=2 o streak chega a 3 no turno 6 e o veredicto sai.

    Fica registrado porque a hipotese errada quase virou refatoracao: o motivo
    real de `offered_deriva` nunca ter disparado foram sessoes curtas (turnos
    observados: 2, 3, 5, 9) e duas ancoras com `embedding: null`, que faziam
    `sim` ser sempre None.
    """

    def test_streak_chega_a_tres_com_amostragem_alternada(self, sensor, tmp_path, monkeypatch):
        monkeypatch.setenv("HARNESS_BRANCH_DRIFT_FLOOR", "0.35")
        streak = 0
        for turno in range(1, 7):
            sim = 0.10 if turno % 2 == 0 else None
            streak = sensor.bump_drift(cwd=str(tmp_path), sim=sim, turn=turno)
        assert streak == 3
        assert sensor.verdict(hit_a=None, sim=0.10, drift_streak=streak)["kind"] == "deriva"

    def test_ancora_cega_e_que_travava_o_streak(self, sensor, tmp_path):
        """Com embedding nulo, `sim` e sempre None e o streak nunca sai de zero.

        Era o estado de 2 das 5 ancoras reais em disco. Ver TestAncoraCega.
        """
        for turno in range(1, 9):
            streak = sensor.bump_drift(cwd=str(tmp_path), sim=None, turn=turno)
        assert streak == 0
        assert sensor.verdict(hit_a=None, sim=None, drift_streak=streak)["kind"] is None


class TestPortaoUnico:
    """Um portao para os quatro caminhos de deteccao.

    A decisao de 2026-09-01 foi manter os quatro juntos: autocheck do modelo,
    `/branch` explicito, camada A e camada B. O risco disso e quatro
    detectores oferecendo o MESMO tema no mesmo turno — o sensor viraria a
    interrupcao que veio evitar. As camadas A e B ja consultavam `already_seen`
    e `budget_allows` dentro do `evaluate`; os outros dois nao consultavam
    nada, porque nao havia o que consultar.
    """

    def test_tema_novo_passa(self, sensor, tmp_path):
        assert sensor.may_offer(cwd=str(tmp_path), topic="indexar sessoes cruzadas",
                                turn=5) == sensor.OK

    def test_tema_ja_registrado_e_duplicata(self, sensor, tmp_path):
        import branch_state

        branch_state.add(cwd=str(tmp_path), name="Ja Existe",
                         topic="indexar sessoes cruzadas")
        assert sensor.may_offer(cwd=str(tmp_path), topic="indexar sessoes cruzadas",
                                turn=5) == sensor.DUPLICATE

    def test_branch_explicito_nao_reoferece_duplicata(self, sensor, tmp_path):
        """Pedir de novo o que ja existe pede `recall`, nao um segundo registro."""
        import branch_state

        branch_state.add(cwd=str(tmp_path), name="Ja Existe",
                         topic="indexar sessoes cruzadas")
        assert sensor.may_offer(cwd=str(tmp_path), topic="indexar sessoes cruzadas",
                                turn=5, explicito=True) == sensor.DUPLICATE

    def test_orcamento_estourado_barra(self, sensor, tmp_path, monkeypatch):
        monkeypatch.setenv("HARNESS_BRANCH_MAX_OFFERS", "0")
        assert sensor.may_offer(cwd=str(tmp_path), topic="assunto inedito aqui",
                                turn=5) == sensor.BUDGET

    def test_branch_explicito_fura_o_orcamento(self, sensor, tmp_path, monkeypatch):
        """O usuario pediu. Teto e cooldown existem para conter o sensor, nao ele."""
        monkeypatch.setenv("HARNESS_BRANCH_MAX_OFFERS", "0")
        assert sensor.may_offer(cwd=str(tmp_path), topic="assunto inedito aqui",
                                turn=5, explicito=True) == sensor.OK

    def test_teto_de_ramos_abertos_nao_e_furado_nem_por_branch(self, sensor, tmp_path,
                                                               monkeypatch):
        """Com o teto atingido o problema e capacidade, nao permissao.

        Tres ramos abertos ja enchem o bloco de parking. A skill recebe
        `max_open` e deve dizer isso, em vez de abrir o quarto.
        """
        import branch_state

        monkeypatch.setenv("HARNESS_BRANCH_MAX_OPEN", "2")
        for n in (1, 2):
            b = branch_state.add(cwd=str(tmp_path), name=f"Ramo {n}",
                                 topic=f"assunto distinto numero {n} aqui")
            branch_state.set_status(cwd=str(tmp_path), slug=b["slug"], status="open")
        for explicito in (False, True):
            assert sensor.may_offer(cwd=str(tmp_path), topic="tema totalmente outro",
                                    turn=5, explicito=explicito) == sensor.MAX_OPEN

    def test_falha_interna_abre_o_portao(self, sensor, tmp_path, monkeypatch):
        """Portao que quebra cala o sensor, e silencio foi o modo de falha original."""
        import branch_state

        def _explode(**_kw):
            raise OSError("registro ilegivel")

        monkeypatch.setattr(branch_state, "already_seen", _explode)
        assert sensor.may_offer(cwd=str(tmp_path), topic="qualquer",
                                turn=5) == sensor.OK

    def test_cli_imprime_uma_palavra(self, sensor, tmp_path, capsys):
        sensor.cli_may_offer(["--cwd", str(tmp_path), "--topic", "assunto inedito"])
        assert capsys.readouterr().out.strip() == sensor.OK
