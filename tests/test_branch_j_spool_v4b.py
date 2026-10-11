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


def test_filtro_e_literal_e_bate_com_o_sha_para_o_abrir_do_mh(spool):
    # o `abrir` do mh le PREFIXOS e MIN_LEN por `ast.literal_eval`, sem executar o spool (design, checagem de D2)
    import ast
    import hashlib

    valores = {}
    for no in ast.parse((ROOT / "scripts" / "branch_j_spool.py").read_text(encoding="utf-8")).body:
        if isinstance(no, ast.Assign) and isinstance(no.targets[0], ast.Name) and no.targets[0].id in ("PREFIXOS", "MIN_LEN"):
            valores[no.targets[0].id] = ast.literal_eval(no.value)
    sha = hashlib.sha256(json.dumps([list(valores["PREFIXOS"]), valores["MIN_LEN"]]).encode("utf-8")).hexdigest()
    assert sha == spool.SHA_FILTRO


def test_hook_de_inicio_so_dispara_em_startup_e_clear():
    h = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]["SessionStart"]
    grupos = [g for g in h if any("branch_j_inicio.py" in x["command"] for x in g["hooks"])]
    assert len(grupos) == 1 and grupos[0].get("matcher") == "startup|clear"
