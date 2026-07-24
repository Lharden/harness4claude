---
adr: 001
title: Fundir as Fases 2 e 3 — o store novo nasce SQLite com scope_id e fencing
status: accepted
date: 2026-07-24
deciders: Leonardo, Harness4Claude
supersedes: PLANO_AUTOREFORMA §7 e §8 como fases sequenciais
---

# ADR-001 — Store fundido: SQLite + scope_id + fencing de uma vez

## Contexto

O plano separa duas fases:

- **Fase 2 (§7)** — identidade por escopo: calcular `scope_id`, migrar o `state.json` singleton para states por escopo, com escada shadow → dual-read → dual-write → new-primary → legacy-read-only.
- **Fase 3 (§8)** — substituir read-modify-write de arquivos por transações SQLite com WAL e fencing token.

O inventário mostra que **nenhuma das duas existe em qualquer grau**: `grep -r 'scope_id|worktree'` retorna zero ocorrências, e o store é `state.json` global com mutex por `mkdir`.

Executar as fases em sequência significaria: (1) construir uma camada de states por escopo em arquivos JSON, com sua própria escada de migração de cinco etapas e seus próprios testes; (2) jogá-la fora ao migrar para SQLite, com uma segunda escada de cinco etapas e uma segunda bateria de testes.

## Decisão

**O store novo nasce SQLite + `scope_id` + fencing.** A escada A–E da Fase 2 aplica-se diretamente a ele.

Concretamente, `harness_lib/store.py` implementa desde o primeiro commit:

- schema do plano §8 — `tasks`, `events`, `artifacts`, `evidence`, `leases`;
- `scope_id` do §7 como coluna obrigatória: `SHA256(session_id ‖ resolved_cwd ‖ git_worktree_root ‖ repository_identity)`;
- `PRAGMA journal_mode=WAL`, busy timeout explícito, `BEGIN IMMEDIATE` apenas onde há escrita, transações curtas, nenhuma chamada externa dentro de transação;
- fencing por `UPDATE ... WHERE revision = :expected AND owner_epoch = :epoch`; zero linhas afetadas significa conflito — reler e revalidar, nunca sobrescrever.

**A escada vira feature flag em runtime**, não re-ship por etapa: `~/.claude/harness/flags.json` com `store_mode ∈ {shadow, dual_read, dual_write, new_primary, legacy_ro}`. Promoção de etapa é mudança de flag com gate humano; rollback é reverter o flag.

## Alternativa considerada

**Manter as fases separadas, fiéis ao plano.** Rejeitada: duas migrações onde uma basta, com a segunda descartando o produto da primeira. O plano proíbe big-bang (§3.2) — mas duas migrações consecutivas do mesmo dado não são mais seguras que uma, apenas mais longas. A segurança vem da escada A–E e dos gates, que são integralmente preservados.

## Consequências

**Positivas.** Uma migração em vez de duas — estimativa de uma onda economizada. A escada por flag satisfaz o requisito de feature flag da Fase 13 desde a Onda 2, e dá rollback instantâneo sem re-ship. O canário-shadow roda em produção instrumentada: os hooks reais escrevem no legado (autoridade) e espelham no SQLite, e a divergência vai para a telemetria — não é preciso construir ambiente sintético.

**Negativas.** A Onda 2 é a mais pesada do programa (5–7 sessões) porque concentra schema, scope, WAL, fencing e crash tests. Mitigado por: os crash tests dependem do ponto de injeção `HARNESS_CRASH_POINT` já entregue em P-1.c, e o WAL em NTFS é validado antes de qualquer promoção (risco R7).

**Preservado do plano.** Todos os gates de §7 (nenhuma interferência entre escopos, migração reversível, states legados importados corretamente, nenhuma task duplicada) e de §8 (atomicidade demonstrada, nenhum lost update, nenhum stale owner escrevendo, recuperação após crash, backup restaurável, dual-write consistente, p95 dentro do orçamento).

## Verificação

Gate da Onda 2: N tasks reais em shadow sem divergência inexplicada; atomicidade sob kill nos nove pontos do §8; stale owner nunca escreve; WAL validado em NTFS; legado intocado como autoridade. Gate da Onda 3: zero divergência de hash normalizado em dual-write.
