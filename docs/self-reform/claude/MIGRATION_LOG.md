---
title: Diário de Bordo — Autorreforma do Harness4Claude
document_type: migration-log
status: active
created: 2026-07-24
---

# Diário de Bordo

Registro cronológico de cada ação estrutural: bootstrap, promoção de onda, mudança de flag, rollback, incidente. Append-only — entradas não são reescritas, apenas corrigidas por entrada posterior.

---

## 2026-07-24 — Bootstrap da reforma (Onda 0)

**Commit-base:** `a56ee80` (merge: skill-router P1 — v3.3.0-beta.1)
**Tag de recuperação:** `pre-reform-base` → `a56ee80`
**Worktree:** `worktrees/harness4claude-self-reform` na branch `self-reform/main`
**Working tree do repo principal antes do bootstrap:** limpo, exceto `PLANO_AUTOREFORMA_HARNESS4CLAUDE.md` untracked (agora versionado)

### Backup de estado

Destino: `docs/self-reform/claude/backups/2026-07-24/` — **gitignored por design** (`backups/` já constava no `.gitignore`). Contém estado de sessão e não deve entrar no histórico do repositório.

| Arquivo | Bytes | SHA-256 |
|---|---|---|
| `state.json` | 863 | `8F6F89F5E5689E619B0E907EE5C47BBF69C29E11816B2BD14857B65678D129BF` |
| `signals.json` | 388 | `C71852CB90A9432111E8FE859831FD7EC31B40FBD6C2FF8BE7D2E528B05E2093` |
| `trace-current.md` | 2546 | `DFD91C55A1C32391948462B830C3602BF7EB27CB4D8BEDAB15BA785E81BD672E` |

`traces/` não existia no momento do backup.

### Observações de campo registradas no bootstrap

1. **Task órfã no state.** `state.json` continha `t-20260724-170615852523`, `L2-feature`, `status: active`, iniciada às 17:06 por sessão não relacionada (prompt sobre preparação de apresentação), com `current_step: null` e `artifacts_so_far: []`. Evidência direta dos riscos **R5** (fechamento de task não confiável) e **R8** (singleton compartilhado entre sessões). Não foi alterada — pertence a outra sessão.
2. **`signals.json` vazio.** `tasks: []` e agregados zerados, com mtime de 2026-06-17. Confirma que a telemetria de tasks nunca operou (**R5**).
3. **Estado de deployment confirmado.** `installed_plugins.json` aponta `installPath` para o cache `3.2.0`, `gitCommitSha: 24c1812`, `installedAt: 2026-06-17T19:38:43Z`. O runtime não executa o código de main (**R3**).

### Artefatos criados

- `docs/self-reform/claude/STRATEGY.md`
- `docs/self-reform/claude/INVENTORY.md` (satisfaz plano §4.1)
- `docs/self-reform/claude/RISK_REGISTER.md` (satisfaz plano §4.3; R1–R9 + P01–P16)
- `docs/self-reform/claude/ADR/ADR-000-objeto-de-medicao.md`
- `docs/self-reform/claude/ADR/ADR-001-store-fundido.md`
- `docs/self-reform/claude/ADR/ADR-002-substitutos-densos.md`
- `docs/self-reform/claude/MIGRATION_LOG.md` (este arquivo)
- `PLANO_AUTOREFORMA_HARNESS4CLAUDE.md` versionado (estava untracked)

### Estado do runtime

**Inalterado.** Nenhum ship executado. O plugin em cache permanece em `3.2.0 @ 24c1812`. Conforme ADR-000, a promoção de P-1.a ocorre apenas na fronteira da Onda 0, em commit único, com gate humano.

### Auditoria de meio de onda (2026-07-24, durante a medição do baseline)

Verificação por comando das afirmações registradas nos documentos, mais os primeiros dados reais da suíte.

**Confirmado:** 185 testes coletados · 89 arquivos versionados em `a56ee80` · divergência `plugin.json` 3.3.0-beta.1 × `marketplace.json` 3.2.0 · 7 ocorrências de `expanduser` compondo caminho de estado em `hooks/` e `scripts/`, consistentes com o INV-4 do design.

**Corrigido:** a contagem de "10 caminhos hardcoded" estava errada — são **18 ocorrências em 9 arquivos**, e três delas **não são defeito** (`sync-machine.sh:88` é o script que clona para o destino; dois documentos são registro histórico). O INVENTORY foi corrigido com a tabela de natureza por arquivo, para que P-1.a não "conserte" o que está certo.

#### ACHADO-1 — O baseline não é verde: `test_router_golden` falha a 47%

Primeira execução do baseline: **1 failed, 184 passed em 308,91 s**.

```
FAILED tests/test_router_golden.py::test_golden_top3_hit_rate
AssertionError: top-3 hit rate 47% < 80%   (7/15)
```

`docs/router.md` documenta **93,3% (14/15)** medido em 2026-07-23. O comportamento atual é metade disso.

**Descartado por verificação direta:** Ollama responde e tem o modelo (`nomic-embed-text-v2-moe:latest` entre 8 modelos) · índice presente, 276 skills, dim 768, sem marker `.stale` · `embeddings.f16.bin` tem 423.936 bytes = 276 × 768 × 2, exatamente o esperado para embeddings reais.

**Padrão observado:** os 8 MISS retornam lista vazia — o router não devolve *nada*, em vez de devolver a skill errada. Os 7 OK são casos que a Camada A resolve.

**Hipóteses abertas, em ordem de plausibilidade:**

1. **Contaminação cruzada dentro da própria suíte.** `skill_router.passes_guards()` lê o `state.json` **real** (hardcoded em `skill_router.py:22`) e retorna `False` quando `status ∈ {active, awaiting_gate}` e `pipeline` não está vazio. `test_harness.py` roda antes de `test_router_golden.py` na ordem alfabética e escreve tasks ativas nesse mesmo arquivo. Se o estado ficar sujo, o router é suprimido e devolve `[]` — exatamente o padrão observado.
2. **Composição do índice mudou.** O índice foi reconstruído às 15:59 de 2026-07-24, depois da medição dos 93,3%. Um conjunto diferente de plugins habilitados altera o ranking e pode derrubar candidatos abaixo de `MIN_COS=0.45` ou da margem sobre a mediana.
3. **Efeito da mudança de política do commit `1b42240`** (Camada B só dispara quando a Camada A não acha nada) sobre prompts que antes eram resolvidos por B.

**Não determinado.** A causa raiz exige investigação dedicada com o state limpo e o índice controlado — o que não pode ser feito enquanto o baseline ocupa a suíte. Fica registrado como **task L1 separada**, fora do escopo de P-1.b.

**Consequências imediatas:**

- O REQ-F10 (marcar `test_router_golden` como `integration` + `touches_real`) ganha justificativa empírica: é o único teste da suíte que depende de ambiente externo e de estado compartilhado, e é o único que falha.
- O gate "suíte verde 3×" de P-1.b passa a significar **184 passed, 1 known-fail documentado** — e não pode ser declarado verde sem essa nota, sob pena de mascarar uma regressão real.
- `docs/router.md` está desatualizado em relação ao comportamento observável. Corrigir faz parte de P-1.d.
- **Não afirmar que P-1.b corrige isso.** Se a hipótese 1 estiver certa, o hermetismo elimina a causa como efeito colateral — mas isso é previsão, não resultado, e só a medição pós-implementação decide.

#### ACHADO-1b — `test_15_counter_increments` é flaky (medido, não hipotético)

Com as três execuções concluídas, os resultados foram:

| Run | Tempo (pytest) | Resultado |
|---|---|---|
| 1 | 308,91 s | 1 failed, 184 passed |
| 2 | 288,90 s | 1 failed, 184 passed |
| 3 | 291,76 s | **2 failed**, 183 passed |

A segunda falha do run 3 é `tests/test_harness.py::TestReclassify::test_15_counter_increments` — **falhou em 1 de 3 execuções idênticas**.

Isto é flakiness real, medida, no exato mecanismo que P-1.b existe para corrigir: `.session-files-count` e `state.json` são compartilhados entre classes de teste dentro do diretório real. Deixa de ser argumento de projeto e passa a ser dado.

Também **enfraquece parcialmente a hipótese 1 do ACHADO-1**: o hit rate de 47% foi *idêntico* nas três execuções, o que indica causa determinística. Contaminação por ordem de execução seria consistente (a ordem é fixa), então a hipótese continua viva — mas a estabilidade do número aponta mais para composição do índice ou efeito da mudança de política do commit `1b42240` do que para uma race.

**Consequência para o gate de P-1.b:** se o hermetismo eliminar a flakiness do `test_15`, isso é resultado verificável do trabalho. Se persistir, a causa é outra e vira task própria. Nos dois casos, mensurável — que é o ponto.

Baseline gravado em `waves/w0-chao-de-fabrica/baseline-suite.json`: média 296,52 s, desvio amostral 10,82 s, CV 3,6%, teto de +10% em 326,17 s. A comparação pós-implementação usará Mann-Whitney U (D5), porque com CV de 3,6% diferenças pequenas são indistinguíveis do ruído.

#### ACHADO-2 — Evidência ao vivo do risco R2

Durante a execução do baseline, uma inspeção do `state.json` **de produção** retornou:

```
task: t-test-snap | status: active
```

`t-test-snap` é um artefato de teste. O estado real do usuário esteve, naquele instante, ocupado por dado sintético de uma suíte em execução. É a demonstração direta — não hipotética — do risco R2, colhida no próprio ato de medir.

**Tempo real da suíte: 308,91 s**, não os ~231 s que o MOC do vault registrava. O `baseline-suite.json` usará o valor medido.

---

### Pendências abertas ao fim desta entrada

- P-1.a — ship 3.3.0 e proveniência (não iniciado)
- P-1.b — testes herméticos (próxima task L2)
- P-1.c — lib compartilhada (depende de P-1.b)
- P-1.d — higiene de docs (task L1 paralela)
- BASELINE.md, TEST_MATRIX.md, SHIP.md (a produzir na Onda 0)
