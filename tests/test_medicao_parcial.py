"""Medicao parcial no sinal da task (fix/sinal-medicao-parcial).

Medido em 2026-09-30 (task t-20260930-135419204512, L1-bug): dois arquivos de
OUTRO repositorio editados por caminho absoluto, `code_revision` 23, e o
`signals.json` gravou `files_modified=0`, `actual_level=L0`. Tres elos:

1. `harness-reclassify.sh` descarta Edit/Write fora da raiz do projeto da sessao
   (`counts_as_modified_file`). A exclusao e DESENHO: escrita fora do repo nao pode
   expirar evidencia de teste (`TestFrescuraExpiraPorCODIGO`). Mas o descarte era
   mudo: nem o contador nem a tabela `files` sabiam que algo foi escrito.
2. `record_signal.build_task` so marcava `atribuicao_incompleta` por shell sem
   arquivo atribuido. Escrita fora da raiz sem nenhum shell saia L0 sem marca.
3. `recompute_aggregates` ignorava a marca: a task parcial entrava no canario
   `proxy_regex_vs_observado` como se L0 fosse fato.

O conserto registra a medicao parcial (causa nomeada) e NAO mexe na exclusao.
As duas metades: o que e parcial deixa de valer como fato; o que o guarda pega
hoje continua sendo pego.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
sys.path.insert(0, str(ROOT / "scripts"))

BASH = shutil.which("bash")
requer_bash = pytest.mark.skipif(BASH is None, reason="bash nao encontrado no PATH")


def _carrega(nome: str):
    spec = importlib.util.spec_from_file_location(nome, ROOT / "scripts" / f"{nome}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------
# Elo 1 — o hook registra o descarte, sem mudar o que o guarda pega
# --------------------------------------------------------------------------
@pytest.fixture()
def ambiente(tmp_path):
    from harness_paths import ensure_state_dir
    from transactional_state import HarnessDatabase

    harness = tmp_path / "harness"
    projeto = tmp_path / "projeto"
    outro = tmp_path / "outro-repo"
    for repo in (projeto, outro):
        (repo / ".git").mkdir(parents=True)
    harness.mkdir()
    balde = Path(str(ensure_state_dir(str(harness), str(projeto))))
    task_id = "t-parcial"
    banco = HarnessDatabase(balde)
    banco.start_task(
        scope_id=str(balde), legacy_level="L1-bug", tier="L1", kind="bug",
        pipeline=["systematic-debugging", "tdd", "verify"], prompt="teste", task_id=task_id,
    )
    (balde / "state.json").write_text(json.dumps({
        "task_id": task_id, "classification": "L1-bug", "status": "active",
        "pipeline": ["systematic-debugging", "tdd", "verify"],
    }), encoding="utf-8")
    (balde / ".session-files-count").write_text(
        json.dumps({"count": 0, "files": [], "task_id": task_id}), encoding="utf-8")
    return {"harness": harness, "projeto": projeto, "outro": outro,
            "balde": balde, "task_id": task_id, "banco": banco}


def _edita(amb, alvo: Path) -> None:
    assert BASH is not None
    proc = subprocess.run(
        [BASH,str(ROOT / "hooks" / "harness-reclassify.sh")],
        input=json.dumps({"cwd": str(amb["projeto"]), "tool_name": "Edit",
                          "tool_input": {"file_path": str(alvo)}}),
        capture_output=True, text=True, timeout=60,
        env={**os.environ, "PYTHONUTF8": "1",
             "HARNESS_DIR": str(amb["harness"]).replace(os.sep, "/")},
    )
    assert proc.returncode == 0, proc.stderr


def _contador(amb) -> dict:
    return json.loads((amb["balde"] / ".session-files-count").read_text(encoding="utf-8"))


@requer_bash
def test_edicao_fora_da_raiz_fica_registrada_como_fora_da_raiz(ambiente):
    alvo = ambiente["outro"] / "hooks" / "x.py"
    _edita(ambiente, alvo)
    assert _contador(ambiente).get("fora_da_raiz") == [str(alvo)]


@requer_bash
def test_edicao_fora_da_raiz_continua_sem_expirar_evidencia(ambiente):
    """Guarda preservado: nao e `files`, nao e contador, nao sobe code_revision."""
    antes = ambiente["banco"].task(ambiente["task_id"])["code_revision"]
    _edita(ambiente, ambiente["outro"] / "x.py")
    assert ambiente["banco"].task(ambiente["task_id"])["code_revision"] == antes
    assert ambiente["banco"].files(ambiente["task_id"]) == []
    assert _contador(ambiente)["count"] == 0


@requer_bash
def test_edicao_dentro_da_raiz_continua_contando_e_nao_marca_fora(ambiente):
    alvo = ambiente["projeto"] / "x.py"
    _edita(ambiente, alvo)
    contador = _contador(ambiente)
    assert contador["count"] == 1
    assert not contador.get("fora_da_raiz")
    assert ambiente["banco"].files(ambiente["task_id"]) != []


# --------------------------------------------------------------------------
# Elo 2 — record_signal nomeia a causa
# --------------------------------------------------------------------------
def test_build_task_marca_parcial_por_escrita_fora_da_raiz(tmp_path):
    rec = _carrega("record_signal")
    state = {"task_id": "t-x", "classification": "L1-bug", "classification_meta": {}}
    counter = {"count": 0, "files": [], "task_id": "t-x", "fora_da_raiz": ["/outro/a.py", "/outro/b.py"]}
    task = rec.build_task(state, counter, completed=True, steps=["tdd"], reason=None,
                          timestamp="2026-09-30T00:00:00+00:00", harness_dir=tmp_path)
    assert task["atribuicao_incompleta"] is True
    assert task["fora_da_raiz"] == 2


def test_build_task_ignora_fora_da_raiz_de_outra_task(tmp_path):
    """O contador tem um slot so; o de outra task nao e desta."""
    rec = _carrega("record_signal")
    state = {"task_id": "t-x", "classification": "L1-bug", "classification_meta": {}}
    counter = {"count": 0, "files": [], "task_id": "t-outra", "fora_da_raiz": ["/outro/a.py"]}
    task = rec.build_task(state, counter, completed=True, steps=[], reason=None,
                          timestamp="2026-09-30T00:00:00+00:00", harness_dir=tmp_path)
    assert task["atribuicao_incompleta"] is False
    assert task["fora_da_raiz"] == 0


# --------------------------------------------------------------------------
# Elo 3 — o canario nao trata medicao parcial como fato
# --------------------------------------------------------------------------
def _task(suggested: str, actual: str, *, incompleta: bool) -> dict:
    return {"task_id": f"t-{suggested}-{actual}-{incompleta}", "classification": suggested,
            "classification_meta": {"suggested": suggested}, "actual_level": actual,
            "atribuicao_incompleta": incompleta, "files_modified": 0}


def test_canario_exclui_task_parcial_que_leu_abaixo_de_L2():
    ms = _carrega("migrate_state")
    tasks = [_task("L1-bug", "L0", incompleta=True), _task("L1-bug", "L1", incompleta=False)]
    classify = ms.recompute_aggregates(tasks)["classify"]
    assert classify["proxy_amostras"] == 1
    assert classify["proxy_excluidas_parciais"] == 1
    assert classify["proxy_regex_vs_observado"] == 1.0


def test_canario_mantem_parcial_que_ja_chegou_a_L2():
    """Piso: com medicao parcial, L2 ja observado nao pode diminuir."""
    ms = _carrega("migrate_state")
    classify = ms.recompute_aggregates([_task("L2-bug", "L2", incompleta=True)])["classify"]
    assert classify["proxy_amostras"] == 1
    assert classify["proxy_excluidas_parciais"] == 0


def test_canario_sem_marca_continua_como_antes():
    ms = _carrega("migrate_state")
    tasks = [_task("L1-bug", "L0", incompleta=False), _task("L0-bug", "L0", incompleta=False)]
    classify = ms.recompute_aggregates(tasks)["classify"]
    assert classify["proxy_amostras"] == 2
    assert classify["proxy_excluidas_parciais"] == 0
    assert classify["proxy_regex_vs_observado"] == 0.5
