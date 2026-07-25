# Verification Report — P-1.b Testes Herméticos

**Date**: 2026-07-25
**Status**: **PASS**
**Spec**: [`p1b-testes-hermeticos-spec.md`](p1b-testes-hermeticos-spec.md) (grilhada round 1, 18 requisitos, 5 user stories, 0 clarificações abertas)
**Design**: [`p1b-testes-hermeticos-design.md`](p1b-testes-hermeticos-design.md) (validate-plan PASS after revision 1)
**Branch**: `self-reform/w0-chao-de-fabrica`
**Commits**: `7498eec` (Fase 1) · `d029082` (Fases 2–3) · `4f13df5` (Fases 4–5)

Fail-fast em `[NEEDS CLARIFICATION]`: **não aplicável** — as quatro foram resolvidas antes do grill-me.

---

## REQs Coverage

| REQ | Descrição | Evidência | Status |
|---|---|---|---|
| **F1** | Resolução por `HARNESS_DIR` com fallback | Padrão `: "${HARNESS_DIR:=...}"` em 9 arquivos: `harness-classify.sh:14`, `harness-session-start.sh:10`, `harness-reclassify.sh:8`, `harness-graphify-autosetup.sh`, `harness-router-warmup.sh`, `harness-skill-router.sh`, `init-state.sh`, `health-check.sh`, `state-lock.sh` (pré-existente) | PASS |
| **F2** | Atravessa bash → Python inline e subprocess | `export HARNESS_DIR` nos hooks + `harness-classify.sh:64` (`os.environ.get('HARNESS_DIR') or ...`) + 4 invocações `python -c` de `health-check.sh` por `sys.argv` | PASS |
| **F3** | Scripts com `--harness-dir` usam a env como default | `record_signal.py::default_harness_dir()`, `migrate_state.py`, `vault_sync.py::_default_harness_dir()`, `build_skills_index.py:23`, `skill_router.py:18`, `check_hermeticity.py::default_harness_dir()` | PASS |
| **F4** | Fixture autouse por classe, generalizando `test_state_lock.py` | `conftest.py::harness_dir` (class-scoped, `pytest.MonkeyPatch()` explícito) | PASS |
| **F5** | Assert com opt-out por `touches_real` | `conftest.py::pytest_runtest_setup` + marcas em `pytest_configure` | PASS |
| **F6** | `HarnessTestBase` sem backup/restore | `test_harness.py::HarnessTestBase` — backup/restore removido, substituído por isolamento | PASS |
| **F7** | Meta-teste do assert | `test_hermeticity_enforcement.py::TestSafetyAssert` (2 casos) + `test_check_hermeticity.py` (8 casos) | PASS |
| **F8** | `setUpClass` cria tmpdir no modo standalone | `test_harness.py::HarnessTestBase.setUpClass` (`_own_tmp`) | PASS |
| **F9** | `tmp_path_factory` / `mkdtemp` no standalone | `conftest.py::harness_dir` usa `tmp_path_factory`; `setUpClass` usa `tempfile.mkdtemp` | PASS |
| **F10** | `test_router_golden` marcado, `TEST_MATRIX.md` criado | `test_router_golden.py:28-29` (`integration`, `touches_real`); `docs/self-reform/claude/TEST_MATRIX.md` | PASS |
| **F11** | `health-check.sh` respeita e imprime o dir | `health-check.sh` — `Inspecionando: $HARNESS_DIR`, verificado em smoke test | PASS |
| **F12** | Override deixa rastro (mitiga R10) | `harness-classify.sh` grava em `debug-classify.log`; `health-check.sh` emite WARN. Teste: `TestOverrideLeavesTrace::test_classify_logs_override` | PASS |
| **F13** | Flag vence a env, com aviso no stderr | `record_signal.py::warn_if_flag_diverges_from_env()`. Teste: `test_flag_overrides_env_with_warning` | PASS |
| **NF1** | Compatibilidade: sem a env, comportamento idêntico | `TestDefaultFallback` (HOME sobrescrito, nunca toca o real) | PASS |
| **NF2** | Nenhuma alteração no conjunto protegido | `check_hermeticity.py --verify`: *"conjunto protegido intacto (nivel A, 3 arquivos)"* após 3 suítes | PASS |
| **NF3** | Tempo ≤ +10% do baseline | 324,30 s contra teto de 326,18 s. Ver Success Criteria | PASS |
| **NF4** | Portabilidade Windows/Git Bash | `TestEdgeCases::test_path_with_space`; ordem resolve→cygpath em `harness-reclassify.sh:8` (INV-3) | PASS |
| **NF5** | Proveniência ignora `HARNESS_DIR` | **Transferido para P-1.a** por GAP-2 do validate-plan. P-1.b entrega o comentário-âncora em `health-check.sh` | HANDOFF |

**17 PASS · 1 HANDOFF documentado · 0 órfãos.**

---

## ACs Coverage

| AC | Resumo | Teste | Status |
|---|---|---|---|
| US1-AC1 | Sem a env → `~/.claude/harness` | `TestDefaultFallback::test_session_start_uses_home_default` | PASS |
| US1-AC2 | Com a env → escreve no destino, real intacto | `TestOverrideRedirectsWrites` (3 casos) | PASS |
| US1-AC3 | Dir inexistente é criado, exit 0 | `TestEdgeCases::test_nonexistent_dir_is_created` | PASS |
| US1-AC4 | Path com espaço/acento no Git Bash | `TestEdgeCases::test_path_with_space` | PASS |
| US1-AC5 | `record_signal` segue a env; flag tem precedência | `TestPythonModuleLayer` (2 casos) | PASS |
| US2-AC1 | Fixture aponta para tmp, `HOME` intacto | `test_fixture_isolates_by_default` | PASS |
| US2-AC2 | Escape sem marca derruba a suíte | `TestSafetyAssert::test_unmarked_escape_fails_the_suite` | PASS |
| US2-AC3 | Conjunto protegido íntegro | `check_hermeticity.py --verify` (3 suítes) + 8 testes do script | PASS |
| US2-AC4 | Classes distintas não compartilham dir | `TestScopeA` / `TestScopeB` | PASS |
| US2-AC5 | Três execuções consecutivas idênticas | 319,09 / 314,31 / 339,49 s — mesmos 2 known-failures | PASS |
| US2-AC6 | `touches_real` libera o teste | `TestSafetyAssert::test_marked_test_is_allowed` | PASS |
| US3-AC1 | Sem backup/restore no `setUpClass` | Inspeção de `test_harness.py::HarnessTestBase` | PASS |
| US3-AC2 | Os 56 testes passam, mesmos nomes | `pytest tests/test_harness.py` → 56/56 em 231,70 s | PASS |
| US3-AC3 | `run_hook` propaga a env ao filho | `run_hook` usa `{**os.environ, ...}`; verificado por `TestOverrideRedirectsWrites` | PASS |
| US4-AC1 | Relatório confirma integridade | `check_hermeticity.py::verify` | PASS |
| US4-AC2 | Detecta arquivo alterado, nomeando-o | `test_verify_detects_modification` | PASS |
| US5-AC1 | Contrato documentado | `docs/HARNESS_DIR.md` | PASS |

**17/17 ACs com teste concreto.**

---

## User Stories Coverage

| Story | Prioridade | Implementada | Notas |
|---|---|---|---|
| US-1 — Resolução por env | P1 | SIM | 12 testes em `test_harness_dir_resolution.py` |
| US-2 — Fixture + assert | P1 | SIM | 9 testes em `test_hermeticity_enforcement.py` |
| US-3 — Migração do `test_harness.py` | P1 | SIM | 56/56 preservados |
| US-4 — Integridade como código | P2 | SIM | 8 testes em `test_check_hermeticity.py` |
| US-5 — Contrato documentado | P3 | SIM | `docs/HARNESS_DIR.md`; README fica com P-1.d por boundary |

**P1: 3/3 · P2: 1/1 · P3: 1/1.**

---

## Boundaries Coverage

| Regra | Tipo | Verificação | Status |
|---|---|---|---|
| Usar o padrão de `state-lock.sh:21` nos bash | ALWAYS | 9 arquivos com `: "${HARNESS_DIR:=...}"` | PASS |
| Resolver para absoluto | ALWAYS | `.expanduser().resolve()` nos módulos | PASS |
| Propagar a env a subprocessos | ALWAYS | `export` nos hooks; `_env()` nos testes | PASS |
| Suíte 3× antes de concluir | ALWAYS | 3 execuções registradas | PASS |
| Hash antes/depois | ALWAYS | `check_hermeticity.py` | PASS |
| Nunca alterar o default | NEVER | `TestDefaultFallback`; REQ-NF1 | PASS |
| Nunca remover `--harness-dir` | NEVER | Flag preservada em 4 scripts | PASS |
| Fixture nunca escreve no real | NEVER | `check_hermeticity --verify` → intacto | PASS |
| Nenhuma dependência nova | NEVER | `requirements.txt` inalterado | PASS |
| Não tocar documentação | NEVER | `docs/HARNESS_DIR.md` é arquivo novo; README intocado | PASS |
| Perguntar se estado não pode ser isolado | ASK | CLARIF-3 (`skills-index`) levada ao usuário | PASS |
| Parar se semântica de teste mudar | ASK | Nenhuma asserção alterada; só caminhos | PASS |

---

## Success Criteria

| Critério | Target | Medido | Status |
|---|---|---|---|
| ACs P1 em testes automatizados | 100% | 14/14 | PASS |
| Suíte verde 3× | 3 execuções | 3 (224 passed, 2 known-failures) | PASS |
| Conjunto protegido íntegro | hash idêntico | `intacto (nivel A, 3 arquivos)` | PASS |
| Meta-teste do assert | dispara e é suprimível | 2 casos passando | PASS |
| `baseline-suite.json` antes da 1ª alteração | existe | gravado em `b00e09e`, antes de `7498eec` | PASS |
| Tempo dentro de +10% | ≤ 326,18 s | 324,30 s | PASS |
| Zero findings críticos | 0 | não executado — ver Gaps | N/A |
| Clarificações resolvidas | 4/4 | 4/4 | PASS |
| `MIGRATION_LOG` atualizado | sim | 5 entradas | PASS |

### Nota sobre o REQ-NF3

Mann-Whitney U=0, z=−1,746, **p=0,0809** → a diferença é **indistinguível do ruído** a α=0,05. E a leitura absoluta engana: a suíte cresceu de **185 para 226 testes (+22%)**. Normalizado, o custo por teste **caiu de 1,603 s para 1,435 s (−10,5%)**.

Registro as duas leituras porque a segunda favorece o resultado.

---

## Gaps Encontrados

1. **`wf-verify-multimodel` não foi executado.** O success criterion "zero findings críticos em review multi-modelo" está marcado `N/A`, não `PASS`. O workflow existe (`scripts/workflows/wf-verify-multimodel.js`) e não foi rodado sobre este diff. **Ação**: executar antes da promoção da Onda 0, ou registrar como dívida explícita.

2. **Dois known-failures permanecem**, ambos pré-existentes e caracterizados em `baseline-suite.json`:
   - `test_router_golden` a 47% — causa raiz em aberto, task L1 separada;
   - `test_two_concurrent_acquires_serialize` — flaky de timing, limite do próprio teste.

3. **`test_15_counter_increments`**: era flaky por estado compartilhado (1 de 3 no baseline). Passou nas 4 execuções pós-implementação. Evidência favorável, mas 4 execuções não provam ausência de flakiness — **não declarar resolvido**; observar ao longo da Onda 0.

4. **REQ-NF5 em handoff** para P-1.a, com o comentário-âncora no `health-check.sh`. Rastreável, não órfão.

5. **Limitação conhecida do REQ-NF2**: o mecanismo cobre o conjunto protegido, não escritas arbitrárias em outros pontos do `$HOME` (ex.: `~/.claude/settings.json`). Registrado no ajuste GAP-4 do validate-plan.

---

## Próximos Passos

Status **PASS** com os gaps acima registrados. P-1.b está completa e não bloqueia a Onda 0.

Antes da promoção da onda: rodar `wf-verify-multimodel` sobre o diff (gap 1), executar P-1.c (lib compartilhada), P-1.d (higiene) e P-1.a (ship 3.3.0), e propagar **R10** ao `RISK_REGISTER.md`.
