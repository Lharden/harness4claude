---
title: Inventário do Estado Real — Harness4Claude
document_type: inventory
status: active
created: 2026-07-24
base_commit: a56ee80
plan_reference: PLANO_AUTOREFORMA_HARNESS4CLAUDE.md §4.1
---

# Inventário — Harness4Claude em `a56ee80`

Levantamento por exploração direta do código em 2026-07-24. Satisfaz o artefato obrigatório §4.1 do plano.

## 1. Árvore relevante

89 arquivos versionados em `a56ee80` (verificado por `git ls-tree -r a56ee80 --name-only | wc -l`; a contagem no HEAD atual é maior porque inclui os artefatos desta reforma). **Não há** `src/`, `lib/`, pacote instalável, `pyproject.toml`, `.github/` (nenhum CI), `CHANGELOG`, `docs/self-reform/` (criado agora) nem `worktrees/`.

```
.claude-plugin/   marketplace.json (3.2.0), plugin.json (3.3.0-beta.1)   ← divergentes
docs/             CONTEXT.md, SYNC.md, router.md
  specs/          graphify-integration-{spec,design,verification}.md, skill-router-design.md
  superpowers/plans/2026-07-23-skill-router-p1.md
hooks/            9 scripts + hooks.json + skill_router.py
schemas/          state.schema.json, signals.schema.json, spec.schema.json
scripts/          12 arquivos + workflows/ (2 .js + validate_workflows.cjs)
skills/           11 skills + 3 templates + 5 references
sync/templates/   3 snippets
tests/            15 arquivos + data/golden-prompts.json
tools/            export_plugins.py, vault_maintenance.py, vault_sync_doctor.py
```

LOC: Python não-teste 2.746 · testes 3.013 · bash 1.663 · JS 262 · Markdown 4.595.

## 2. Deployment — três cópias, e a que roda não é a que se edita

| Caminho | Papel | Estado |
|---|---|---|
| `Documents/projects/harness4claude` | clone de trabalho | `a56ee80`, v3.3.0-beta.1 |
| `~/.claude/plugins/local/harness4claude` | segundo clone, dev | `a56ee80`; sujo (`.pytest_tmp_*`, `graphify-out/`) |
| `~/.claude/plugins/cache/harness4claude/harness4claude/3.2.0` | **carregado pelo Claude Code** | v**3.2.0**, `gitCommitSha: 24c1812`, `installedAt: 2026-06-17` |

Inspeção do cache confirma ausência de `harness-skill-router.sh`, `skill_router.py`, `harness-router-warmup.sh` e `harness-graphify-autosetup.sh`; seu `hooks.json` registra apenas os 5 hooks originais.

**Consequências.** O skill-router e o graphify-autosetup estão mergeados em main e **inativos em runtime**. O índice em `~/.claude/harness/skills-index/` (276 skills, 2026-07-24T15:59) veio de execução manual, não do hook. A mensagem do commit `bfa4a93` — "já ativo no cache 3.2.0" — é factualmente incorreta.

**Agravante.** Caminhos hardcoded para o clone dev fazem o LLM executar scripts de **uma** árvore enquanto os hooks rodam de **outra**.

> **Correção da auditoria (2026-07-24):** a primeira redação dizia "10 caminhos". A contagem verificada é de **18 ocorrências em 9 arquivos** — e, mais importante, **nem todas são defeito**. Distribuição real:
>
> | Arquivo | Natureza |
> |---|---|
> | `skills/harness-workflow/SKILL.md` (L35, 83, 264, 269) | **defeito** — instrui o LLM a rodar o clone dev |
> | `skills/compress-memory/SKILL.md:37` | **defeito** |
> | `sync/templates/claude-md.harness.snippet.md` (L34, 46) | **defeito** — propaga o erro para novas máquinas |
> | `scripts/setup-graphify.sh` | **defeito** |
> | `README.md:357-360` | **defeito** |
> | `docs/SYNC.md` | contextual — runbook de sincronização |
> | `scripts/sync-machine.sh:88` | **uso legítimo** — é o script que *clona* para `~/.claude/plugins/local/`; a referência é a função dele |
> | `docs/specs/graphify-integration-verification.md` | registro histórico — não alterar |
> | `docs/superpowers/plans/2026-07-23-skill-router-p1.md` | registro histórico — não alterar |
>
> P-1.a deve corrigir apenas as cinco primeiras linhas da tabela. Trocar as demais seria erro: quebraria o `sync-machine.sh` e reescreveria registro histórico.

`docs/router.md:89-93` já documenta o caminho correto de ship (push + `/plugin update`) e alerta para nunca editar só o cache. O único mecanismo de resolução é `CLAUDE_PLUGIN_ROOT`, com fallback `$(cd "$(dirname "$0")/.." && pwd)`.

## 3. Estado — singleton global

Localização única: `$HOME/.claude/harness/state.json`. Hardcoded em ~10 lugares: `hooks/harness-classify.sh:14-16`, `harness-reclassify.sh:6`, `harness-session-start.sh:10`, `harness-precompact.sh:7`, `hooks/skill_router.py:18-22`, `scripts/init-state.sh:8`, `record_signal.py:106`, `migrate_state.py:209`, `health-check.sh:15`.

`grep -r 'scope_id|worktree'` no código: **zero ocorrências**.

### Escritores

| Escritor | Mecanismo | Atômico | Lock |
|---|---|---|---|
| `harness-classify.sh` `_atomic_write_json` (L85-97) | tmp + flush + fsync + `os.replace` | **sim** | **sim** |
| `harness-reclassify.sh` (L112-113, L85-86) | `json.dump` direto | não | sim |
| `harness-session-start.sh` (L14-26) | heredoc `cat >` (só se ausente) | não | não |
| `scripts/init-state.sh` | heredoc `cat >` (só se ausente) | não | não |
| `scripts/migrate_state.py` `_write` (L168-175) | `json.dump` direto | não | não |
| **skill `harness-workflow`** (SKILL.md L28, L255) | **o LLM edita via `Edit` tool** | não | não |
| `hooks/skill_router.py` (L140) | leitura sem lock (deliberado) | — | — |

O escritor mais frequente em runtime — transições de fase do pipeline — é o modelo, fora de qualquer controle. Ver risco **R1**.

### Lock (`scripts/state-lock.sh`, 126 L)

Mutex cooperativo por `mkdir` de diretório (`state.json.lockdir`), não `flock`. Timeout 5 s, poll 50 ms, stale por mtime de 30 s (`_state_lock_remove_if_stale` → `rm -rf`), release comparando o PID gravado em `lockdir/owner`. Fail-closed nos hooks (`acquire_state_lock || exit 0`).

**Sem fencing token, revision ou owner_epoch** — nada impede um processo que dormiu além do stale de voltar a escrever depois de perder a propriedade (ABA clássico).

### Idempotência

- `task_id` gerado só em `harness-classify.sh:337`: `now.strftime("t-%Y%m%d-%H%M%S%f")` — microssegundos adicionados para evitar colisão destrutiva.
- `record_signal.py:85-90`: remove task de mesmo `task_id` e re-append; agregados recalculados de `tasks`.
- `--expect-task` (`record_signal.py:113-131`): exit 2 sem gravar se `state.json.task_id` divergir. Nasceu do incidente de 2026-06-12 (task fantasma `t-20260612-034438`). É a **única** defesa contra troca de singleton — e depende do LLM lembrar da flag. Ver **R6**.

### Evidência de campo colhida no bootstrap

`state.json` em 2026-07-24 continha task `t-20260724-170615852523`, `L2-feature`, `status: active`, iniciada às 17:06 por uma sessão não relacionada (prompt sobre preparação de apresentação), com `current_step: null` e `artifacts_so_far: []`. Órfã há horas. Confirma **R5** (fechamento não confiável) e **R8** (singleton compartilhado entre sessões).

## 4. Hooks

`hooks/hooks.json` — todos `type: command`, `bash "${CLAUDE_PLUGIN_ROOT}/hooks/..."`:

| Evento | Script | Timeout | Papel |
|---|---|---|---|
| UserPromptSubmit | `harness-classify.sh` (419 L) | 10 s | classifica L0/L1/L2 × tipo, escreve state, emite `systemMessage` |
| UserPromptSubmit | `harness-skill-router.sh` → `skill_router.py` (252 L) | 5 s | injeta `[skill-hint]` |
| PreToolUse `Bash` | `harness-git-guard.sh` (64 L) | 5 s | bloqueia git destrutivo (exit 2) |
| PostToolUse `Edit\|Write` | `harness-reclassify.sh` (124 L) | 5 s | conta arquivos, promove L0→L1 em 3+ |
| PreCompact | `harness-precompact.sh` (89 L) | 10 s | snapshot no trace, rotação, `vault_sync.py` |
| SessionStart | `harness-session-start.sh` (150 L) | 15 s | bootstrap, migração v2→v3, dep-check, resume |
| SessionStart | `harness-graphify-autosetup.sh` (84 L) | 10 s | AST pass 1× por repo |
| SessionStart | `harness-router-warmup.sh` (23 L) | 5 s | `--check-stale`, rebuild bg, warm ping Ollama |

Dormente e versionado, **não registrado**: `hooks/context7-trigger.sh` + `.py` (129 L, ~120 libs).

Arquitetura: **bash como casca, Python como motor** — heredocs `python << 'PYEOF'`. Padrão Windows repetido em todos: `PYTHONUTF8=1`, `PYTHONIOENCODING=utf-8`, `cygpath -w`, `MSYS_NO_PATHCONV=1`.

## 5. Telemetria e sinais

**Não há telemetria estruturada.** Zero ocorrências de otel/trace_id/span/jsonl.

- `~/.claude/harness/signals.json` (schema em `schemas/signals.schema.json`) — escrito **só no DONE**, granularidade de task inteira. **Estado real da máquina: `tasks: []`, agregados zerados. Nunca registrou uma única task.**
- `aggregates` recalculados por `migrate_state.recompute_aggregates` (L94-138): contagens L0/L1/L2, `pipeline_completion_rate`, `avg_files_per_task`, `sdd_usage` (copiado do anterior — **nunca incrementado por código**), e o bloco `classify` (`avg_classify_accuracy`, `regex_vs_semantic_agreement`, `human_override_count`), todos derivados de `classification_meta.agreed`.
- Traces em Markdown: `trace-current.md`, append de `## [SNAPSHOT]` no PreCompact, rotação >50 KB para `traces/`. O formato por fase (`[INIT]`/`[EXECUTE]`/`[VERIFY]`) existe apenas como documentação em `skills/harness-workflow/references/trace-format.md` — **é o LLM que escreve**.
- `summaries/` é citado em `trace-format.md:7` e **nenhum código o escreve**.
- Logs do router: `router/debug-router.log`, `shim-errors.log`, `session-{id}.json`.

## 6. Classificador

Inline em `hooks/harness-classify.sh` L80-419 (heredoc Python). Normalização lowercase + NFKD + strip de combining chars. Tabelas regex PT/EN com `\b` obrigatório: `l0_questions` (18), `l0_cosmetic` (12), `l0_meta` (7), `l1_bug` (22), `l1_refactor` (20), `l1_small_feature` (6), `l2_*` incluindo regex de coocorrência. Precedência: L0 só se nenhum L1/L2 casar; L2 vence L1 no empate; default **L1**. Tipo: `bug` > `refactor` > `architecture` > `feature`.

`classification_meta` (L349-355): `{suggested, final, source:"regex", confidence, agreed}`. Para L1+, `final` fica `None` até a confirmação semântica — que **não é código**: é protocolo em Markdown para o LLM. `wf-classify-semantic`, citado no comentário L348, **não existe**.

Sig-guards (L114-145): `AUTOMATION_SIGNATURES`, `MAX_CLASSIFY_LEN=30000`, `MAX_SWITCH_LEN=1500` com `SWITCH_PATTERNS`.

## 7. Git guard

`hooks/harness-git-guard.sh` — 6 `grep -qE` sobre a string bruta. Bloqueia `push --force` (inclui `--force-with-lease`), `reset --hard`, `clean -*f`, `branch -*D`, `checkout .`, `restore .`; avisa em `push` normal.

**Não cobre**: `rm -rf`, `curl | bash`, `chmod`, redireções destrutivas, `sudo`, nem qualquer ofuscação trivial (`bash -c`, `env`, variáveis, espaçamento). Sem AST, sem policy file, sem allowlist. É o único PreToolUse do plugin.

## 8. Skill-router v3.3.0-beta.1

12 commits (`a1efa4c`…`a56ee80`), TDD conforme `docs/superpowers/plans/2026-07-23-skill-router-p1.md`. **Padrão de qualidade de referência para a reforma.**

- `scripts/build_skills_index.py` (230 L, stdlib): `scan_skills()`, `parse_frontmatter()`, `fingerprint()` sha1, embeds Ollama `nomic-embed-text-v2-moe` em lotes de 64, f16 row-major, saídas atômicas via `os.replace`, `--no-embed`, `--check-stale`.
- `hooks/skill_router.py`: `passes_guards()` (20 ≤ len ≤ 30000, assinaturas de automação, pipeline ativo suprime), Camada A regex word-boundary + `_is_specific_name()`, Camada B dot product puro-Python com boost logarítmico de uso, `route()` (B só dispara se A vazio — política do commit `1b42240`), `pick()` (TOP_K=3, MIN_COS=0.45, MIN_MARGIN=0.05 sobre a mediana, barra extra para plugin desabilitado), `apply_dedupe()`. Contrato: `except Exception → sys.exit(0)`.
- `scripts/skill-aliases.json`: 8 entradas curadas.
- `tests/data/golden-prompts.json` (15 positivos PT/EN + 4 negativos) + `test_router_golden.py` com gate `rate >= 0.80`; `scripts/bench_router.py` mede p50/p95/max por subprocess real.
- `docs/router.md`: knobs, medições (**93.3% = 14/15**; A p95 ~470-535 ms; B p95 ~1.4-1.5 s; 276 skills dim 768), riscos conhecidos (documenta a race do `os.replace` no Windows), rollback.

## 9. Graphify

- `scripts/setup-graphify.sh` (39 L) — **rodado pelo usuário**; instala `graphifyy` (duplo y, com alerta de typosquat), backups de settings/CLAUDE.md.
- `hooks/harness-graphify-autosetup.sh` — dispara `graphify update` em background 1× por repo, com marker; sempre exit 0.
- `skills/graph-context/SKILL.md` (40 L) — fase de contexto L2; lê `GRAPH_REPORT.md`, aprofunda com `graphify query`, checa staleness contra HEAD, espelha no vault.
- Grafo e manifesto ficam em `graphify-out/` do repo analisado — **gitignored**. Não existe manifesto versionado, cache com invalidação nem métrica de utilidade.
- Specs em `docs/specs/graphify-integration-*`; verification reporta 775 nós / 1.041 arestas / 62 comunidades e 1.080 notas exportadas, com 4 gaps abertos.

## 10. Verificação multimodelo

`scripts/workflows/wf-verify-multimodel.js` (135 L):
- fase **Review** — `parallel()` de 5 dimensões (`spec-coverage`, `correctness`, `security`, `edge-cases`, `regressions`) com `FINDINGS_SCHEMA`;
- dedupe por `${file}::${title[:40]}`;
- fase **Adjudicate** — adversarial: cada finding vai a um agente instruído a **refutar**, com `VERDICT_SCHEMA {is_real, confidence, reason}`;
- filtro `is_real && confidence >= 0.5`; `pass` = zero critical/high.

**Sem calibração**: threshold fixo, sem medição de FP/FN, sem registro dos vereditos. A confidence bruta é descartada no filtro — exatamente o dado que a Fase 9 precisaria. Ver **R9**.

Complementos: `wf-context-scan.js` (5 ângulos), `validate_workflows.cjs` (valida `meta` + sintaxe por `vm.Script`, sem executar), `skills/verify-against-spec/SKILL.md` (checklist single-model).

## 11. Testes

**185 testes** coletados em 0,24 s.

| Arquivo | N | Foco |
|---|---|---|
| `test_harness.py` | 56 | classify(24), reclassify(11), git-guard(7), precompact(2), SDD(10), integração(2) — `unittest`, subprocess bash real |
| `test_compress_memory.py` | 34 | blacklist, backup, savings |
| `test_context7_trigger.py` | 21 | hook dormente |
| `test_vault_maintenance.py` | 13 | |
| `test_skill_router.py` | 10 | camadas, pick, guards, dedupe |
| `test_state_lock.py` | 9 | **concorrência** |
| `test_record_signal.py` | 9 | idempotência, `--expect-task` |
| `test_build_skills_index.py` | 9 | |
| `test_state_migration.py` | 8 | |
| demais | 15 | doctor, workflows, export, vault sync, router golden |

Execução: `python -m pytest tests/ -v`. Histórico: `124 passed in 112.92s` (2026-06-12); a suíte atual em Windows fica na casa dos 231 s (cada teste do classify faz spawn de bash + Python).

**Concorrência**: boa para o lock — `TestConcurrency`, `TestStaleHandling` (backdate de mtime), `TestWriteRaceProtection` parametrizado com 5 e 10 workers assertando zero lost updates, `TestReentrancySemantics`.

**Crash: zero.** Nenhum kill no meio de escrita. Nenhum property-based (sem `hypothesis`; `requirements.txt` tem pytest, pyyaml, bandit, pip-audit). Nenhum fuzzing.

**Gap estrutural**: `test_harness.py` opera sobre `~/.claude/harness` **real**, com backup/restore em `setUpClass`/`tearDownClass` (L116-138) e `write_state()` direto no arquivo de produção. Consequência direta do singleton. Ver **R2**.

## 12. Arquitetura de código

Sem módulo Python compartilhado. Cada hook bash embute seu próprio Python, duplicando leitura de state, cygpath e configuração de UTF-8. `_atomic_write_json` tem **três implementações divergentes** (`harness-classify.sh`, `record_signal.py`, `build_skills_index.py`) e está **ausente** em `harness-reclassify.sh` e `migrate_state.py`. Único import cross-módulo: `record_signal.py:32-33` faz `sys.path.insert` para importar de `migrate_state`. `tools/` é o único pacote real.

## 13. Operação

- **Circuit breakers**: existem apenas como prosa para o LLM (`references/cycle-protocol.md:75`, `verify-gates.md:53-54`). **Nenhum código conta ciclos ou interrompe nada.**
- `scripts/health-check.sh` (193 L): dependências, state, hooks (5 — **não inclui router nem graphify-autosetup**), skills, SDD, v3.1 workflows/schemas, artefatos órfãos (task ativa >24 h = WARN), Obsidian (WARN-only), skill-router (WARN-only), e plugin load (`claude plugin list | grep harness4claude`, FAIL se ausente).
- `scripts/sync-machine.sh` (264 L): merge aditivo e idempotente de config, backups `.bak-sync-<ts>`, `--dry-run`, nunca escreve segredo.
- `scripts/diagnose_ollama.py`, `init-state.sh`, `migrate_state.py` (com `--dry-run`, validação jsonschema opcional).
- Vault: `scripts/vault_sync.py` (espelha traces/specs/remember por mtime, precedência `AI_BRAIN_PATH` > `$VAULT_PATH/AI-Brain` > default), disparado no PreCompact sempre com `|| true`; `tools/vault_sync_doctor.py` (read-only, `--check-rest`); `tools/export_plugins.py`; `tools/vault_maintenance.py` (724 L, maior Python do repo).

## 14. Docs e versão

- `docs/specs/`: o triplete graphify (spec→design→verification) é o **único ciclo SDD completo** materializado.
- **`docs/self-reform/` não existia** (criado nesta onda). **Sem CHANGELOG.**
- `plugin.json` = `3.3.0-beta.1`; `marketplace.json` = `3.2.0` — divergência não corrigida no bump `41326e0`.
- `README.md` (431 L) dessincronizado: tabela de pipelines diverge do `PIPELINES` real (ex.: L1-feature); L156-186 afirma que a classificação roda como `PreToolUse` (é `UserPromptSubmit`); L209 manda rodar `tests/test_classify.py`, que não existe; não menciona o skill-router.
- `skills/harness-workflow/SKILL.md` ainda se declara "Orquestrador de pipeline v2" e cita skills fantasma (`write-a-prd`, `prd-to-plan`).

## 15. Git

`main` local; 8 branches remotas antigas (a mais recente depois de main é `feat/harden-validate`, 2026-06-17). Tags: `v3.0.0` e, a partir desta onda, `pre-reform-base`. Sem CI. Worktree de reforma criado em `worktrees/harness4claude-self-reform` (branch `self-reform/main`).

Últimos commits: `a56ee80` (merge skill-router P1) ← `ee84f8b` ← `41326e0` ← `1b42240` ← `f35f438` ← `3fcf74f` ← `d96c87b` ← `2b75ced` ← `a8321f2` ← `9eefbd0` ← `f8d4d5e` ← `9b045c0` ← `a1efa4c` ← `bfa4a93` ← `71aa852` ← `24c1812` (2026-06-17, base do cache em runtime).

## 16. Lacunas frente às fases do plano

| Fase | Estado | Âncora |
|---|---|---|
| 0 Baseline/inventário | **não** (este documento inicia) | — |
| 1 Telemetria | **parcial fraca** — signals por task, nunca disparou; zero eventos | `record_signal.py` |
| 2 scope_id | **zero** | grep = 0 |
| 3 SQLite + fencing | **zero** — JSON + mkdir-mutex sem revision/epoch | `state-lock.sh` |
| 4 API única | **zero** — 7 escritores, incluindo o LLM | §3 acima |
| 5 FSM formal | **schema pronto, código ausente** — `state.schema.json` já tem `phase`, `pipeline_phases`, `pending_gate`, `workflow_runs`, `status: awaiting_gate`; só `skill_router.passes_guards` consulta | `schemas/state.schema.json:37-65` |
| 6 Graphify manifesto | **zero** | `graph-context/SKILL.md` |
| 7 FTS5 | **zero** — memória é markdown + `shutil.copy2` | `vault_sync.py` |
| 8 Shell AST | **zero** — 6 regex de git | `harness-git-guard.sh` |
| 9 Multimodelo calibrado | **parcial** — 5 dimensões + adjudicação existem; threshold fixo, sem registro | `wf-verify-multimodel.js:118` |
| 10 Property/crash | **zero** de crash e property; concorrência de lock existe | `test_state_lock.py` |
| 11 Process mining | **zero** | — |
| 12 Hot paths | **zero** (correto: depende das anteriores) | — |
| 13 Canário | **zero** — o mais próximo é o marker `.stale` e o rollback manual do router | `docs/router.md:85-88` |
