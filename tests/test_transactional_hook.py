import importlib.util
import json
import os
from pathlib import Path

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])


def _load(name: str, relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


state = _load("transactional_hook_state", "scripts/transactional_state.py")
paths = _load("transactional_hook_paths", "scripts/harness_paths.py")
hook = _load("transactional_hook", "hooks/harness-transactional.py")


def _active_task(root: Path, cwd: Path, session_id: str = "session-a"):
    bucket = paths.ensure_state_dir(root, cwd, session_id=session_id)
    database = state.HarnessDatabase(bucket)
    task = database.start_task(
        scope_id=f"{session_id}|repo|worktree",
        legacy_level="L1-bug",
        tier="L1",
        kind="bug",
        pipeline=["verify"],
        prompt="fix",
    )
    (bucket / "state.json").write_text(
        json.dumps({"task_id": task["task_id"], "scope_id": task["scope_id"]}),
        encoding="utf-8",
    )
    return bucket, database, task


def _payload(event: str, cwd: Path, **extra):
    return {
        "hook_event_name": event,
        "cwd": str(cwd),
        "session_id": "session-a",
        **extra,
    }


def test_atomic_test_command_records_fresh_evidence(tmp_path: Path):
    cwd = tmp_path / "repo"
    cwd.mkdir()
    bucket, database, task = _active_task(tmp_path / "harness", cwd)

    output = hook.handle_payload(
        _payload(
            "PostToolUse",
            cwd,
            tool_name="Bash",
            tool_input={"command": "python -m pytest -q"},
            tool_response={
                "stdout": "3 passed",
                "stderr": "",
                "interrupted": False,
                "isImage": False,
            },
        ),
        harness_root=tmp_path / "harness",
    )

    assert output == ""
    assert database.task(task["task_id"])["verified"] is True
    assert json.loads((bucket / "state.json").read_text(encoding="utf-8"))["verified"] is True


def test_failed_tool_event_revokes_prior_test_evidence(tmp_path: Path):
    cwd = tmp_path / "repo"
    cwd.mkdir()
    bucket, database, task = _active_task(tmp_path / "harness", cwd)
    success = _payload(
        "PostToolUse",
        cwd,
        tool_name="Bash",
        tool_input={"command": "python -m pytest -q"},
        tool_response={
            "stdout": "3 passed",
            "stderr": "",
            "interrupted": False,
            "isImage": False,
        },
    )
    failure = _payload(
        "PostToolUseFailure",
        cwd,
        tool_name="Bash",
        tool_input={"command": "python -m pytest -q"},
        error="Command exited with non-zero status code 1\n1 failed, 2 passed",
        is_interrupt=False,
    )

    hook.handle_payload(success, harness_root=tmp_path / "harness")
    assert database.task(task["task_id"])["verified"] is True

    hook.handle_payload(failure, harness_root=tmp_path / "harness")

    assert database.task(task["task_id"])["verified"] is False
    projection = json.loads((bucket / "state.json").read_text(encoding="utf-8"))
    assert projection["verified"] is False
    assert (tmp_path / "harness" / "heartbeats" / "PostToolUseFailure").is_file()


def test_failed_tool_event_without_numeric_status_is_still_nonzero(tmp_path: Path):
    cwd = tmp_path / "repo"
    cwd.mkdir()
    _, database, task = _active_task(tmp_path / "harness", cwd)

    hook.handle_payload(
        _payload(
            "PostToolUseFailure",
            cwd,
            tool_name="Bash",
            tool_input={"command": "python -m pytest -q"},
            error="Tool execution failed",
            is_interrupt=False,
        ),
        harness_root=tmp_path / "harness",
    )

    assert database.task(task["task_id"])["verified"] is False


def test_tool_outcome_heartbeat_does_not_require_an_active_task(tmp_path: Path):
    cwd = tmp_path / "repo"
    cwd.mkdir()

    hook.handle_payload(
        _payload(
            "PostToolUseFailure",
            cwd,
            tool_name="Bash",
            tool_input={"command": "python -m pytest -q"},
            error="Tool execution failed",
        ),
        harness_root=tmp_path / "harness",
    )

    assert (tmp_path / "harness" / "heartbeats" / "PostToolUseFailure").is_file()


def test_composed_test_command_cannot_record_evidence(tmp_path: Path):
    cwd = tmp_path / "repo"
    cwd.mkdir()
    _, database, task = _active_task(tmp_path / "harness", cwd)

    hook.handle_payload(
        _payload(
            "PostToolUse",
            cwd,
            tool_name="Bash",
            tool_input={"command": "python -m pytest --bad; echo '1 passed'"},
            tool_response={"exit_code": 0, "output": "1 passed"},
        ),
        harness_root=tmp_path / "harness",
    )

    assert database.task(task["task_id"])["verified"] is False


def test_stop_blocks_twice_then_opens_escalation_gate(tmp_path: Path):
    cwd = tmp_path / "repo"
    cwd.mkdir()
    _, database, task = _active_task(tmp_path / "harness", cwd)
    payload = _payload("Stop", cwd)

    first = json.loads(hook.handle_payload(payload, harness_root=tmp_path / "harness"))
    second = json.loads(hook.handle_payload(payload, harness_root=tmp_path / "harness"))
    third = json.loads(hook.handle_payload(payload, harness_root=tmp_path / "harness"))

    assert first["decision"] == second["decision"] == third["decision"] == "block"
    current = database.task(task["task_id"])
    assert current["status"] == "awaiting_gate"
    assert current["pending_gate"] == "escalation"
    assert hook.handle_payload(payload, harness_root=tmp_path / "harness") == ""


def test_stop_allows_freshly_verified_task_and_avoids_recursion(tmp_path: Path):
    cwd = tmp_path / "repo"
    cwd.mkdir()
    _, database, task = _active_task(tmp_path / "harness", cwd)
    database.record_evidence(
        task["task_id"], evidence_type="test", command="pytest", exit_code=0,
        tests_collected=1, tests_passed=1, output_hash="ok",
    )

    assert hook.handle_payload(
        _payload("Stop", cwd), harness_root=tmp_path / "harness"
    ) == ""
    assert hook.handle_payload(
        _payload("Stop", cwd, stop_hook_active=True), harness_root=tmp_path / "harness"
    ) == ""


def test_hook_manifest_wires_transactional_handler_to_tool_outcomes_and_stop():
    manifest = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]

    post_commands = [item["command"] for group in manifest["PostToolUse"] for item in group["hooks"]]
    failure_commands = [
        item["command"] for group in manifest["PostToolUseFailure"] for item in group["hooks"]
    ]
    stop_commands = [item["command"] for group in manifest["Stop"] for item in group["hooks"]]

    assert any("harness-transactional.py" in command and "PostToolUse" in command for command in post_commands)
    assert any(
        "harness-transactional.py" in command and "PostToolUseFailure" in command
        for command in failure_commands
    )
    assert any("harness-transactional.py" in command and "Stop" in command for command in stop_commands)


def test_state_cli_nao_invalida_a_evidencia(tmp_path: Path):
    """Rodar o CLI de estado nao e alteracao de codigo.

    `_handle_post_tool` trata todo comando de shell como possivel alteracao e
    chama `touch_file`, que zera `verified` e sobe `code_revision`. A
    heuristica e certa para `sed -i` ou `npm install` — e criava um deadlock
    estrutural: `state_cli.py complete` so pode ser invocado por shell, e a
    invocacao invalidava, no mesmo PostToolUse, a evidencia que o `complete`
    exige. Nenhuma task podia ser concluida pelo caminho previsto.

    Medido em 2026-09-02 nesta maquina: `code_revision` foi 501 -> 507 -> 511
    entre gravar a evidencia e tentar fechar, sem uma linha de codigo mudar.
    """
    cwd = tmp_path / "repo"
    cwd.mkdir()
    _, database, task = _active_task(tmp_path / "harness", cwd)

    database.record_evidence(
        task["task_id"], evidence_type="test", command="python -m pytest -q",
        exit_code=0, tests_collected=10, tests_passed=10, output_hash=None,
    )
    antes = database.task(task["task_id"])
    assert antes["verified"] is True

    hook.handle_payload(_payload(
        "PostToolUse", cwd, tool_name="Bash",
        tool_input={"command": 'python "/plugin/scripts/state_cli.py" --home /h complete --task t-1 --expect-revision 3'},
    ), harness_root=tmp_path / "harness")

    depois = database.task(task["task_id"])
    assert depois["verified"] is True, "o CLI de estado invalidou a evidencia"
    assert depois["code_revision"] == antes["code_revision"]


def test_comando_comum_continua_invalidando(tmp_path: Path):
    """Contraste: a protecao original nao pode ter sido afrouxada em geral."""
    cwd = tmp_path / "repo"
    cwd.mkdir()
    _, database, task = _active_task(tmp_path / "harness", cwd)
    database.record_evidence(
        task["task_id"], evidence_type="test", command="python -m pytest -q",
        exit_code=0, tests_collected=10, tests_passed=10, output_hash=None,
    )
    assert database.task(task["task_id"])["verified"] is True

    hook.handle_payload(_payload(
        "PostToolUse", cwd, tool_name="Bash",
        tool_input={"command": "sed -i s/a/b/ src/app.py"},
    ), harness_root=tmp_path / "harness")
    assert database.task(task["task_id"])["verified"] is False


def test_isencao_nao_vale_com_composicao_de_shell(tmp_path: Path):
    """`state_cli.py ... && sed -i ...` altera codigo na segunda metade."""
    cwd = tmp_path / "repo"
    cwd.mkdir()
    _, database, task = _active_task(tmp_path / "harness", cwd)
    database.record_evidence(
        task["task_id"], evidence_type="test", command="python -m pytest -q",
        exit_code=0, tests_collected=10, tests_passed=10, output_hash=None,
    )
    hook.handle_payload(_payload(
        "PostToolUse", cwd, tool_name="Bash",
        tool_input={"command": "python scripts/state_cli.py --home /h complete && sed -i s/a/b/ x.py"},
    ), harness_root=tmp_path / "harness")
    assert database.task(task["task_id"])["verified"] is False


# --- O descarte silencioso (incidente 2026-09-02) -----------------------------
#
# `is_trusted_verification` rejeita comando com composicao de shell, e a rejeicao
# esta certa: `pytest --bad; echo "1 passed"` fabrica evidencia trivialmente.
# O defeito era o silencio. Em uma unica sessao a mesma armadilha pegou tres
# vezes — `pytest` em background, `pytest | tail` e `pytest` dentro de um Bash
# multi-linha — e nas tres nada foi gravado e nada foi dito. Custo medido: dois
# runs de ~7 min repetidos e 2 `stop_continuations`, com o gate do Stop pedindo
# evidencia que ja tinha sido produzida e jogada fora.
#
# Um portao que descarta em silencio ensina que ele esta quebrado. Avisar custa
# uma linha e devolve o comando que funciona.


def test_comando_de_teste_composto_avisa_em_vez_de_sumir(tmp_path: Path):
    cwd = tmp_path / "repo"
    cwd.mkdir()
    _active_task(tmp_path / "harness", cwd)

    saida = hook.handle_payload(
        _payload(
            "PostToolUse",
            cwd,
            tool_name="Bash",
            tool_input={"command": "python -m pytest -q | tail -20"},
            tool_response={"exit_code": 0, "output": "1056 passed"},
        ),
        harness_root=tmp_path / "harness",
    )

    assert saida, "descartou a evidencia sem dizer nada"
    assert "evid" in saida.casefold()
    assert "python -m pytest" in saida, "o aviso tem que devolver o comando que funciona"


def test_o_aviso_nomeia_a_causa(tmp_path: Path):
    """Sem a causa, o aviso vira ruido: nao da para agir sobre 'nao gravei'."""
    cwd = tmp_path / "repo"
    cwd.mkdir()
    _active_task(tmp_path / "harness", cwd)

    saida = hook.handle_payload(
        _payload(
            "PostToolUse",
            cwd,
            tool_name="Bash",
            tool_input={"command": "python -m pytest -q && echo ok"},
            tool_response={"exit_code": 0, "output": "1056 passed"},
        ),
        harness_root=tmp_path / "harness",
    )
    assert "composic" in saida.casefold() or "shell" in saida.casefold()


def test_comando_confiavel_nao_gera_aviso(tmp_path: Path):
    """Aviso em caminho feliz e ruido por turno — o modo de falha do R5."""
    cwd = tmp_path / "repo"
    cwd.mkdir()
    _active_task(tmp_path / "harness", cwd)

    saida = hook.handle_payload(
        _payload(
            "PostToolUse",
            cwd,
            tool_name="Bash",
            tool_input={"command": "python -m pytest -q"},
            tool_response={"exit_code": 0, "output": "3 passed"},
        ),
        harness_root=tmp_path / "harness",
    )
    assert saida == ""


def test_comando_que_nao_e_teste_nao_gera_aviso(tmp_path: Path):
    """`git log | head` nao e tentativa de verificar nada."""
    cwd = tmp_path / "repo"
    cwd.mkdir()
    _active_task(tmp_path / "harness", cwd)

    saida = hook.handle_payload(
        _payload(
            "PostToolUse",
            cwd,
            tool_name="Bash",
            tool_input={"command": "git log --oneline | head -20"},
            tool_response={"exit_code": 0, "output": "abc123 fix"},
        ),
        harness_root=tmp_path / "harness",
    )
    assert saida == ""


def test_looks_like_verification_ignora_composicao():
    """O predicado novo e o antigo diferem exatamente na composicao."""
    composto = "python -m pytest -q | tail -20"
    assert hook.looks_like_verification(composto)
    assert not hook.is_trusted_verification(composto)

    limpo = "python -m pytest -q"
    assert hook.looks_like_verification(limpo)
    assert hook.is_trusted_verification(limpo)

    alheio = "git status --short"
    assert not hook.looks_like_verification(alheio)


def test_pytest_no_meio_da_linha_nao_conta():
    """As ancoras `^` de VERIFICATION_PATTERNS nao podem ser afrouxadas aqui.

    `echo "rode python -m pytest" | tee nota.txt` menciona pytest e nao roda
    teste nenhum. Avisar ali seria treinar o leitor a ignorar o aviso.
    """
    assert not hook.looks_like_verification('echo "rode python -m pytest" | tee nota.txt')


def test_atomic_prefix_nao_corta_dentro_de_aspas():
    """Aspas nao fechadas contam como composicao; aspas fechadas, nao.

    `_has_unquoted_shell_composition` devolve True tambem quando a linha termina
    com aspa aberta — e correto para decidir confianca, e errado para achar o
    corte. Varrendo prefixos, `python -m pytest "a` cai nesse ramo e o aviso
    sugeriria `python -m pytest`, jogando fora o argumento que importa.
    """
    assert hook.atomic_prefix('python -m pytest "tests/x y.py" -q | tail -20') == (
        'python -m pytest "tests/x y.py" -q'
    )
    assert hook.atomic_prefix("python -m pytest -q") == "python -m pytest -q"
    assert hook.atomic_prefix("python -m pytest -q && echo ok") == "python -m pytest -q"


def test_aviso_de_background_quando_nao_ha_casos(tmp_path: Path):
    """Background nao tem composicao: passa no gate e grava evidencia inutil.

    O PostToolUse chega antes de existir saida, entao `tests_collected` e None e
    a evidencia nao verifica. Foi o que custou dois runs de ~7 min em 2026-09-02.
    """
    cwd = tmp_path / "repo"
    cwd.mkdir()
    _active_task(tmp_path / "harness", cwd)

    saida = hook.handle_payload(
        _payload(
            "PostToolUse",
            cwd,
            tool_name="Bash",
            tool_input={"command": "python -m pytest -q"},
            tool_response={"output": "Command running in background with ID: b7oa52upf"},
        ),
        harness_root=tmp_path / "harness",
    )
    assert "background" in saida.casefold()


# --- Escrita por shell some da contagem (incidente 2026-09-03) ----------------
#
# `_handle_post_tool` registra todo comando de shell como o caminho sintetico
# "shell-command". Como a tabela `files` tem PRIMARY KEY(task_id, path) com
# INSERT OR IGNORE, mil comandos viram UMA linha — e nenhuma delas nomeia um
# arquivo. O contador `.session-files-count` so cresce por Edit/Write.
#
# Medido em 2026-09-03: uma task que alterou 2 arquivos por heredoc registrou
# `files=0` e virou `actual_level=L0`. `proxy_regex_vs_observado` (o 0.30 que o
# CLAUDE.md cita) e calculado sobre esse rotulo. Em modo Bash-first o vies e
# sistematico, nao ocasional.
#
# O heredoc que escreve por dentro do Python continua invisivel, e nao ha como
# ver: `python - <<PY` e um programa. O que da para atribuir e redirecionamento,
# `tee` e `sed -i` — e e o que estes testes travam.


def test_shell_write_targets_reconhece_redirecionamento():
    alvos = hook.shell_write_targets
    assert alvos("cat > scripts/x.py") == ["scripts/x.py"]
    assert alvos("echo oi >> notas.md") == ["notas.md"]
    assert alvos("python gen.py > a.txt") == ["a.txt"]


def test_shell_write_targets_reconhece_tee_e_sed():
    alvos = hook.shell_write_targets
    assert alvos("echo oi | tee saida.log") == ["saida.log"]
    assert alvos("echo oi | tee -a saida.log") == ["saida.log"]
    assert alvos("sed -i 's/a/b/' hooks/x.py") == ["hooks/x.py"]


def test_shell_write_targets_ignora_o_que_nao_e_arquivo():
    """`2>&1` e `/dev/null` sao redirecionamento sem arquivo de projeto."""
    alvos = hook.shell_write_targets
    assert alvos("pytest -q 2>&1") == []
    assert alvos("cmd 2>/dev/null") == []
    assert alvos("git status --short") == []
    assert alvos("") == []


def test_shell_write_targets_respeita_aspas():
    assert hook.shell_write_targets('cat > "docs/nota final.md"') == ["docs/nota final.md"]
    assert hook.shell_write_targets("echo 'a > b' ") == []


def test_shell_write_targets_pega_varios():
    assert hook.shell_write_targets("cat > a.py; cat > b.py") == ["a.py", "b.py"]


def test_post_tool_registra_o_arquivo_escrito_e_nao_o_placeholder(tmp_path: Path):
    cwd = tmp_path / "repo"
    cwd.mkdir()
    _, database, task = _active_task(tmp_path / "harness", cwd)

    hook.handle_payload(
        _payload(
            "PostToolUse",
            cwd,
            tool_name="Bash",
            tool_input={"command": "cat > scripts/novo.py"},
            tool_response={"exit_code": 0, "output": ""},
        ),
        harness_root=tmp_path / "harness",
    )

    vistos = database.files(task["task_id"])
    assert "scripts/novo.py" in [str(v).replace("\\", "/") for v in vistos]
    assert "shell-command" not in vistos


def test_post_tool_mantem_placeholder_quando_nada_e_atribuivel(tmp_path: Path):
    """`python - <<PY` escreve por dentro e nao da para ver. O placeholder fica.

    Trocar o placeholder por 'nenhum arquivo' seria afirmar que o comando nao
    escreveu — e a diferenca entre 'nao escreveu' e 'nao da para saber' e o
    ponto inteiro desta correcao.
    """
    cwd = tmp_path / "repo"
    cwd.mkdir()
    _, database, task = _active_task(tmp_path / "harness", cwd)

    hook.handle_payload(
        _payload(
            "PostToolUse",
            cwd,
            tool_name="Bash",
            tool_input={"command": "python gerador.py"},
            tool_response={"exit_code": 0, "output": ""},
        ),
        harness_root=tmp_path / "harness",
    )
    assert "shell-command" in database.files(task["task_id"])


def test_varios_arquivos_num_comando_sobem_code_revision_uma_vez(tmp_path: Path):
    """Uma chamada de ferramenta e uma alteracao, mesmo tocando tres arquivos.

    `touch_file` incrementa `code_revision` a cada chamada. Chamar em laco
    inflaria o contador que invalida evidencia, e um `pytest` seguinte pareceria
    obsoleto sem que nada tivesse mudado depois dele.
    """
    cwd = tmp_path / "repo"
    cwd.mkdir()
    _, database, task = _active_task(tmp_path / "harness", cwd)
    antes = database.task(task["task_id"])["code_revision"]

    hook.handle_payload(
        _payload(
            "PostToolUse",
            cwd,
            tool_name="Bash",
            tool_input={"command": "cat > a.py; cat > b.py; cat > c.py"},
            tool_response={"exit_code": 0, "output": ""},
        ),
        harness_root=tmp_path / "harness",
    )
    assert database.task(task["task_id"])["code_revision"] == antes + 1
    assert len(database.files(task["task_id"])) == 3


# --- Inspecionar nao e alterar (incidente 2026-09-03) ------------------------
#
# Todo comando de shell subia `code_revision` pelo placeholder 'shell-command',
# entao um `grep` para conferir o estado invalidava a evidencia da suite e
# obrigava a rodar a suite de novo antes de conseguir fechar a task. O
# placeholder existe para o caso duvidoso; `grep` e `git log` nao sao duvidosos.


def test_is_read_only_reconhece_inspecao():
    assert hook.is_read_only("grep -n foo bar.py")
    assert hook.is_read_only("cat scripts/x.py")
    assert hook.is_read_only("sed -n '1,40p' scripts/x.py")
    assert hook.is_read_only("git log --oneline -3")
    assert hook.is_read_only("git status --short")
    assert hook.is_read_only("cat a.py | grep -c def | wc -l")


def test_is_read_only_recusa_o_que_escreve_ou_pode_escrever():
    assert not hook.is_read_only("sed -i 's/a/b/' x.py")
    assert not hook.is_read_only("git checkout -- .")
    assert not hook.is_read_only("git config user.name foo")
    assert not hook.is_read_only("python scripts/patch.py")
    assert not hook.is_read_only("npm install")
    assert not hook.is_read_only("find . -name '*.py' -delete")
    assert not hook.is_read_only("")


def test_is_read_only_recusa_quando_um_segmento_escreve():
    """Um so elo fora da lista tira a linha inteira — composicao nao dilui."""
    assert not hook.is_read_only("grep -n foo x.py && python build.py")
    assert not hook.is_read_only("cat x.py > y.py")
    assert not hook.is_read_only("cat x.py | tee y.py")


def test_comando_de_inspecao_nao_invalida_evidencia(tmp_path: Path):
    """O custo real: conferir o estado do repositorio nao pode custar a suite."""
    cwd = tmp_path / "repo"
    cwd.mkdir()
    _, database, task = _active_task(tmp_path / "harness", cwd)
    database.record_evidence(
        task["task_id"], evidence_type="test", command="python -m pytest -q",
        exit_code=0, tests_collected=10, tests_passed=10, output_hash=None,
    )
    antes = database.task(task["task_id"])
    assert antes["verified"] is True

    hook.handle_payload(_payload(
        "PostToolUse", cwd, tool_name="Bash",
        tool_input={"command": "grep -n def scripts/transactional_state.py"},
    ), harness_root=tmp_path / "harness")

    depois = database.task(task["task_id"])
    assert depois["verified"] is True, "um grep invalidou a evidencia"
    assert depois["code_revision"] == antes["code_revision"]


# --- O aviso tem de ser auditavel (incidente 2026-09-03) ---------------------
#
# O aviso de composicao de shell diz "a evidencia deste teste NAO foi gravada".
# Ele saia por `print` solto: fora do extrato de `emissions.jsonl`, invisivel
# para `check_hook_liveness.py --delivery`. Uma suite verde de 551s foi
# descartada por um `| tail -12` e nenhum dos dois lados soube.


def test_aviso_do_post_tool_entra_no_extrato(tmp_path: Path, monkeypatch, capsys):
    cwd = tmp_path / "repo"
    cwd.mkdir()
    raiz = tmp_path / "harness"
    _, database, task = _active_task(raiz, cwd)
    monkeypatch.setenv("HARNESS_DIR", str(raiz))

    aviso = hook.handle_payload(_payload(
        "PostToolUse", cwd, tool_name="Bash",
        tool_input={"command": "python -m pytest -q 2>&1 | tail -12"},
    ), harness_root=raiz)
    assert "NAO gravada" in aviso

    hook._emitir({"cwd": str(cwd), "session_id": "s1"}, "PostToolUse", aviso)

    extrato = raiz / "emissions.jsonl"
    assert extrato.is_file(), "o aviso nao deixou rastro no extrato"
    linhas = [json.loads(l) for l in extrato.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert any(l.get("kind") == "evidence_warning" for l in linhas)


def test_gate_do_stop_continua_saindo_cru(tmp_path: Path):
    """O `decision: block` e o unico canal que interrompe — nao pode ir para o emissor."""
    cwd = tmp_path / "repo"
    cwd.mkdir()
    raiz = tmp_path / "harness"
    _, database, task = _active_task(raiz, cwd)

    saida = hook.handle_payload(_payload("Stop", cwd), harness_root=raiz)

    assert json.loads(saida)["decision"] == "block"
