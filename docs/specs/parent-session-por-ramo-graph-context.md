# graph-context — subsistema Branch Keeper

**Task:** `t-20260909-195611274295` · fase `graph-context` · 2026-09-09

## Estado do grafo

`graphify-out/` existe neste repo, mas `graphify query "quem consome
branches.json e parent_session"` devolveu 61 nós, **todos** de
`.claude-plugin/marketplace.json`, `schemas/spec.schema.json` e
`schemas/state.schema.json` — nenhum módulo Python. O grafo está desatualizado
para `scripts/` e não responde a esta pergunta.

Degradação: o mapa abaixo veio de varredura direta do repositório. Registrar a
falha importa mais que contorná-la em silêncio — o grafo precisa de
`graphify update .` antes da próxima fase que dependa dele.

## Mapa estrutural

### Escrita

| Símbolo | Arquivo | Escreve |
|---|---|---|
| `add` | `scripts/branch_state.py:411` | registro do ramo + `parent_session` de arquivo (`:458`) |
| `set_status` | `scripts/branch_state.py:467` | status, `conclusion`, `seed_path`, `launcher_path` |
| `attach_files` | `scripts/branch_state.py:544` | `seed_path`, `launcher_path` |
| `decide` | `scripts/branch_state.py:579` | remove o ramo (`discard`) |
| `_marcar_entregues` | `scripts/branch_state.py:758` | `conclusion_delivered` |
| `save` | `scripts/branch_state.py:374` | escrita atômica (`.tmp` + `os.replace`) |

### Leitura

| Símbolo | Arquivo | Lê | Caminho quente? |
|---|---|---|---|
| `load` | `scripts/branch_state.py:356` | o arquivo inteiro; **nunca levanta** | sim |
| `parked_block` | `scripts/branch_state.py:759` | `parent_session` de arquivo (`:778`) | **sim — todo `UserPromptSubmit`** |
| `_transaction_context` | `scripts/branch_state.py:92` | `parent_session` como candidato nº 2 (`:141`) | não |
| `_sweep_sessions` | `scripts/branch_state.py:206` | buckets `sessions/*/harness.db` | só em erro |
| `branch_links` | `scripts/build_sessions_index.py:124` | `parent_session` de arquivo (`:157`) | não (build do índice) |
| `already_seen` | `scripts/branch_state.py:653` | `topic` de cada ramo | sim (dedupe do sensor) |
| `can_open` | `scripts/branch_state.py:628` | status dos ramos | sim |

### Fronteiras

- **Registro JSON** — `~/.claude/harness/projects/<slug>/branches.json`. Escopo
  de PROJETO, nunca de sessão (travado em
  `tests/test_branch_state.py::TestRegistroEPorProjeto`).
- **Registro transacional** — tabela `branches` do `harness.db`, no bucket da
  SESSÃO (`scripts/transactional_state.py:161`). Colunas relevantes:
  `branch_id`, `task_id`, `scope_id`, `slug`, `status`, `conclusion`.
  `scope_id` é o caminho do bucket — é o único lugar onde o dono real já está
  gravado, mas ele vive *dentro* do banco que se quer encontrar.
- **Contrato** — `contract/schemas/branch-record.schema.json` modela
  `parent_session_id` e `child_session_id` por ramo. Vocabulário canônico.
- **Emissão** — `hooks/emit.py:176` acumula `branch` + `parked` e emite uma vez
  por turno, via `additionalContext`.

## Invariantes que a mudança não pode quebrar

1. `load()` e `parked_block()` nunca levantam — hook quebrado é pior que ramo
   não encontrado (docstrings em `:356` e `:758`).
2. `branches.json` fica no bucket do projeto, sem `session_id` no caminho.
3. `pending → open → closed | recalled`; `closed` e `recalled` são terminais.
4. Recusar parkeia; só `discard()` apaga.
5. Conclusão é entregue **uma vez**, a quem o registro considera a mãe.
6. O bloco de parking tem teto (`MAX_PARKED_LINES = 5`, `TOPIC_TRUNC = 80`).
