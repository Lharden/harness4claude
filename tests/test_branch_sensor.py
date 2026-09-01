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

    def test_ramo_emite_sinal_com_tema(self, sensor, tmp_path, monkeypatch):
        monkeypatch.setattr(sensor, "embed", self._fake_embed([0.0, 1.0]))
        sensor.set_anchor(cwd=str(tmp_path), text="ancora", source="first-prompt",
                          session_id="s1", embedding=[1.0, 0.0])
        out = sensor.evaluate(cwd=str(tmp_path), text="e se a gente indexasse os traces?",
                              session_id="s1", turn=3)
        assert "BRANCH SIGNAL" in out and "ramo" in out
        assert "branch-out" in out

    def test_assunto_do_proprio_trabalho_fica_em_silencio(self, sensor, tmp_path, monkeypatch):
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
        chamadas = []
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
