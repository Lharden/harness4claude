"""O agente usa o balde resolvido ANTES de um repin genuino (incidente 2026-10-02).

O protocolo manda o agente resolver o balde uma vez por sessao e reusar o
caminho literal (`sync/templates/claude-md.harness.snippet.md`, skill
`harness-workflow`). O pin de sessao repina quando ficou parado alem de
`HARNESS_PIN_TTL_H` *e* o projeto corrente e outro (incidente 2026-09-21). Depois
do repin os hooks gravam a task no balde novo, e o agente segue operando o
velho. Medido na sessao `b46f67bc`:

    confirm_classification   -> "state.json contem task None"
    agente                   -> recria a task a mao no balde velho
    record_signal            -> files=0, actual_level L0

O conserto nao muda o protocolo: o agente aprende que o balde mudou no primeiro
comando que ele roda no balde velho. `state_cli.py` e `record_signal.py` recusam
o balde que o pin da sessao deixou, nomeando o balde atual e a data do repin.

Os testes vem em duas metades, e as duas sao necessarias:

- **pega** — o balde abandonado e recusado com o caminho novo na mensagem;
- **nao pega** — o balde atual, a task que ja vivia no balde velho antes do
  repin, e o balde que so viu deriva continuam funcionando.

Um guarda que recusa tudo passa na primeira metade; um que nao recusa nada
passa na segunda. So o guarda certo passa nas duas.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
SCRIPTS = ROOT / "scripts"
PATHS_PY = SCRIPTS / "harness_paths.py"
STATE_CLI = SCRIPTS / "state_cli.py"
RECORD = SCRIPTS / "record_signal.py"
SESSAO = "b46f67bc-7cc2-4794-af23-2cc855cdfa26"


@pytest.fixture(scope="module")
def hp():
    spec = importlib.util.spec_from_file_location("harness_paths", PATHS_PY)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["harness_paths"] = mod
    spec.loader.exec_module(mod)
    return mod


def _repo_git(base: Path, nome: str) -> Path:
    """Um repositorio git de verdade: o repin exige projeto OUTRO, nao subpasta."""
    d = base / nome
    d.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(d)], check=True, capture_output=True)
    return d


def _env(root: Path) -> dict:
    env = dict(os.environ)
    env["HARNESS_DIR"] = str(root)
    env["HARNESS_PIN_TTL_H"] = "24"
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("HARNESS_SCOPE", None)
    return env


def _roda(root: Path, script: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True, text=True, encoding="utf-8", env=_env(root), timeout=60,
    )


def _resolve(root: Path, cwd: Path) -> str:
    """O que o agente (e o hook) obtem do CLI, como texto literal."""
    res = _roda(root, PATHS_PY, "--cwd", str(cwd), "--session-id", SESSAO)
    assert res.returncode == 0, res.stderr
    return res.stdout.strip()


def _init(root: Path, balde: str, task: str, classificacao: str = "L2-bug"):
    return _roda(root, STATE_CLI, "--home", balde, "init", "--scope", "escopo",
                 "--task", task, "--classification", classificacao)


def _envelhece_pin(root: Path, hp, horas: float = 25) -> None:
    arquivo = root / hp.PINS_SUBDIR / f"{hp.session_slug(SESSAO)}.json"
    pin = json.loads(arquivo.read_text(encoding="utf-8"))
    antigo = (datetime.now(timezone.utc) - timedelta(hours=horas)).isoformat()
    pin["pinned_at"] = pin["last_seen_at"] = antigo
    arquivo.write_text(json.dumps(pin), encoding="utf-8")


def _pin(root: Path, hp) -> dict:
    arquivo = root / hp.PINS_SUBDIR / f"{hp.session_slug(SESSAO)}.json"
    return json.loads(arquivo.read_text(encoding="utf-8"))


def _tasks(balde: str) -> set[str]:
    db = Path(balde) / "harness.db"
    if not db.is_file():
        return set()
    with sqlite3.connect(db) as c:
        return {r[0] for r in c.execute("SELECT task_id FROM tasks")}


@pytest.fixture()
def cena(hp, tmp_path):
    """Pin parado alem do TTL, projeto genuinamente outro, agente com o caminho velho.

    1. o agente resolve o balde em `lojas` e anota o caminho (`velho`);
    2. uma task nasce no balde velho, antes do repin (`t-antes`);
    3. a sessao fica parada 25 h;
    4. o hook do prompt seguinte resolve em `harness4claude` e repina (`novo`);
    5. o hook cria a task do prompt no balde novo (`t-depois`).
    """
    root = tmp_path / "harness"
    lojas = _repo_git(tmp_path, "lojas")
    harness = _repo_git(tmp_path, "harness4claude")

    velho = _resolve(root, lojas)
    assert _init(root, velho, "t-antes", "L1-feature").returncode == 0
    _envelhece_pin(root, hp)

    novo = _resolve(root, harness)
    # Pre-condicao: o repin aconteceu de verdade. Sem isto o teste passaria
    # por nao haver o que recusar.
    assert Path(novo) != Path(velho)
    pin = _pin(root, hp)
    assert pin["project_slug"] == hp.project_slug(str(harness))
    repinned_at = pin["repins"][-1]["repinned_at"]

    assert _init(root, novo, "t-depois").returncode == 0
    return {"root": root, "velho": velho, "novo": novo, "repinned_at": repinned_at,
            "lojas": lojas, "harness": harness}


class TestPega:
    """O balde que o pin deixou e recusado, nomeando o balde atual."""

    def test_recriar_a_task_a_mao_no_balde_velho_e_recusado(self, cena):
        res = _init(cena["root"], cena["velho"], "t-depois")
        saida = res.stdout + res.stderr

        assert res.returncode == 2, saida
        assert cena["novo"] in saida
        assert cena["repinned_at"] in saida
        assert "t-depois" not in _tasks(cena["velho"])

    def test_record_signal_no_balde_velho_e_recusado(self, cena):
        # O estado do incidente: a task ja foi recriada a mao no balde velho
        # (pelo state_cli de antes do conserto). O registro e a ultima chance
        # de nao gravar files=0 / L0 em signals.json.
        with sqlite3.connect(Path(cena["novo"]) / "harness.db") as c:
            c.row_factory = sqlite3.Row
            linha = dict(c.execute("SELECT * FROM tasks WHERE task_id='t-depois'").fetchone())
        with sqlite3.connect(Path(cena["velho"]) / "harness.db") as c:
            c.execute("UPDATE tasks SET status='done' WHERE status='active'")
            colunas = ", ".join(linha)
            marcas = ", ".join("?" for _ in linha)
            c.execute(f"INSERT INTO tasks({colunas}) VALUES ({marcas})", tuple(linha.values()))

        res = _roda(cena["root"], RECORD, "--completed", "--steps", "tdd",
                    "--expect-task", "t-depois", "--harness-dir", cena["velho"],
                    "--signals-dir", str(cena["root"]))
        saida = res.stdout + res.stderr

        assert res.returncode == 2, saida
        assert cena["novo"] in saida
        assert cena["repinned_at"] in saida
        sinais = cena["root"] / "signals.json"
        if sinais.is_file():
            ids = [t.get("task_id") for t in json.loads(sinais.read_text(encoding="utf-8")).get("tasks", [])]
            assert "t-depois" not in ids

    def test_operar_a_task_nova_pelo_balde_velho_e_recusado(self, cena):
        res = _roda(cena["root"], STATE_CLI, "--home", cena["velho"], "artifact",
                    "--task", "t-depois", "--type", "spec", "--path", "x.md")
        saida = res.stdout + res.stderr

        assert res.returncode == 2, saida
        assert cena["novo"] in saida


class TestNaoPega:
    """O guarda nao pode recusar o que esta certo."""

    def test_o_balde_atual_funciona(self, cena):
        res = _roda(cena["root"], STATE_CLI, "--home", cena["novo"], "artifact",
                    "--task", "t-depois", "--type", "spec", "--path", "x.md")
        assert res.returncode == 0, res.stdout + res.stderr

    def test_a_task_anterior_ao_repin_segue_operavel_no_balde_velho(self, cena):
        # `t-antes` nasceu no balde velho quando ele ainda era o da sessao. O
        # repin nao a move; recusar o registro dela a tornaria irregistravel.
        res = _roda(cena["root"], STATE_CLI, "--home", cena["velho"], "artifact",
                    "--task", "t-antes", "--type", "spec", "--path", "x.md")
        assert res.returncode == 0, res.stdout + res.stderr

        res = _roda(cena["root"], RECORD, "--abandoned", "--reason", "repin",
                    "--expect-task", "t-antes", "--harness-dir", cena["velho"],
                    "--signals-dir", str(cena["root"]))
        assert res.returncode == 0, res.stdout + res.stderr

    def test_deriva_sem_repin_nao_recusa(self, hp, tmp_path):
        # Mesmo cd de projeto, mas sem o pin ter ficado parado: deriva. O balde
        # nao muda, e o caminho anotado continua certo.
        root = tmp_path / "harness"
        lojas = _repo_git(tmp_path, "lojas")
        outro = _repo_git(tmp_path, "outro")
        velho = _resolve(root, lojas)
        assert _resolve(root, outro) == velho

        assert _init(root, velho, "t-1").returncode == 0

    def test_volta_ao_projeto_de_origem_reabilita_o_balde(self, hp, cena):
        # A -> B -> A: o pin volta a apontar para o balde de lojas, e ele deixa
        # de ser abandonado.
        _envelhece_pin(cena["root"], hp)
        assert _resolve(cena["root"], cena["lojas"]) == cena["velho"]

        res = _roda(cena["root"], STATE_CLI, "--home", cena["velho"], "artifact",
                    "--task", "t-antes", "--type", "spec", "--path", "y.md")
        assert res.returncode == 0, res.stdout + res.stderr
        res = _init(cena["root"], cena["velho"], "t-volta", "L1-feature")
        assert res.returncode == 0, res.stdout + res.stderr


class TestBaldeRepinado:
    """O predicado puro, sobre o caminho e o pin, sem CLI no meio."""

    def test_caminho_fora_do_formato_de_balde_de_sessao_nao_julga(self, hp, tmp_path):
        assert hp.balde_repinado(tmp_path) is None
        assert hp.balde_repinado(tmp_path / "projects" / "x") is None

    def test_sem_pin_nao_julga(self, hp, tmp_path):
        balde = tmp_path / "projects" / "p" / "sessions" / "s-1"
        assert hp.balde_repinado(balde) is None

    def test_pin_ilegivel_nao_julga(self, hp, tmp_path):
        balde = tmp_path / "projects" / "p" / "sessions" / "s-1"
        (tmp_path / "pins").mkdir()
        (tmp_path / "pins" / "s-1.json").write_text("{", encoding="utf-8")
        assert hp.balde_repinado(balde) is None

    def test_pin_que_nunca_esteve_no_projeto_nao_julga(self, hp, tmp_path):
        # Projeto diferente do pin sem repin registrado e deriva, ou balde
        # anterior ao pin: nao ha evidencia de abandono, e o guarda nao inventa.
        balde = tmp_path / "projects" / "p" / "sessions" / "s-1"
        (tmp_path / "pins").mkdir()
        (tmp_path / "pins" / "s-1.json").write_text(
            json.dumps({"project_slug": "q", "drifts": [{"project_slug": "p"}]}), encoding="utf-8")
        assert hp.balde_repinado(balde) is None

    def test_repin_para_fora_do_projeto_julga(self, hp, tmp_path):
        balde = tmp_path / "projects" / "p" / "sessions" / "s-1"
        (tmp_path / "pins").mkdir()
        (tmp_path / "pins" / "s-1.json").write_text(json.dumps({
            "project_slug": "q",
            "repins": [{"project_slug": "p", "repinned_at": "2026-10-02T12:00:00+00:00"}],
        }), encoding="utf-8")

        achado = hp.balde_repinado(balde)

        assert achado is not None
        assert Path(achado["atual"]) == tmp_path / "projects" / "q" / "sessions" / "s-1"
        assert achado["repinned_at"] == "2026-10-02T12:00:00+00:00"
