"""Sondas do contrato que entram pelo CHAMADOR DE PRODUCAO (L-61, L-65).

Cada teste aqui roda o hook `.sh` por subprocesso com o bash do Git, o CLI de um
script por subprocesso, ou um `workflows/*.js` no node, do jeito que o host o
invoca em uso real. O nome do teste e o da entrada declarada em
`contract/behavioral-probes.json` (`adapters` e `entradas`); o token da entrada
aparece em literal de string ou em simbolo no corpo do teste ou nos helpers
deste arquivo, que e o que a cobranca do `mh paridade` procura.

Isolamento: casa temporaria (`HARNESS_DIR` por teste), `HARNESS_ROUTER=0`,
`HARNESS_BRANCH_LAYER_B=0`, sem Ollama, rede, MCP ou modelo. `timeout=60` em
todo subprocesso.

Fonte: master-harness `docs/specs/sondas-de-producao-{spec,design,auditoria}.md`.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
sys.path.insert(0, str(ROOT / "scripts"))

import harness_paths  # noqa: E402
from transactional_state import HarnessDatabase  # noqa: E402

TIMEOUT = 60
NODE = shutil.which("node")
WF_HARNESS = Path(__file__).parent / "wf_run_harness.cjs"

PROMPT_BUG = "corrigir o bug: o login falha com erro ao salvar a sessao do usuario"
PROMPT_L2 = "planeje a arquitetura completa de um novo sistema de pipeline com spec e design"

HOOK_CLASSIFY = "hooks/harness-classify.sh"
HOOK_SESSION_START = "hooks/harness-session-start.sh"
HOOK_GRAPHIFY = "hooks/harness-graphify-autosetup.sh"
HOOK_PRECOMPACT = "hooks/harness-precompact.sh"
CLI_CONFIRM = "scripts/confirm_classification.py"
CLI_BRANCH_STATE = "scripts/branch_state.py"
CLI_LIVENESS = "scripts/check_hook_liveness.py"
CLI_WIKI_QUERY = "tools/wiki_query.py"
HOOKS_JSON = "hooks/hooks.json"
WF_CONTEXT_SCAN = "scripts/workflows/wf-context-scan.js"
WF_VERIFY = "scripts/workflows/wf-verify-multimodel.js"


def _achar_bash() -> str | None:
    achado = shutil.which("bash")
    if achado:
        return achado
    for c in (
        Path(r"C:\Program Files\Git\bin\bash.exe"),
        Path(r"C:\Program Files\Git\usr\bin\bash.exe"),
    ):
        if c.exists():
            return str(c)
    return None


BASH = _achar_bash()

exige_bash = pytest.mark.skipif(BASH is None, reason="Bash integration runtime is not installed on this host")
exige_node = pytest.mark.skipif(NODE is None, reason="node nao disponivel no PATH")


# --------------------------------------------------------------------------- helpers


def _env(tmp_path: Path, **extra: str) -> dict:
    """Ambiente hermetico: tudo que o hook escreve cai em `tmp_path`."""
    harness = tmp_path / "harness"
    harness.mkdir(exist_ok=True)
    env = {
        **os.environ,
        "PYTHONUTF8": "1",
        "HARNESS_DIR": str(harness),
        "AI_BRAIN_PATH": str(tmp_path / "ai-brain"),
        "MASTER_HARNESS_HOME": str(tmp_path / "mh"),
        "HARNESS_SKIP_DEPCHECK": "1",
        "HARNESS_ROUTER": "0",
        "HARNESS_BRANCH": "0",
        "HARNESS_BRANCH_LAYER_B": "0",
        "HARNESS_OLLAMA_URL": "http://127.0.0.1:9",
    }
    env.pop("VAULT_PATH", None)
    env.update(extra)
    return env


def _repo(tmp_path: Path, nome: str) -> Path:
    d = tmp_path / nome
    (d / ".git").mkdir(parents=True)
    return d


def _repo_git(tmp_path: Path, nome: str) -> Path:
    """Repositorio de verdade, para o hook que pergunta `git rev-parse` ao git."""
    d = tmp_path / nome
    d.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(d)], check=True, timeout=TIMEOUT, capture_output=True)
    return d


def _balde(tmp_path: Path, cwd: Path, sessao: str) -> Path:
    return Path(harness_paths.ensure_state_dir(str(tmp_path / "harness"), str(cwd), session_id=sessao))


def _hook(tmp_path: Path, arquivo: str, payload: dict, **extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [BASH, str(ROOT / arquivo)],
        input=json.dumps(payload), capture_output=True, text=True, encoding="utf-8",
        timeout=TIMEOUT, env=_env(tmp_path, **extra),
    )


def _classificar(tmp_path: Path, cwd: Path, sessao: str, prompt: str = PROMPT_BUG) -> subprocess.CompletedProcess:
    return _hook(tmp_path, HOOK_CLASSIFY, {"session_id": sessao, "cwd": str(cwd), "prompt": prompt})


def _estado(balde: Path) -> dict:
    return json.loads((balde / "state.json").read_text(encoding="utf-8"))


def _script(tmp_path: Path, arquivo: str, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ROOT / arquivo), *args],
        capture_output=True, text=True, encoding="utf-8", timeout=TIMEOUT,
        env=_env(tmp_path), cwd=cwd,
    )


def _abrir_task(tmp_path: Path, nome: str, sessao: str, prompt: str) -> tuple[Path, Path, str]:
    cwd = _repo(tmp_path, nome)
    res = _classificar(tmp_path, cwd, sessao, prompt)
    assert "HARNESS v3 CLASSIFIED" in res.stdout, res.stdout + res.stderr
    balde = _balde(tmp_path, cwd, sessao)
    return cwd, balde, _estado(balde)["task_id"]


def _confirmar(tmp_path: Path, balde: Path, tid: str, final: str, *extra: str) -> subprocess.CompletedProcess:
    return _script(
        tmp_path, CLI_CONFIRM, "--final", final, "--confidence", "0.9",
        "--expect-task", tid, "--harness-dir", str(balde), *extra,
    )


def _rodar_wf(tmp_path: Path, arquivo: str, cenario: dict) -> dict:
    cenario_json = tmp_path / "cenario.json"
    cenario_json.write_text(json.dumps(cenario), encoding="utf-8")
    res = subprocess.run(
        [NODE, str(WF_HARNESS), str(ROOT / arquivo), str(cenario_json)],
        capture_output=True, text=True, encoding="utf-8", timeout=TIMEOUT,
    )
    assert res.returncode == 0, f"wf_run_harness falhou:\n{res.stdout}\n{res.stderr}"
    return json.loads(res.stdout)


# --------------------------------------------------------------------------- classification


@exige_bash
def test_classify_hook_grava_sugestao_deterministica(tmp_path):
    """UserPromptSubmit real: prompt de bug vira `L?-bug` e a sugestao do regex fica na meta."""
    cwd = _repo(tmp_path, "alpha")
    res = _classificar(tmp_path, cwd, "s-sugestao")
    assert res.returncode == 0, res.stderr
    assert "HARNESS v3 CLASSIFIED" in res.stdout, res.stdout

    estado = _estado(_balde(tmp_path, cwd, "s-sugestao"))
    assert estado["classification"].endswith("-bug"), estado
    meta = estado["classification_meta"]
    assert meta["suggested"] == estado["classification"]
    assert meta["final"] is None, "L1+ so ganha `final` na confirmacao semantica"


@exige_bash
def test_confirm_cli_grava_confirmacao_semantica(tmp_path):
    """CLI que a skill harness-workflow manda rodar: corrige a classificacao e troca o pipeline."""
    _, balde, tid = _abrir_task(tmp_path, "beta", "s-semantica", PROMPT_L2)

    res = _confirmar(tmp_path, balde, tid, "L1-bug")
    assert res.returncode == 0, res.stdout + res.stderr

    estado = _estado(balde)
    assert estado["classification"] == "L1-bug"
    contrato = json.loads((ROOT / "contract" / "pipelines.json").read_text(encoding="utf-8"))["pipelines"]
    assert estado["pipeline"] == contrato["L1-bug"]
    meta = estado["classification_meta"]
    assert meta["source"] == "semantic"
    assert meta["final"] == "L1-bug"
    assert meta["agreed"] is False, "o regex sugeriu outro nivel"
    assert HarnessDatabase(balde).classification(tid)["source"] == "semantic"


@exige_bash
def test_confirm_cli_grava_human_override(tmp_path):
    """O mesmo CLI com `--source human_override`: a origem gravada e a humana."""
    _, balde, tid = _abrir_task(tmp_path, "gama", "s-override", PROMPT_L2)

    res = _confirmar(tmp_path, balde, tid, "L1-feature", "--source", "human_override")
    assert res.returncode == 0, res.stdout + res.stderr

    assert _estado(balde)["classification_meta"]["source"] == "human_override"
    assert HarnessDatabase(balde).classification(tid)["source"] == "human_override"


# --------------------------------------------------------------------------- state


@exige_bash
def test_classify_hook_isola_duas_sessoes_no_mesmo_repo(tmp_path):
    cwd = _repo(tmp_path, "delta")
    for sessao in ("s-a", "s-b"):
        res = _classificar(tmp_path, cwd, sessao)
        assert "HARNESS v3 CLASSIFIED" in res.stdout, res.stdout + res.stderr

    balde_a = _balde(tmp_path, cwd, "s-a")
    balde_b = _balde(tmp_path, cwd, "s-b")
    assert balde_a != balde_b
    assert (balde_a / "state.json").is_file() and (balde_b / "state.json").is_file()
    tid_a, tid_b = _estado(balde_a)["task_id"], _estado(balde_b)["task_id"]
    assert tid_a != tid_b
    with sqlite3.connect(f"file:{balde_a / 'harness.db'}?mode=ro", uri=True) as c:
        assert [r[0] for r in c.execute("SELECT task_id FROM tasks")] == [tid_a]
    with sqlite3.connect(f"file:{balde_b / 'harness.db'}?mode=ro", uri=True) as c:
        assert [r[0] for r in c.execute("SELECT task_id FROM tasks")] == [tid_b]


@exige_bash
def test_session_start_hook_abandona_task_vencida(tmp_path):
    cwd, balde, tid = _abrir_task(tmp_path, "epsilon", "s-ttl", PROMPT_L2)
    with sqlite3.connect(balde / "harness.db") as raw:
        raw.execute("UPDATE tasks SET started_at = '2026-01-01T00:00:00+00:00'")

    res = _hook(tmp_path, HOOK_SESSION_START,
                {"session_id": "s-ttl", "cwd": str(cwd), "source": "startup"})

    assert res.returncode == 0, res.stderr
    assert "HARNESS v3 EXPIRED" in res.stdout, res.stdout + res.stderr
    assert tid in res.stdout
    assert HarnessDatabase(balde).task(tid)["status"] == "abandoned"


# --------------------------------------------------------------------------- workflow


@exige_bash
def test_classify_hook_grava_pipeline_do_contrato(tmp_path):
    _, balde, _ = _abrir_task(tmp_path, "zeta", "s-pipeline", PROMPT_L2)
    estado = _estado(balde)
    assert estado["classification"].startswith("L2-"), estado["classification"]
    contrato = json.loads((ROOT / "contract" / "pipelines.json").read_text(encoding="utf-8"))["pipelines"]
    assert estado["pipeline"] == contrato[estado["classification"]]


@exige_node
def test_workflow_de_contexto_nomeia_o_no_morto(tmp_path):
    """`wf-context-scan.js` no node: um angulo que morre aparece em `cobertura.angulos_mortos`."""
    saida = _rodar_wf(tmp_path, WF_CONTEXT_SCAN, {
        "args": {"question": "como o hook classifica", "target": "hooks/"},
        "scans": {"risks": None, "entry-points": {"items": [{"label": "hook", "detail": "d", "path": "hooks/x"}]}},
    })
    cobertura = saida["result"]["cobertura"]
    assert cobertura["angulos_mortos"] == ["risks"]
    assert cobertura["vivos"] == cobertura["esperado"] - 1
    assert any("risks" in linha for linha in saida["calls"]["logs"]), "o censo avisa em log"


@exige_node
def test_verify_multimodel_com_dimensao_morta_nao_aprova(tmp_path):
    """`wf-verify-multimodel.js` no node: nenhuma dimensao devolve finding e uma morre."""
    saida = _rodar_wf(tmp_path, WF_VERIFY, {
        "args": {"task_id": "t-x", "changed_files": ["a.py"], "spec_path": "docs/specs/x-spec.md", "base_ref": "HEAD"},
        "reviews": {"security": None},
    })
    resultado = saida["result"]
    assert resultado["pass"] is False
    assert resultado["nos_mortos"] == ["security"]


# --------------------------------------------------------------------------- context


@exige_bash
def test_graphify_autosetup_dispara_update_sem_grafo(tmp_path):
    """O hook dispara `graphify update` em segundo plano (nohup); o duble grava uma marca."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    marca = tmp_path / "graphify-chamado.txt"
    duble = bin_dir / "graphify"
    marca_sh = marca.as_posix()
    duble.write_text(f'#!/usr/bin/env bash\necho "$@" > "{marca_sh}"\n', encoding="utf-8", newline="\n")
    duble.chmod(0o755)
    path = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")

    sem_grafo = _repo_git(tmp_path, "sem-grafo")
    res = _hook(tmp_path, HOOK_GRAPHIFY, {"session_id": "s-g", "cwd": str(sem_grafo)}, PATH=path)
    assert res.returncode == 0, res.stderr
    assert "GRAPHIFY AUTO-SETUP" in res.stdout, res.stdout

    prazo = time.monotonic() + 60
    while not marca.exists() and time.monotonic() < prazo:
        time.sleep(0.2)
    assert marca.exists(), "o duble `graphify` nao foi chamado em 60 s"
    assert marca.read_text(encoding="utf-8").split()[0] == "update"

    # Controle: com grafo presente o hook nao faz nada.
    com_grafo = _repo_git(tmp_path, "com-grafo")
    (com_grafo / "graphify-out").mkdir()
    (com_grafo / "graphify-out" / "graph.json").write_text("{}", encoding="utf-8")
    res = _hook(tmp_path, HOOK_GRAPHIFY, {"session_id": "s-g2", "cwd": str(com_grafo)}, PATH=path)
    assert res.returncode == 0 and res.stdout.strip() == ""
    assert not (com_grafo / "graphify-out" / ".autosetup-ast.log").exists()


# --------------------------------------------------------------------------- memory


@exige_bash
def test_precompact_hook_espelha_spec_no_vault(tmp_path):
    projeto = _repo(tmp_path, "projeto-x")
    (projeto / "docs" / "specs").mkdir(parents=True)
    (projeto / "docs" / "specs" / "feature-spec.md").write_text("# Feature\n\nREQ-001: algo.", encoding="utf-8")

    (tmp_path / "ai-brain").mkdir()  # sem vault o sync sai 0 sem escrever

    res = _hook(tmp_path, HOOK_PRECOMPACT, {"session_id": "s-pc", "cwd": str(projeto)})

    assert res.returncode == 0, res.stderr
    espelhada = tmp_path / "ai-brain" / "wiki" / "specs" / "feature-spec.md"
    assert espelhada.is_file(), res.stdout + res.stderr
    texto = espelhada.read_text(encoding="utf-8")
    assert texto.startswith("---\ntype: spec\n")
    assert "REQ-001: algo." in texto


def test_wiki_query_cli_acha_por_alias(tmp_path):
    import build_wiki_index as bwi

    pagina = tmp_path / "wiki" / "decisions" / "assimilacoes-2026.md"
    pagina.parent.mkdir(parents=True)
    longo = "conteudo relevante o suficiente para virar um chunk proprio nesta secao. " * 3
    pagina.write_text(
        "---\ntype: decision\ncreated: 2026-01-01\nupdated: 2026-01-01\nstatus: active\ntags: [x]\n---\n\n"
        f"# Assimilacoes\n\n## Adotado\n{longo}\n## Recusas registradas\n{longo}",
        encoding="utf-8",
    )
    indice = tmp_path / "indice"
    bwi.build(str(tmp_path), str(indice), no_embed=True,
              pages=bwi.scan_pages(str(tmp_path), aliases_map={"decisions/assimilacoes-2026": ["recusas"]}))

    res = _script(tmp_path, CLI_WIKI_QUERY, "quais recusas ja foram registradas",
                  "--index", str(indice), "--json")

    assert res.returncode == 0, res.stderr
    achado = json.loads(res.stdout)
    assert achado["hits"][0]["id"] == "decisions/assimilacoes-2026"
    assert achado["hits"][0]["layer"] == "A"
    assert achado["hits"][0]["confident"] is True


# --------------------------------------------------------------------------- branch


@exige_bash
def test_branch_state_cli_recusa_abrir_alem_do_limite(tmp_path):
    # O teto de abertos e transacional: precisa de task viva no banco da sessao
    # que o sensor aponta, como no uso real (o hook de classificacao abre a task).
    projeto, balde, _ = _abrir_task(tmp_path, "proj", "s-ramos", PROMPT_L2)
    projeto_home = Path(harness_paths.ensure_state_dir(str(tmp_path / "harness"), str(projeto)))
    (projeto_home / "branch-sensor.json").write_text(
        json.dumps({"session_id": "s-ramos", "turn": 10}), encoding="utf-8")
    assert (balde / "harness.db").is_file()
    env = _env(tmp_path, HARNESS_BRANCH_MAX_OPEN="1", HARNESS_BRANCH_MAX_OFFERS="3")

    def rodar(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(ROOT / CLI_BRANCH_STATE), *args, "--cwd", str(projeto)],
            capture_output=True, text=True, encoding="utf-8", timeout=TIMEOUT, env=env,
        )

    primeiro = rodar("add", "--name", "Primeiro", "--topic", "tema um", "--origin-turn", "10")
    segundo = rodar("add", "--name", "Segundo", "--topic", "outro assunto", "--origin-turn", "30")
    assert primeiro.returncode == 0 and segundo.returncode == 0, primeiro.stderr + segundo.stderr

    abre = rodar("status", "--slug", "primeiro", "--set", "open", "--seed", "primeiro.seed.md")
    assert abre.returncode == 0, abre.stderr

    recusa = rodar("status", "--slug", "segundo", "--set", "open", "--seed", "segundo.seed.md")
    assert recusa.returncode != 0, "o teto de ramos abertos nao foi aplicado"
    assert "open branch limit" in recusa.stderr, recusa.stderr
    lista = json.loads(rodar("list").stdout)["branches"]
    assert {b["slug"]: b["status"] for b in lista} == {"primeiro": "open", "segundo": "pending"}


# --------------------------------------------------------------------------- lifecycle / observability


@exige_bash
def test_todo_comando_de_hooks_json_roda(tmp_path):
    """Cada comando registrado em hooks.json roda como o Claude Code o roda, com payload minimo."""
    registrados = json.loads((ROOT / HOOKS_JSON).read_text(encoding="utf-8"))["hooks"]
    cwd = _repo(tmp_path, "hooks")
    falhas = []
    total = 0
    for evento, grupos in registrados.items():
        for grupo in grupos:
            for hook in grupo["hooks"]:
                total += 1
                payload = {
                    "session_id": "s-hooks", "cwd": str(cwd), "hook_event_name": evento,
                    "prompt": "ola", "source": "startup",
                    "tool_name": "Bash", "tool_input": {"command": "echo oi"},
                    "tool_response": {"stdout": "oi"},
                }
                comando = hook["command"].replace("${CLAUDE_PLUGIN_ROOT}", ROOT.as_posix())
                res = subprocess.run(
                    [BASH, "-c", comando],
                    input=json.dumps(payload), capture_output=True, text=True, encoding="utf-8",
                    timeout=TIMEOUT, env=_env(tmp_path, CLAUDE_PLUGIN_ROOT=str(ROOT)), cwd=cwd,
                )
                if res.returncode != 0 or "Traceback" in res.stderr:
                    falhas.append(f"{evento}: {hook['command']} -> {res.returncode}\n{res.stderr[-400:]}")
    assert total >= 15, "hooks.json perdeu comandos"
    assert not falhas, "\n".join(falhas)


def test_check_hook_liveness_cli_sai_zero_e_reprova_evento_mudo(tmp_path):
    agora = time.time()
    harness = tmp_path / "harness-liveness"
    (harness / "heartbeats").mkdir(parents=True)
    home = tmp_path / "home"
    projeto = home / ".claude" / "projects" / "algum-projeto"
    projeto.mkdir(parents=True)
    (projeto / "sessao.jsonl").write_text("{}", encoding="utf-8")

    def rodar() -> subprocess.CompletedProcess:
        return _script(tmp_path, CLI_LIVENESS, "--harness-dir", str(harness),
                       "--hooks-json", str(ROOT / HOOKS_JSON), "--home", str(home))

    for evento in ("UserPromptSubmit", "SessionStart"):
        (harness / "heartbeats" / evento).write_text(str(agora - 60), encoding="utf-8")
    assert rodar().returncode == 0, rodar().stdout

    (harness / "heartbeats" / "UserPromptSubmit").write_text(str(agora - 3 * 86400), encoding="utf-8")
    mudo = rodar()
    assert mudo.returncode == 1, mudo.stdout
    assert "UserPromptSubmit" in mudo.stdout and "[FAIL]" in mudo.stdout


# --------------------------------------------------------------------------- editorial


@exige_bash
def test_classify_hook_manda_carregar_workflow_com_drop_constrain_retain(tmp_path):
    """L-62: o hook manda carregar a skill, e o arquivo empacotado contem o portao."""
    cwd = _repo(tmp_path, "eta")
    res = _classificar(tmp_path, cwd, "s-drop", PROMPT_L2)
    assert "HARNESS v3 CLASSIFIED" in res.stdout, res.stdout + res.stderr
    assert "harness-workflow" in res.stdout

    skill = (ROOT / "skills" / "harness-workflow" / "SKILL.md").read_text(encoding="utf-8")
    assert "DROP / CONSTRAIN / RETAIN" in skill


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
