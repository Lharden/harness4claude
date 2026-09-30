"""O sinal da task substituida sai do banco, pelo id — e o abandono tambem.

Depois de `fix/sim-nao-fecha-entrega`, a projecao `state.json` descreve sempre a
task VIVA do escopo. O `record_signal --expect-task <substituida>` lia a task da
projecao, achava a viva e saia 2: o desfecho da substituida ficava no
`harness.db` e a linha dela nunca chegava a `signals.json`. E `--abandoned`
encerrava a task DA PROJECAO — numa troca de assunto, a que o usuario acabou de
pedir.

Diagnostico, grill e plano de teste (S1-S8):
`docs/specs/sinal-da-task-substituida-diagnostico.md`.

Cada caso nomeia o que reprova contra o codigo de antes; S6 e controle e passa
nos dois.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
sys.path.insert(0, str(ROOT / "scripts"))

from transactional_state import TERMINAL_STATUSES, HarnessDatabase

ENV_UTF8 = {**os.environ, "PYTHONUTF8": "1"}
PIPELINE_L1_BUG = ["systematic-debugging", "tdd", "verify"]
PIPELINE_L2_FEATURE = ["discuss", "brainstorming", "write-spec", "tdd", "verify-multimodel"]
#: O marcador que a linha copiavel leva no lugar do id. So o chamador sabe qual
#: task quer encerrar; o id que o script tem a mao e o da projecao.
MARCADOR = "<task_id anotado no inicio do pipeline>"


def _run(balde: Path, *argumentos: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "record_signal.py"), *argumentos,
         "--harness-dir", str(balde), "--signals-dir", str(balde / "sinais")],
        capture_output=True, text=True, timeout=60, encoding="utf-8", env=ENV_UTF8,
    )


def _sinais(balde: Path) -> list[dict]:
    caminho = balde / "sinais" / "signals.json"
    return json.loads(caminho.read_text(encoding="utf-8"))["tasks"] if caminho.exists() else []


def _projecao(task: dict, meta: dict) -> dict:
    return {"task_id": task["task_id"], "schema_version": 3,
            "classification": task["legacy_level"], "classification_meta": meta,
            "status": task["status"], "pipeline": task["pipeline"],
            "prompt_excerpt": "implementa o sistema de filas"}


def _gravar(balde: Path, nome: str, dado: dict) -> bytes:
    (balde / "sinais").mkdir(exist_ok=True)
    corpo = json.dumps(dado, ensure_ascii=False, indent=2).encode("utf-8")
    (balde / nome).write_bytes(corpo)
    return corpo


def _substituida_e_viva(balde: Path) -> tuple[dict, dict]:
    """A: L2-docs corrigida para L1-bug, com dois arquivos de Edit no banco, e
    substituida por B (L2-feature viva, um arquivo). A projecao e o contador sao
    de B, como o classify os deixa ao abrir a task nova."""
    db = HarnessDatabase(balde)
    a = db.start_task(scope_id=str(balde), legacy_level="L2-docs", tier="L2", kind="docs",
                      pipeline=["source-selection", "documentation", "verify"],
                      prompt="documenta o parser", task_id="t-20260930-100000000001")
    db.confirm_classification(a["task_id"], tier="L1", kind="bug", pipeline=PIPELINE_L1_BUG,
                              source="semantic", confidence=0.8)
    db.touch_files(a["task_id"], ["scripts/a1.py", "tests/test_a1.py"], origem="edit")
    b = db.start_task(scope_id=str(balde), legacy_level="L2-feature", tier="L2", kind="feature",
                      pipeline=PIPELINE_L2_FEATURE, prompt="implementa o sistema de filas",
                      task_id="t-20260930-110000000002")
    db.touch_files(b["task_id"], ["scripts/fila.py"], origem="edit")
    a, b = db.task(a["task_id"]), db.task(b["task_id"])
    assert a["status"] in TERMINAL_STATUSES, "premissa: A saiu do caminho"
    assert b["status"] == "active", "premissa: B e a viva"
    return a, b


def _balde_da_viva(balde: Path) -> tuple[dict, dict, bytes, bytes]:
    a, b = _substituida_e_viva(balde)
    projecao = _gravar(balde, "state.json", _projecao(b, HarnessDatabase(balde).classification(b["task_id"])))
    contador = _gravar(balde, ".session-files-count",
                       {"count": 2, "files": ["scripts/fila.py", "docs/fila.md"], "task_id": b["task_id"]})
    return a, b, projecao, contador


class TestRegistroPeloBanco:
    def test_S1_substituida_registra_com_o_proprio_meta(self, tmp_path):
        """Antes: exit 2, nada gravado."""
        a, _, _, _ = _balde_da_viva(tmp_path)

        res = _run(tmp_path, "--completed", "--expect-task", a["task_id"])

        assert res.returncode == 0, res.stdout + res.stderr
        [gravada] = _sinais(tmp_path)
        db = HarnessDatabase(tmp_path)
        assert gravada["task_id"] == a["task_id"]
        assert gravada["classification"] == "L1-bug"
        assert gravada["classification_meta"] == db.classification(a["task_id"])
        assert gravada["classification_meta"]["suggested"] == "L2-docs"
        assert gravada["steps_executed"] == PIPELINE_L1_BUG, "sem --steps, o pipeline vem do banco"
        assert gravada["pipeline_completed"] is True

    def test_S2_contador_da_viva_fica_fora_e_intocado(self, tmp_path):
        """Os arquivos do contador sao de B; somados, A viraria L2 com 4 arquivos.
        Antes: exit 2."""
        a, b, projecao, contador = _balde_da_viva(tmp_path)
        viva_antes = HarnessDatabase(tmp_path).task(b["task_id"])

        res = _run(tmp_path, "--completed", "--expect-task", a["task_id"])

        assert res.returncode == 0, res.stdout + res.stderr
        [gravada] = _sinais(tmp_path)
        assert gravada["files_modified"] == 2
        assert gravada["actual_level"] == "L1"
        assert gravada["contador_usado"] is False
        assert (tmp_path / "state.json").read_bytes() == projecao, "NEVER: escrever a projecao"
        assert (tmp_path / ".session-files-count").read_bytes() == contador, "NEVER: escrever o contador"
        assert HarnessDatabase(tmp_path).task(b["task_id"]) == viva_antes

    def test_S3_contador_da_propria_task_entra_na_uniao(self, tmp_path):
        """Antes: o registro nao tinha `contador_usado`."""
        a, _, _, _ = _balde_da_viva(tmp_path)
        _gravar(tmp_path, ".session-files-count",
                {"count": 2, "files": ["scripts/a1.py", "docs/a1.md"], "task_id": a["task_id"]})

        res = _run(tmp_path, "--completed", "--expect-task", a["task_id"])

        assert res.returncode == 0, res.stdout + res.stderr
        [gravada] = _sinais(tmp_path)
        assert gravada["files_modified"] == 3, "banco {a1, test_a1} uniao contador {a1, a1.md}"
        assert gravada["contador_usado"] is True

    @pytest.mark.parametrize("contador", [None, {"count": 1, "files": ["x.py"]}, "nao e json"],
                             ids=["ausente", "sem-task_id", "ilegivel"])
    def test_S3b_contador_sem_dono_nao_e_usado(self, tmp_path, contador):
        a, _, _, _ = _balde_da_viva(tmp_path)
        caminho = tmp_path / ".session-files-count"
        if contador is None:
            caminho.unlink()
        elif isinstance(contador, str):
            caminho.write_text(contador, encoding="utf-8")
        else:
            _gravar(tmp_path, ".session-files-count", contador)

        res = _run(tmp_path, "--completed", "--expect-task", a["task_id"])

        assert res.returncode == 0, res.stdout + res.stderr
        [gravada] = _sinais(tmp_path)
        assert (gravada["files_modified"], gravada["contador_usado"]) == (2, False)


class TestAbandonoExigeATask:
    @pytest.mark.parametrize("extra", [[], ["--expect-task", ""], ["--expect-task", "  "]],
                             ids=["sem-flag", "vazia", "so-espaco"])
    def test_S4_abandono_sem_task_esperada_recusa(self, tmp_path, extra):
        """Antes: exit 0 e a viva — a task que o usuario acabou de pedir —
        encerrada como `abandoned`."""
        a, b, projecao, _ = _balde_da_viva(tmp_path)

        res = _run(tmp_path, "--abandoned", "--reason", "user_switch", *extra)

        assert res.returncode == 2, res.stdout + res.stderr
        assert HarnessDatabase(tmp_path).task(b["task_id"])["status"] == "active", "a viva segue viva"
        assert _sinais(tmp_path) == [], "nada gravado"
        assert (tmp_path / "state.json").read_bytes() == projecao
        [linha] = [l for l in res.stderr.splitlines() if "record_signal.py" in l and "--abandoned" in l]
        assert f'--expect-task "{MARCADOR}"' in linha, "a linha que corrige tem de ser copiavel"
        assert b["task_id"] not in linha, "o id da projecao e o da viva: copia-lo repete o defeito"
        assert a["task_id"] in res.stderr, "quem perdeu o id acha a substituida na lista do banco"

    def test_S5_abandono_vai_para_a_task_esperada(self, tmp_path):
        """Projecao velha apontando para A (ja terminal); a esperada e B.
        Antes: exit 2 (projecao != esperada) e B seguia viva."""
        a, b, _, _ = _balde_da_viva(tmp_path)
        projecao = _gravar(tmp_path, "state.json", _projecao(a, HarnessDatabase(tmp_path).classification(a["task_id"])))
        a_antes = HarnessDatabase(tmp_path).task(a["task_id"])

        res = _run(tmp_path, "--abandoned", "--reason", "user_switch", "--expect-task", b["task_id"])

        assert res.returncode == 0, res.stdout + res.stderr
        db = HarnessDatabase(tmp_path)
        assert db.task(b["task_id"])["status"] == "abandoned"
        assert db.task(a["task_id"]) == a_antes, "a task da projecao nao e tocada"
        assert (tmp_path / "state.json").read_bytes() == projecao
        [gravada] = _sinais(tmp_path)
        assert (gravada["task_id"], gravada["reason"]) == (b["task_id"], "user_switch")

    def test_S8_abandono_de_task_fora_do_banco(self, tmp_path):
        """Banco sem a task, projecao dela: vale a projecao, e o banco nao e
        tocado. Antes: sinal gravado e exit 1 (`abandon_task` levantava)."""
        HarnessDatabase(tmp_path)
        _gravar(tmp_path, "state.json", {"task_id": "t-so-na-projecao", "classification": "L1-bug",
                                         "status": "active", "pipeline": PIPELINE_L1_BUG})

        res = _run(tmp_path, "--abandoned", "--reason", "user_switch", "--expect-task", "t-so-na-projecao")

        assert res.returncode == 0, res.stdout + res.stderr
        assert [t["task_id"] for t in _sinais(tmp_path)] == ["t-so-na-projecao"]
        with pytest.raises(Exception, match="task not found"):
            HarnessDatabase(tmp_path).task("t-so-na-projecao")


class TestForaDoCaminhoDoBanco:
    def test_S6_controle_banco_sem_a_task_vale_a_projecao(self, tmp_path):
        """Controle: passa antes e depois."""
        HarnessDatabase(tmp_path)
        _gravar(tmp_path, "state.json", {"task_id": "t-velha", "classification": "L1-bug",
                                         "classification_meta": {"suggested": "L1-bug"},
                                         "status": "active", "pipeline": PIPELINE_L1_BUG})

        res = _run(tmp_path, "--completed", "--expect-task", "t-velha")
        assert res.returncode == 0, res.stdout + res.stderr
        [gravada] = _sinais(tmp_path)
        assert (gravada["classification"], gravada["classification_meta"]) == ("L1-bug", {"suggested": "L1-bug"})
        assert "contador_usado" not in gravada, "o campo e do caminho do banco"

        res = _run(tmp_path, "--completed", "--expect-task", "t-outra")
        assert res.returncode == 2, res.stdout + res.stderr
        assert [t["task_id"] for t in _sinais(tmp_path)] == ["t-velha"]

    def test_S7_banco_ilegivel_nao_grava(self, tmp_path):
        """"Nao consegui perguntar" nao vira "grava o que a projecao disser".
        Antes: exit 0, gravado pela projecao."""
        (tmp_path / "harness.db").write_bytes(b"isto nao e um banco sqlite" * 100)
        _gravar(tmp_path, "state.json", {"task_id": "t-x", "classification": "L1-bug",
                                         "status": "active", "pipeline": PIPELINE_L1_BUG})

        res = _run(tmp_path, "--completed", "--expect-task", "t-x")

        assert res.returncode == 1, res.stdout + res.stderr
        assert _sinais(tmp_path) == []
        assert "harness.db" in res.stderr
