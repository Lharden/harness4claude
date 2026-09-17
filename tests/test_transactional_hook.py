import importlib.util
import json
import os
import sys
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


# --- R3 etapa 1: o corpo do heredoc sai antes da tokenizacao -----------------
#
# `_tokenize` e um tokenizador POSIX aplicado a strings que muitas vezes nao sao
# comandos POSIX — corpo de heredoc, codigo de programa, PowerShell. Dentro
# dessas regioes o rastreio de aspas nao significa nada e todo `>` vira
# operador. Os alvos abaixo sao reproducoes do mapa §6.2; `'0.75:'` e literal-
# mente uma linha real da tabela `files`, e `'MSGEOF'` foi gravado pelo commit
# do proprio mapa (a linha de atribuicao termina em `>`).
#
# Medido contra e4212fb: 6 de 6 casos nao-controle produziam alvo espurio, 0
# depois; 1 de 7 controles quebrava, 0 depois.

HEREDOCS_QUE_PRODUZIAM_LIXO = [
    ("comparacao em codigo", "python - <<'PY'\nif riqueza>0.75:\n    print('alto')\nPY", []),
    ("markdown com citacao", "cat > nota.md <<'EOF'\n> Passar o titulo.\nEOF", ["nota.md"]),
    ("dois-pontos e comparacao", "python - <<'PY'\nprint('score:', x)\nif a>b: pass\nPY", []),
    ("commit com linha de atribuicao",
     "git commit -F - <<'MSGEOF'\ndocs: x\n\nCo-Authored-By: C <noreply@anthropic.com>\nMSGEOF", []),
    ("delimitador sem aspas", "python - <<PY\nif a>b: pass\nPY", []),
    ("tabulacao ignorada", "cat <<-FIM\n\tif a>b: pass\n\tFIM", []),
]


@pytest.mark.parametrize(
    "nome, comando, esperado",
    HEREDOCS_QUE_PRODUZIAM_LIXO,
    ids=[n for n, _, _ in HEREDOCS_QUE_PRODUZIAM_LIXO],
)
def test_corpo_de_heredoc_nao_vira_arquivo(nome: str, comando: str, esperado: list[str]):
    assert hook.shell_write_targets(comando) == esperado


def test_heredoc_que_tambem_redireciona_devolve_o_alvo_e_ignora_o_corpo():
    """Nao cega: o alvo legitimo e o corpo excluido convivem no mesmo comando.

    Antes: `['saida.txt', 'b']` — o `b` vinha de `a>b` DENTRO do corpo.
    """
    assert hook.shell_write_targets("cat <<'EOF' > saida.txt\ncorpo com a>b\nEOF") == ["saida.txt"]
    assert hook.shell_write_targets('cat > "docs/nota final.md" <<\'EOF\'\n> citacao\nEOF') == [
        "docs/nota final.md"
    ]


def test_heredoc_dentro_de_aspas_nao_abre_corpo():
    """`echo "a <<EOF b"` nao abre heredoc nenhum — e a linha inteira continua viva."""
    assert hook.shell_write_targets('echo "a <<EOF b" > saida.txt') == ["saida.txt"]
    assert hook.shell_write_targets("cat <<<'x' > saida.txt") == ["saida.txt"]


# --- R3 etapa 3: toda recusa fica escrita ------------------------------------
#
# O risco do conserto e na direcao perigosa: candidato rejeitado por engano e
# escrita real que some do contador, e o contador nao denuncia a propria
# cegueira. "O ruido caiu" e "o guarda parou de ver" dao o MESMO numero.


def test_recusa_do_extrator_e_nomeada_com_motivo():
    recusas: list[dict] = []
    assert hook.shell_write_targets("pytest -q 2>&1", recusas) == []
    assert [r["motivo"] for r in recusas] == ["vazio-ou-operador"]

    recusas.clear()
    assert hook.shell_write_targets("cmd 2>/dev/null", recusas) == []
    assert [r["candidato"] for r in recusas] == ["/dev/null"]
    assert [r["motivo"] for r in recusas] == ["destino-nulo"]

    recusas.clear()
    hook.shell_write_targets("python - <<'PY'\nif a>b: pass\nPY", recusas)
    motivos = [r["motivo"] for r in recusas]
    assert "corpo-de-heredoc" in motivos and "delimitador-de-heredoc" in motivos


def test_recusa_chega_ao_disco_com_comando_candidato_e_motivo(tmp_path: Path):
    """O consumidor da etapa 3: sem arquivo, a proxima medicao e impossivel.

    O mapa so mediu alguma coisa porque as entradas ACEITAS ficavam em `files`.
    As recusadas nunca ficaram em lugar nenhum.
    """
    cwd = tmp_path / "repo"
    cwd.mkdir()
    bucket, _database, task = _active_task(tmp_path / "harness", cwd)

    hook.handle_payload(
        _payload("PostToolUse", cwd, tool_name="Bash",
                 tool_input={"command": "cat > nota.md <<'EOF'\n> citacao\nEOF"},
                 tool_response={"exit_code": 0, "output": ""}),
        harness_root=tmp_path / "harness",
    )

    linhas = [
        json.loads(linha)
        for linha in (bucket / hook.ARQUIVO_DE_RECUSAS).read_text(encoding="utf-8").splitlines()
        if linha.strip()
    ]
    assert linhas, "recusa silenciosa e o mesmo erro numa direcao nova"
    assert {r["motivo"] for r in linhas} == {"corpo-de-heredoc", "delimitador-de-heredoc"}
    for registro in linhas:
        assert registro["task_id"] == task["task_id"]
        assert registro["comando_hash"]
        assert "nota.md" in registro["comando"]
        assert registro["aceitos"] == ["nota.md"], "o que foi ACEITO fica junto do recusado"


def test_comando_sem_recusa_nao_cria_arquivo(tmp_path: Path):
    """Ruido tambem custa: um `jsonl` com uma linha por comando limpo seria lixo."""
    cwd = tmp_path / "repo"
    cwd.mkdir()
    bucket, _database, _task = _active_task(tmp_path / "harness", cwd)

    hook.handle_payload(
        _payload("PostToolUse", cwd, tool_name="Bash",
                 tool_input={"command": "cat > scripts/x.py"},
                 tool_response={"exit_code": 0, "output": ""}),
        harness_root=tmp_path / "harness",
    )

    assert not (bucket / hook.ARQUIVO_DE_RECUSAS).exists()


# --- A barra invertida entre aspas duplas -------------------------------------
#
# Achado desta sessao, encontrado ao ligar R2: `_tokenize` escapava
# INCONDICIONALMENTE dentro de aspas duplas, entao comia os separadores de todo
# caminho absoluto do Windows. `"C:\\Users\\me\\x.py"` chegava a `files` como
# `C:UsersmeX.py` — escrita REAL que aparece no banco parecendo lixo, e que a
# regua "sem separador e sem extensao" do mapa classificava como nao-caminho.
#
# No POSIX (e no bash), dentro de aspas duplas a barra so escapa " \ $ ` e nova
# linha. Antes de qualquer outro caractere ela e literal.


def test_caminho_windows_entre_aspas_duplas_mantem_os_separadores():
    alvo = "C:" + chr(92) + "Users" + chr(92) + "me" + chr(92) + "repo" + chr(92) + "x.py"
    assert hook.shell_write_targets('cat > "' + alvo + '"') == [alvo]
    assert hook._tokenize('"' + alvo + '"') == [alvo]


def test_barra_invertida_continua_escapando_o_que_o_posix_manda():
    """A outra metade: afrouxar a regra nao pode quebrar o escape que existe."""
    barra = chr(92)
    aspa = chr(34)
    # \" dentro de aspas duplas: a aspa e literal e a string CONTINUA.
    assert hook._tokenize(aspa + "a" + barra + aspa + "b" + aspa) == ['a' + aspa + 'b']
    # \\ vira uma barra so.
    assert hook._tokenize(aspa + "a" + barra + barra + "b" + aspa) == ["a" + barra + "b"]
    # Operador entre aspas continua sem valer, que e a razao de `_tokenize` existir.
    assert hook.shell_write_targets("echo 'a > b'") == []
    assert hook.shell_write_targets('echo "a > b"') == []


# --- R2: a mesma pergunta nos dois caminhos ----------------------------------
#
# `b771b6b` ensinou `counts_as_modified_file` a distinguir *escreveu algo* de
# *mudou o codigo sob teste*, e ligou isso no caminho Edit/Write
# (`harness-reclassify.sh:135`). O caminho do shell chamava `touch_files` sem
# passar por ele. Mesmo arquivo, mesmo lugar, dois veredictos — medido ao vivo
# no mapa §5: `files_dump.txt` no scratchpad, fora do repositorio. Criado com
# `Write` nao contava; criado com `>` contou, e virou a linha rev=7 daquela task.


def _repo_de_verdade(tmp_path: Path) -> Path:
    """Um diretorio que `find_repo_root` reconhece: basta existir `.git`."""
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    return repo


def test_alvo_fora_da_raiz_nao_vira_linha_em_files(tmp_path: Path):
    """A linha rev=7 do mapa, agora recusada.

    O placeholder CONTINUA: comando nao read-only com lista vazia ainda sobe o
    contador. O que muda e a ATRIBUICAO — quem le `files` deixa de ver um
    arquivo de scratchpad como codigo alterado.
    """
    repo = _repo_de_verdade(tmp_path)
    fora = tmp_path / "scratchpad"
    fora.mkdir()
    _, database, task = _active_task(tmp_path / "harness", repo)

    hook.handle_payload(
        _payload("PostToolUse", repo, tool_name="Bash",
                 tool_input={"command": f'echo x > "{fora / "nota.txt"}"'},
                 tool_response={"exit_code": 0, "output": ""}),
        harness_root=tmp_path / "harness",
    )

    vistos = database.files(task["task_id"])
    assert "nota.txt" not in " ".join(vistos)
    assert vistos == ["shell-command"], "a segunda metade de :576-579 continua viva"
    assert database.task(task["task_id"])["code_revision"] == 1


def test_alvo_dentro_da_raiz_continua_contando(tmp_path: Path):
    """A outra metade: R2 nao pode virar desculpa para parar de contar."""
    repo = _repo_de_verdade(tmp_path)
    _, database, task = _active_task(tmp_path / "harness", repo)

    hook.handle_payload(
        _payload("PostToolUse", repo, tool_name="Bash",
                 tool_input={"command": "cat > scripts/novo.py"},
                 tool_response={"exit_code": 0, "output": ""}),
        harness_root=tmp_path / "harness",
    )

    assert [v.replace("\\", "/") for v in database.files(task["task_id"])] == ["scripts/novo.py"]


def test_sem_cwd_no_payload_conta_tudo(tmp_path: Path):
    """Fail-closed, repetindo a decisao de `harness-reclassify.sh:123-126`.

    Sem `cwd` nao ha projeto declarado, e sem projeto nao ha dentro nem fora.
    Cair em `os.getcwd()` inventaria a fronteira a partir de onde o hook por
    acaso roda — foi o que derrubou 7 testes de `TestReclassify`.
    """
    repo = _repo_de_verdade(tmp_path)
    fora = tmp_path / "scratchpad"
    fora.mkdir()
    _, database, task = _active_task(tmp_path / "harness", repo)
    alvo = fora / "nota.txt"

    payload = _payload("PostToolUse", repo, tool_name="Bash",
                       tool_input={"command": f'echo x > "{alvo}"'},
                       tool_response={"exit_code": 0, "output": ""})
    # O balde ja existe; o `cwd` some so para a decisao de fronteira.
    assert hook._apenas_dentro_da_raiz({}, [str(alvo)], []) == [str(alvo)]
    assert hook._apenas_dentro_da_raiz({"cwd": str(repo)}, [str(alvo)], []) == []
    hook.handle_payload(payload, harness_root=tmp_path / "harness")
    assert database.files(task["task_id"]) == ["shell-command"]


def test_write_e_redirecionamento_dao_o_mesmo_veredicto(tmp_path: Path):
    """A simetria — o teste que nao existia, e a assimetria que ele mede.

    Mesmo caminho, mesmo lugar, nos dois lados da fronteira. As duas metades
    tem de concordar; ate 2026-09-17 discordavam fora da raiz.
    """
    politica = _load("transactional_hook_policy", "scripts/post_tool_policy.py")
    repo = _repo_de_verdade(tmp_path)
    fora = tmp_path / "scratchpad"
    fora.mkdir()
    (repo / "scripts").mkdir()

    for alvo, esperado in ((repo / "scripts" / "x.py", True), (fora / "x.py", False)):
        veredicto_write = politica.counts_as_modified_file("Write", str(alvo), str(repo))
        pelo_shell = hook._apenas_dentro_da_raiz(
            {"cwd": str(repo)}, hook.shell_write_targets(f'cat > "{alvo}"'), []
        )
        veredicto_shell = bool(pelo_shell)
        assert veredicto_write == esperado, alvo
        assert veredicto_shell == esperado, alvo
        assert veredicto_write == veredicto_shell, f"os dois caminhos discordam em {alvo}"


def test_recusa_por_raiz_fica_registrada(tmp_path: Path):
    """Filtrar sem registrar seria trocar ruido por cegueira de novo."""
    repo = _repo_de_verdade(tmp_path)
    fora = tmp_path / "scratchpad"
    fora.mkdir()
    bucket, _database, _task = _active_task(tmp_path / "harness", repo)

    hook.handle_payload(
        _payload("PostToolUse", repo, tool_name="Bash",
                 tool_input={"command": f'echo x > "{fora / "nota.txt"}"'},
                 tool_response={"exit_code": 0, "output": ""}),
        harness_root=tmp_path / "harness",
    )

    linhas = [
        json.loads(linha)
        for linha in (bucket / hook.ARQUIVO_DE_RECUSAS).read_text(encoding="utf-8").splitlines()
        if linha.strip()
    ]
    assert [r["motivo"] for r in linhas] == ["fora-da-raiz"]
    assert "nota.txt" in linhas[0]["candidato"]


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


def test_git_que_nao_toca_a_arvore_nao_expira_evidencia():
    """`git add` e `git commit` escrevem em `.git/`, nao no codigo sob teste.

    Sem isto, a sequencia obrigatoria do workflow era impossivel de completar:
    rodar a suite -> gravar evidencia -> commitar -> responder. O commit
    invalidava a evidencia que o justificou. Medido em 2026-09-16:
    `code_revision` foi de 218 para 220 por um `git add` e um `git commit`,
    com a arvore de trabalho limpa e `git status` vazio.
    """
    assert hook.nao_muda_a_arvore("git add -A")
    assert hook.nao_muda_a_arvore("git commit -m mensagem")
    assert hook.nao_muda_a_arvore("git add -A && git commit -m x")
    assert hook.nao_muda_a_arvore("git status")   # read-only tambem passa aqui


def test_CONTROLE_o_que_muda_a_arvore_continua_expirando():
    """Metade 1. Sem estes, o conserto acima seria silenciamento.

    Cada um destes MUDA o codigo que a suite mede, e evidencia colhida antes
    deles nao fala sobre o codigo depois deles.
    """
    for comando in (
        "git checkout main",
        "git reset --hard HEAD~1",
        "git clean -fdx",
        "git stash",
        "git merge outra",
        "git pull",
        "sed -i 's/a/b/' x.py",
        "rm -rf scripts",
        "echo oi > scripts/x.py",
        "npm install",
        "python scripts/patch.py",
    ):
        assert not hook.nao_muda_a_arvore(comando), comando


def test_composicao_nao_dilui_a_nova_categoria():
    """Um elo que muda a arvore tira a linha inteira, como em `is_read_only`."""
    assert not hook.nao_muda_a_arvore("git add -A && sed -i 's/a/b/' x.py")
    assert not hook.nao_muda_a_arvore("git commit -m x && git checkout main")
    assert not hook.nao_muda_a_arvore("git add -A > log.txt")
    assert not hook.nao_muda_a_arvore("")


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


# ---------------------------------------------------------------------------
# Achado 1 — a mensagem do gate nao dizia o que ele leu
# ---------------------------------------------------------------------------
# Medido 2026-09-16: duas sessoes diferentes receberam o bloqueio e concluiram
# "a task e fantasma", sobre uma task que existia e que de fato nunca tinha
# recebido evidencia. O portao contou certo as duas vezes. O que faltava era
# ele MOSTRAR a contagem — sem task_id, sem balde e sem o comando que
# registraria, a saida mais barata para quem le e inventar um diagnostico.


def test_mensagem_do_gate_cita_o_que_leu(tmp_path: Path):
    cwd = tmp_path / "repo"
    cwd.mkdir()
    bucket, _database, task = _active_task(tmp_path / "harness", cwd)

    saida = hook.handle_payload(_payload("Stop", cwd), harness_root=tmp_path / "harness")

    motivo = json.loads(saida)["reason"]
    assert task["task_id"] in motivo, "tem de citar a task que foi lida"
    assert str(bucket) in motivo, "tem de citar o balde que foi lido"
    assert "state_cli.py" in motivo, "tem de dizer como registrar evidencia"
    assert "--tests-skipped" in motivo, "a regua nova tem de aparecer no comando"
    assert "VAZIA" in motivo, "sem evidencia nenhuma, tem de dizer isso com todas as letras"


def test_mensagem_do_gate_conta_a_evidencia_que_existe(tmp_path: Path):
    """Com evidencia gravada mas insuficiente, a contagem tem de aparecer.

    E a diferenca entre "ninguem registrou nada" e "registrou e nao bastou" —
    dois diagnosticos distintos que a mensagem antiga nao distinguia.
    """
    cwd = tmp_path / "repo"
    cwd.mkdir()
    _bucket, database, task = _active_task(tmp_path / "harness", cwd)
    database.record_evidence(
        task["task_id"], evidence_type="test", command="python -m pytest -q",
        exit_code=1, tests_collected=10, tests_passed=9, tests_skipped=0, output_hash="h",
    )

    motivo = json.loads(
        hook.handle_payload(_payload("Stop", cwd), harness_root=tmp_path / "harness")
    )["reason"]

    assert "VAZIA" not in motivo
    assert "1 linha(s) de evidence" in motivo


def test_mensagem_do_gate_nomeia_o_que_invalidou(tmp_path: Path):
    """R5, consumidor nomeado: `code_revision=24` diz QUE expirou, nao POR QUE.

    A tabela `touches` existe para responder isso, e esta e a tela onde a
    resposta e util. Sem este teste a tabela seria capacidade sem consumidor.
    """
    cwd = tmp_path / "repo"
    cwd.mkdir()
    _bucket, database, task = _active_task(tmp_path / "harness", cwd)
    database.touch_files(task["task_id"], ["scripts/alvo.py"], origem="shell")
    database.touch_files(task["task_id"], ["shell-command"], origem="shell-placeholder")

    motivo = json.loads(
        hook.handle_payload(_payload("Stop", cwd), harness_root=tmp_path / "harness")
    )["reason"]

    assert "Ultima(s) invalidacao(oes)" in motivo
    assert "alvo.py" in motivo and "(shell)" in motivo
    assert "shell-command" in motivo and "(shell-placeholder)" in motivo


def test_toque_por_shell_registra_a_origem(tmp_path: Path):
    """A origem separa escrita atribuida de placeholder — sao causas diferentes.

    Medido nesta sessao: 8 de 9 invalidacoes vieram do placeholder (toda
    invocacao de interpretador), 1 de redirecionamento, 0 de mudanca de codigo.
    Sem a coluna, as tres somem na mesma linha do contador.
    """
    cwd = tmp_path / "repo"
    cwd.mkdir()
    _, database, task = _active_task(tmp_path / "harness", cwd)
    raiz = tmp_path / "harness"

    hook.handle_payload(
        _payload("PostToolUse", cwd, tool_name="Bash",
                 tool_input={"command": "cat > scripts/novo.py"},
                 tool_response={"exit_code": 0, "output": ""}),
        harness_root=raiz,
    )
    hook.handle_payload(
        _payload("PostToolUse", cwd, tool_name="Bash",
                 tool_input={"command": "python -c import os"},
                 tool_response={"exit_code": 0, "output": ""}),
        harness_root=raiz,
    )

    toques = database.touches(task["task_id"])
    assert [(t["path"].replace("\\", "/"), t["origem"]) for t in toques] == [
        ("scripts/novo.py", "shell"),
        ("shell-command", "shell-placeholder"),
    ]


def test_comando_que_a_mensagem_imprime_de_fato_roda(tmp_path: Path):
    """O comando sugerido passa pelo parser real do `state_cli`.

    A primeira versao desta mensagem imprimia `evidence --home <balde>`, e
    `--home` e do parser RAIZ: a ordem certa e `--home <balde> evidence`. O
    comando saia com `error: the following arguments are required: --home`.
    Uma mensagem de portao que entrega instrucao impossivel de seguir repete o
    defeito que ela veio consertar, entao quem a verifica tem de executa-la.
    """
    import shlex

    cli = _load("transactional_hook_cli", "scripts/state_cli.py")
    cwd = tmp_path / "repo"
    cwd.mkdir()
    _bucket, _database, task = _active_task(tmp_path / "harness", cwd)

    motivo = json.loads(
        hook.handle_payload(_payload("Stop", cwd), harness_root=tmp_path / "harness")
    )["reason"]

    linha = next(l for l in motivo.splitlines() if "state_cli.py" in l)
    argv = shlex.split(linha, posix=False)
    corte = argv.index([a for a in argv if a.endswith('state_cli.py"')][0]) + 1
    argv = [a.strip('"') for a in argv[corte:]]
    argv = [("1" if a in ("<N>", "<P>", "<S>") else a) for a in argv]

    args = cli._parser().parse_args(argv) if hasattr(cli, "_parser") else None
    if args is None:  # o CLI monta o parser dentro de `main`
        import subprocess
        p = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "state_cli.py"), *argv],
            capture_output=True, text=True, timeout=60,
        )
        assert p.returncode == 0, f"o comando sugerido nao roda:\n{p.stderr}"
    else:
        assert args.command == "evidence"
        assert args.task == task["task_id"]
