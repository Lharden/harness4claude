"""`timeout` do hooks.json e em SEGUNDOS (HC-00g).

Ate 2026-09-23 os valores eram escritos como milissegundos — 10000, 5000,
8000, 15000 —, e a doc oficial (code.claude.com/docs/en/hooks) diz segundos.
Na pratica nenhum hook tinha limite: 10000 s sao 2,8 h.

Dividir por mil, a leitura literal da intencao, mataria hooks que hoje
terminam. Medido nos transcripts desta maquina em 2026-09-23 (`durationMs`):

    SessionStart  harness-session-start.sh  n=3541  p99 18,5 s  max 84 s
    PostToolUse   harness-reclassify.sh     n=5     max 4,98 s
    PostToolUse   harness-transactional.py  n=155   max 4,24 s
    PreToolUse    harness-git-guard.sh      n=69    max 3,19 s

A regra adotada: cerca de 2x o p99 medido, e nunca abaixo do teto interno
que o proprio hook declara. Os dois testes abaixo travam as duas metades:
a unidade, e o teto interno.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
HOOKS_JSON = ROOT / "hooks" / "hooks.json"

#: Acima disto o numero so faz sentido como milissegundo — o defeito.
TETO_PLAUSIVEL_S = 120

#: Folga para spawn do interpretador e leitura de indice antes do trabalho.
FOLGA_SPAWN_S = 3


def _entradas():
    dados = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
    for evento, grupos in dados["hooks"].items():
        for grupo in grupos:
            for hook in grupo["hooks"]:
                yield evento, hook


def _timeout_de(trecho: str, evento: str) -> int:
    achados = [h["timeout"] for ev, h in _entradas()
               if ev == evento and trecho in h["command"]]
    assert achados, f"{trecho} nao registrado em {evento}"
    return min(achados)


def test_todo_timeout_esta_em_segundos():
    ruins = [(ev, h["command"], h.get("timeout")) for ev, h in _entradas()
             if not isinstance(h.get("timeout"), int)
             or not 1 <= h["timeout"] <= TETO_PLAUSIVEL_S]
    assert not ruins, f"timeout fora da escala de segundos: {ruins}"


def test_timeout_cobre_o_teto_interno_de_cada_hook():
    sys.path.insert(0, str(ROOT / "hooks"))
    import skill_router as sr

    session_start = (ROOT / "hooks" / "harness-session-start.sh").read_text(encoding="utf-8")
    subprocesso = max(int(x) for x in re.findall(r"timeout=(\d+)\)", session_start))
    warmup = (ROOT / "hooks" / "harness-router-warmup.sh").read_text(encoding="utf-8")
    curl = int(re.search(r"curl -s -m (\d+)", warmup).group(1))

    tetos = {
        ("UserPromptSubmit", "harness-skill-router.sh"):
            sr.CONNECT_TIMEOUT + sr.EMBED_TIMEOUT + FOLGA_SPAWN_S,
        ("SessionStart", "harness-session-start.sh"): subprocesso + FOLGA_SPAWN_S,
        ("SessionStart", "harness-router-warmup.sh"): curl + FOLGA_SPAWN_S,
    }
    for (evento, trecho), teto in tetos.items():
        assert _timeout_de(trecho, evento) > teto, (
            f"{evento} {trecho}: timeout {_timeout_de(trecho, evento)} s "
            f"nao cobre o teto interno de {teto} s")
