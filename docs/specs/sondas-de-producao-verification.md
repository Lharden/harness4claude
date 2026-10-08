# Verificacao: lote H1 da troca de sondas (L-61..L-65), harness4claude

**Spec**: master-harness `docs/specs/sondas-de-producao-spec.md` (US-1, US-3); **design** §2.1, §5.
**Ramo**: `feat/sondas-de-producao` (a partir de `main` b3adaed). **Medido em**: 2026-10-08.

## Sabotagem dos 15 testes novos (AC-1.3)

Cada teste novo roda duas vezes contra uma COPIA temporaria do plugin (`hooks scripts tools tests contract skills
schemas sync`), apontada por `HARNESS_PLUGIN_ROOT`: intacta (tem de passar) e com o chamador desligado (tem de
reprovar). Nada disso entra em commit. Comando:

```
python sabotagem.py <raiz do worktree>      # script no apendice
```

| Teste | Sabotagem (chamador desligado) | Copia intacta | Copia sabotada | Veredito |
|---|---|---|---|---|
| `test_classify_hook_grava_sugestao_deterministica` | hooks/harness-classify.sh: exit 0 na 2a linha | rc=0 (1 passed in 2.58s) | rc=1 (1 failed in 0.42s) | OK |
| `test_confirm_cli_grava_confirmacao_semantica` | scripts/confirm_classification.py: sai 0 antes de rodar | rc=0 (1 passed in 2.86s) | rc=1 (1 failed in 2.81s) | OK |
| `test_confirm_cli_grava_human_override` | scripts/confirm_classification.py: sai 0 antes de rodar | rc=0 (1 passed in 2.85s) | rc=1 (1 failed in 2.71s) | OK |
| `test_classify_hook_isola_duas_sessoes_no_mesmo_repo` | hooks/harness-classify.sh: exit 0 | rc=0 (1 passed in 4.90s) | rc=1 (1 failed in 0.35s) | OK |
| `test_session_start_hook_abandona_task_vencida` | hooks/harness-session-start.sh: exit 0 | rc=0 (1 passed in 9.83s) | rc=1 (1 failed in 2.81s) | OK |
| `test_classify_hook_grava_pipeline_do_contrato` | hooks/harness-classify.sh: exit 0 | rc=0 (1 passed in 2.34s) | rc=1 (1 failed in 0.35s) | OK |
| `test_workflow_de_contexto_nomeia_o_no_morto` | wf-context-scan.js: censoNos vira identidade | rc=0 (1 passed in 0.28s) | rc=1 (1 failed in 0.39s) | OK |
| `test_verify_multimodel_com_dimensao_morta_nao_aprova` | wf-verify-multimodel.js: censoNos vira identidade | rc=0 (1 passed in 0.27s) | rc=1 (1 failed in 0.39s) | OK |
| `test_graphify_autosetup_dispara_update_sem_grafo` | hooks/harness-graphify-autosetup.sh: exit 0 | rc=0 (1 passed in 3.39s) | rc=1 (1 failed in 0.41s) | OK |
| `test_precompact_hook_espelha_spec_no_vault` | hooks/harness-precompact.sh: exit 0 | rc=0 (1 passed in 3.32s) | rc=1 (1 failed in 0.34s) | OK |
| `test_wiki_query_cli_acha_por_alias` | tools/wiki_query.py: sai 0 antes de rodar | rc=0 (1 passed in 0.38s) | rc=1 (1 failed in 0.42s) | OK |
| `test_branch_state_cli_recusa_abrir_alem_do_limite` | scripts/branch_state.py: max_open=10**6 | rc=0 (1 passed in 2.96s) | rc=1 (1 failed in 3.30s) | OK |
| `test_todo_comando_de_hooks_json_roda` | hooks/harness-classify.sh (registrado em hooks.json): exit 7 | rc=0 (1 passed in 22.72s) | rc=1 (1 failed in 20.15s) | OK |
| `test_check_hook_liveness_cli_sai_zero_e_reprova_evento_mudo` | scripts/check_hook_liveness.py: sai 0 antes de rodar | rc=0 (1 passed in 0.42s) | rc=1 (1 failed in 0.40s) | OK |
| `test_classify_hook_manda_carregar_workflow_com_drop_constrain_retain` | hooks/harness-classify.sh: exit 0 | rc=0 (1 passed in 2.24s) | rc=1 (1 failed in 0.31s) | OK |

Resultado: 15 de 15 passam intactos e reprovam sabotados.

Leitura de cada sabotagem: `.sh` ganha `exit 0` (ou `exit 7`) na 2a linha; `.py` ganha `sys.exit(0)` antes do primeiro
import; `wf-*.js` tem `censoNos` trocado por identidade (a chamada que a regra de ouro do design manda desligar);
`branch_state.py` troca `max_open` por `10**6`.

## Extensao do harness de teste (nao do workflow)

`tests/wf_run_harness.cjs` ganhou o rotulo `scan:` (cenario `scans`, valor `null` = no morto) e o `parallel` passou a
resolver thunk que lanca a `null`, a semantica do runtime de Workflow documentada em `workflow-authoring`. Os testes
existentes que usam o harness (`test_verify_por_tabela.py`, `test_workflow_returns.py`) seguem verdes.

## Cobranca de entrada (L-65)

`mh.paridade.cobrar_sondas` (ramo `feat/sondas-de-producao` do master-harness, regra de execucao da entrada) contra este
worktree, depois da troca do `EVIDENCE`: `exit 0`, so as 2 pendencias `integration.*` listadas.

## Apendice: script de sabotagem

```python
"""Sabotagem por teste novo, numa COPIA temporaria do plugin (nunca commitada).

Para cada caso: copia a arvore, desliga o chamador, roda o teste com
HARNESS_PLUGIN_ROOT apontando para a copia. Controle: o mesmo teste na copia
sem sabotagem tem de passar (as duas metades).
"""
import os, re, shutil, subprocess, sys, tempfile
from pathlib import Path

REPO = Path(sys.argv[1]).resolve()
SEL = sys.argv[2:]  # filtros opcionais por nome
PASTAS = ["hooks", "scripts", "tools", "tests", "contract", "skills", "schemas", "sync"]
T = "tests/test_sondas_de_producao.py::"


def sh_exit(code=0):
    def f(root):
        for p in (root / "hooks").glob("*.sh"):
            pass
    return f


def editar(arquivo, fn):
    def f(root):
        p = root / arquivo
        s = p.read_text(encoding="utf-8")
        novo = fn(s)
        assert novo != s, f"sabotagem nao mudou {arquivo}"
        p.write_text(novo, encoding="utf-8", newline="\n")
    return f


def sh_sai(code):
    return lambda s: re.sub(r"^(#!.*\n)", rf"\1exit {code}\n", s, count=1)


def py_sai(s):
    m = re.search(r"^(import |from (?!__future__))", s, re.M)
    return s[: m.start()] + "import sys; sys.exit(0)\n" + s[m.start():]


def censo_identidade(s):
    return s.replace(
        "function censoNos(rotulo, rotulos, obtidos) {",
        "function censoNos(rotulo, rotulos, obtidos) {\n  return { vivos: obtidos.filter(Boolean), mortos: [], esperado: rotulos.length }",
        1,
    )


CASOS = [
    ("test_classify_hook_grava_sugestao_deterministica", "hooks/harness-classify.sh: exit 0 na 2a linha", "hooks/harness-classify.sh", sh_sai(0)),
    ("test_confirm_cli_grava_confirmacao_semantica", "scripts/confirm_classification.py: sai 0 antes de rodar", "scripts/confirm_classification.py", py_sai),
    ("test_confirm_cli_grava_human_override", "scripts/confirm_classification.py: sai 0 antes de rodar", "scripts/confirm_classification.py", py_sai),
    ("test_classify_hook_isola_duas_sessoes_no_mesmo_repo", "hooks/harness-classify.sh: exit 0", "hooks/harness-classify.sh", sh_sai(0)),
    ("test_session_start_hook_abandona_task_vencida", "hooks/harness-session-start.sh: exit 0", "hooks/harness-session-start.sh", sh_sai(0)),
    ("test_classify_hook_grava_pipeline_do_contrato", "hooks/harness-classify.sh: exit 0", "hooks/harness-classify.sh", sh_sai(0)),
    ("test_workflow_de_contexto_nomeia_o_no_morto", "wf-context-scan.js: censoNos vira identidade", "scripts/workflows/wf-context-scan.js", censo_identidade),
    ("test_verify_multimodel_com_dimensao_morta_nao_aprova", "wf-verify-multimodel.js: censoNos vira identidade", "scripts/workflows/wf-verify-multimodel.js", censo_identidade),
    ("test_graphify_autosetup_dispara_update_sem_grafo", "hooks/harness-graphify-autosetup.sh: exit 0", "hooks/harness-graphify-autosetup.sh", sh_sai(0)),
    ("test_precompact_hook_espelha_spec_no_vault", "hooks/harness-precompact.sh: exit 0", "hooks/harness-precompact.sh", sh_sai(0)),
    ("test_wiki_query_cli_acha_por_alias", "tools/wiki_query.py: sai 0 antes de rodar", "tools/wiki_query.py", py_sai),
    ("test_branch_state_cli_recusa_abrir_alem_do_limite", "scripts/branch_state.py: max_open=10**6", "scripts/branch_state.py",
     lambda s: s.replace('max_open=_integer_setting("HARNESS_BRANCH_MAX_OPEN", 3),', "max_open=10**6,", 1)),
    ("test_todo_comando_de_hooks_json_roda", "hooks/harness-classify.sh (registrado em hooks.json): exit 7", "hooks/harness-classify.sh", sh_sai(7)),
    ("test_check_hook_liveness_cli_sai_zero_e_reprova_evento_mudo", "scripts/check_hook_liveness.py: sai 0 antes de rodar", "scripts/check_hook_liveness.py", py_sai),
    ("test_classify_hook_manda_carregar_workflow_com_drop_constrain_retain", "hooks/harness-classify.sh: exit 0", "hooks/harness-classify.sh", sh_sai(0)),
]


def copiar() -> Path:
    base = Path(tempfile.mkdtemp(prefix="sabotagem-"))
    for p in PASTAS:
        shutil.copytree(REPO / p, base / p, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    shutil.copy(REPO / "pyproject.toml", base / "pyproject.toml")
    return base


def rodar(root: Path, teste: str):
    env = {**os.environ, "HARNESS_PLUGIN_ROOT": str(root)}
    r = subprocess.run(
        [sys.executable, "-m", "pytest", T + teste, "-q", "-p", "no:cacheprovider", "--no-header", "-x"],
        cwd=root, env=env, capture_output=True, text=True, encoding="utf-8", timeout=600,
    )
    ultima = [l for l in r.stdout.strip().splitlines() if l.strip()][-1]
    return r.returncode, ultima


for teste, descr, arquivo, fn in CASOS:
    if SEL and not any(s in teste for s in SEL):
        continue
    ctl = copiar()
    c_rc, c_txt = rodar(ctl, teste)
    shutil.rmtree(ctl, ignore_errors=True)
    sab = copiar()
    editar(arquivo, fn)(sab)
    s_rc, s_txt = rodar(sab, teste)
    shutil.rmtree(sab, ignore_errors=True)
    ok = c_rc == 0 and s_rc != 0
    print(f"{'OK ' if ok else 'ERRO'} | {teste} | {descr} | intacto rc={c_rc} ({c_txt}) | sabotado rc={s_rc} ({s_txt})", flush=True)
```
