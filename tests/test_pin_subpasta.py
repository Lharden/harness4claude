"""Subpasta de diretorio sem repositorio nao e outro projeto (incidente 2026-10-02).

Sessao `b46f67bc`, projeto `C:\\Users\\LHarden2\\Documents\\lojas` (sem `.git`).
O pin da sessao estava em `lojas-755c1f1a` desde 2026-09-28; o agente resolveu
o balde em 2026-09-30 e passou a usar o caminho literal, como manda o protocolo
("resolvido uma vez por sessao"). Dois dias depois, a sessao voltou com o shell
em `lojas\\cardapio`:

    2026-09-30T19:24  last_seen_at do pin (lojas-755c1f1a)
    2026-10-02T16:21  classify: payload cwd = lojas\\cardapio, pin parado 45 h
                      -> repin para cardapio-c4581090; L0 nasce LA
    2026-10-02T16:26  reclassify: 3 arquivos, L0 -> L1 no balde cardapio
    2026-10-02T16:27  agente: confirm_classification --harness-dir lojas-755c1f1a
                      -> "state.json contem task None", exit 2
    depois            init manual no balde lojas; record_signal la: files=0, L0

A task nunca deixou de existir: estava inteira no balde `cardapio`, promovida,
com 45 toques. O agente procurou no balde que o pin tinha acabado de abandonar.

O repin existe para a sessao retomada dias depois em OUTRO projeto (incidente
2026-09-21, ver `harness_paths.py`). Em diretorio sem repositorio, o slug e o
proprio diretorio, entao `cd cardapio` parecia troca de projeto. Nao e: a
pasta esta dentro do diretorio fixado. Medido em 2026-10-02 nos 193 pins da
maquina: esse foi o UNICO repin ja gravado, e era falso.

Este teste roda os hooks reais na ordem do incidente e pergunta ao balde que o
agente resolveu se a task esta la.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
SCRIPTS = ROOT / "scripts"
HOOKS = ROOT / "hooks"
sys.path.insert(0, str(SCRIPTS))

BASH = shutil.which("bash")
requer_bash = pytest.mark.skipif(BASH is None, reason="bash nao encontrado no PATH")

SESSAO = "b46f67bc-7cc2-4794-af23-2cc855cdfa26"


def _env(harness: Path) -> dict[str, str]:
    env = {**os.environ, "PYTHONUTF8": "1", "HARNESS_SKIP_DEPCHECK": "1",
           "HARNESS_PIN_TTL_H": "24",
           "HARNESS_DIR": str(harness).replace(os.sep, "/")}
    env.pop("HARNESS_SCOPE", None)
    return env


def _hook(nome: str, payload: dict, harness: Path) -> str:
    assert BASH is not None
    proc = subprocess.run(
        [BASH, str(HOOKS / nome)], input=json.dumps(payload),
        capture_output=True, text=True, timeout=60, env=_env(harness),
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def _script(args: list[str], harness: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, *args], capture_output=True, text=True,
                          timeout=60, env=_env(harness))


@pytest.fixture()
def incidente(tmp_path):
    """O estado de 2026-10-02T16:21, um instante antes do prompt L0."""
    harness = tmp_path / "harness"
    lojas = tmp_path / "lojas"
    cardapio = lojas / "cardapio"
    cardapio.mkdir(parents=True)
    harness.mkdir()

    # 2026-09-30: a sessao trabalha em `lojas`, e o agente resolve o balde com o
    # cwd do projeto e guarda o caminho.
    _hook("harness-classify.sh", {"user_prompt": "o que e um decorator?",
                                  "cwd": str(lojas), "session_id": SESSAO}, harness)
    res = _script([str(SCRIPTS / "harness_paths.py"), "--cwd", str(lojas),
                   "--session-id", SESSAO], harness)
    assert res.returncode == 0, res.stderr
    balde_do_agente = Path(res.stdout.strip())

    # A sessao fica parada 45 h, como o pin real (19:24 de 30/09 a 16:21 de 02/10).
    import harness_paths

    pin_path = harness / "pins" / f"{harness_paths.session_slug(SESSAO)}.json"
    pin = json.loads(pin_path.read_text(encoding="utf-8"))
    parado = (datetime.now(timezone.utc) - timedelta(hours=45)).isoformat()
    pin["pinned_at"] = pin["last_seen_at"] = parado
    pin_path.write_text(json.dumps(pin), encoding="utf-8")

    return {"harness": harness, "lojas": lojas, "cardapio": cardapio,
            "balde": balde_do_agente, "pin": pin_path, "tmp": tmp_path}


def _prompt_l0_e_tres_edicoes(amb) -> str:
    """O prompt L0 e os 3 Edit, com o shell da sessao em `cardapio`."""
    saida = _hook("harness-classify.sh", {
        "user_prompt": "o que e um singleton?", "cwd": str(amb["cardapio"]),
        "session_id": SESSAO,
    }, amb["harness"])
    assert "level: L0" in saida, saida
    achado = re.search(r"task_id: (t-\S+)", saida)
    assert achado is not None, saida
    task_id = achado.group(1)

    ultima = ""
    for nome in ("simular_precos.py", "testar_simulador.py", "gerar_relatorio.py"):
        ultima = _hook("harness-reclassify.sh", {
            "session_id": SESSAO, "cwd": str(amb["cardapio"]), "tool_name": "Edit",
            "tool_input": {"file_path": str(amb["cardapio"] / nome)},
        }, amb["harness"])
    assert "<harness-reclassification>" in ultima, "a promocao L0 -> L1 nao disparou"
    return task_id


@requer_bash
def test_task_promovida_esta_no_balde_que_o_agente_resolveu(incidente):
    """Passos 1-3 do incidente: a task tem de estar onde o agente vai procurar."""
    from transactional_state import HarnessDatabase

    task_id = _prompt_l0_e_tres_edicoes(incidente)
    balde = incidente["balde"]

    estado = json.loads((balde / "state.json").read_text(encoding="utf-8"))
    assert estado.get("task_id") == task_id, (
        f"state.json do balde do agente contem task {estado.get('task_id')!r}: "
        "o hook gravou a task em outro balde da mesma sessao"
    )
    linha = HarnessDatabase(balde).task(task_id)
    assert linha["legacy_level"] == "L1-feature"
    assert linha["status"] == "active"


@requer_bash
def test_confirmacao_e_sinal_funcionam_no_balde_do_agente(incidente):
    """Passos 3 e 5: confirm sai 0 e o sinal registra o nivel e os arquivos reais."""
    task_id = _prompt_l0_e_tres_edicoes(incidente)
    balde = incidente["balde"]

    confirm = _script([str(SCRIPTS / "confirm_classification.py"), "--final", "L1-feature",
                       "--expect-task", task_id, "--harness-dir", str(balde)],
                      incidente["harness"])
    assert confirm.returncode == 0, confirm.stderr

    sinais = incidente["tmp"] / "sinais"
    sinais.mkdir()
    registro = _script([str(SCRIPTS / "record_signal.py"), "--completed",
                        "--expect-task", task_id, "--harness-dir", str(balde),
                        "--signals-dir", str(sinais)], incidente["harness"])
    assert registro.returncode == 0, registro.stderr
    tasks = json.loads((sinais / "signals.json").read_text(encoding="utf-8"))["tasks"]
    gravada = next(t for t in tasks if t["task_id"] == task_id)
    assert gravada["files_modified"] == 3, gravada
    assert gravada["actual_level"] == "L1", gravada
    assert gravada["classification"] == "L1-feature", gravada


@requer_bash
def test_o_pin_segue_no_projeto_e_a_subpasta_fica_como_deriva(incidente):
    """O conserto nao pode ser mudo: a ida a `cardapio` continua registrada."""
    import harness_paths

    _prompt_l0_e_tres_edicoes(incidente)
    pin = json.loads(incidente["pin"].read_text(encoding="utf-8"))
    assert pin["project_slug"] == harness_paths.project_slug(str(incidente["lojas"]))
    assert not pin.get("repins"), "a subpasta repinou a sessao"
    derivas = [d["project_slug"] for d in pin.get("drifts", [])]
    assert harness_paths.project_slug(str(incidente["cardapio"])) in derivas
