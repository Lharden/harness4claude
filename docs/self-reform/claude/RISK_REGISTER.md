---
title: Registro de Riscos — Autorreforma do Harness4Claude
document_type: risk-register
status: active
created: 2026-07-24
plan_reference: PLANO_AUTOREFORMA_HARNESS4CLAUDE.md §4.3
---

# Registro de Riscos

Formato conforme plano §4.3. Duas seções: **R** — riscos do estado real levantados no inventário, ausentes do plano genérico; **P** — riscos mínimos exigidos pelo plano.

Probabilidade e impacto em escala baixa/média/alta. `status`: aberto · mitigando · fechado · aceito.

---

## Seção R — Riscos do estado real

### R1 — LLM escreve `state.json` diretamente via `Edit`

| Campo | Conteúdo |
|---|---|
| **Causa** | `skills/harness-workflow/SKILL.md` (L28, L255) instrui o modelo a atualizar o state com o `Edit` tool. É o escritor mais frequente em runtime. |
| **Efeito** | Bypass total de lock, atomicidade, schema-check e idempotência. Gate humano forjável por edição de arquivo. Torna todo invariante de escrita inaplicável. |
| **Probabilidade** | alta — acontece em toda transição de fase |
| **Impacto** | alto |
| **Detecção** | `harness-state-guard.sh` (PreToolUse em Edit/Write/MultiEdit com path casando `*harness/state.json`), modo observe na O1 |
| **Mitigação** | O1: guard em observe, medindo a frequência real. O3: guard em **deny** + skill reescrita para `harnessctl task transition` + mutante crítico "gate bypass por edição de arquivo" na suíte. |
| **Rollback** | Guard tem flag de desativação; a skill legada permanece em wrapper durante a O3. |
| **Owner** | reforma |
| **Status** | aberto |

### R2 — Testes operam sobre o `~/.claude/harness` de produção

| Campo | Conteúdo |
|---|---|
| **Causa** | `test_harness.py:116-138` faz backup/restore do diretório real em `setUpClass`; `write_state()` escreve no arquivo de produção. |
| **Efeito** | Risco de corromper estado real durante a reforma; impede crash-injection (não se injeta kill num restore best-effort); gera flakiness que invalida o requisito de variância do §5. |
| **Probabilidade** | alta |
| **Impacto** | alto |
| **Detecção** | Assert de segurança no `conftest.py` que falha a suíte se algum teste resolver para o path real; hash do diretório antes/depois. |
| **Mitigação** | P-1.b: `HARNESS_DIR` override + fixture temporária por teste. |
| **Rollback** | Default de `HARNESS_DIR` permanece `~/.claude/harness` — nenhuma quebra de comportamento em runtime. |
| **Owner** | reforma |
| **Status** | mitigando (task L2 da O0) |

### R3 — Runtime executa código diferente do medido

| Campo | Conteúdo |
|---|---|
| **Causa** | Cache `3.2.0 @ 24c1812` carregado; main em `a56ee80`. |
| **Efeito** | Baseline atribui comportamento ao código errado; features mergeadas (skill-router) não são exercitadas; conclusões da reforma ficam sem lastro. |
| **Probabilidade** | alta — é o estado atual |
| **Impacto** | alto |
| **Detecção** | `VERSION_STAMP` emitido por todo hook; bloco de proveniência no `health-check.sh` comparando cache × HEAD × marketplace. |
| **Mitigação** | P-1.a: ship 3.3.0 e fixar o objeto-de-medição ([ADR-000](ADR/ADR-000-objeto-de-medicao.md)); daí em diante, ship só em fronteira de onda. |
| **Rollback** | `/plugin update` para a tag da onda anterior. |
| **Owner** | Leonardo (executa `/plugin update`) |
| **Status** | aberto |

### R4 — Duas árvores de código em uso simultâneo

| Campo | Conteúdo |
|---|---|
| **Causa** | 10 caminhos hardcoded para o clone dev em skills, templates e docs, enquanto os hooks resolvem por `CLAUDE_PLUGIN_ROOT` (cache). |
| **Efeito** | O LLM executa scripts de uma versão e os hooks de outra; comportamento irreprodutível; diagnósticos enganosos. |
| **Probabilidade** | alta |
| **Impacto** | médio |
| **Detecção** | grep por `Documents/projects/harness4claude` e `plugins/local/harness4claude` fora de docs de sincronização. |
| **Mitigação** | P-1.a: substituir por `${CLAUDE_PLUGIN_ROOT}`; checker no health-check. |
| **Rollback** | Trivial (reversão textual). |
| **Owner** | reforma |
| **Status** | aberto |

### R5 — Telemetria de tasks nunca funcionou

| Campo | Conteúdo |
|---|---|
| **Causa** | `record_signal.py` depende de o LLM lembrar de executá-lo no DONE. `signals.json` real: `tasks: []`, agregados zerados. Evidência corroborante: task órfã `t-20260724-170615852523` ativa há horas sem fechamento. |
| **Efeito** | Todas as métricas de qualidade do plano §4.2 são inobserváveis; `sdd_usage` nunca incrementa; a Fase 9 não tem dados para calibrar. |
| **Probabilidade** | certa — já ocorreu 100% das vezes |
| **Impacto** | alto |
| **Detecção** | Contagem de tasks em `signals.json` × tasks observadas na telemetria JSONL da O1. |
| **Mitigação** | O1: root-cause e mudança do disparo para o hook **Stop** (código, não disciplina). Regra geral derivada: nenhum invariante novo pode depender de disciplina do LLM. |
| **Rollback** | `record_signal.py` mantém a CLI atual; o hook é aditivo. |
| **Owner** | reforma |
| **Status** | aberto |

### R6 — `--expect-task` depende de memória do LLM

| Campo | Conteúdo |
|---|---|
| **Causa** | A única defesa contra troca de singleton é uma flag opcional que o modelo precisa lembrar de passar. |
| **Efeito** | Sinal gravado contra a task errada quando duas sessões se alternam (foi exatamente o incidente de 2026-06-12). |
| **Probabilidade** | média |
| **Impacto** | médio |
| **Detecção** | Divergência entre `task_id` do sinal e o da telemetria de eventos. |
| **Mitigação** | O3: a verificação passa a ser interna ao `harnessctl task finish`, por CAS de revision — não opcional. |
| **Rollback** | Wrapper legado preserva a flag. |
| **Owner** | reforma |
| **Status** | aberto |

### R7 — Especificidades de Windows/MSYS

| Campo | Conteúdo |
|---|---|
| **Causa** | `os.replace` entre volumes; granularidade de mtime no stale-lock; `cygpath`; CRLF; WAL do SQLite em NTFS; race documentada em `docs/router.md` (replace sobre arquivo aberto). |
| **Efeito** | Falhas intermitentes que se manifestam como corrupção ou lock preso. |
| **Probabilidade** | média |
| **Impacto** | alto |
| **Detecção** | Crash tests no Git Bash; teste explícito de WAL em NTFS antes de qualquer promoção. |
| **Mitigação** | P-1.c centraliza resolução de path em `paths.py`; O2 valida WAL/NTFS antes de promover a etapa. |
| **Rollback** | Flag `store_mode` volta ao legado. |
| **Owner** | reforma |
| **Status** | aberto |

### R8 — Reforma e uso normal compartilham o singleton até a O4

| Campo | Conteúdo |
|---|---|
| **Causa** | O state é global por máquina; a sessão de reforma e as sessões de trabalho disputam o mesmo arquivo. Evidência: task órfã de outra sessão encontrada no bootstrap. |
| **Efeito** | Contaminação cruzada; a reforma pode ser interrompida ou corromper trabalho não relacionado. |
| **Probabilidade** | alta |
| **Impacto** | médio |
| **Detecção** | `--expect-task`; inspeção de `state.json` no início de cada sessão de reforma. |
| **Mitigação** | Uma sessão de reforma por vez até `new_primary`; backup pré-onda; janela de reforma sem sessões concorrentes. |
| **Rollback** | Restauração do backup em `backups/<data>/`. |
| **Owner** | Leonardo (disciplina operacional) |
| **Status** | aceito com mitigação |

### R9 — Threshold fixo descarta os dados de calibração

| Campo | Conteúdo |
|---|---|
| **Causa** | `wf-verify-multimodel.js:118` filtra por `confidence >= 0.5` e não registra os vereditos — inclusive os descartados. |
| **Efeito** | A Fase 9 não tem histórico para calibrar; o campo `posterior` do §14 fica sem base empírica. |
| **Probabilidade** | certa |
| **Impacto** | médio |
| **Detecção** | Ausência de registros de finding na tabela `evidence`. |
| **Mitigação** | O1: logar confidence bruta de **todos** os findings, inclusive filtrados. O5: isotonic regression (PAV) + Brier + posterior Beta-Binomial sobre o histórico acumulado. |
| **Rollback** | Log é aditivo; o filtro atual permanece até haver dados. |
| **Owner** | reforma |
| **Status** | aberto |

---

## Seção P — Riscos mínimos exigidos pelo plano §4.3

| ID | Risco | Detecção | Mitigação | Rollback | Onda | Status |
|---|---|---|---|---|---|---|
| P01 | Perda de compatibilidade | Wrappers legados testados contra o mesmo corpus | Escada de flags; wrappers mantidos até a O6 | Flag para `legacy` | O2–O6 | aberto |
| P02 | Duplicação de tasks | `UNIQUE(scope_id) WHERE status='active'`; contagem cruzada legado × novo | CAS por revision; task_id com microssegundos | Flag | O2–O3 | aberto |
| P03 | Migração incompleta | Comparação de hash normalizado por escrita em dual-write | Dual-write com bloqueio de promoção em qualquer divergência | Voltar flag | O3 | aberto |
| P04 | Deadlock | Timeout de busy do SQLite; teste de stress com 16 workers | `BEGIN IMMEDIATE` só onde escreve; transações curtas; nenhuma chamada externa dentro de transação | Flag | O2–O4 | aberto |
| P05 | Starvation | Medição de lock wait na telemetria | Timeout + backoff; lease com heartbeat | Flag | O4 | aberto |
| P06 | WAL crescendo sem checkpoint | Tamanho do WAL na telemetria | Checkpoint controlado; backup consistente | Flag | O2 | aberto |
| P07 | Regressão de hook | `bench_hooks.py` com Mann-Whitney contra BASELINE | Gate de latência por onda | Rollback de onda | todas | aberto |
| P08 | Graphify stale | Manifesto com `repository_head` × HEAD atual | Nunca marcar fresh o que é stale; fallback explícito por nível | Desativar overlay | O5 | aberto |
| P09 | Política local maliciosa | Política global não sobrescrevível por `WORKFLOW.md` | Precedência fixa em código; teste dirigido | — | O5 | aberto |
| P10 | Falso positivo do shell guard | Corpus de comandos seguros parecidos com destrutivos | Etapa dual-decision antes de AST-primary; limite de FP documentado | Voltar a regex | O5 | aberto |
| P11 | Falso negativo do shell guard | Corpus adversarial obrigatório (multiline, `env`, `bash -c`, interpolação) | Zero-bypass como condição de promoção; `unknown` falha fechado | Voltar a regex | O5 | aberto |
| P12 | Calibração incorreta de reviewer | Brier score + reliability diagram por revisor×dimensão | Isotonic regression sobre histórico; nunca usar confidence autodeclarada como peso único | Threshold fixo atual | O5 | aberto |
| P13 | Early stopping prematuro | Replay offline antes de ativar | Só com concordância independente, evidência concreta, dimensão obrigatória presente e posterior acima do limiar versionado | Desativar early stopping | O5 | aberto |
| P14 | Cache incoerente | Chave de cache inclui head, manifest hash, query normalizada e configuração | Invalidação em qualquer componente alterado; teste metamórfico | Desativar cache | O5 | aberto |
| P15 | Vazamento entre projetos | Testes de escopo (10k combinações, mesmo nome de repo, symlink, Unicode, case do Windows) | `scope_id` = SHA256(session, cwd resolvido, worktree root, identidade do repo) | Flag | O2 | aberto |
| P16 | Inconsistência multi-máquina | `sync-machine.sh` idempotente; doctor | Store local por máquina; vault só recebe artefatos descritivos | — | O6 | aberto |

---

## Stop conditions ativas

Conforme plano §3.3, interromper a onda corrente e executar rollback parcial diante de: perda ou corrupção de estado · task de uma sessão visível em outra · conclusão sem evidência exigida · bypass de gate humano · comando Git destrutivo não bloqueado · lock antigo escrevendo após perder propriedade · regressão de testes existentes · divergência não explicada entre stores · aumento significativo de latência sem ganho demonstrado · alteração inesperada fora do worktree · Graphify indexando arquivo que deveria estar ignorado · erro de migração sem recuperação determinística.
