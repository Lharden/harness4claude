"""Portao de Stop num pipeline de docs, e leitura que nao sobe revisao.

Incidente 2026-09-25, sessao `146dc03e` (apresentacao PPEGPS, cwd
`Documents/mestrado/pdtd`, sem `.git` e sem suite). A task foi confirmada
`L2-docs` antes do primeiro bloqueio, e mesmo assim o portao pediu evidencia de
teste tres vezes, na fase 1 de 4, e escalou. Depois disso a sessao escreveu
127 testes sobre o proprio texto para passar pelo portao.

Duas causas, uma por metade deste arquivo:

1. So `evidence_type='test'` ligava `verified`, e os tres consumidores da
   coluna (Stop, `complete`, `continua`) nao olham o tipo do pipeline. Os
   pipelines de docs nao tem fase `tdd` (`contract/pipelines.json`); o que o
   passo final deles confere esta em `skills/documentation/SKILL.md`
   §Verificacao, e nenhum item e pytest.
2. Dos 59 toques `shell-placeholder` da task, 27 eram leitura pura, barrada
   por `cd` e `find` fora do vocabulario e por `_segmentos` transformar o alvo
   de `2>/dev/null` em cabeca de segmento.

Diagnostico em `docs/specs/portao-stop-sem-codigo-diagnostico.md`; requisitos,
decisoes do grill (D1-D3) e ACs em `docs/specs/portao-stop-sem-codigo-plano.md`.
"""
import importlib.util
import json
import os
import shlex
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


state = _load("portao_docs_state", "scripts/transactional_state.py")
paths = _load("portao_docs_paths", "scripts/harness_paths.py")
hook = _load("portao_docs_hook", "hooks/harness-transactional.py")

PIPELINES = json.loads((ROOT / "contract" / "pipelines.json").read_text(encoding="utf-8"))["pipelines"]
PIPELINE_L2_DOCS = PIPELINES["L2-docs"]

#: Os comandos de leitura que o incidente mediu, na forma em que rodaram.
LEITURAS_DO_INCIDENTE = (
    'cd "/c/Users/me/pdtd/articles" && ls',
    'cd "/c/Users/me/pdtd/build" && grep -n "Governanca 4" deck_conteudo.md',
    'ls -lah "C:\\Users\\me\\projeto" 2>/dev/null | grep -E "README|CLAUDE|docs"',
    'find "C:\\Users\\me\\projeto" -maxdepth 3 -type f -name "references.bib" 2>/dev/null | head -5',
)


def _pasta_sem_suite(tmp_path: Path) -> Path:
    """Como a pasta do incidente: sem `.git`, sem `tests/`, so texto."""
    cwd = tmp_path / "pdtd"
    (cwd / "build").mkdir(parents=True)
    (cwd / "build" / "deck_conteudo.md").write_text("# Deck\n", encoding="utf-8")
    return cwd


def _task(root: Path, cwd: Path, *, kind: str, pipeline: list[str]):
    bucket = paths.ensure_state_dir(root, cwd, session_id="sessao-docs")
    database = state.HarnessDatabase(bucket)
    task = database.start_task(
        scope_id=str(bucket),
        legacy_level=f"L2-{kind}",
        tier="L2",
        kind=kind,
        pipeline=pipeline,
        prompt="apresentacao",
    )
    (bucket / "state.json").write_text(
        json.dumps({"task_id": task["task_id"], "scope_id": task["scope_id"]}),
        encoding="utf-8",
    )
    return bucket, database, task


def _payload(event: str, cwd: Path, **extra):
    return {"hook_event_name": event, "cwd": str(cwd), "session_id": "sessao-docs", **extra}


def _bash(cwd: Path, raiz: Path, comando: str) -> str:
    return hook.handle_payload(
        _payload("PostToolUse", cwd, tool_name="Bash",
                 tool_input={"command": comando},
                 tool_response={"exit_code": 0, "output": ""}),
        harness_root=raiz,
    )


def _stop(cwd: Path, raiz: Path) -> str:
    return hook.handle_payload(_payload("Stop", cwd), harness_root=raiz)


def _ate_a_fase_final(database, task_id: str) -> dict:
    """Anda o pipeline ate a ultima fase, cumprindo as obrigacoes de artefato."""
    atual = database.task(task_id)
    while atual["phase"] != atual["pipeline"][-1]:
        if atual["phase"] in state.ARTIFACT_OBLIGATIONS:
            atual = database.record_artifact(
                task_id, state.ARTIFACT_OBLIGATIONS[atual["phase"]], "docs/fontes.md", None
            )
        proxima = atual["pipeline"][atual["pipeline"].index(atual["phase"]) + 1]
        atual = database.transition(task_id, proxima, expected_revision=atual["revision"])
    return atual


def _evidencia_de_docs(database, task_id: str, **campos):
    valores = {
        "evidence_type": "docs",
        "command": "docs/specs/deck-verification.md",
        "exit_code": 0,
        "tests_collected": 12,
        "tests_passed": 11,
        "tests_skipped": 1,
        "output_hash": "sha256-do-relatorio",
    }
    valores.update(campos)
    return database.record_evidence(task_id, **valores)


def _linha_da_receita(motivo: str) -> str:
    return next(linha for linha in motivo.splitlines() if "state_cli.py" in linha)


def _rodar(linha: str) -> subprocess.CompletedProcess:
    """Roda a linha como quem a copiou da mensagem: tokens do shell, sem shell."""
    argv = [a.strip('"') for a in shlex.split(linha, posix=False)]
    assert argv[0] == "python", linha
    return subprocess.run(
        [sys.executable, *argv[1:]], capture_output=True, text=True, timeout=60,
    )


def _preencher(linha: str, relatorio: Path) -> str:
    for marcador, valor in {"<N>": "12", "<P>": "11", "<S>": "1",
                            "<relatorio>": relatorio.as_posix()}.items():
        linha = linha.replace(marcador, valor)
    return linha


# ---------------------------------------------------------------------------
# Parte A — a evidencia vem do pipeline, cobrada na fase que a produz
# ---------------------------------------------------------------------------


def test_AC1_pipeline_de_docs_em_pasta_sem_suite_fecha_sem_pytest(tmp_path: Path):
    """A reproducao do incidente, com o desfecho que ele deveria ter tido.

    Antes do conserto, em ordem: as leituras subiam `code_revision`; o Stop da
    fase 1 bloqueava pedindo pytest; a evidencia `docs` nao ligava `verified`;
    e `complete` recusava.
    """
    cwd = _pasta_sem_suite(tmp_path)
    raiz = tmp_path / "harness"
    _bucket, database, task = _task(raiz, cwd, kind="docs", pipeline=PIPELINE_L2_DOCS)
    antes = database.task(task["task_id"])

    for comando in LEITURAS_DO_INCIDENTE:
        _bash(cwd, raiz, comando)
    assert database.task(task["task_id"])["code_revision"] == antes["code_revision"], (
        "leitura pura subiu code_revision: "
        f"{database.touches(task['task_id'], limite=len(LEITURAS_DO_INCIDENTE))}"
    )

    # Um interpretador continua contando (REQ-13) — e mesmo assim a fase 1 nao
    # e lugar de cobrar verificacao de uma doc que ainda nao existe (D1).
    _bash(cwd, raiz, 'cd "/c/Users/me/pdtd" && python -c "import docx"')
    assert database.task(task["task_id"])["code_revision"] == antes["code_revision"] + 1
    assert _stop(cwd, raiz) == "", "o Stop cobrou verificacao de docs na fase 1 de 4"
    assert database.task(task["task_id"])["stop_continuations"] == 0

    _ate_a_fase_final(database, task["task_id"])
    bloqueio = json.loads(_stop(cwd, raiz))
    assert bloqueio["decision"] == "block"

    relatorio = cwd / "build" / "deck-verification.md"
    relatorio.write_text("| afirmacao | fonte | estado |\n", encoding="utf-8")
    receita = _preencher(_linha_da_receita(bloqueio["reason"]), relatorio)
    cli = _rodar(receita)
    assert cli.returncode == 0, f"a receita impressa nao roda:\n{receita}\n{cli.stdout}{cli.stderr}"
    _bash(cwd, raiz, receita)

    verificada = database.task(task["task_id"])
    assert verificada["verified"] is True, (
        "a verificacao de docs registrada pela receita do portao nao ligou `verified`"
    )
    assert _stop(cwd, raiz) == ""
    feita = database.complete(task["task_id"], expected_revision=verificada["revision"])
    assert feita["status"] == "done"


def test_AC2_na_fase_final_o_bloqueio_pede_docs_e_nao_pytest(tmp_path: Path):
    cwd = _pasta_sem_suite(tmp_path)
    raiz = tmp_path / "harness"
    bucket, database, task = _task(raiz, cwd, kind="docs", pipeline=PIPELINE_L2_DOCS)
    _ate_a_fase_final(database, task["task_id"])

    motivo = json.loads(_stop(cwd, raiz))["reason"]

    assert "--type docs" in motivo
    assert "<relatorio>" in motivo, "a receita nao pode trazer relatorio preenchido"
    assert task["task_id"] in motivo and str(bucket) in motivo
    # O balde mora sob o `tmp_path` do pytest (`pytest-of-<user>`): tirado antes
    # de procurar a palavra, senao o teste mede o caminho e nao a mensagem.
    assert "pytest" not in motivo.replace(str(bucket), "<balde>")


def test_AC3_pytest_nao_verifica_nem_desverifica_a_doc(tmp_path: Path):
    """REQ-3, as duas direcoes: teste mede codigo, nao a afirmacao da doc."""
    cwd = _pasta_sem_suite(tmp_path)
    raiz = tmp_path / "harness"
    _bucket, database, task = _task(raiz, cwd, kind="docs", pipeline=PIPELINE_L2_DOCS)

    verde = database.record_evidence(
        task["task_id"], evidence_type="test", command="python -m pytest -q",
        exit_code=0, tests_collected=127, tests_passed=127, tests_skipped=0, output_hash="h",
    )
    assert verde["verified"] is False, "127 testes sobre o texto verificaram a doc"

    _evidencia_de_docs(database, task["task_id"])
    vermelho = database.record_evidence(
        task["task_id"], evidence_type="test", command="python -m pytest -q",
        exit_code=1, tests_collected=3, tests_passed=2, tests_skipped=0, output_hash="h",
    )
    assert vermelho["verified"] is True, "um pytest vermelho desverificou a doc"


def test_AC4_CONTROLE_codigo_continua_exigindo_teste_em_qualquer_fase(tmp_path: Path):
    """Metade que prova que o guarda de codigo nao afrouxou."""
    cwd = tmp_path / "repo"
    cwd.mkdir()
    raiz = tmp_path / "harness"
    _bucket, database, task = _task(raiz, cwd, kind="bug", pipeline=PIPELINES["L2-bug"])

    depois = _evidencia_de_docs(database, task["task_id"])

    assert depois["verified"] is False
    assert depois["phase"] == PIPELINES["L2-bug"][0], "o controle tem de rodar fora da fase final"
    motivo = json.loads(_stop(cwd, raiz))["reason"]
    # `python -m pytest`, e nao so `pytest`: o `tmp_path` do balde ja contem
    # `pytest-of-<user>`, e a assercao passaria sobre o caminho.
    assert "python -m pytest" in motivo and "--type test" in motivo


def test_AC5_editar_a_doc_verificada_invalida_a_verificacao(tmp_path: Path):
    """REQ-6: a doc e o produto sob verificacao; muda-la expira a evidencia."""
    cwd = _pasta_sem_suite(tmp_path)
    raiz = tmp_path / "harness"
    _bucket, database, task = _task(raiz, cwd, kind="docs", pipeline=PIPELINE_L2_DOCS)
    assert _evidencia_de_docs(database, task["task_id"])["verified"] is True

    _bash(cwd, raiz, "sed -i 's/Deck/Apresentacao/' build/deck_conteudo.md")

    assert database.task(task["task_id"])["verified"] is False


@pytest.mark.parametrize(
    "campos",
    [
        {"exit_code": 1},
        {"exit_code": None},
        {"tests_passed": 0, "tests_skipped": 12},
        {"tests_collected": 0, "tests_passed": 0, "tests_skipped": 0},
        {"tests_passed": 10, "tests_skipped": 1},
        {"tests_passed": 13, "tests_skipped": -1},
        {"output_hash": None},
    ],
    ids=["exit-nao-zero", "sem-exit", "nada-confirmado", "nada-conferido",
         "soma-nao-fecha", "descarte-negativo", "sem-relatorio"],
)
def test_AC6_evidencia_de_docs_fora_da_regua_nao_verifica(tmp_path: Path, campos: dict):
    cwd = _pasta_sem_suite(tmp_path)
    raiz = tmp_path / "harness"
    _bucket, database, task = _task(raiz, cwd, kind="docs", pipeline=PIPELINE_L2_DOCS)

    assert _evidencia_de_docs(database, task["task_id"], **campos)["verified"] is False


def test_AC8_receita_de_docs_e_isenta(tmp_path: Path):
    """A receita de docs grava em N e fica em N, como a de teste."""
    cwd = _pasta_sem_suite(tmp_path)
    raiz = tmp_path / "harness"
    bucket, database, task = _task(raiz, cwd, kind="docs", pipeline=PIPELINE_L2_DOCS)
    _evidencia_de_docs(database, task["task_id"])
    antes = database.task(task["task_id"])

    comando = _preencher(
        hook.comando_de_evidencia(bucket, task["task_id"], kind="docs"),
        cwd / "build" / "deck_conteudo.md",
    )
    assert hook.is_state_management(comando), comando
    _bash(cwd, raiz, comando)

    depois = database.task(task["task_id"])
    assert depois["code_revision"] == antes["code_revision"]
    assert depois["verified"] is True


def test_AC8_evidencia_de_docs_sem_relatorio_em_disco_e_recusada(tmp_path: Path):
    """D3: sem arquivo que a sustente, a evidencia de docs nao entra."""
    cwd = _pasta_sem_suite(tmp_path)
    raiz = tmp_path / "harness"
    bucket, database, task = _task(raiz, cwd, kind="docs", pipeline=PIPELINE_L2_DOCS)

    cli = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "state_cli.py"), "--home", str(bucket),
         "evidence", "--task", task["task_id"], "--type", "docs",
         "--command-text", str(cwd / "nao-existe.md"), "--exit-code", "0",
         "--tests-collected", "1", "--tests-passed", "1", "--tests-skipped", "0"],
        capture_output=True, text=True, timeout=60,
    )

    assert cli.returncode == 2, cli.stdout + cli.stderr
    assert "nao-existe.md" in cli.stdout
    with sqlite3.connect(database.path) as raw:
        assert raw.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 0


def test_AC8_evidencia_de_docs_grava_o_hash_do_relatorio(tmp_path: Path):
    import hashlib

    cwd = _pasta_sem_suite(tmp_path)
    raiz = tmp_path / "harness"
    bucket, database, task = _task(raiz, cwd, kind="docs", pipeline=PIPELINE_L2_DOCS)
    relatorio = cwd / "build" / "deck-verification.md"
    relatorio.write_text("| A1 | fonte.pdf:3 | confirmada |\n", encoding="utf-8")

    cli = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "state_cli.py"), "--home", str(bucket),
         "evidence", "--task", task["task_id"], "--type", "docs",
         "--command-text", str(relatorio), "--exit-code", "0",
         "--tests-collected", "1", "--tests-passed", "1", "--tests-skipped", "0"],
        capture_output=True, text=True, timeout=60,
    )

    assert cli.returncode == 0, cli.stdout + cli.stderr
    with sqlite3.connect(database.path) as raw:
        gravado = raw.execute("SELECT output_hash FROM evidence").fetchone()[0]
    assert gravado == hashlib.sha256(relatorio.read_bytes()).hexdigest()
    assert database.task(task["task_id"])["verified"] is True


def test_AC9_trocar_o_kind_zera_a_verificacao(tmp_path: Path):
    """REQ-8: a evidencia de um `kind` nao atravessa a reclassificacao."""
    cwd = _pasta_sem_suite(tmp_path)
    raiz = tmp_path / "harness"
    _bucket, database, task = _task(raiz, cwd, kind="docs", pipeline=PIPELINE_L2_DOCS)
    assert _evidencia_de_docs(database, task["task_id"])["verified"] is True

    depois = database.confirm_classification(
        task["task_id"], tier="L2", kind="feature", pipeline=PIPELINES["L2-feature"],
        source="semantic", confidence=0.9,
    )

    assert depois["verified"] is False
    assert _evidencia_de_docs(database, task["task_id"])["verified"] is False


#: Pipelines sem fase `tdd` que NAO tem evidencia propria, e por que. Um pipeline
#: novo sem `tdd` reprova o teste abaixo ate alguem decidir o que o verifica.
SEM_TDD_DECLARADOS = {
    "question": "L0 nao tem pipeline, logo nao tem portao",
    "review": "fora do escopo do ramo portao-de-stop-sem-codigo; dono: sessao pai",
}


def test_AC10_o_mapa_de_evidencia_cobre_o_contrato():
    """REQ-9: o que `transactional_state` exige e ligado ao que o contrato diz."""
    sem_tdd = sorted({
        nome.split("-", 1)[1] for nome, fases in PIPELINES.items() if "tdd" not in fases
    })
    assert "docs" in sem_tdd, "censo: o contrato deixou de ter pipeline de docs sem tdd"
    orfaos = [
        kind for kind in sem_tdd
        if state.tipo_de_evidencia(kind) == "test" and kind not in SEM_TDD_DECLARADOS
    ]
    assert not orfaos, (
        f"pipelines sem fase `tdd` caindo na regua de teste sem decisao: {orfaos}. "
        "Mapeie em `EVIDENCIA_DO_KIND` ou declare em SEM_TDD_DECLARADOS com o motivo."
    )
    for kind in PIPELINES:
        if "tdd" in PIPELINES[kind]:
            assert state.tipo_de_evidencia(kind.split("-", 1)[1]) == "test", kind


def test_AC11_teste_com_descarte_negativo_nao_verifica(tmp_path: Path):
    """REQ-2: `3 = 5 + (-2)` fechava a soma, e valia tambem para teste."""
    cwd = tmp_path / "repo"
    cwd.mkdir()
    raiz = tmp_path / "harness"
    _bucket, database, task = _task(raiz, cwd, kind="bug", pipeline=PIPELINES["L2-bug"])

    depois = database.record_evidence(
        task["task_id"], evidence_type="test", command="python -m pytest -q",
        exit_code=0, tests_collected=3, tests_passed=5, tests_skipped=-2, output_hash="h",
    )

    assert depois["verified"] is False


# ---------------------------------------------------------------------------
# Parte B — leitura pura nao sobe revisao
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "comando",
    [
        "cd x && grep -n alvo y.md",
        "cd build",
        "ls x 2>/dev/null | grep y",
        "cat x.md >/dev/null",
        "grep -rn alvo . 2> NUL",
        'grep -n "a > b" doc.md',
        "grep -rn alvo . 2>&1",
        'find . -name "*.py"',
        "find . -name '*-delete*'",
        'find x -maxdepth 3 -type f -name "*.md" 2>/dev/null | head -5',
        *LEITURAS_DO_INCIDENTE,
    ],
)
def test_AC7_leitura_pura_e_leitura(comando: str):
    assert hook.is_read_only(comando) is True, comando


@pytest.mark.parametrize(
    "comando",
    [
        "cat x > $OUT",
        "cat x > y.txt",
        "cat x >> y.txt",
        "ls 2>/dev/null > lista.txt",
        "find . -delete",
        "find . -name '*.pyc' -exec rm {} +",
        "find . -execdir rm {} +",
        "find . -fprint lista.txt",
        "cd x && python build.py",
        "cd x && sed -i s/a/b/ y.md",
        "cd x && awk '{print > \"saida\"}' y",
        "for f in *.md; do cat $f; done",
    ],
)
def test_AC7_CONTROLE_o_que_escreve_ou_pode_escrever_continua_contando(comando: str):
    assert hook.is_read_only(comando) is False, comando


def test_AC7_redirecionamento_para_variavel_nao_isenta_git():
    """REQ-11: sem `_segmentos` tropecar no `$OUT`, a recusa tem de ser explicita."""
    assert hook.nao_muda_a_arvore("git log > $OUT") is False
    assert hook.nao_muda_a_arvore("git log > saida.txt") is False
    assert hook.nao_muda_a_arvore("git log 2>/dev/null") is True
