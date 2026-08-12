"""Liveness dos hooks: o CLI host ainda os CHAMA? (2026-07-29)

O smoke-test do health-check prova que os hooks funcionam quando executados. Nao
prova que continuam sendo chamados: se um host renomear ou remover um evento, os
hooks ficam inertes e todo diagnostico permanece verde — a falha silenciosa que
originou a auditoria, um nivel acima.

Cada hook grava `heartbeats/<Evento>` ao ser invocado, antes de qualquer guard.
`check_hook_liveness.py` confronta esse sinal com a atividade de sessao
registrada pelo proprio host.

O equilibrio que estes testes travam: **detectar morte silenciosa sem inventar
alarme**. Um check que reprova instalacao nova, ou que reprova porque uma sessao
nao rodou Bash, seria descartado pelo usuario na primeira semana — e um alarme
ignorado nao vale mais que alarme nenhum.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import ClassVar

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
LIVENESS_PY = ROOT / "scripts" / "check_hook_liveness.py"
HOOKS = ROOT / "hooks"

BASH = "bash"
if sys.platform == "win32":
    for candidate in (
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
        "bash",
    ):
        if Path(candidate).exists() or candidate == "bash":
            BASH = candidate
            break

NOW = 1_800_000_000.0
HOUR = 3600.0
DAY = 86400.0


@pytest.fixture(scope="module")
def hl():
    spec = importlib.util.spec_from_file_location("check_hook_liveness", LIVENESS_PY)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_hook_liveness"] = mod
    spec.loader.exec_module(mod)
    return mod


class TestSemAlarmeFalso:
    """Os casos em que NAO pode haver FAIL. Sao o que mantem o check confiavel."""

    def test_instalacao_nova_nao_reprova(self, hl):
        """Nenhum heartbeat ainda: o mecanismo nao teve chance de rodar."""
        level, msg = hl.verdict("UserPromptSubmit", None, NOW - HOUR, NOW, any_beat=0.0)
        assert level == "INFO"
        assert "nao inicializado" in msg

    def test_evento_condicional_ausente_nao_reprova(self, hl):
        """Uma sessao pode legitimamente nao rodar Bash nem editar arquivo."""
        for event in ("PreToolUse", "PostToolUse", "PreCompact"):
            level, _ = hl.verdict(event, None, NOW - HOUR, NOW, any_beat=NOW - HOUR)
            assert level == "INFO", f"{event} condicional nao pode reprovar"

    def test_evento_condicional_atrasado_nao_reprova(self, hl):
        level, _ = hl.verdict("PreCompact", NOW - 5 * DAY, NOW - HOUR, NOW, any_beat=NOW)
        assert level == "INFO"

    def test_host_sem_uso_nao_reprova(self, hl):
        """Ninguem usa o CLI ha semanas: ausencia de heartbeat nao prova nada."""
        level, msg = hl.verdict("UserPromptSubmit", None, NOW - 30 * DAY, NOW, any_beat=NOW - 30 * DAY)
        assert level == "INFO"
        assert "sem uso" in msg

    def test_sem_atividade_registrada_nao_reprova(self, hl):
        level, _ = hl.verdict("UserPromptSubmit", None, 0.0, NOW, any_beat=NOW)
        assert level == "INFO"

    def test_sessao_longa_dentro_da_folga_nao_reprova(self, hl):
        """Transcript continua sendo escrito enquanto o assistente responde,
        bem depois do prompt que disparou o hook. Sem folga isso seria FAIL."""
        beat = NOW - 2 * HOUR
        activity = beat + hl.GRACE_SECONDS - 60
        level, _ = hl.verdict("UserPromptSubmit", beat, activity, NOW, any_beat=beat)
        assert level == "OK"


class TestDetectaMorteSilenciosa:
    """O que o check existe para pegar."""

    def test_assertivel_que_nunca_disparou_reprova(self, hl):
        """Outros hooks dispararam, este nao: o host nao chama este evento."""
        level, msg = hl.verdict("UserPromptSubmit", None, NOW - HOUR, NOW, any_beat=NOW - HOUR)
        assert level == "FAIL"
        assert "NUNCA disparou" in msg

    def test_assertivel_defasado_reprova(self, hl):
        level, msg = hl.verdict("UserPromptSubmit", NOW - 3 * DAY, NOW - HOUR, NOW, any_beat=NOW)
        assert level == "FAIL"
        assert "parou de chamar" in msg

    def test_session_start_tambem_e_assertivel(self, hl):
        level, _ = hl.verdict("SessionStart", NOW - 3 * DAY, NOW - HOUR, NOW, any_beat=NOW)
        assert level == "FAIL"

    def test_disparo_recente_passa(self, hl):
        level, _ = hl.verdict("UserPromptSubmit", NOW - 60, NOW - 120, NOW, any_beat=NOW)
        assert level == "OK"


class TestMensagensLegiveis:
    def test_sem_caractere_nao_ascii(self, hl):
        """Console do Windows entrega mojibake em travessao — e diagnostico
        ilegivel nao diagnostica."""
        casos = [
            ("UserPromptSubmit", None, NOW - HOUR, 0.0),
            ("UserPromptSubmit", None, NOW - HOUR, NOW - HOUR),
            ("PreCompact", None, NOW - HOUR, NOW),
            ("UserPromptSubmit", NOW - 3 * DAY, NOW - HOUR, NOW),
            ("UserPromptSubmit", NOW - 60, NOW - 120, NOW),
            ("UserPromptSubmit", None, NOW - 30 * DAY, NOW - 30 * DAY),
        ]
        for event, beat, activity, any_beat in casos:
            _, msg = hl.verdict(event, beat, activity, NOW, any_beat=any_beat)
            msg.encode("ascii")  # levanta se houver nao-ASCII


class TestRunEndToEnd:
    def _setup(self, tmp_path: Path, beats: dict[str, float], atividade: float | None) -> tuple[Path, Path]:
        harness = tmp_path / "harness"
        (harness / "heartbeats").mkdir(parents=True)
        for event, ts in beats.items():
            (harness / "heartbeats" / event).write_text(str(ts), encoding="utf-8")

        home = tmp_path / "home"
        proj = home / ".claude" / "projects" / "algum-projeto"
        proj.mkdir(parents=True)
        transcript = proj / "sessao.jsonl"
        transcript.write_text("{}", encoding="utf-8")
        if atividade is not None:
            os.utime(transcript, (atividade, atividade))
        return harness, home

    def test_tudo_disparando_sai_zero(self, hl, tmp_path):
        agora = time.time()
        harness, home = self._setup(
            tmp_path,
            {"UserPromptSubmit": agora - 60, "SessionStart": agora - 60},
            agora - 120,
        )
        code, lines = hl.run(harness, ROOT / "hooks" / "hooks.json", home, agora)
        assert code == 0, "\n".join(lines)

    def test_hook_morto_sai_um(self, hl, tmp_path):
        agora = time.time()
        harness, home = self._setup(
            tmp_path,
            {"UserPromptSubmit": agora - 3 * DAY, "SessionStart": agora - 60},
            agora - 60,
        )
        code, lines = hl.run(harness, ROOT / "hooks" / "hooks.json", home, agora)
        assert code == 1
        assert any("UserPromptSubmit" in ln and "[FAIL]" in ln for ln in lines)

    def test_hooks_json_ilegivel_nao_quebra(self, hl, tmp_path):
        harness, home = self._setup(tmp_path, {}, time.time())
        code, lines = hl.run(harness, tmp_path / "nao-existe.json", home, time.time())
        assert code == 0
        assert any("ilegivel" in ln for ln in lines)


class TestHooksGravamHeartbeat:
    """Integracao: cada hook registrado precisa deixar seu rastro ao ser chamado."""

    CASOS: ClassVar[list[tuple[str, str, dict]]] = [
        ("harness-classify.sh", "UserPromptSubmit", {"prompt": "cria um sistema completo"}),
        ("harness-git-guard.sh", "PreToolUse", {"tool_input": {"command": "ls -la"}}),
        ("harness-reclassify.sh", "PostToolUse", {"tool_input": {"file_path": "/tmp/x.py"}}),
        ("harness-precompact.sh", "PreCompact", {}),
        ("harness-session-start.sh", "SessionStart", {}),
    ]

    @pytest.mark.parametrize("hook,event,payload", CASOS, ids=[c[1] for c in CASOS])
    def test_hook_grava_seu_evento(self, tmp_path, hook, event, payload):
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["HARNESS_SKIP_DEPCHECK"] = "1"
        env["HARNESS_DIR"] = str(tmp_path)
        subprocess.run([BASH, str(HOOKS / hook)], input=json.dumps(payload),
                       capture_output=True, text=True, timeout=60, env=env)

        marca = tmp_path / "heartbeats" / event
        assert marca.is_file(), f"{hook} nao registrou heartbeat de {event}"
        assert float(marca.read_text(encoding="utf-8").strip()) > 0

    def test_heartbeat_gravado_mesmo_com_payload_inutil(self, tmp_path):
        """Mede a CHAMADA, nao o trabalho: prompt vazio ainda e uma chamada."""
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["HARNESS_DIR"] = str(tmp_path)
        subprocess.run([BASH, str(HOOKS / "harness-classify.sh")], input="{}",
                       capture_output=True, text=True, timeout=60, env=env)
        assert (tmp_path / "heartbeats" / "UserPromptSubmit").is_file()

    def test_cobre_todos_os_eventos_registrados(self):
        """Se hooks.json ganhar um evento novo, este teste exige heartbeat nele."""
        with (ROOT / "hooks" / "hooks.json").open(encoding="utf-8") as fh:
            registrados = set(json.load(fh).get("hooks", {}))
        cobertos = {event for _, event, _ in self.CASOS}
        assert registrados <= cobertos, (
            f"eventos sem heartbeat nem teste: {sorted(registrados - cobertos)}"
        )
