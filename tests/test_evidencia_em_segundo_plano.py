"""A suite que vai para segundo plano vira evidencia quando termina.

Spec: `docs/specs/evidencia-em-segundo-plano-spec.md`. Design:
`docs/specs/evidencia-em-segundo-plano-design.md`.

Consumidor nomeado: a suite do harness4claude, ~21 min, empurrada para o fundo
pelo timeout de 10 min do Bash. Hoje o lancamento nao grava nada (D4 de
`portao-stop-em-voo-diagnostico.md`) e o modelo registra a mao.

O insumo real (`tests/fixtures/evidencia_em_segundo_plano/b8jmxvjka/`) e o job
`b8jmxvjka` do balde `037312e7`, guardado em 2026-09-30 antes de o host apagar
o arquivo de saida (~24 h). So entram as entradas do HOST no transcript; as do
modelo ficaram de fora. A unica alteracao que os testes fazem nele e reescrever
o caminho de `<output-file>` para o `tmp_path`, e cada teste que faz isso diz.
"""

import hashlib
import importlib.util
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
INSUMO = Path(__file__).parent / "fixtures" / "evidencia_em_segundo_plano" / "b8jmxvjka"


def _load(name: str, relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    # `@dataclass` com `from __future__ import annotations` procura o proprio
    # modulo em `sys.modules`; o import normal de producao ja o registra.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


state = _load("bg_state", "scripts/transactional_state.py")
paths = _load("bg_paths", "scripts/harness_paths.py")
hook = _load("bg_hook", "hooks/harness-transactional.py")


def _modulo():
    # Carregado por teste, e nao na coleta: sem o modulo, os testes do hook
    # ainda rodam e reprovam pela assercao, que e o vermelho que importa.
    return _load("bg_modulo", "scripts/evidencia_em_segundo_plano.py")


def _banco(tmp_path: Path, kind: str = "feature"):
    database = state.HarnessDatabase(tmp_path / "balde")
    task = database.start_task(
        scope_id="s|repo|wt", legacy_level="L2-feature", tier="L2", kind=kind, pipeline=["tdd"], prompt="x"
    )
    return database, task


def _evidencias(database, task_id: str) -> list[dict]:
    with sqlite3.connect(database.path) as raw:
        raw.row_factory = sqlite3.Row
        return [
            dict(linha)
            for linha in raw.execute("SELECT * FROM evidence WHERE task_id = ? ORDER BY id", (task_id,))
        ]


# --- Fase 1: a contagem e uma so ------------------------------------------------


def test_insumo_real_esta_intacto():
    """O fixture e o arquivo que o host gravou, byte a byte (`.gitattributes`)."""
    dados = (INSUMO / "b8jmxvjka.output").read_bytes()
    assert hashlib.sha256(dados).hexdigest() == (
        "a8f1ad1f01585b7be6ca73b4c36730a9a2bdc9301db3a027a1f7461970e7f8f3"
    )


def test_contar_testes_no_insumo_real():
    """`2 failed, 1610 passed, 1 skipped, 6 subtests passed`: os 2 falhos entram em collected.

    O arquivo real nao e UTF-8 valido (byte 0x97 na posicao 3661, travessao do
    cp1252 vindo de uma mensagem de teste) e tem CRLF. Decodificar estrito
    derrubava a leitura inteira por um caractere de mensagem.
    """
    texto = (INSUMO / "b8jmxvjka.output").read_bytes().decode("utf-8", errors="replace")

    coletados, passaram, pulados, digest = _modulo().contar_testes(texto)

    assert (coletados, passaram, pulados) == (1613, 1610, 1)
    assert digest == hashlib.sha256(texto.encode("utf-8")).hexdigest()


def test_test_counts_do_hook_delega_para_contar_testes(monkeypatch):
    """REQ-F5: uma contagem, dois consumidores. Uma copia no hook passaria aqui sem delegar."""
    vistos = []

    def espiao(texto):
        vistos.append(texto)
        return 7, 7, 0, "d"

    monkeypatch.setattr(hook, "contar_testes", espiao)

    assert hook._test_counts({"tool_response": {"stdout": "3 passed in 0.01s"}}) == (7, 7, 0, "d")
    assert vistos == ["3 passed in 0.01s"]


# --- Fase 1: evidencia numa revisao que nao e a corrente --------------------------


def test_record_evidence_sem_revisao_grava_na_corrente_e_verifica(tmp_path: Path):
    """AC-2.3: omitida, e o comportamento de hoje."""
    database, task = _banco(tmp_path)
    database.touch_file(task["task_id"], "a.py")

    depois = database.record_evidence(
        task["task_id"], evidence_type="test", command="pytest", exit_code=0,
        tests_collected=3, tests_passed=3, tests_skipped=0, output_hash="h",
    )

    assert depois["verified"] is True
    assert [e["code_revision"] for e in _evidencias(database, task["task_id"])] == [1]


def test_record_evidence_na_revisao_corrente_explicita_e_o_mesmo_que_omitida(tmp_path: Path):
    database, task = _banco(tmp_path)
    database.touch_file(task["task_id"], "a.py")

    depois = database.record_evidence(
        task["task_id"], evidence_type="test", command="pytest", exit_code=0,
        tests_collected=3, tests_passed=3, tests_skipped=0, output_hash="h", code_revision=1,
    )

    assert depois["verified"] is True
    assert depois["stop_continuations"] == 0


def test_record_evidence_em_revisao_antiga_verde_nao_verifica(tmp_path: Path):
    """AC-2.1 no banco: a suite testou N', o codigo esta em N'+1."""
    database, task = _banco(tmp_path)
    database.touch_file(task["task_id"], "a.py")
    database.touch_file(task["task_id"], "b.py")
    database.register_stop_continuation(task["task_id"], limit=5)
    antes = database.task(task["task_id"])

    depois = database.record_evidence(
        task["task_id"], evidence_type="test", command="pytest", exit_code=0,
        tests_collected=3, tests_passed=3, tests_skipped=0, output_hash="h", code_revision=1,
    )

    assert depois["verified"] is False
    assert depois["stop_continuations"] == antes["stop_continuations"] == 1
    assert depois["status"] == antes["status"]
    assert depois["code_revision"] == 2
    assert [e["code_revision"] for e in _evidencias(database, task["task_id"])] == [1]


def test_record_evidence_em_revisao_antiga_vermelha_nao_desverifica(tmp_path: Path):
    """AC-2.2: historico nao mexe em `verified` para nenhum lado."""
    database, task = _banco(tmp_path)
    database.touch_file(task["task_id"], "a.py")
    database.touch_file(task["task_id"], "b.py")
    database.record_evidence(
        task["task_id"], evidence_type="test", command="pytest", exit_code=0,
        tests_collected=3, tests_passed=3, tests_skipped=0, output_hash="h",
    )

    depois = database.record_evidence(
        task["task_id"], evidence_type="test", command="pytest", exit_code=1,
        tests_collected=3, tests_passed=1, tests_skipped=0, output_hash="h2", code_revision=1,
    )

    assert depois["verified"] is True


# --- Sessao no layout real: transcript e pasta de tarefas do host ----------------
#
#   <tmp>/projects/<projeto>/<sessao>.jsonl            transcript
#   <tmp>/temp/claude/<projeto>/<sessao>/tasks/J.output arquivo de saida
#
# O mesmo layout do caso real (`037312e7`): o diretorio do projeto tem o mesmo
# nome nos dois lados, e a pasta da sessao tem o nome do transcript sem `.jsonl`.

PROJETO = "C--repo"
SESSAO = "0000aaaa-sess"
U = "toolu_01lancamento"
VERDE = "..........\r\n3 passed, 1 skipped in 0.10s\r\n\n[exited with code 0]\n"
VERMELHO = "F..\r\n1 failed, 2 passed in 0.10s\r\n\n[exited with code 1]\n"


class Sessao:
    def __init__(self, tmp_path: Path):
        self.tmp = tmp_path
        self.cwd = tmp_path / "repo"
        self.cwd.mkdir()
        self.root = tmp_path / "harness"
        self.transcript = tmp_path / "projects" / PROJETO / f"{SESSAO}.jsonl"
        self.transcript.parent.mkdir(parents=True)
        self.tasks = tmp_path / "temp" / "claude" / PROJETO / SESSAO / "tasks"
        self.tasks.mkdir(parents=True)
        self.entradas: list[dict] = []
        bucket = paths.ensure_state_dir(self.root, self.cwd, session_id="session-a")
        self.database = state.HarnessDatabase(bucket)
        self.task = self.database.start_task(
            scope_id="session-a|repo|worktree",
            legacy_level="L1-bug",
            tier="L1",
            kind="bug",
            pipeline=["systematic-debugging", "tdd", "verify"],
            prompt="suite longa",
        )
        (bucket / "state.json").write_text(
            json.dumps({"task_id": self.task["task_id"], "scope_id": self.task["scope_id"]}), encoding="utf-8"
        )

    @property
    def task_id(self) -> str:
        return self.task["task_id"]

    def atual(self) -> dict:
        return self.database.task(self.task_id)

    def _grava(self):
        self.transcript.write_text(
            "\n".join(json.dumps(e, ensure_ascii=False) for e in self.entradas) + "\n", encoding="utf-8"
        )

    def lanca(self, job: str = "bjob1", *, tool_use_id: str | None = U, ferramenta: str = "Bash",
              comando: str = "python -m pytest -q", transcript: bool = True) -> str:
        """PostToolUse de producao, com a resposta na forma real (empurrada por timeout)."""
        resposta = {
            "stdout": "", "stderr": "", "interrupted": False, "isImage": False,
            "noOutputExpected": False, "backgroundTaskId": job, "timedOutAfterMs": 600000,
        }
        self.entradas.append({
            "isSidechain": False, "type": "user", "timestamp": "2026-09-30T12:00:00.000Z",
            "message": {"role": "user", "content": [{"tool_use_id": tool_use_id, "type": "tool_result", "content": ""}]},
            "toolUseResult": resposta,
        })
        self._grava()
        payload = {
            "hook_event_name": "PostToolUse", "cwd": str(self.cwd), "session_id": "session-a",
            "tool_name": ferramenta, "tool_input": {"command": comando}, "tool_response": resposta,
        }
        if tool_use_id is not None:
            payload["tool_use_id"] = tool_use_id
        if transcript:
            payload["transcript_path"] = str(self.transcript)
        return hook.handle_payload(payload, harness_root=self.root)

    def saida(self, job: str, texto: str = VERDE) -> Path:
        caminho = self.tasks / f"{job}.output"
        caminho.write_bytes(texto.encode("utf-8"))
        return caminho

    def bloco(self, job: str = "bjob1", *, status: str = "completed", resumo: str | None = None,
              tool_use_id: str = U, arquivo: Path | None = None) -> str:
        if resumo is None:
            resumo = f'Background command "suite" {status} (exit code 0)' if status == "completed" else (
                f'Background command "suite" failed with exit code 1')
        caminho = arquivo if arquivo is not None else self.tasks / f"{job}.output"
        return (
            "<task-notification>\n"
            f"<task-id>{job}</task-id>\n"
            f"<tool-use-id>{tool_use_id}</tool-use-id>\n"
            f"<output-file>{caminho}</output-file>\n"
            f"<status>{status}</status>\n"
            f"<summary>{resumo}</summary>\n"
            "</task-notification>"
        )

    def notifica(self, bloco: str, *, forma: str = "anexo", quando: str = "2026-09-30T12:21:00.000Z"):
        """Grava a notificacao numa das formas medidas no transcript."""
        if forma == "anexo":
            entrada = {"isSidechain": False, "type": "attachment", "timestamp": quando,
                       "attachment": {"type": "queued_command", "prompt": bloco,
                                      "commandMode": "task-notification",
                                      "origin": {"kind": "task-notification", "producer": "session-task"}}}
        elif forma == "anexo-sem-origin":
            entrada = {"isSidechain": False, "type": "attachment", "timestamp": quando,
                       "attachment": {"type": "queued_command", "prompt": bloco, "commandMode": "task-notification"}}
        elif forma == "usuario":
            entrada = {"isSidechain": False, "type": "user", "timestamp": quando,
                       "message": {"role": "user", "content": bloco}, "origin": {"kind": "task-notification"}}
        elif forma == "fila":
            entrada = {"type": "queue-operation", "operation": "enqueue", "timestamp": quando, "content": bloco}
        elif forma == "prompt-humano-enfileirado":
            entrada = {"isSidechain": False, "type": "attachment", "timestamp": quando,
                       "attachment": {"type": "queued_command", "prompt": bloco, "commandMode": "prompt",
                                      "origin": {"kind": "human"}}}
        elif forma == "usuario-humano":
            entrada = {"isSidechain": False, "type": "user", "timestamp": quando,
                       "message": {"role": "user", "content": bloco}, "origin": {"kind": "human"}}
        elif forma == "assistente":
            entrada = {"isSidechain": False, "type": "assistant", "timestamp": quando,
                       "message": {"role": "assistant", "content": [{"type": "text", "text": bloco}]}}
        elif forma == "resultado-de-ferramenta":
            entrada = {"isSidechain": False, "type": "user", "timestamp": quando,
                       "message": {"role": "user", "content": [{"type": "tool_result", "content": bloco}]},
                       "toolUseResult": {"stdout": bloco, "stderr": ""}, "origin": {"kind": "task-notification"}}
        elif forma == "subagente":
            entrada = {"isSidechain": True, "type": "attachment", "timestamp": quando,
                       "attachment": {"type": "queued_command", "prompt": bloco, "commandMode": "task-notification"}}
        elif forma == "anexo-de-arquivo":
            entrada = {"isSidechain": False, "type": "attachment", "timestamp": quando,
                       "attachment": {"type": "file", "content": bloco}}
        else:
            raise AssertionError(forma)
        self.entradas.append(entrada)
        self._grava()

    def toca(self, arquivo: str = "a.py"):
        """Uma escrita depois do lancamento, pelo caminho de producao (PostToolUse de Edit)."""
        self.database.touch_file(self.task_id, str(self.cwd / arquivo), origem="edit")

    def stop(self) -> str:
        return hook.handle_payload(
            {"hook_event_name": "Stop", "cwd": str(self.cwd), "session_id": "session-a",
             "transcript_path": str(self.transcript)},
            harness_root=self.root,
        )

    def lancamentos(self) -> list[dict]:
        return self.database.lancamentos(self.task_id)

    def evidencias(self) -> list[dict]:
        return _evidencias(self.database, self.task_id)


@pytest.fixture
def sessao(tmp_path: Path) -> Sessao:
    return Sessao(tmp_path)


# --- Fase 2: lancamento -----------------------------------------------------------


def test_lancamento_em_segundo_plano_fica_registrado_sem_evidencia(sessao: Sessao):
    """AC-1.1: job, tool_use_id, transcript e a revisao logo depois do proprio toque."""
    antes = sessao.atual()["code_revision"]

    sessao.lanca("bjob1")

    (lancamento,) = sessao.lancamentos()
    assert lancamento["job_id"] == "bjob1"
    assert lancamento["tool_use_id"] == U
    assert lancamento["transcript_path"] == str(sessao.transcript)
    assert lancamento["command"] == "python -m pytest -q"
    assert lancamento["code_revision"] == antes + 1 == sessao.atual()["code_revision"]
    assert lancamento["estado"] == "pendente"
    assert sessao.evidencias() == []


def test_lancamento_repetido_nao_duplica(sessao: Sessao):
    """AC-1.6: hook registrado duas vezes, ou PostToolUse e PostToolUseFailure da mesma chamada."""
    sessao.lanca("bjob1")
    sessao.lanca("bjob1")

    assert [l["job_id"] for l in sessao.lancamentos()] == ["bjob1"]


def test_comando_composto_em_segundo_plano_nao_e_lancamento(sessao: Sessao):
    """So verificacao confiavel vira lancamento; `pytest | tail` pode fabricar saida."""
    sessao.lanca("bjob1", comando="python -m pytest -q | tail -5")

    assert sessao.lancamentos() == []


# --- Fase 2: captura no Stop (US-1) ---------------------------------------------


def test_suite_verde_em_segundo_plano_verifica_no_stop(sessao: Sessao):
    """AC-1.2, metade 1 da falsificacao (L-07)."""
    sessao.lanca("bjob1")
    revisao = sessao.atual()["code_revision"]
    sessao.saida("bjob1", VERDE)
    sessao.notifica(sessao.bloco("bjob1"))

    saida = sessao.stop()

    assert saida == "", "task verificada: o Stop nao bloqueia"
    (evidencia,) = sessao.evidencias()
    assert evidencia["code_revision"] == revisao
    assert (evidencia["exit_code"], evidencia["tests_collected"], evidencia["tests_passed"],
            evidencia["tests_skipped"]) == (0, 4, 3, 1)
    assert sessao.atual()["verified"] is True
    assert sessao.atual()["stop_continuations"] == 0
    (lancamento,) = sessao.lancamentos()
    assert lancamento["estado"] == "capturado"
    assert lancamento["evidence_id"] == evidencia["id"]


def test_captura_feita_nao_se_repete(sessao: Sessao):
    """AC-1.4: nem evidencia nem evento de novo."""
    sessao.lanca("bjob1")
    sessao.saida("bjob1", VERDE)
    sessao.notifica(sessao.bloco("bjob1"))
    sessao.stop()

    sessao.stop()
    assert _modulo().capturar_lancamentos(sessao.database, sessao.task_id) == 0
    assert len(sessao.evidencias()) == 1
    with sqlite3.connect(sessao.database.path) as raw:
        eventos = raw.execute(
            "SELECT COUNT(*) FROM events WHERE task_id = ? AND event_type = 'lancamento_resolvido'",
            (sessao.task_id,),
        ).fetchone()[0]
    assert eventos == 1


def test_powershell_em_segundo_plano_e_capturado_igual(sessao: Sessao):
    """AC-1.5: 107 lancamentos PowerShell medidos, mesmo trailer, UTF-8."""
    sessao.lanca("bjob1", ferramenta="PowerShell")
    sessao.saida("bjob1", VERDE)
    sessao.notifica(sessao.bloco("bjob1"))

    sessao.stop()

    assert sessao.atual()["verified"] is True


@pytest.mark.parametrize("forma", ["anexo", "anexo-sem-origin", "usuario"])
def test_formas_do_host_sao_aceitas(sessao: Sessao, forma: str):
    """ASSUMPTION-007: `queued_command` com commandMode do host, ou `user` com origin do host."""
    sessao.lanca("bjob1")
    sessao.saida("bjob1", VERDE)
    sessao.notifica(sessao.bloco("bjob1"), forma=forma)

    sessao.stop()

    assert [l["estado"] for l in sessao.lancamentos()] == ["capturado"]


@pytest.mark.parametrize(
    "forma",
    ["fila", "prompt-humano-enfileirado", "usuario-humano", "assistente", "resultado-de-ferramenta",
     "subagente", "anexo-de-arquivo"],
)
def test_formas_que_nao_sao_do_host_sao_recusadas(sessao: Sessao, forma: str):
    """AC-3.7: texto colado, citado ou escrito pelo modelo nao prova termino."""
    sessao.lanca("bjob1")
    sessao.saida("bjob1", VERDE)
    sessao.notifica(sessao.bloco("bjob1"), forma=forma)

    sessao.stop()

    assert [l["estado"] for l in sessao.lancamentos()] == ["pendente"]
    assert sessao.evidencias() == []
    assert sessao.atual()["verified"] is False


def test_insumo_real_pela_funcao_de_producao(sessao: Sessao):
    """Success Criteria: o job `b8jmxvjka` real, lancado e terminado pelo caminho de producao.

    Unica alteracao no insumo: o caminho de `<output-file>` e reescrito para o
    `tmp_path` (o original apontava para o `%TEMP%` desta maquina).
    """
    job, uid = "b8jmxvjka", "toolu_01E8XvW2dqajQ6M9XdenSpCK"
    linhas = [json.loads(l) for l in (INSUMO / "transcript.jsonl").read_text(encoding="utf-8").splitlines()]
    lancamento = linhas[0]
    assert lancamento["toolUseResult"]["backgroundTaskId"] == job
    original = (
        "C:\\Users\\LHarden2\\AppData\\Local\\Temp\\claude\\C--Users-LHarden2-Documents-projects-harness4claude"
        "--claude-worktrees-exciting-bhabha-3659cc\\037312e7-3781-4ec6-b5b6-813d7af1f567\\tasks\\b8jmxvjka.output"
    )
    destino = sessao.tasks / f"{job}.output"
    destino.write_bytes((INSUMO / "b8jmxvjka.output").read_bytes())
    texto = (INSUMO / "transcript.jsonl").read_text(encoding="utf-8").replace(
        json.dumps(original)[1:-1], json.dumps(str(destino))[1:-1]
    )
    assert texto != (INSUMO / "transcript.jsonl").read_text(encoding="utf-8"), "a reescrita tem de ter acontecido"
    sessao.transcript.write_text(texto, encoding="utf-8")
    hook.handle_payload(
        {"hook_event_name": "PostToolUse", "cwd": str(sessao.cwd), "session_id": "session-a",
         "tool_name": "Bash", "tool_input": {"command": "python -m pytest -q -p no:cacheprovider"},
         "tool_response": lancamento["toolUseResult"], "tool_use_id": uid,
         "transcript_path": str(sessao.transcript)},
        harness_root=sessao.root,
    )

    sessao.stop()

    (evidencia,) = sessao.evidencias()
    assert (evidencia["exit_code"], evidencia["tests_collected"], evidencia["tests_passed"],
            evidencia["tests_skipped"]) == (1, 1613, 1610, 1)
    assert sessao.atual()["verified"] is False
    assert [l["estado"] for l in sessao.lancamentos()] == ["capturado"]


# --- Fase 2: sem prova, nao verifica (US-3) ---------------------------------------


def test_sem_notificacao_continua_pendente(sessao: Sessao):
    """AC-3.1."""
    sessao.lanca("bjob1")
    sessao.saida("bjob1", VERDE)

    sessao.stop()

    assert [l["estado"] for l in sessao.lancamentos()] == ["pendente"]
    assert sessao.evidencias() == []


@pytest.mark.parametrize(
    ("status", "resumo"),
    [
        ("stopped", 'Background command "suite" was stopped'),
        ("killed", 'Background command "suite" was killed'),
        ("completed", "No completion record was found for this background shell command from the previous session"),
        ("failed", "Background shell command didn't finish before the previous session ended"),
    ],
)
def test_sem_codigo_de_saida_rejeita(sessao: Sessao, status: str, resumo: str):
    """AC-3.2."""
    sessao.lanca("bjob1")
    sessao.saida("bjob1", VERDE)
    sessao.notifica(sessao.bloco("bjob1", status=status, resumo=resumo))

    sessao.stop()

    (lancamento,) = sessao.lancamentos()
    assert (lancamento["estado"], lancamento["motivo"]) == ("rejeitado", "sem-codigo")
    assert sessao.evidencias() == []


@pytest.mark.parametrize("status", ["stopped", "killed"])
def test_status_sem_termino_normal_rejeita_mesmo_com_codigo(sessao: Sessao, status: str):
    """AC-3.2, grill #7: job parado nao terminou a suite, ainda que o resumo traga codigo.

    Morto por M10 (sem a exigencia de status): nenhum teste de `stopped`/`killed`
    trazia codigo no resumo, e o mutante sobreviveu.
    """
    sessao.lanca("bjob1")
    sessao.saida("bjob1", VERDE)
    sessao.notifica(sessao.bloco("bjob1", status=status, resumo=f'Background command "suite" was {status} (exit code 0)'))

    sessao.stop()

    (lancamento,) = sessao.lancamentos()
    assert (lancamento["estado"], lancamento["motivo"]) == ("rejeitado", "sem-codigo")
    assert sessao.atual()["verified"] is False


def test_suite_vermelha_e_capturada_e_nao_verifica(sessao: Sessao):
    """AC-3.3: o mesmo que o primeiro plano faria."""
    sessao.lanca("bjob1")
    sessao.saida("bjob1", VERMELHO)
    sessao.notifica(sessao.bloco("bjob1", status="failed"))

    sessao.stop()

    (evidencia,) = sessao.evidencias()
    assert (evidencia["exit_code"], evidencia["tests_collected"], evidencia["tests_passed"]) == (1, 3, 2)
    assert sessao.atual()["verified"] is False
    assert [l["estado"] for l in sessao.lancamentos()] == ["capturado"]


def test_completed_com_codigo_diferente_de_zero_e_aceito(sessao: Sessao):
    """Forma medida: `completed (exit code 1: No matches found)`."""
    sessao.lanca("bjob1")
    sessao.saida("bjob1", VERMELHO)
    sessao.notifica(sessao.bloco("bjob1", resumo='Background command "suite" completed (exit code 1: No matches found)'))

    sessao.stop()

    (evidencia,) = sessao.evidencias()
    assert evidencia["exit_code"] == 1


def test_arquivo_de_saida_inexistente_rejeita(sessao: Sessao):
    """AC-3.4."""
    sessao.lanca("bjob1")
    sessao.notifica(sessao.bloco("bjob1"))

    sessao.stop()

    (lancamento,) = sessao.lancamentos()
    assert (lancamento["estado"], lancamento["motivo"]) == ("rejeitado", "sem-arquivo")
    assert sessao.evidencias() == []


def test_trailer_diverge_do_resumo_rejeita(sessao: Sessao):
    """AC-3.5: 0 divergencias em 226 medidas; se acontecer, nao e prova."""
    sessao.lanca("bjob1")
    sessao.saida("bjob1", "3 passed in 0.10s\r\n\n[exited with code 1]\n")
    sessao.notifica(sessao.bloco("bjob1"))

    sessao.stop()

    (lancamento,) = sessao.lancamentos()
    assert (lancamento["estado"], lancamento["motivo"]) == ("rejeitado", "codigo-diverge")
    assert sessao.evidencias() == []


def test_tool_use_id_diferente_nao_casa(sessao: Sessao):
    """AC-3.6."""
    sessao.lanca("bjob1")
    sessao.saida("bjob1", VERDE)
    sessao.notifica(sessao.bloco("bjob1", tool_use_id="toolu_outro"))

    sessao.stop()

    assert [l["estado"] for l in sessao.lancamentos()] == ["pendente"]


def test_lancamento_sem_tool_use_id_nunca_casa(sessao: Sessao):
    """AC-3.6, a outra metade: payload sem o campo."""
    sessao.lanca("bjob1", tool_use_id=None)
    sessao.saida("bjob1", VERDE)
    sessao.notifica(sessao.bloco("bjob1"))

    sessao.stop()

    assert [l["estado"] for l in sessao.lancamentos()] == ["pendente"]
    assert sessao.evidencias() == []


@pytest.mark.parametrize("onde", ["nome", "pasta-de-outra-sessao", "pasta-de-outro-projeto"])
def test_arquivo_de_saida_fora_do_lugar_rejeita(sessao: Sessao, onde: str):
    """AC-3.8: nome `J.output` e pasta `<projeto>/<sessao>/tasks` do transcript."""
    sessao.lanca("bjob1")
    if onde == "nome":
        arquivo = sessao.tasks / "outro.output"
    elif onde == "pasta-de-outra-sessao":
        arquivo = sessao.tmp / "temp" / "claude" / PROJETO / "outra-sessao" / "tasks" / "bjob1.output"
    else:
        arquivo = sessao.tmp / "temp" / "claude" / "C--outro" / SESSAO / "tasks" / "bjob1.output"
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    arquivo.write_text(VERDE, encoding="utf-8")
    sessao.notifica(sessao.bloco("bjob1", arquivo=arquivo))

    sessao.stop()

    (lancamento,) = sessao.lancamentos()
    assert (lancamento["estado"], lancamento["motivo"]) == ("rejeitado", "arquivo-estranho")
    assert sessao.evidencias() == []


def test_saida_sem_contagem_grava_e_nao_verifica(sessao: Sessao):
    """AC-3.9: a regua de hoje decide."""
    sessao.lanca("bjob1")
    sessao.saida("bjob1", "no tests ran in 0.01s\r\n\n[exited with code 0]\n")
    sessao.notifica(sessao.bloco("bjob1"))

    sessao.stop()

    (evidencia,) = sessao.evidencias()
    assert evidencia["tests_collected"] == 0
    assert sessao.atual()["verified"] is False


def test_arquivo_que_nao_abre_fica_pendente(sessao: Sessao):
    """AC-3.10: erro de leitura que nao e ausencia (aqui, um diretorio no lugar) tenta de novo."""
    sessao.lanca("bjob1")
    (sessao.tasks / "bjob1.output").mkdir()
    sessao.notifica(sessao.bloco("bjob1"))

    sessao.stop()

    assert [l["estado"] for l in sessao.lancamentos()] == ["pendente"]


def test_transcript_ausente_fica_pendente_e_stop_decide_como_hoje(sessao: Sessao):
    """AC-3.10."""
    sessao.lanca("bjob1")
    sessao.transcript.unlink()

    saida = json.loads(sessao.stop())

    assert saida["decision"] == "block"
    assert [l["estado"] for l in sessao.lancamentos()] == ["pendente"]


def test_notificacoes_que_discordam_rejeitam(sessao: Sessao):
    """AC-3.11: duas terminais aceitas de J, status diferente."""
    sessao.lanca("bjob1")
    sessao.saida("bjob1", VERDE)
    sessao.notifica(sessao.bloco("bjob1"))
    sessao.notifica(sessao.bloco("bjob1", status="stopped", resumo='Background command "suite" was stopped'),
                    forma="usuario", quando="2026-09-30T12:22:00.000Z")

    sessao.stop()

    (lancamento,) = sessao.lancamentos()
    assert (lancamento["estado"], lancamento["motivo"]) == ("rejeitado", "notificacao-diverge")


def test_copias_identicas_da_mesma_notificacao_contam_como_uma(sessao: Sessao):
    sessao.lanca("bjob1")
    sessao.saida("bjob1", VERDE)
    bloco = sessao.bloco("bjob1")
    sessao.notifica(bloco, forma="anexo")
    sessao.notifica(bloco, forma="usuario")

    sessao.stop()

    assert [l["estado"] for l in sessao.lancamentos()] == ["capturado"]


def test_failed_com_codigo_zero_rejeita(sessao: Sessao):
    """AC-3.12."""
    sessao.lanca("bjob1")
    sessao.saida("bjob1", VERDE)
    sessao.notifica(sessao.bloco("bjob1", status="failed", resumo='Background command "suite" failed with exit code 0'))

    sessao.stop()

    (lancamento,) = sessao.lancamentos()
    assert (lancamento["estado"], lancamento["motivo"]) == ("rejeitado", "status-inconsistente")


# --- Fase 2: captura no `complete` (AC-1.3) ---------------------------------------


def test_complete_captura_antes_de_recusar(sessao: Sessao):
    """O modelo acorda com o job terminado e fecha no mesmo turno, sem Stop no meio."""
    for fase in ("tdd", "verify"):
        sessao.database.transition(sessao.task_id, fase, expected_revision=sessao.atual()["revision"])
    sessao.lanca("bjob1")
    sessao.saida("bjob1", VERDE)
    sessao.notifica(sessao.bloco("bjob1"))
    lida_antes = sessao.atual()["revision"]

    fechada = sessao.database.complete(sessao.task_id, expected_revision=lida_antes)

    assert fechada["status"] == "done"
    assert [l["estado"] for l in sessao.lancamentos()] == ["capturado"]


def test_CONTROLE_complete_sem_lancamento_recusa_como_hoje(sessao: Sessao):
    for fase in ("tdd", "verify"):
        sessao.database.transition(sessao.task_id, fase, expected_revision=sessao.atual()["revision"])

    with pytest.raises(state.StateTransitionError, match="fresh verification evidence"):
        sessao.database.complete(sessao.task_id, expected_revision=sessao.atual()["revision"])


def test_complete_com_revisao_velha_recusa_sem_capturar(sessao: Sessao):
    """Contrato do design: CAS errado recusa antes de qualquer escrita."""
    for fase in ("tdd", "verify"):
        sessao.database.transition(sessao.task_id, fase, expected_revision=sessao.atual()["revision"])
    sessao.lanca("bjob1")
    sessao.saida("bjob1", VERDE)
    sessao.notifica(sessao.bloco("bjob1"))

    with pytest.raises(state.StateTransitionError, match="revision mismatch"):
        sessao.database.complete(sessao.task_id, expected_revision=sessao.atual()["revision"] - 1)

    assert [l["estado"] for l in sessao.lancamentos()] == ["pendente"]


# --- Fase 3: revisao mudou desde o lancamento (US-2) -----------------------------


def test_revisao_mudou_desde_o_lancamento_vira_historico(sessao: Sessao):
    """AC-2.1, metade 2 da falsificacao (L-07): verde em N', codigo em N'+1."""
    sessao.lanca("bjob1")
    revisao_do_lancamento = sessao.atual()["code_revision"]
    sessao.toca("a.py")
    sessao.saida("bjob1", VERDE)
    sessao.notifica(sessao.bloco("bjob1"))

    saida = json.loads(sessao.stop())

    assert saida["decision"] == "block"
    (evidencia,) = sessao.evidencias()
    assert evidencia["code_revision"] == revisao_do_lancamento
    assert evidencia["code_revision"] != sessao.atual()["code_revision"]
    assert sessao.atual()["verified"] is False
    assert [l["estado"] for l in sessao.lancamentos()] == ["historico"]


def test_historico_nao_verifica_nos_stops_seguintes(sessao: Sessao):
    """AC-2.4: a revisao so sobe; a linha de N' nunca decide `verified`."""
    sessao.lanca("bjob1")
    sessao.toca("a.py")
    sessao.saida("bjob1", VERDE)
    sessao.notifica(sessao.bloco("bjob1"))
    sessao.stop()

    sessao.toca("b.py")
    sessao.stop()

    assert sessao.atual()["verified"] is False
    assert len(sessao.evidencias()) == 1


def test_historico_vermelho_nao_desverifica_primeiro_plano_verde(sessao: Sessao):
    """AC-2.2 pelo hook: pytest em primeiro plano verde em N'+1, suite de N' termina vermelha."""
    sessao.lanca("bjob1")
    hook.handle_payload(
        {"hook_event_name": "PostToolUse", "cwd": str(sessao.cwd), "session_id": "session-a",
         "tool_name": "Bash", "tool_input": {"command": "python -m pytest -q tests/test_x.py"},
         "tool_response": {"stdout": "3 passed in 0.01s", "stderr": "", "interrupted": False}},
        harness_root=sessao.root,
    )
    assert sessao.atual()["verified"] is True
    sessao.saida("bjob1", VERMELHO)
    sessao.notifica(sessao.bloco("bjob1", status="failed"))

    sessao.stop()

    assert sessao.atual()["verified"] is True
    assert [l["estado"] for l in sessao.lancamentos()] == ["historico"]


# --- Fase 4: quem terminou por ultimo decide (US-4) -------------------------------


def _iso(minutos: float) -> str:
    """Carimbo relativo ao relogio real: `created_at` de evidencia e `datetime.now`."""
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) + timedelta(minutes=minutos)).isoformat().replace("+00:00", "Z")


def _evidencia_direta(sessao: Sessao, *, verde: bool):
    """Evidencia gravada agora, na revisao corrente (o que o `state_cli evidence` faria)."""
    sessao.database.record_evidence(
        sessao.task_id, evidence_type="test", command="python -m pytest -q",
        exit_code=0 if verde else 1, tests_collected=3, tests_passed=3 if verde else 2,
        tests_skipped=0, output_hash="direta",
    )


def test_captura_atrasada_nao_cobre_vermelho_mais_novo(sessao: Sessao):
    """AC-4.1: verde terminou ha 10 min; vermelho da mesma revisao gravado agora."""
    sessao.lanca("bjob1")
    _evidencia_direta(sessao, verde=False)
    sessao.saida("bjob1", VERDE)
    sessao.notifica(sessao.bloco("bjob1"), quando=_iso(-10))

    sessao.stop()

    assert sessao.atual()["verified"] is False
    assert len(sessao.evidencias()) == 1
    (lancamento,) = sessao.lancamentos()
    assert lancamento["estado"] == "superado"


def test_captura_mais_nova_que_evidencia_gravada_vale(sessao: Sessao):
    """AC-4.2: verde gravado agora; a suite em segundo plano terminou depois, vermelha."""
    sessao.lanca("bjob1")
    _evidencia_direta(sessao, verde=True)
    assert sessao.atual()["verified"] is True
    sessao.saida("bjob1", VERMELHO)
    sessao.notifica(sessao.bloco("bjob1", status="failed"), quando=_iso(+1))

    sessao.stop()

    assert sessao.atual()["verified"] is False
    assert [l["estado"] for l in sessao.lancamentos()] == ["capturado"]


def test_dois_lancamentos_na_mesma_revisao_decide_o_ultimo_a_terminar(sessao: Sessao):
    """AC-4.3: J2 verde terminou antes, J1 vermelho depois; os dois capturados no mesmo Stop.

    A captura de J2 grava a linha com `created_at` = agora, depois do termino de
    J1. Se a regra `superado` olhasse `created_at` da linha capturada, J1 sairia
    `superado` por uma execucao que terminou ANTES dele.
    """
    sessao.lanca("bjob1")
    sessao.database.registrar_lancamento(
        sessao.task_id, job_id="bjob2", tool_use_id="toolu_02", command="python -m pytest -q tests/a",
        transcript_path=str(sessao.transcript),
    )
    sessao.saida("bjob1", VERMELHO)
    sessao.saida("bjob2", VERDE)
    sessao.notifica(sessao.bloco("bjob1", status="failed"), quando=_iso(-5))
    sessao.notifica(sessao.bloco("bjob2", tool_use_id="toolu_02"), quando=_iso(-10))

    sessao.stop()

    estados = {l["job_id"]: l["estado"] for l in sessao.lancamentos()}
    assert estados == {"bjob1": "capturado", "bjob2": "capturado"}
    assert [e["exit_code"] for e in sessao.evidencias()] == [0, 1], "J2 gravado antes de J1"
    assert sessao.atual()["verified"] is False


# --- Fase 5: o portao diz o que aconteceu (US-5) ----------------------------------


def test_mensagem_do_stop_lista_os_lancamentos(sessao: Sessao):
    """AC-5.1: id, revisao, estado e motivo — pendente acumulado e o alarme de formato novo."""
    sessao.lanca("bjob1")
    revisao = sessao.atual()["code_revision"]
    sessao.database.registrar_lancamento(
        sessao.task_id, job_id="bjob2", tool_use_id="toolu_02", command="python -m pytest -q",
        transcript_path=str(sessao.transcript),
    )
    sessao.notifica(sessao.bloco("bjob2", tool_use_id="toolu_02"))

    motivo = json.loads(sessao.stop())["reason"]

    assert f"bjob1 rev={revisao} pendente" in motivo
    assert f"bjob2 rev={revisao} rejeitado:sem-arquivo" in motivo


def test_recusa_do_complete_lista_os_lancamentos(sessao: Sessao):
    for fase in ("tdd", "verify"):
        sessao.database.transition(sessao.task_id, fase, expected_revision=sessao.atual()["revision"])
    sessao.lanca("bjob1")

    with pytest.raises(state.StateTransitionError) as recusa:
        sessao.database.complete(sessao.task_id, expected_revision=sessao.atual()["revision"])

    assert "fresh verification evidence" in str(recusa.value)
    assert "bjob1" in str(recusa.value) and "pendente" in str(recusa.value)


def test_aviso_do_lancamento_anuncia_a_captura(sessao: Sessao):
    """AC-5.3: automatica, na revisao N', e a condicao da receita manual."""
    aviso = sessao.lanca("bjob1")
    revisao = sessao.atual()["code_revision"]

    assert "bjob1" in aviso
    assert f"code_revision {revisao}" in aviso
    assert "capturada" in aviso
    assert "state_cli.py" in aviso


def test_evento_de_resolucao_tem_job_estado_motivo_e_revisao(sessao: Sessao):
    """AC-5.2."""
    sessao.lanca("bjob1")
    revisao = sessao.atual()["code_revision"]
    sessao.notifica(sessao.bloco("bjob1"))

    sessao.stop()

    with sqlite3.connect(sessao.database.path) as raw:
        (payload,) = raw.execute(
            "SELECT payload_json FROM events WHERE task_id = ? AND event_type = 'lancamento_resolvido'",
            (sessao.task_id,),
        ).fetchone()
    assert json.loads(payload) == {
        "job": "bjob1", "estado": "rejeitado", "motivo": "sem-arquivo",
        "code_revision": revisao, "evidence_id": None,
    }
