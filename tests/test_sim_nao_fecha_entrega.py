"""Um "sim" nao pode fechar a entrega.

Incidente 2026-09-28, sessao `5a45e264`: a task `t-20260928-203110092956`
(L1-bug) estava na fase final `verify`, com evidencia verde e sem gate,
esperando a aprovacao que o modelo pediu em texto. O usuario respondeu `yes`:

1. o classify abriu task L0 nova e `start_task` fechou a L1 como `superseded`
   — contra a decisao D2 do usuario (2026-09-23): task L0 nao encerra pipeline
   vivo; so troca explicita ou L1/L2 novo encerra;
2. `state_cli complete` na L1 projetou `task_id`/`status` dela por cima da
   projecao do `yes`, e `classification_meta`, `started_at`, `prompt_len` e
   `prompt_excerpt` do `yes` sobreviveram. O `record_signal` le o meta dali
   para a telemetria de acuracia.

Diagnostico e linha do tempo: `docs/specs/sim-nao-fecha-entrega-diagnostico.md`.

Cada classe traz as duas metades: o que tinha de mudar (vermelho medido antes do
conserto) e o que nao podia mudar (verde antes e depois) — sem a segunda,
"conserto" e "silenciamento" sao indistinguiveis.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
sys.path.insert(0, str(ROOT / "scripts"))

import branch_state
import continuation_policy
import harness_paths
from transactional_state import ARTIFACT_OBLIGATIONS, HarnessDatabase

RESPOSTAS_CURTAS = ("yes", "sim", "pode")
ABRE_L1_BUG = "corrige o bug do parser de datas"
ABRE_L2_BUG = "o bug quebrou o sistema de pipeline inteiro"
PEDIDO_L2_NOVO = "planeje a arquitetura completa de um novo sistema de pipeline com spec e design"
TROCA_EXPLICITA = "esquece isso, me diz as horas"
NOTA_DE_ENTREGA = "Final phase already verified"

PIPELINE_L1_BUG = ["systematic-debugging", "tdd", "verify"]
PIPELINE_L2_DOCS = ["source-selection", "graph-context", "documentation", "verify-against-spec"]
PIPELINE_L2_FEATURE = [
    "discuss", "brainstorming", "graph-context", "write-spec", "grill-me", "approve-spec",
    "design-doc", "validate-plan", "approve-plan", "tdd", "verify-multimodel",
]


def _bash() -> str:
    achado = shutil.which("bash")
    if achado:
        return achado
    for c in (
        Path(r"C:\Program Files\Git\bin\bash.exe"),
        Path(r"C:\Program Files\Git\usr\bin\bash.exe"),
    ):
        if c.exists():
            return str(c)
    return "bash"


BASH = _bash()


def _env(tmp_path: Path) -> dict:
    return {
        **os.environ,
        "PYTHONUTF8": "1",
        "MASTER_HARNESS_HOME": str(tmp_path / "mh"),
        "HARNESS_ROUTER": "0",
        "HARNESS_BRANCH": "0",
    }


def _repo(base: Path, nome: str) -> Path:
    d = base / nome
    (d / ".git").mkdir(parents=True)
    return d


def _balde(cwd: Path, sessao: str) -> Path:
    return Path(harness_paths.ensure_state_dir(os.environ["HARNESS_DIR"], str(cwd), session_id=sessao))


def _prompt(tmp_path: Path, cwd: Path, sessao: str, texto: str) -> subprocess.CompletedProcess:
    payload = {"session_id": sessao, "cwd": str(cwd), "prompt": texto}
    return subprocess.run(
        [BASH, str(ROOT / "hooks" / "harness-classify.sh")],
        input=json.dumps(payload), capture_output=True, text=True, timeout=60,
        env=_env(tmp_path), encoding="utf-8",
    )


def _tasks(balde: Path) -> dict[str, str]:
    with sqlite3.connect(f"file:{balde / 'harness.db'}?mode=ro", uri=True) as c:
        return dict(c.execute("SELECT task_id, status FROM tasks").fetchall())


def _classificacoes(balde: Path) -> list[tuple]:
    with sqlite3.connect(f"file:{balde / 'harness.db'}?mode=ro", uri=True) as c:
        return c.execute("SELECT * FROM classifications ORDER BY task_id").fetchall()


def _ler(balde: Path) -> dict:
    return json.loads((balde / "state.json").read_text(encoding="utf-8"))


def _gravar(balde: Path, projecao: dict) -> None:
    (balde / "state.json").write_text(json.dumps(projecao, ensure_ascii=False, indent=2), encoding="utf-8")


def _transacional():
    spec = importlib.util.spec_from_file_location(
        "harness_transactional_sim", ROOT / "hooks" / "harness-transactional.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _evidencia_verde(db: HarnessDatabase, task_id: str) -> dict:
    return db.record_evidence(
        task_id, evidence_type="test", command="python -m pytest -q", exit_code=0,
        tests_collected=10, tests_passed=10, tests_skipped=0, output_hash="h",
    )


def _ate_a_fase_final(db: HarnessDatabase, task_id: str) -> dict:
    """Anda o pipeline pelas chamadas do `state_cli`: artefato onde a fase exige,
    `transition` com a revisao esperada."""
    task = db.task(task_id)
    while task["phase"] != task["pipeline"][-1]:
        obrigacao = ARTIFACT_OBLIGATIONS.get(task["phase"])
        if obrigacao:
            task = db.record_artifact(task_id, obrigacao, f"docs/{obrigacao}.md", None)
        proxima = task["pipeline"][task["pipeline"].index(task["phase"]) + 1]
        task = db.transition(task_id, proxima, expected_revision=task["revision"])
    return task


# ============================================================================
# Metade 1 — resposta curta a uma entrega, pelo hook de producao
# ============================================================================


def _aberta_pelo_hook(tmp_path: Path, nome: str, abertura: str) -> tuple[Path, str, Path, str]:
    cwd = _repo(tmp_path, nome)
    sessao = f"s-{nome}"
    res = _prompt(tmp_path, cwd, sessao, abertura)
    assert "HARNESS v3 CLASSIFIED" in res.stdout, res.stdout + res.stderr
    balde = _balde(cwd, sessao)
    return cwd, sessao, balde, _ler(balde)["task_id"]


def _na_fase_final(tmp_path: Path, nome: str, abertura: str, *, evidencia: bool):
    """Task aberta pelo classify e levada a ultima fase; com `evidencia`, entregue.

    A projecao acompanha pelo escritor do PostToolUse, e o contador ganha os
    arquivos que o Edit/Write da task teria contado.
    """
    cwd, sessao, balde, tid = _aberta_pelo_hook(tmp_path, nome, abertura)
    db = HarnessDatabase(balde)
    task = _ate_a_fase_final(db, tid)
    if evidencia:
        task = _evidencia_verde(db, tid)
    tx = _transacional()
    tx._sync_projection(balde, tx._projection(balde), task)
    (balde / ".session-files-count").write_text(json.dumps(
        {"count": 2, "files": ["src/parser.py", "tests/test_parser.py"], "task_id": tid}),
        encoding="utf-8")
    return cwd, sessao, balde, tid


class TestRespostaCurtaContinuaAEntrega:
    @pytest.mark.parametrize("abertura", [ABRE_L1_BUG, ABRE_L2_BUG], ids=["L1-bug", "L2-bug"])
    @pytest.mark.parametrize("resposta", RESPOSTAS_CURTAS)
    def test_resposta_curta_continua_a_entrega(self, tmp_path, abertura, resposta):
        """A1. Reproducao do incidente: antes do conserto, task L0 nova e a
        entrega `superseded`, com o contador zerado para a task do `yes`."""
        # Sessao unica por caso: o balde e fixado pela sessao (pin), e o
        # `HARNESS_DIR` e da classe — sessao repetida cairia no balde do caso anterior.
        nome = f"{resposta}-{'l2' if abertura == ABRE_L2_BUG else 'l1'}"
        cwd, sessao, balde, tid = _na_fase_final(tmp_path, nome, abertura, evidencia=True)
        projecao = _ler(balde)
        contador = (balde / ".session-files-count").read_text(encoding="utf-8")
        classificacoes = _classificacoes(balde)

        res = _prompt(tmp_path, cwd, sessao, resposta)

        assert "HARNESS v3 CONTINUING" in res.stdout, res.stdout + res.stderr
        assert tid in res.stdout
        assert NOTA_DE_ENTREGA in res.stdout, "a continuacao tem de dizer que so falta o `complete`"
        assert _tasks(balde) == {tid: "active"}, "nenhuma task nova, e a entrega continua viva"
        # Nada do prompt que continuou pode ser gravado: nem na projecao
        # (meta, prompt_excerpt), nem no banco (linha de classificacao).
        assert _ler(balde) == projecao, "a projecao continua sendo so da entrega"
        assert _classificacoes(balde) == classificacoes
        assert (balde / ".session-files-count").read_text(encoding="utf-8") == contador, \
            "os arquivos da entrega nao podem sumir do contador"

    def test_pedido_novo_ainda_fecha_a_entrega(self, tmp_path):
        """A2. A F2 continua valendo: trabalho novo nao e engolido pela entrega."""
        cwd, sessao, balde, tid = _na_fase_final(tmp_path, "f2", ABRE_L1_BUG, evidencia=True)

        res = _prompt(tmp_path, cwd, sessao, PEDIDO_L2_NOVO)

        assert "HARNESS v3 CLASSIFIED" in res.stdout, res.stdout + res.stderr
        estados = _tasks(balde)
        assert estados.pop(tid) == "superseded"
        assert list(estados.values()) == ["active"]

    def test_troca_explicita_ainda_fecha_a_entrega(self, tmp_path):
        """A3. A porta de saida da D2 continua aberta, mesmo com pedido L0
        (relatorio do ciclo de vida, 8.2: `esquece isso, me diz as horas`)."""
        cwd, sessao, balde, tid = _na_fase_final(tmp_path, "troca", ABRE_L1_BUG, evidencia=True)

        res = _prompt(tmp_path, cwd, sessao, TROCA_EXPLICITA)

        assert "HARNESS v3 CONTINUING" not in res.stdout, res.stdout
        estados = _tasks(balde)
        assert estados.pop(tid) == "superseded"
        assert list(estados.values()) == ["done"], "a troca abre a task L0 do pedido"

    def test_fase_final_sem_evidencia_ja_continuava(self, tmp_path):
        """A4. Falsificacao: fora da entrega a resposta curta sempre continuou,
        e a nota de entrega nao pode aparecer onde nao ha entrega."""
        cwd, sessao, balde, tid = _na_fase_final(tmp_path, "sem-prova", ABRE_L1_BUG, evidencia=False)

        res = _prompt(tmp_path, cwd, sessao, "yes")

        assert "HARNESS v3 CONTINUING" in res.stdout, res.stdout + res.stderr
        assert NOTA_DE_ENTREGA not in res.stdout
        assert _tasks(balde) == {tid: "active"}


# ============================================================================
# Metade 1 — a regra, na politica que o classify consulta
# ============================================================================


def _entrega_no_banco(balde: Path) -> dict:
    db = HarnessDatabase(balde)
    task = db.start_task(scope_id=str(balde), legacy_level="L1-bug", tier="L1", kind="bug",
                         pipeline=PIPELINE_L1_BUG, prompt="corrige o bug")
    _ate_a_fase_final(db, task["task_id"])
    return _evidencia_verde(db, task["task_id"])


class TestPoliticaDaEntrega:
    def test_prompt_l0_continua_a_entrega(self, tmp_path):
        """A5. D2: prompt que nao abre pipeline nao fecha a entrega."""
        task = _entrega_no_banco(tmp_path)
        r = continuation_policy.task_viva(str(tmp_path), nivel_do_prompt="L0")
        assert r.resposta == continuation_policy.VIVA
        assert r.task is not None and r.task["task_id"] == task["task_id"]

    @pytest.mark.parametrize("nivel", ["L1", "L2"])
    def test_prompt_que_abre_pipeline_fecha_a_entrega(self, tmp_path, nivel):
        """F2 preservada: trabalho novo nao vira CONTINUING da task velha."""
        _entrega_no_banco(tmp_path)
        r = continuation_policy.task_viva(str(tmp_path), nivel_do_prompt=nivel)
        assert r.resposta == continuation_policy.NENHUMA

    def test_sem_prompt_a_f2_fica_inteira(self, tmp_path):
        """Session-start e router perguntam sem prompt: nada muda para eles."""
        _entrega_no_banco(tmp_path)
        assert continuation_policy.task_viva(str(tmp_path)).resposta == continuation_policy.NENHUMA

    def test_entregue_e_so_a_fase_final_com_prova_e_sem_gate(self):
        base = {"status": "active", "pipeline": PIPELINE_L1_BUG, "phase": "verify",
                "verified": True, "pending_gate": None}
        assert continuation_policy.entregue(base) is True
        assert continuation_policy.entregue(base | {"phase": "tdd"}) is False
        assert continuation_policy.entregue(base | {"verified": False}) is False
        assert continuation_policy.entregue(base | {"pending_gate": "escalation"}) is False

    @pytest.mark.parametrize("nivel", [None, "L0", "L1", "L2"])
    def test_fora_da_entrega_o_nivel_nao_importa(self, nivel):
        """HC-00h: task viva no meio do pipeline continua para qualquer prompt."""
        task = {"status": "active", "pipeline": PIPELINE_L1_BUG, "phase": "tdd",
                "verified": True, "pending_gate": None}
        assert continuation_policy.continua(task, nivel_do_prompt=nivel) is True

    @pytest.mark.parametrize("nivel", ["L0-question", "l0", ""])
    def test_nivel_fora_do_dominio_levanta(self, nivel):
        """'L0-question' comparado com 'L0' cairia calado no ramo que fecha."""
        task = {"status": "active", "pipeline": PIPELINE_L1_BUG, "phase": "verify",
                "verified": True, "pending_gate": None}
        with pytest.raises(ValueError):
            continuation_policy.continua(task, nivel_do_prompt=nivel)

    def test_nivel_fora_do_dominio_falha_fechado(self, tmp_path):
        """Pela pergunta do classify o erro vira DESCONHECIDA, que nunca abre
        task nem fecha a entrega."""
        _entrega_no_banco(tmp_path)
        r = continuation_policy.task_viva(str(tmp_path), nivel_do_prompt="L0-question")
        assert r.resposta == continuation_policy.DESCONHECIDA
        assert "nivel_do_prompt" in (r.erro or "")


def _router():
    spec = importlib.util.spec_from_file_location("skill_router_sim", ROOT / "hooks" / "skill_router.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestRouterPerguntaComONivel:
    """O router roda no mesmo UserPromptSubmit que o classify e tem o prompt:
    a resposta dele sobre a entrega tem de ser a mesma."""

    RESPOSTA_LONGA = "sim, pode seguir com isso agora mesmo"  # >= MIN_LEN do router, L0

    def test_router_cala_quando_o_classify_continua_a_entrega(self, tmp_path):
        _entrega_no_banco(tmp_path)
        assert _router().passes_guards(self.RESPOSTA_LONGA, str(tmp_path / "state.json")) is False

    def test_router_fala_quando_o_pedido_novo_fecha_a_entrega(self, tmp_path):
        """Falsificacao: trabalho novo fecha a entrega, e o router segue falando."""
        _entrega_no_banco(tmp_path)
        assert _router().passes_guards(PEDIDO_L2_NOVO, str(tmp_path / "state.json")) is True


# ============================================================================
# Metade 2 — a projecao descreve uma task so
# ============================================================================


#: UTF-8 no console dos subprocessos, como os hooks forcam: o `record_signal`
#: escreve travessao no log de recusa, e em cp1252 ele vira 0x97.
ENV_UTF8 = {**os.environ, "PYTHONUTF8": "1"}


def _state_cli(balde: Path, *argumentos: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "state_cli.py"), "--home", str(balde), *argumentos],
        capture_output=True, text=True, timeout=60, encoding="utf-8", env=ENV_UTF8,
    )


def _projecao_do_classify(task: dict, *, meta: dict, prompt: str, started_at: str) -> dict:
    """A projecao como `harness-classify.sh` a escreve ao abrir a task
    (`new_state` + campos do dual-write). `started_at` do classify e anterior ao
    do banco, por isso vem de fora."""
    return {
        "task_id": task["task_id"],
        "schema_version": 3,
        "classification": task["legacy_level"],
        "classification_meta": meta,
        "status": task["status"],
        "pipeline": task["pipeline"],
        "current_step": None,
        "artifacts_so_far": [],
        "started_at": started_at,
        "prompt_len": len(prompt),
        "prompt_excerpt": prompt[:300],
        "revision": task["revision"],
        "code_revision": task["code_revision"],
        "owner_epoch": task["owner_epoch"],
        "verified": task["verified"],
        "pending_gate": task["pending_gate"],
        "scope_id": task["scope_id"],
    }


META_DO_YES = {"suggested": "L0-question", "final": "L0-question", "source": "regex",
               "confidence": None, "agreed": None}
CAMPOS_SO_DA_PROJECAO = ("prompt_len", "prompt_excerpt")


PIPELINE_L2_BUG = ["systematic-debugging", "graph-context", "grill-me", "tdd", "verify"]


def _entrega_substituida(balde: Path, *, por: str) -> tuple[dict, dict]:
    """A entrega do incidente, e a task que a substituiu.

    - `por="L0"`: o `yes` de 21:17:31 (L0 `done`), a replica literal;
    - `por="L2"`: trabalho novo, vivo — o caminho que sobra depois da metade 1;
    - `por="L2-entregue"`: trabalho novo vivo que TAMBEM ja esta entregue. Sem
      prompt, a politica diz NENHUMA para ela; e a task que mais precisa da
      projecao, porque espera o `sim` do usuario.
    """
    db = HarnessDatabase(balde)
    a = db.start_task(scope_id=str(balde), legacy_level="L2-docs", tier="L2", kind="docs",
                      pipeline=PIPELINE_L2_DOCS, prompt="<system-reminder> repositorio harness-lite",
                      task_id="t-20260928-203110092956")
    db.confirm_classification(a["task_id"], tier="L1", kind="bug", pipeline=PIPELINE_L1_BUG,
                              source="semantic", confidence=0.8)
    _ate_a_fase_final(db, a["task_id"])
    _evidencia_verde(db, a["task_id"])
    if por == "L0":
        b = db.start_task(scope_id=str(balde), legacy_level="L0-question", tier="L0",
                          kind="question", pipeline=[], prompt="yes",
                          task_id="t-20260928-211731216430")
        projecao_b = _projecao_do_classify(b, meta=META_DO_YES, prompt="yes",
                                           started_at="2026-09-28T21:17:31.216430+00:00")
    else:
        pipeline = PIPELINE_L2_FEATURE if por == "L2" else PIPELINE_L2_BUG
        b = db.start_task(scope_id=str(balde), legacy_level=f"L2-{'feature' if por == 'L2' else 'bug'}",
                          tier="L2", kind="feature" if por == "L2" else "bug", pipeline=pipeline,
                          prompt="implementa o sistema de filas", task_id="t-20260928-220000000000")
        if por == "L2-entregue":
            _ate_a_fase_final(db, b["task_id"])
            b = _evidencia_verde(db, b["task_id"])
            assert continuation_policy.entregue(b), "premissa: a viva esta entregue"
        projecao_b = _projecao_do_classify(
            b, meta={"suggested": b["legacy_level"], "final": None, "source": "regex",
                     "confidence": None, "agreed": None},
            prompt="implementa o sistema de filas", started_at="2026-09-28T22:00:00.000000+00:00")
    assert db.task(a["task_id"])["status"] == "superseded", "premissa: a entrega foi substituida"
    _gravar(balde, projecao_b)
    return db.task(a["task_id"]), projecao_b


def _record_signal(balde: Path, sinais: Path, task_id: str) -> subprocess.CompletedProcess:
    sinais.mkdir(exist_ok=True)
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "record_signal.py"), "--completed",
         "--steps", ",".join(PIPELINE_L1_BUG), "--expect-task", task_id,
         "--harness-dir", str(balde), "--signals-dir", str(sinais)],
        capture_output=True, text=True, timeout=60, encoding="utf-8", env=ENV_UTF8,
    )


def _evidence(balde: Path, task_id: str) -> subprocess.CompletedProcess:
    return _state_cli(balde, "evidence", "--task", task_id, "--type", "test",
                      "--command-text", "bun test", "--exit-code", "0",
                      "--tests-collected", "3", "--tests-passed", "3", "--tests-skipped", "0")


def _so_da_task(balde: Path, projecao: dict, task_id: str) -> None:
    """Tudo o que a projecao afirma e desta task, conferido contra o banco."""
    db = HarnessDatabase(balde)
    task = db.task(task_id)
    assert projecao["task_id"] == task_id
    assert projecao["classification_meta"] == db.classification(task_id)
    assert projecao["started_at"] == task["started_at"]
    assert projecao["classification"] == task["legacy_level"]
    for campo in CAMPOS_SO_DA_PROJECAO:
        assert campo not in projecao, f"{campo} de outra task sobreviveu na projecao"


class TestProjecaoDeUmaTaskSo:
    def test_replica_do_incidente_evidence_e_sinal(self, tmp_path):
        """B1. 21:33:07: `evidence` na L1 ja `superseded`, com a projecao do `yes`,
        e depois o `record_signal` do DONE.

        O `complete` de 21:33:58 fica fora da replica: ele so passa porque
        `complete` aceita task terminal, defeito declarado a parte (§7 do
        diagnostico). O `_sync` que misturou e o mesmo nos dois, e o primeiro a
        rodar foi o do `evidence`.
        """
        a, _ = _entrega_substituida(tmp_path, por="L0")

        res = _evidence(tmp_path, a["task_id"])

        assert res.returncode == 0, res.stdout + res.stderr
        _so_da_task(tmp_path, _ler(tmp_path), a["task_id"])
        res = _record_signal(tmp_path, tmp_path / "sinais", a["task_id"])
        assert res.returncode == 0, res.stdout + res.stderr
        gravada = json.loads((tmp_path / "sinais" / "signals.json").read_text(encoding="utf-8"))["tasks"][0]
        assert gravada["classification_meta"] == HarnessDatabase(tmp_path).classification(a["task_id"]), \
            "a telemetria de acuracia recebeu o meta de outra task"

    def test_complete_da_viva_sobre_projecao_morta(self, tmp_path):
        """B1b. `complete` da task viva quando a projecao ficou com uma task ja
        encerrada (o escritor falhou, ou a L0 de antes): projecao nova, so dela."""
        db = HarnessDatabase(tmp_path)
        morta = db.start_task(scope_id=str(tmp_path), legacy_level="L0-question", tier="L0",
                              kind="question", pipeline=[], prompt="yes")
        _gravar(tmp_path, _projecao_do_classify(morta, meta=META_DO_YES, prompt="yes",
                                                started_at="2026-09-28T21:17:31.216430+00:00"))
        viva = db.start_task(scope_id=str(tmp_path), legacy_level="L1-bug", tier="L1", kind="bug",
                             pipeline=PIPELINE_L1_BUG, prompt="corrige o bug")
        _ate_a_fase_final(db, viva["task_id"])
        viva = _evidencia_verde(db, viva["task_id"])

        res = _state_cli(tmp_path, "complete", "--task", viva["task_id"],
                         "--expect-revision", str(viva["revision"]))

        assert res.returncode == 0, res.stdout + res.stderr
        projecao = _ler(tmp_path)
        _so_da_task(tmp_path, projecao, viva["task_id"])
        assert projecao["status"] == "done"

    @pytest.mark.parametrize("por", ["L2", "L2-entregue"])
    def test_task_substituida_nao_rouba_a_projecao_da_viva(self, tmp_path, por):
        """B2/B2b. Com trabalho novo vivo — entregue ou nao —, a projecao e dele:
        o PostToolUse acha a task pelo `task_id` daqui, e desvia-lo manda toque e
        prova para a entrega morta. E o DONE da substituida recusa em vez de
        gravar o meta da viva."""
        a, projecao_b = _entrega_substituida(tmp_path, por=por)

        res = _evidence(tmp_path, a["task_id"])

        assert res.returncode == 0, res.stdout + res.stderr
        assert _ler(tmp_path) == projecao_b
        assert projecao_b["task_id"] in res.stderr, "o aviso tem de nomear a dona da projecao"
        res = _record_signal(tmp_path, tmp_path / "sinais", a["task_id"])
        assert res.returncode == 2, res.stdout + res.stderr
        assert not (tmp_path / "sinais" / "signals.json").exists(), "nada pode ser gravado"

    def test_troca_de_task_durante_a_decisao_nao_e_sobrescrita(self, tmp_path):
        """B6. Entre ler a projecao e grava-la, outro escritor pos uma terceira
        task (o classify abrindo trabalho novo): nada e gravado — a regra F7 do
        PostToolUse, aqui."""
        import projecao

        a, _ = _entrega_substituida(tmp_path, por="L0")
        intrusa = {"task_id": "t-intrusa", "status": "active"}

        class BancoQueAbreOutraTask:
            def current_task(self, _scope_id):
                _gravar(tmp_path, intrusa)  # o classify grava enquanto o banco responde
                return None

            def classification(self, task_id):
                return HarnessDatabase(tmp_path).classification(task_id)

        dona = projecao.projetar(tmp_path, a, {"task_id": a["task_id"]}, BancoQueAbreOutraTask())

        assert dona == "t-intrusa"
        assert _ler(tmp_path) == intrusa

    @pytest.mark.parametrize("arquivo", ["scripts/state_cli.py", "scripts/branch_state.py"])
    def test_escritor_de_task_escolhida_passa_pela_regra(self, arquivo):
        """Os dois escritores que projetam a task que o CHAMADOR escolheu (e nao a
        viva do escopo) passam por `projecao.projetar`. Um `update` proprio de
        volta aqui e o defeito de volta."""
        texto = (ROOT / arquivo).read_text(encoding="utf-8")
        assert "projetar(" in texto, arquivo
        assert "projection.update(" not in texto, arquivo

    def test_propria_task_atualiza_no_lugar(self, tmp_path):
        """B3. Falsificacao: a projecao da propria task nao e reconstruida — o
        que so ela guarda (meta, prompt, started_at do classify) fica."""
        db = HarnessDatabase(tmp_path)
        a = db.start_task(scope_id=str(tmp_path), legacy_level="L1-bug", tier="L1", kind="bug",
                          pipeline=PIPELINE_L1_BUG, prompt="corrige o bug")
        projecao_a = _projecao_do_classify(
            a, meta={"suggested": "L1-bug", "final": "L1-bug", "source": "semantic",
                     "confidence": 0.9, "agreed": True},
            prompt="corrige o bug", started_at="2026-01-01T00:00:00+00:00")
        _gravar(tmp_path, projecao_a)

        res = _state_cli(tmp_path, "transition", "--task", a["task_id"], "--to", "tdd",
                         "--expect-revision", str(a["revision"]))

        assert res.returncode == 0, res.stdout + res.stderr
        projecao = _ler(tmp_path)
        assert projecao["current_step"] == "tdd"
        for campo in ("classification_meta", "started_at", "schema_version", *CAMPOS_SO_DA_PROJECAO):
            assert projecao[campo] == projecao_a[campo], campo


# ============================================================================
# Metade 2 — o mesmo `update` cego no Branch Keeper
# ============================================================================


def _ramo_com_dona_substituida(tmp_path: Path, *, por: str) -> tuple[dict, dict, Path, dict]:
    """Ramo registrado pela task A; depois outra task toma o escopo.

    `_transaction_context` devolve a DONA do ramo (`branches.task_id`), nao a
    task da projecao — e e ela que `_sync_task` projeta.
    """
    sessao = "sessao-mae"
    projeto = harness_paths.ensure_state_dir(cwd=str(tmp_path))
    (projeto / "branch-sensor.json").write_text(json.dumps({"session_id": sessao, "turn": 10}),
                                                encoding="utf-8")
    balde = harness_paths.ensure_state_dir(cwd=str(tmp_path), session_id=sessao)
    db = HarnessDatabase(balde)
    a = db.start_task(scope_id=f"{sessao}|repo|worktree", legacy_level="L2-feature", tier="L2",
                      kind="feature", pipeline=["discuss", "tdd"], prompt="trabalho com ramo")
    _gravar(balde, {"task_id": a["task_id"], "scope_id": a["scope_id"]})
    ramo = branch_state.add(cwd=str(tmp_path), name="Ramo", topic="x", parent_session=sessao)

    if por == "viva":
        b = db.start_task(scope_id=a["scope_id"], legacy_level="L2-bug", tier="L2", kind="bug",
                          pipeline=["systematic-debugging", "tdd"], prompt="trabalho novo")
    else:
        b = db.start_task(scope_id=a["scope_id"], legacy_level="L0-question", tier="L0",
                          kind="question", pipeline=[], prompt="yes")
    projecao_b = _projecao_do_classify(
        b, meta={"suggested": b["legacy_level"], "final": None, "source": "regex",
                 "confidence": None, "agreed": None},
        prompt="trabalho novo" if por == "viva" else "yes",
        started_at="2026-09-28T22:30:00.000000+00:00")
    _gravar(balde, projecao_b)
    return a, ramo, balde, projecao_b


def _anexar(tmp_path: Path, ramo: dict) -> None:
    semente = tmp_path / "semente.md"
    launcher = tmp_path / "launcher.ps1"
    semente.write_text("# semente", encoding="utf-8")
    launcher.write_text("# launcher", encoding="utf-8")
    branch_state.attach_files(cwd=str(tmp_path), slug=ramo["slug"],
                              seed_path=str(semente), launcher_path=str(launcher))


class TestRamoNaoMisturaProjecao:
    def test_dona_substituida_nao_rouba_a_projecao_da_viva(self, tmp_path):
        """B4."""
        _, ramo, balde, projecao_b = _ramo_com_dona_substituida(tmp_path, por="viva")
        _anexar(tmp_path, ramo)
        assert _ler(balde) == projecao_b

    def test_sem_task_viva_a_projecao_e_so_da_dona(self, tmp_path):
        """B5."""
        a, ramo, balde, _ = _ramo_com_dona_substituida(tmp_path, por="L0")
        _anexar(tmp_path, ramo)
        _so_da_task(balde, _ler(balde), a["task_id"])


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
