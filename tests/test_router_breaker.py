"""Disjuntor da Camada B do skill-router (auditoria 2026-07-28).

Evidencia que motivou o disjuntor: `~/.claude/harness/router/debug-router.log`
com 88 linhas, 100% identicas (`layer B degraded: TimeoutError: timed out`),
zero sucessos, entre 24/07 e 25/07. Cada uma dessas linhas custou um
EMBED_TIMEOUT completo num hook de UserPromptSubmit — latencia paga a cada
prompt para nada, mais ruido que esconde qualquer falha nova no mesmo arquivo.

Funcoes puras: rodam sem Ollama e sem indice.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
ROUTER_PATH = ROOT / "hooks" / "skill_router.py"


@pytest.fixture(scope="module")
def sr():
    spec = importlib.util.spec_from_file_location("skill_router", ROUTER_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["skill_router"] = mod
    spec.loader.exec_module(mod)
    return mod


class TestBreakerOpen:
    def test_fechado_sem_falhas(self, sr):
        assert sr.breaker_open({"failures": 0, "opened_at": 0.0}, 1000.0) is False

    def test_fechado_abaixo_do_limiar(self, sr):
        st = {"failures": sr.BREAKER_THRESHOLD - 1, "opened_at": 1000.0}
        assert sr.breaker_open(st, 1000.0) is False

    def test_abre_no_limiar(self, sr):
        st = {"failures": sr.BREAKER_THRESHOLD, "opened_at": 1000.0}
        assert sr.breaker_open(st, 1000.0) is True

    def test_reabre_apos_cooldown(self, sr):
        st = {"failures": sr.BREAKER_THRESHOLD, "opened_at": 1000.0}
        assert sr.breaker_open(st, 1000.0 + sr.BREAKER_COOLDOWN_S + 1) is False


class TestBreakerRecord:
    def test_sucesso_zera(self, sr):
        st = sr.breaker_record({"failures": 9, "opened_at": 500.0}, True, 1000.0)
        assert st["failures"] == 0
        assert st["opened_at"] == 0.0

    def test_falhas_acumulam(self, sr):
        st = {"failures": 0, "opened_at": 0.0}
        for i in range(1, sr.BREAKER_THRESHOLD + 1):
            sr.breaker_record(st, False, 1000.0)
            assert st["failures"] == i

    def test_opened_at_marcado_no_limiar(self, sr):
        st = {"failures": sr.BREAKER_THRESHOLD - 1, "opened_at": 0.0}
        sr.breaker_record(st, False, 1234.0)
        assert st["opened_at"] == 1234.0

    def test_falha_durante_cooldown_nao_adia_reabertura(self, sr):
        """Sem esta trava, uma falha por prompt empurraria o cooldown para sempre."""
        st = {"failures": sr.BREAKER_THRESHOLD, "opened_at": 1000.0}
        sr.breaker_record(st, False, 1100.0)  # ainda dentro do cooldown
        assert st["opened_at"] == 1000.0

    def test_falha_apos_cooldown_reinicia_janela(self, sr):
        st = {"failures": sr.BREAKER_THRESHOLD, "opened_at": 1000.0}
        depois = 1000.0 + sr.BREAKER_COOLDOWN_S + 10
        sr.breaker_record(st, False, depois)
        assert st["opened_at"] == depois


class TestShouldLog:
    def test_mensagem_nova_loga(self, sr):
        st = {"last_msg": "erro A", "last_msg_ts": 1000.0}
        assert sr.should_log(st, "erro B", 1001.0) is True

    def test_mensagem_repetida_suprimida(self, sr):
        st = {"last_msg": "erro A", "last_msg_ts": 1000.0}
        assert sr.should_log(st, "erro A", 1001.0) is False

    def test_mensagem_repetida_volta_apos_janela(self, sr):
        st = {"last_msg": "erro A", "last_msg_ts": 1000.0}
        assert sr.should_log(st, "erro A", 1000.0 + sr.DBG_REPEAT_WINDOW_S) is True

    def test_estado_vazio_loga(self, sr):
        assert sr.should_log({}, "erro A", 1000.0) is True


class TestBreakerPersistence:
    def test_ausente_retorna_zerado(self, sr, tmp_path):
        st = sr.read_breaker(str(tmp_path))
        assert st["failures"] == 0

    def test_corrompido_retorna_zerado(self, sr, tmp_path):
        (tmp_path / "layer-b-breaker.json").write_text("{ nao e json", encoding="utf-8")
        assert sr.read_breaker(str(tmp_path))["failures"] == 0

    def test_nao_objeto_retorna_zerado(self, sr, tmp_path):
        (tmp_path / "layer-b-breaker.json").write_text("[1,2,3]", encoding="utf-8")
        assert sr.read_breaker(str(tmp_path))["failures"] == 0

    def test_roundtrip(self, sr, tmp_path):
        sr.write_breaker({"failures": 3, "opened_at": 42.0,
                          "last_msg": "x", "last_msg_ts": 7.0}, str(tmp_path))
        st = sr.read_breaker(str(tmp_path))
        assert st["failures"] == 3
        assert st["opened_at"] == 42.0


class TestDbgDedupe:
    def test_repeticao_nao_escreve_no_log(self, sr, tmp_path):
        st = {"failures": 0, "opened_at": 0.0, "last_msg": "", "last_msg_ts": 0.0}
        log = tmp_path / "debug-router.log"

        sr._dbg("layer B degraded: TimeoutError", st, 1000.0, str(tmp_path))
        for i in range(20):
            sr._dbg("layer B degraded: TimeoutError", st, 1000.0 + i, str(tmp_path))

        assert log.read_text(encoding="utf-8").count("TimeoutError") == 1

    def test_sem_estado_sempre_escreve(self, sr, tmp_path):
        """Erros raros (fatal, index load) nao passam pelo dedupe."""
        log = tmp_path / "debug-router.log"
        sr._dbg("fatal: X", None, 1000.0, str(tmp_path))
        sr._dbg("fatal: X", None, 1001.0, str(tmp_path))
        assert log.read_text(encoding="utf-8").count("fatal: X") == 2
