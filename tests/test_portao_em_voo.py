"""O Stop nao cobra continuacao enquanto a task espera trabalho que ela mesma lancou.

Incidente 2026-09-29, task `t-20260929-130710180778`: tres fins de turno com job
da propria sessao rodando em segundo plano, tres cobrancas, `escalation` aberto
em doze minutos sem falha de verificacao nenhuma. Ver
`docs/specs/portao-stop-em-voo-diagnostico.md`.

As entradas de transcript abaixo copiam a forma medida nos transcripts reais da
maquina (`toolUseResult`, `<task-notification>`, `queue-operation`), e nao uma
forma inventada: o payload do hook e o `toolUseResult` sao o mesmo objeto
(mesmo sha256, diagnostico, grill item 3).
"""

import importlib.util
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])


def _load(name: str, relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


state = _load("em_voo_state", "scripts/transactional_state.py")
paths = _load("em_voo_paths", "scripts/harness_paths.py")
hook = _load("em_voo_hook", "hooks/harness-transactional.py")


def _em_voo():
    # Carregado por teste, e nao na coleta: sem o modulo, os testes do hook
    # ainda rodam e reprovam pela assercao, que e o vermelho que importa.
    return _load("em_voo_modulo", "scripts/trabalho_em_voo.py")


# --- Entradas de transcript, na forma real --------------------------------------

AGORA = datetime(2026, 9, 29, 13, 21, 0, tzinfo=timezone.utc)


def _ts(minutos_antes: float) -> str:
    return (AGORA - timedelta(minutes=minutos_antes)).isoformat().replace("+00:00", "Z")


def _resultado(tool_use_result: dict, minutos_antes: float, *, sidechain: bool = False) -> dict:
    return {
        "isSidechain": sidechain,
        "type": "user",
        "timestamp": _ts(minutos_antes),
        "message": {
            "role": "user",
            "content": [{"tool_use_id": "toolu_x", "type": "tool_result", "content": "..."}],
        },
        "toolUseResult": tool_use_result,
    }


def bash_em_background(job: str, minutos_antes: float = 5, *, por_timeout: bool = False, sidechain: bool = False):
    resultado = {
        "stdout": "",
        "stderr": "",
        "interrupted": False,
        "isImage": False,
        "noOutputExpected": False,
        "backgroundTaskId": job,
    }
    if por_timeout:
        resultado["timedOutAfterMs"] = 600000
    else:
        resultado["backgroundCwdHint"] = "Session cwd remains C:\\x; directory changes ..."
    return _resultado(resultado, minutos_antes, sidechain=sidechain)


def monitor(job: str, minutos_antes: float = 5, *, timeout_ms: int = 1800000, persistente: bool = False):
    return _resultado({"taskId": job, "timeoutMs": timeout_ms, "persistent": persistente}, minutos_antes)


def workflow(job: str, minutos_antes: float = 5):
    return _resultado(
        {
            "status": "async_launched",
            "taskId": job,
            "taskType": "local_workflow",
            "workflowName": "wf-grill",
            "runId": "wf_x",
            "summary": "...",
            "transcriptDir": "C:\\x",
            "scriptPath": "C:\\x\\wf-grill.js",
        },
        minutos_antes,
    )


def agente(job: str, minutos_antes: float = 5):
    return _resultado(
        {
            "isAsync": True,
            "status": "async_launched",
            "agentId": job,
            "description": "...",
            "resolvedModel": "claude-opus-5-5",
            "prompt": "...",
            "outputFile": "C:\\x\\out",
            "canReadOutputFile": True,
        },
        minutos_antes,
    )


def task_stop(job: str, minutos_antes: float = 1):
    return _resultado(
        {
            "message": f"Successfully stopped task: {job} (medicao)",
            "task_id": job,
            "task_type": "local_bash",
            "command": "medicao",
        },
        minutos_antes,
    )


def notificacao_terminal(job: str, status: str = "completed") -> str:
    return (
        "<task-notification>\n"
        f"<task-id>{job}</task-id>\n"
        "<tool-use-id>toolu_x</tool-use-id>\n"
        f"<output-file>C:\\x\\tasks\\{job}.output</output-file>\n"
        f"<status>{status}</status>\n"
        '<summary>Background command "medicao" completed (exit code 0)</summary>\n'
        "</task-notification>"
    )


def evento_de_monitor(job: str, evento: str = "seis-c0-r1 81.9 falhas 0") -> str:
    return (
        "<task-notification>\n"
        f"<task-id>{job}</task-id>\n"
        '<summary>Monitor event: "uma linha por rodada"</summary>\n'
        f"<event>{evento}</event>\n"
        "If this event is something the user would act on now, send a PushNotification.\n"
        "</task-notification>"
    )


def como_usuario(texto: str, minutos_antes: float = 1) -> dict:
    return {
        "isSidechain": False,
        "type": "user",
        "timestamp": _ts(minutos_antes),
        "message": {"role": "user", "content": texto},
        "origin": {"kind": "task-notification"},
    }


def como_fila(texto: str, minutos_antes: float = 1, operacao: str = "enqueue") -> dict:
    return {
        "type": "queue-operation",
        "operation": operacao,
        "timestamp": _ts(minutos_antes),
        "sessionId": "session-a",
        "content": texto,
    }


def como_anexo(texto: str, minutos_antes: float = 1) -> dict:
    return {
        "isSidechain": False,
        "type": "attachment",
        "timestamp": _ts(minutos_antes),
        "attachment": {"type": "queued_command", "prompt": texto, "commandMode": "task-notification"},
    }


def _transcript(tmp_path: Path, *entradas, linhas_cruas: tuple[str, ...] = ()) -> Path:
    caminho = tmp_path / "transcript.jsonl"
    linhas = [json.dumps(e, ensure_ascii=False) for e in entradas]
    linhas.extend(linhas_cruas)
    caminho.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    return caminho


def _ids(caminho, **kwargs) -> list[str]:
    kwargs.setdefault("agora", AGORA)
    return sorted(job["id"] for job in _em_voo().jobs_em_voo(caminho, **kwargs))


# --- jobs_em_voo: o que e lancamento --------------------------------------------


@pytest.mark.parametrize(
    "lancamento",
    [
        bash_em_background("bzrg3z3z3"),
        bash_em_background("bzrg3z3z3", por_timeout=True),
        monitor("bzrg3z3z3"),
        workflow("bzrg3z3z3"),
        agente("bzrg3z3z3"),
    ],
    ids=["bash", "bash-empurrado-por-timeout", "monitor", "workflow", "agent"],
)
def test_lancamento_sem_termino_esta_em_voo(tmp_path: Path, lancamento: dict):
    assert _ids(_transcript(tmp_path, lancamento)) == ["bzrg3z3z3"]


def test_monitor_persistente_nao_e_trabalho_esperado(tmp_path: Path):
    """Vigia que so acaba com a sessao nao e resultado pelo qual se espera."""
    assert _ids(_transcript(tmp_path, monitor("bpers", persistente=True))) == []


def test_lancamento_de_subagente_nao_conta(tmp_path: Path):
    """Job lancado por subagente nao acorda a sessao principal."""
    assert _ids(_transcript(tmp_path, bash_em_background("bsub", sidechain=True))) == []


def test_texto_que_cita_o_campo_nao_e_lancamento(tmp_path: Path):
    """Ler um transcript ou esta spec traz `backgroundTaskId` no texto do resultado.

    So chave de primeiro nivel de `toolUseResult` lanca job; texto aninhado nao.
    """
    leitura = _resultado(
        {"type": "text", "file": {"filePath": "x.jsonl", "content": '{"backgroundTaskId": "bfalso"}'}},
        2,
    )
    assert _ids(_transcript(tmp_path, leitura)) == []


# --- jobs_em_voo: o que e termino -----------------------------------------------


@pytest.mark.parametrize("status", ["completed", "failed", "killed", "stopped", "status-que-ainda-nao-existe"])
def test_notificacao_com_status_encerra_o_job(tmp_path: Path, status: str):
    """Qualquer `<status>` encerra. O desconhecido tambem: errar para esse lado cobra."""
    caminho = _transcript(tmp_path, bash_em_background("bj1"), como_usuario(notificacao_terminal("bj1", status)))
    assert _ids(caminho) == []


@pytest.mark.parametrize("embrulho", [como_usuario, como_fila, como_anexo], ids=["user", "queue-operation", "queued_command"])
def test_termino_vale_em_qualquer_entrada_do_host(tmp_path: Path, embrulho):
    """`queue-operation` com `absorbed_mid_turn` e o caso que o UserPromptSubmit perde."""
    caminho = _transcript(tmp_path, bash_em_background("bj1"), embrulho(notificacao_terminal("bj1")))
    assert _ids(caminho) == []


def test_termino_de_outro_job_nao_encerra_este(tmp_path: Path):
    caminho = _transcript(
        tmp_path,
        bash_em_background("bj1"),
        bash_em_background("bj2"),
        como_usuario(notificacao_terminal("bj1")),
    )
    assert _ids(caminho) == ["bj2"]


def test_evento_de_monitor_nao_encerra_o_monitor(tmp_path: Path):
    caminho = _transcript(tmp_path, monitor("bmon"), como_usuario(evento_de_monitor("bmon")))
    assert _ids(caminho) == ["bmon"]


def test_monitor_expirado_esta_encerrado(tmp_path: Path):
    expirou = evento_de_monitor("bmon", "[Monitor expired after 30m with no events delivered. Re-arm it if needed.]")
    caminho = _transcript(tmp_path, monitor("bmon"), como_fila(expirou))
    assert _ids(caminho) == []


def test_monitor_alem_do_prazo_do_host_esta_encerrado(tmp_path: Path):
    """Lancado ha 31 min com `timeoutMs` de 30 min: o host ja o encerrou."""
    assert _ids(_transcript(tmp_path, monitor("bmon", minutos_antes=31))) == []
    assert _ids(_transcript(tmp_path, monitor("bmon", minutos_antes=29))) == ["bmon"]


def test_resumo_de_orfaos_encerra_todos_os_ids_do_bloco(tmp_path: Path):
    """Na retomada o host lista num bloco so os jobs que morreram com a sessao."""
    orfaos = (
        "<task-notification>\n"
        "<task-id>bj1</task-id>\n<task-id>bj2</task-id>\n<task-id>__orphan_summary__:shell</task-id>\n"
        "<status>stopped</status>\n"
        "<summary>2 background shell command tasks didn't finish before the previous session ended.</summary>\n"
        "</task-notification>"
    )
    caminho = _transcript(tmp_path, bash_em_background("bj1"), bash_em_background("bj2"), como_usuario(orfaos))
    assert _ids(caminho) == []


def test_task_stop_bem_sucedido_encerra_sem_notificacao(tmp_path: Path):
    """39 de 55 jobs parados por TaskStop nao recebem notificacao nenhuma."""
    caminho = _transcript(tmp_path, agente("a45260d772ce7a0d3"), task_stop("a45260d772ce7a0d3"))
    assert _ids(caminho) == []


# --- jobs_em_voo: recorte e leitura ---------------------------------------------


def test_so_conta_lancamento_depois_do_inicio_da_task(tmp_path: Path):
    """D2: um servidor subido numa task anterior nao isenta a task nova."""
    caminho = _transcript(tmp_path, bash_em_background("bvelho", 60), bash_em_background("bnovo", 5))
    assert _ids(caminho, desde=(AGORA - timedelta(minutes=30)).isoformat()) == ["bnovo"]


def test_transcript_ausente_devolve_nada(tmp_path: Path):
    assert _ids(tmp_path / "nao-existe.jsonl") == []
    assert _ids(None) == []


def test_linha_quebrada_nao_esconde_as_outras(tmp_path: Path):
    """O host pode estar gravando a ultima linha quando o Stop le."""
    caminho = _transcript(
        tmp_path,
        bash_em_background("bj1"),
        linhas_cruas=('{"type": "user", "toolUseResult": {"backgroundTaskId": "bcort',),
    )
    assert _ids(caminho) == ["bj1"]


# --- O incidente, pelo hook de producao -----------------------------------------


def _task_ativa(root: Path, cwd: Path, session_id: str = "session-a"):
    bucket = paths.ensure_state_dir(root, cwd, session_id=session_id)
    database = state.HarnessDatabase(bucket)
    task = database.start_task(
        scope_id=f"{session_id}|repo|worktree",
        legacy_level="L1-bug",
        tier="L1",
        kind="bug",
        pipeline=["systematic-debugging", "tdd", "verify"],
        prompt="medir",
    )
    (bucket / "state.json").write_text(
        json.dumps({"task_id": task["task_id"], "scope_id": task["scope_id"]}), encoding="utf-8"
    )
    return bucket, database, task


def _stop(cwd: Path, transcript: Path | None, root: Path) -> str:
    payload = {"hook_event_name": "Stop", "cwd": str(cwd), "session_id": "session-a"}
    if transcript is not None:
        payload["transcript_path"] = str(transcript)
    return hook.handle_payload(payload, harness_root=root)


def _agora_real(minutos_antes: float) -> str:
    """Carimbo relativo ao relogio real: o hook julga contra `datetime.now`.

    Com `minutos_antes=0` e chamado DEPOIS de `_task_ativa`, o lancamento nasce
    depois de `started_at` — senao o recorte D2 o descartaria e o teste mediria
    o recorte, nao a regra.
    """
    return (datetime.now(timezone.utc) - timedelta(minutes=minutos_antes)).isoformat().replace("+00:00", "Z")


def _bash_real(job: str, minutos_antes: float = 0) -> dict:
    entrada = bash_em_background(job)
    entrada["timestamp"] = _agora_real(minutos_antes)
    return entrada


def _eventos_nao_cobrados(database, task_id: str) -> list[dict]:
    with sqlite3.connect(database.path) as raw:
        linhas = raw.execute(
            "SELECT payload_json FROM events WHERE task_id = ? AND event_type = 'stop_nao_cobrado' ORDER BY id",
            (task_id,),
        ).fetchall()
    return [json.loads(linha[0]) for linha in linhas]


def test_incidente_job_em_voo_bloqueia_sem_cobrar(tmp_path: Path):
    """Metade 1. Tres Stops com o job rodando: hoje o terceiro abre `escalation`."""
    cwd = tmp_path / "repo"
    cwd.mkdir()
    root = tmp_path / "harness"
    _, database, task = _task_ativa(root, cwd)
    transcript = _transcript(tmp_path, _bash_real("b36bf65ya"))

    saidas = [json.loads(_stop(cwd, transcript, root)) for _ in range(3)]

    assert [s["decision"] for s in saidas] == ["block", "block", "block"], "D1: com job em voo o Stop continua bloqueando"
    atual = database.task(task["task_id"])
    assert atual["stop_continuations"] == 0
    assert atual["status"] == "active"
    assert atual["pending_gate"] is None
    assert all("b36bf65ya" in s["reason"] for s in saidas), "a mensagem tem de nomear o job que suspendeu a cobranca"
    assert [e["jobs"] for e in _eventos_nao_cobrados(database, task["task_id"])] == [["b36bf65ya"]] * 3


def test_CONTROLE_job_terminado_nao_isenta(tmp_path: Path):
    """Metade 2. Mesmo roteiro, job ja encerrado: a escalada continua na terceira."""
    cwd = tmp_path / "repo"
    cwd.mkdir()
    root = tmp_path / "harness"
    _, database, task = _task_ativa(root, cwd)
    transcript = _transcript(tmp_path, _bash_real("bj1"), como_usuario(notificacao_terminal("bj1")))

    for _ in range(3):
        assert json.loads(_stop(cwd, transcript, root))["decision"] == "block"

    atual = database.task(task["task_id"])
    assert atual["status"] == "awaiting_gate"
    assert atual["pending_gate"] == "escalation"
    assert _eventos_nao_cobrados(database, task["task_id"]) == []


def test_CONTROLE_job_de_antes_da_task_nao_isenta(tmp_path: Path):
    """D2 pelo hook: o recorte por `started_at` chega ate o portao."""
    cwd = tmp_path / "repo"
    cwd.mkdir()
    root = tmp_path / "harness"
    _, database, task = _task_ativa(root, cwd)
    transcript = _transcript(tmp_path, _bash_real("bservidor", minutos_antes=90))

    for _ in range(3):
        _stop(cwd, transcript, root)

    assert database.task(task["task_id"])["pending_gate"] == "escalation"


@pytest.mark.parametrize("transcript", ["ausente", "sem-campo"])
def test_CONTROLE_sem_transcript_legivel_cobra_como_hoje(tmp_path: Path, transcript: str):
    cwd = tmp_path / "repo"
    cwd.mkdir()
    root = tmp_path / "harness"
    _, database, task = _task_ativa(root, cwd)
    caminho = tmp_path / "nao-existe.jsonl" if transcript == "ausente" else None

    for _ in range(3):
        _stop(cwd, caminho, root)

    assert database.task(task["task_id"])["pending_gate"] == "escalation"


def test_contador_fica_congelado_durante_o_voo_e_nao_zera(tmp_path: Path):
    """Empurrado duas vezes, lanca a suite e espera: o voo nao apaga as duas.

    Zerar no fim do job abriria o laco "lanca, espera, para" que nunca chega a 2.
    """
    cwd = tmp_path / "repo"
    cwd.mkdir()
    root = tmp_path / "harness"
    _, database, task = _task_ativa(root, cwd)
    sem_job = _transcript(tmp_path)
    _stop(cwd, sem_job, root)
    _stop(cwd, sem_job, root)
    assert database.task(task["task_id"])["stop_continuations"] == 2

    em_voo_agora = _transcript(tmp_path, _bash_real("bsuite"))
    assert json.loads(_stop(cwd, em_voo_agora, root))["decision"] == "block"
    assert database.task(task["task_id"])["pending_gate"] is None

    terminou = _transcript(tmp_path, _bash_real("bsuite"), como_usuario(notificacao_terminal("bsuite", "failed")))
    _stop(cwd, terminou, root)
    assert database.task(task["task_id"])["pending_gate"] == "escalation"


def test_escalada_mostra_quantos_stops_nao_foram_cobrados(tmp_path: Path):
    """D3: o evento tem consumidor — a mensagem que chega ao modelo e ao humano."""
    cwd = tmp_path / "repo"
    cwd.mkdir()
    root = tmp_path / "harness"
    _, database, task = _task_ativa(root, cwd)
    voando = _transcript(tmp_path, _bash_real("bj1"))
    _stop(cwd, voando, root)
    _stop(cwd, voando, root)

    terminou = _transcript(tmp_path, _bash_real("bj1"), como_usuario(notificacao_terminal("bj1")))
    saidas = [json.loads(_stop(cwd, terminou, root)) for _ in range(3)]

    assert database.task(task["task_id"])["pending_gate"] == "escalation"
    assert "stops_nao_cobrados=2" in saidas[-1]["reason"]


def test_register_stop_continuation_com_job_em_voo_nao_mexe_no_contador(tmp_path: Path):
    """A decisao de contar mora no banco: o hook so entrega a lista."""
    database = state.HarnessDatabase(tmp_path / "balde")
    task = database.start_task(
        scope_id="s|repo|wt", legacy_level="L1-bug", tier="L1", kind="bug", pipeline=["verify"], prompt="x"
    )
    database.register_stop_continuation(task["task_id"], limit=2)
    database.register_stop_continuation(task["task_id"], limit=2)

    depois = database.register_stop_continuation(task["task_id"], limit=2, em_voo=["bj1", "bj2"])

    assert depois["stop_continuations"] == 2
    assert depois["status"] == "active"
    assert depois["pending_gate"] is None
    assert database.stops_nao_cobrados(task["task_id"]) == 1


# --- Lancamento nao e resultado --------------------------------------------------


@pytest.mark.parametrize(
    "resposta",
    [
        {
            "stdout": "",
            "stderr": "",
            "interrupted": False,
            "isImage": False,
            "noOutputExpected": False,
            "backgroundTaskId": "b8jmxvjka",
            "timedOutAfterMs": 600000,
        },
        {"output": "Command running in background with ID: b8jmxvjka"},
    ],
    ids=["forma-real-do-payload", "so-texto"],
)
def test_suite_lancada_em_background_nao_grava_evidencia(tmp_path: Path, resposta: dict):
    """Caso n. 2: a linha com `tests_collected=NULL` nao prova nada e deixa de existir."""
    cwd = tmp_path / "repo"
    cwd.mkdir()
    root = tmp_path / "harness"
    _, database, task = _task_ativa(root, cwd)
    antes = database.task(task["task_id"])["code_revision"]

    aviso = hook.handle_payload(
        {
            "hook_event_name": "PostToolUse",
            "cwd": str(cwd),
            "session_id": "session-a",
            "tool_name": "Bash",
            "tool_input": {"command": "python -m pytest -q -p no:cacheprovider"},
            "tool_response": resposta,
        },
        harness_root=root,
    )

    with sqlite3.connect(database.path) as raw:
        linhas = raw.execute("SELECT COUNT(*) FROM evidence WHERE task_id = ?", (task["task_id"],)).fetchone()[0]
    assert linhas == 0
    assert database.task(task["task_id"])["code_revision"] == antes + 1, "o job pode escrever: o toque continua"
    assert "b8jmxvjka" in aviso
    assert "state_cli.py" in aviso, "o aviso tem de trazer a receita que registra a evidencia depois"


def test_CONTROLE_suite_em_primeiro_plano_continua_gravando(tmp_path: Path):
    cwd = tmp_path / "repo"
    cwd.mkdir()
    root = tmp_path / "harness"
    _, database, task = _task_ativa(root, cwd)

    hook.handle_payload(
        {
            "hook_event_name": "PostToolUse",
            "cwd": str(cwd),
            "session_id": "session-a",
            "tool_name": "Bash",
            "tool_input": {"command": "python -m pytest -q"},
            "tool_response": {"stdout": "3 passed in 0.01s", "stderr": "", "interrupted": False},
        },
        harness_root=root,
    )

    assert database.task(task["task_id"])["verified"] is True
