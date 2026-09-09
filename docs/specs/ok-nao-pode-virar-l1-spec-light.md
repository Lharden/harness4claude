# Um "ok" não pode virar L1-feature

**Status:** registrado, não iniciado · aberto em 2026-09-09
**Origem:** o Stop gate escalou e o bloqueador era uma task nascida de uma
mensagem de uma palavra.

## O problema

A promoção L0→L1 por contagem de arquivos lê o contador do **projeto**, não o
**prompt**. Numa sessão produtiva, qualquer mensagem curta — `ok`, `pode seguir`,
`sim` — herda um pipeline de feature e passa a exigir spec, TDD e verificação
para o Stop hook parar de bloquear.

O prompt não trouxe trabalho nenhum. Quem trouxe foi a sessão inteira, e o
crédito foi para a última linha digitada.

## Evidência

```
t-20260909-212232947129 | L1-feature | active | phase 0 = write-spec-light
pipeline: ["write-spec-light", "tdd", "verify-against-spec"]
verified: 0 | nascida 21:22 | nunca tocada
```

O prompt que a criou foi **`ok`**.

O bloco injetado pelo hook naquele turno dizia `classification: L0-question`.
O banco gravou `L1-feature`. As duas coisas vieram do mesmo hook, no mesmo
turno, e discordam.

Consequência medida: essa task ficou como a única não-terminal do bucket, e o
gate de `_handle_stop` (`hooks/harness-transactional.py:535`) bloqueia enquanto
houver task `active` com pipeline não-vazio e `verified` falso. Três turnos
seguidos foram bloqueados; no terceiro veio o gate de `escalation`. O trabalho
real da sessão (`t-20260909-195611274295`) estava `verified: true` o tempo todo.

## Dois defeitos, não um

**D-A — a promoção usa um sinal que não é do prompt.** O contador
(`.session-files-count`) mede o que a SESSÃO fez, e é usado para decidir o nível
de um TURNO. Ele existe para pegar trabalho que cresceu sem ser classificado —
mas não distingue "este prompt pediu muita coisa" de "esta sessão já fez muita
coisa". Numa sessão longa, todo prompt curto é promovido.

**D-B — o expirador não enxerga a task que ele deveria expirar.**
`scripts/expire_stale_pipeline.py` trabalha a partir do `state.json`, e a
projeção apontava para outra task (`t-20260909-195611274295`, já `superseded`).
Rodá-lo com `--ttl-hours 0 --apply` não fez nada, sem erro e sem saída. O que
resolveu foi `HarnessDatabase.expire_stale_task(scope, ...)` — a autoridade é o
banco, e a ferramenta de linha de comando lê a projeção.

É a mesma família do incidente de 2026-09-09 no `harness-workflow/SKILL.md`
(resolver o bucket do projeto em vez do da sessão): **ferramenta que lê a
projeção enquanto o banco manda.**

## O que a resposta precisa ter

- **Um sinal por prompt, não por sessão.** Se a promoção por volume ficar, ela
  precisa de um segundo predicado que olhe o prompt — comprimento, verbo,
  presença de pedido. Um `ok` não passa em nenhum deles.
- **Concordância entre o que o hook anuncia e o que ele grava.** Hoje o bloco
  injetado diz `L0-question` e o banco recebe `L1-feature`. Qualquer um dos dois
  pode estar certo; discordarem em silêncio é o que impede de descobrir qual.
- **Task sem nenhuma transição não deveria bloquear o Stop.** Uma task em
  `phase_index 0`, sem artefato e sem evidência, que nunca teve um `transition`,
  é indistinguível de uma que nunca começou. Bloquear por ela é cobrar
  verificação de trabalho que não existe.
- **O expirador precisa trabalhar sobre o banco**, e reportar quando não achou
  nada para expirar em vez de sair calado.

## Fora de escopo

A promoção por volume em si não é o defeito — ela pega trabalho real que foi
subclassificado. O defeito é ela decidir sozinha, com um sinal que não veio do
prompt.
