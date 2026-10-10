"""Rodada 3 da verify-multimodel sobre a v4: sessao excluida nao volta, e o hook de inicio so roda em startup/clear."""
from __future__ import annotations

import json
import os
from pathlib import Path

from test_branch_j_spool import HUMANO, SID, abrir, inicio, linhas, payload, spool  # noqa: F401

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])


def test_sessao_excluida_nao_volta_mesmo_com_marcador_nova_sobrando(spool):
    p = abrir(spool)
    spool.iniciar_sessao(inicio())
    (p / f"{SID}.excluida").write_text(json.dumps({"excluida": "x"}), encoding="utf-8")
    spool.registrar(payload(HUMANO))
    assert linhas(spool) is None


def test_sessionstart_nao_marca_sessao_excluida(spool):
    p = abrir(spool)
    (p / f"{SID}.excluida").write_text(json.dumps({"excluida": "x"}), encoding="utf-8")
    spool.iniciar_sessao(inicio())
    assert not (p / f"{SID}.nova").exists()


def test_hook_de_inicio_so_dispara_em_startup_e_clear():
    h = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]["SessionStart"]
    grupos = [g for g in h if any("branch_j_inicio.py" in x["command"] for x in g["hooks"])]
    assert len(grupos) == 1 and grupos[0].get("matcher") == "startup|clear"
