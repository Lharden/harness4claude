---
title: Matriz de Testes — Harness4Claude
document_type: test-matrix
status: active
created: 2026-07-25
scope: versão mínima criada em P-1.b (REQ-F10); expandida ao longo da Onda 0
---

# Matriz de Testes

Versão mínima, criada pela task P-1.b. Cobre as marcas, o gate hermético e os testes com pré-condição externa. Será expandida ao longo da Onda 0 com a cobertura por invariante.

## Marcas

| Marca | Significado | Efeito |
|---|---|---|
| `touches_real` | O teste usa `~/.claude/harness` **real** de propósito | A fixture `harness_dir` não isola, e o assert de segurança do `conftest.py` não dispara. Exige justificativa escrita no próprio teste. |
| `integration` | Requer ambiente externo — Ollama, índice real de skills | Fora do gate hermético; pode ser deselecionado com `-m "not integration"` |

Ambas registradas em `tests/conftest.py::pytest_configure`. Marca não registrada gera `PytestUnknownMarkWarning`, e há teste que falha se isso acontecer.

## Gate hermético

```bash
python -m pytest tests/ -q
```

O gate exige que o **conjunto protegido** em `~/.claude/harness/` permaneça com hashes idênticos antes e depois. Ele tem dois níveis, porque nem todo arquivo de estado é estável sob uma sessão do Claude Code ativa.

### Nível A — verificado sempre

`state.json` · `signals.json` · `trace-current.md` · `traces/**`

São escritos por hooks disparados por **prompt** (`UserPromptSubmit`, `PreCompact`) ou pelo fechamento de task. Durante uma execução de suíte, ninguém os toca.

### Nível B — verificado apenas com a sessão quiescente

`.session-files-count`

Escrito pelo hook `PostToolUse` a cada `Edit`/`Write` do usuário. Se houver uma sessão do Claude Code trabalhando enquanto a suíte roda — que é o caso normal durante a reforma — este arquivo muda por atividade legítima, sem relação com os testes.

**Como distinguir** vazamento de teste de atividade de sessão: inspecionar o campo `files`. Caminhos de trabalho real indicam a sessão; caminhos sob `tmp`/`_synthetic_` indicariam vazamento.

### Fora do conjunto

`router/` · `skills-index/` · `graphify-autosetup/` — cache derivado e log, escritos pela sessão que executa a própria suíte. Incluí-los tornaria o gate um falso positivo garantido.

## Testes com pré-condição externa

| Teste | Marcas | Pré-condição | Comportamento sem ela |
|---|---|---|---|
| `test_router_golden.py::test_golden_top3_hit_rate` | `integration`, `touches_real` | Índice real com 276 skills **e** Ollama servindo `nomic-embed-text-v2-moe` | `skipif` — pula com motivo declarado |

**Nota sobre `touches_real` neste caso:** `skill_router.IDX_DIR` é resolvido no *import* do módulo, antes de qualquer fixture. Isolar `HARNESS_DIR` não teria efeito sobre ele e apenas mascararia a dependência real. A marca torna a dependência explícita em vez de acidental.

## Known-failures

Registrados em `waves/w0-chao-de-fabrica/baseline-suite.json`. Qualquer afirmação de "suíte verde" deve ser lida contra esta lista.

| Teste | Natureza | Status |
|---|---|---|
| `test_router_golden::test_golden_top3_hit_rate` | Determinístico — 47% contra os 93,3% documentados em `docs/router.md` | Causa raiz em aberto; task L1 separada |
| `test_state_lock::TestConcurrency::test_two_concurrent_acquires_serialize` | Flaky de timing — `assert elapsed <= 3` recebeu `4` sob carga da suíte | Limite apertado demais no próprio teste; candidato a ajuste |
| `test_harness::TestReclassify::test_15_counter_increments` | Era flaky por estado compartilhado (1 de 3 execuções no baseline) | **Deve ser resolvido por P-1.b** — verificar após o hermetismo |

## Testes de hermetismo

| Arquivo | N | Cobre |
|---|---|---|
| `test_harness_dir_resolution.py` | 12 | US-1 — resolução nas três camadas, edge cases, precedência flag/env, INV-4 |
| `test_hermeticity_enforcement.py` | 9 | US-2 — isolamento por classe, assert de segurança, opt-out por marca, registro das marcas |

O meta-teste do assert roda `pytest` em subprocess sobre arquivos sintéticos criados **dentro de `tests/`** — necessário porque o `conftest.py` é descoberto pelo caminho do arquivo de teste, não pelo cwd. Um sintético em `/tmp` não enxergaria o conftest sob teste, e o meta-teste passaria por engano.

O vazamento é simulado pelo **ambiente externo** (`HARNESS_DIR=<real>` no subprocess), porque `pytest_runtest_setup` roda antes de qualquer fixture — nenhum `monkeypatch` dentro do teste consegue enganá-lo.
