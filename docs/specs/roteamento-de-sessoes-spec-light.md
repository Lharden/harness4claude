# Spec-light: roteamento de modelo e esforço para sessões criadas por chip

**Status:** desenho, não iniciado · 2026-09-30 · ramo `feat/roteamento-de-sessoes`
**Tamanho:** passa das 80 linhas da skill porque o pedido exige medida, falsificação e riscos. O núcleo (REQ, AC e boundaries) cabe em 50.

## Objetivo

Fazer a sessão criada por chip (`spawn_task`) rodar no modelo e no esforço que a tabela de roteamento manda para o trabalho dela, sem o usuário baixar à mão. A tabela já vale para os subagentes, porque `~/.claude/agents/{juiz-alto-risco,analise-complexa,execucao-mecanica,busca-leve}.md` declaram `model:`/`effort:` no cabeçalho. Para chip ela não vale: `spawn_task` não recebe modelo, e a filha não pode mudar a si mesma.

## Medida atual (`get_session`, 2026-09-30 14:20Z)

Há 26 sessões com `parentSessionId` criadas entre 28/09 e 30/09. O `get_session` mostra o valor **atual**, não o de nascimento, e o valor atual já inclui o ajuste manual do usuário.

| Criadas | Opus·medium | Sonnet·medium | Opus·high | Opus·xhigh | Opus·max |
|---|---|---|---|---|---|
| 28/09 (5) | – | 1 (`a71a9a10`) | – | 2 (`d4f0ddd0`, `299988f4`) | 2 (`14168da9`, `271f9b72`) |
| 29/09 (7) | 1 (`a91da3d9`) | – | 3 (`052092ad`, `60ca1fcc`, `d5e8ccee`) | 2 (`79bc94ee`, `58160f62`) | 1 (`517e9432`) |
| 30/09 (14) | 10 | 3 (`22823788`, `7b805436`, `96caa10d`) | – | 1 (`4811f0fd`) | – |

- Das 26, 11 continuam acima de medium. São as que ninguém ajustou à mão: 10 de 28 e 29/09 (todas menos `a91da3d9` e `a71a9a10`) e `4811f0fd`, arquivada em 30/09.
- Sonnet só aparece onde houve ajuste: 4 de 26. Haiku não aparece em nenhuma.
- A herança "filha nasce com o esforço da mãe" **não se confirma** pelos valores atuais. `d4f0ddd0` (xhigh) tem filhas em xhigh, high, max e medium. O valor de nascimento precisa de outra fonte (ver NC-4).
- Os chips vêm **sem** `startedBy`/`name`. `list_sessions` só põe esses campos na sessão "linked" criada por `start_session`. Consequência: para o app, a filha de chip provavelmente **não** é "sessão que a mãe iniciou". Nesse caso até baixar o esforço dela passa pela aprovação do app, e em modo auto o app pode decidir sozinho. É por isso que existe a SPIKE-1.

## Desenho: 2 hooks novos + 1 protocolo de mensagem

Descartes:
- **Skill** sozinha: depende de o modelo lembrar de invocá-la, e nenhum teste falsifica isso.
- **SessionStart**: não vê o prompt, então injetaria o protocolo em toda sessão, com ou sem chip.
- **Só texto no CLAUDE.md**: é o menor em código, mas não tem oráculo. É também o estado de hoje: a tabela já está lá e não pega o chip.

O menor mecanismo que funciona dispara **só** onde o defeito nasce, e cada gatilho é determinístico:

1. **Mãe: `PreToolUse` com matcher `mcp__ccd_session__spawn_task`** (`hooks/harness-roteamento.py --event PreToolUse`).
   - Nega o chip cujo `prompt` não termina em `Roteamento: <modelo> · <esforço> (<tipo>)`.
   - Nega também o chip cujo par não for o do cabeçalho de `~/.claude/agents/<tipo>.md`. A fonte única da tabela é esse cabeçalho, que já rege os subagentes.
   - Quando aceita, anota `{ts, tipo, modelo, esforco, prompt_sha}` em `roteamento-chips.jsonl` no balde do projeto da mãe.
2. **Filha: `UserPromptSubmit` com a linha no prompt** (mesmo script, `--event UserPromptSubmit`, com pré-filtro bash por `Roteamento:`/`[roteamento]`). O hook injeta, via `additionalContext` pelo `emit.py`, esta instrução:
   - chamar `get_session("self")`;
   - se modelo e esforço já batem, seguir normalmente;
   - se não batem, mandar **uma** mensagem à `parentSessionId`: `[roteamento] <modelo> · <esforço> (<tipo>) prompt_sha=<sha>`;
   - encerrar o turno com uma linha ao usuário e **sem começar o trabalho**, porque o turno em voo termina no modelo antigo.
3. **Mãe: `UserPromptSubmit` com `<cross-session-message ...>[roteamento]`**. O hook confere, no próprio jsonl, se existe um chip com aquele `prompt_sha` e aquele par.
   - Se existir, injeta a instrução: aplicar `set_session_model`/`set_session_effort` no `from` e responder **uma** vez `[roteamento-ok]`.
   - Se não existir, injeta a instrução de responder `[roteamento-recusado: <motivo>]` e não aplicar nada.
   - A decisão é da mãe, contra o que ela mesma escreveu, e não contra o que a filha pede. Assim o pedido não serve para contornar a recusa de a sessão mudar o próprio modelo e esforço.
4. **Filha ao receber `[roteamento-ok]`**: o turno novo já roda no nível novo, e ela começa o trabalho. Ao receber `recusado`, ou sem mãe disponível, age como diz a NC-2.

Desligamento: `HARNESS_ROTEAMENTO=0`. O desenho não toca `harness-classify.sh`, `classify_prompt.py`, `continuation_policy.py` nem `harness-transactional.py`. A entrada nova de `UserPromptSubmit` é separada da de classificação.

## Requisitos

- **REQ-1**: o `PreToolUse` de `spawn_task` nega (`permissionDecision: deny`, com o motivo e a tabela no texto) o prompt sem linha de roteamento válida. Aceita o prompt com linha válida e anota o chip no jsonl. `consumidor:` toda sessão que chama `spawn_task`.
- **REQ-2**: a linha só é válida quando `<tipo>` existe em `~/.claude/agents/` e o par é igual ao `model:`/`effort:` do cabeçalho do agente.
- **REQ-3**: com a linha no prompt, o `UserPromptSubmit` injeta a instrução da filha **uma vez por sessão**, deduplicada por `session_id`. Sem a linha, não emite nada. `consumidor:` a filha, no primeiro turno.
- **REQ-4**: com uma mensagem `[roteamento]`, o `UserPromptSubmit` injeta a instrução de aplicar quando o `prompt_sha` e o par batem com o jsonl, e a de recusar quando não batem. Nunca injeta instrução de aplicar um par fora da tabela. `consumidor:` a mãe.
- **REQ-5**: o protocolo tem no máximo 2 mensagens por filha: 1 pedido e 1 resposta. Nenhuma das duas respostas (`-ok`/`-recusado`) casa com o gatilho do REQ-4, e isso impede o laço.

## Acceptance Criteria (1 AC = 1 teste)

- **SPIKE-1** (manual, antes do TDD): Given uma mãe em opus·xhigh no modo auto, When ela cria um chip, o usuário clica e a mãe chama `set_session_effort(filha, "medium")`, Then fica registrado se o app aplicou sem perguntar, pediu aprovação ou recusou. Registra também se `PreToolUse` dispara para `mcp__ccd_session__spawn_task` e se `<cross-session-message>` dispara o `UserPromptSubmit` na mãe. **Se algum dos três falhar, o desenho volta para a mesa antes de escrever código.**
- **AC-1** (REQ-1): Given um `tool_input.prompt` sem linha de roteamento, When o hook roda em `PreToolUse`, Then a saída é `deny` e o jsonl não muda.
- **AC-2** (REQ-2): Given `Roteamento: opus · xhigh (analise-complexa)`, When o hook roda, Then a saída é `deny`, porque o cabeçalho diz `opus`/`medium`.
- **AC-3** (REQ-1/2): Given `Roteamento: sonnet · medium (execucao-mecanica)`, When o hook roda, Then a chamada é permitida e o jsonl ganha uma linha com `prompt_sha`.
- **AC-4** (REQ-3): Given um primeiro prompt com a linha, When o `UserPromptSubmit` roda duas vezes com o mesmo `session_id`, Then o `additionalContext` sai só na primeira vez e contém o par e o tipo.
- **AC-5** (REQ-4): Given um jsonl com o chip X, When chega `[roteamento] sonnet · medium (execucao-mecanica) prompt_sha=X`, Then a instrução é de aplicar. Com `prompt_sha=Y` desconhecido, Then a instrução é de recusar.
- **AC-6** (REQ-5): Given as mensagens `[roteamento-ok]` e `[roteamento-recusado: x]`, When o `UserPromptSubmit` roda com cada uma, Then não sai nada.

## Falsificação: as duas metades, medidas

- **Unidade.** AC-1 é o defeito de hoje: um chip sem roteamento passa. Com `HARNESS_ROTEAMENTO=0`, o AC-1 tem de ficar vermelho, ou seja, a chamada sai permitida. Com o gate ligado, ele fica verde. O teste roda nos dois modos.
- **Ponta a ponta** (manual, com o app real e registrada na verificação). A partir de uma mãe opus·xhigh, criar 2 chips: um `execucao-mecanica` e um `analise-complexa`. Depois do `[roteamento-ok]`, `get_session` deve dar `claude-sonnet-*`/`medium` e `claude-opus-*`/`medium`.
- **Controle** (manual). O mesmo par de chips com `HARNESS_ROTEAMENTO=0` deve nascer e **ficar** fora da tabela. Se ficar dentro da tabela mesmo assim, a medida não prova nada.

## Boundaries

- **ALWAYS:**
  - A mãe decide contra o próprio registro, e não contra o pedido da filha.
  - O aumento de esforço passa pela aprovação do app.
  - Todo texto sai pelo `emit.py`.
- **NEVER:**
  - Mudar `~/.claude/CLAUDE.md` ou `settings.json`. Se o usuário quiser, a spec propõe uma linha na seção de roteamento: "Chip: terminar o prompt de `spawn_task` com `Roteamento: <modelo> · <esforço> (<tipo>)`; o hook do harness4claude cobra".
  - Tocar o incumbente P1 ou `harness-transactional.py`.
  - Ter um orquestrador varrendo `list_sessions`.
  - Mais de 2 mensagens por filha.
- **ASK:**
  - Qualquer ampliação para `start_session` ou para sessões sem chip.
- **ENTREGA:** hook e testes unitários verdes, mais uma medida ponta a ponta com controle. A spec **não** promete economia em tokens, porque isso não foi medido.

## Custo e riscos

- **Custo:**
  - O primeiro turno da filha continua no nível herdado, mas é curto por construção.
  - Cada chip custa 1 turno da mãe, de uma chamada e uma resposta.
  - O pré-filtro bash evita um processo python em todo prompt.
- **A mãe terminou, está arquivada ou está ocupada:**
  - Arquivada: o envio falha e a filha avisa o usuário (NC-2).
  - Ocupada: a mensagem fica `queued` e a filha espera ociosa, com o aviso visível.
- **Mensagem retida:** uma mãe em modo de permissão diferente retém a mensagem para o usuário aprovar e pode deixá-la expirar. O Desktop não avisa quando isso acontece, então silêncio nunca vale como resposta.
- **Aumento de esforço** (filha `juiz-alto-risco` em opus·high que nasce em medium): o app pergunta ao usuário. Isso é intencional.
- **Laço:** fechado pelo REQ-5 (gatilhos disjuntos) e pela deduplicação do REQ-3.

## Decisões do usuário e resultado da SPIKE-1 (2026-09-30)

**Decisões** (chat da sessão de orquestração, 2026-09-30, ~11:30), com a spec aprovada para seguir:
- **NC-1 → um dos 4 tipos.** Não existe tipo `livre`. Conversa e planejamento contam como "complexo com risco
  baixo" (`analise-complexa`, opus · medium).
- **NC-2 → avisar e seguir.** Filha sem mãe que responda diz, na primeira resposta, qual roteamento a tabela pedia
  e segue no nível herdado.
- **NC-4 → sim.** O hook da filha grava `get_session("self")` do nascimento como telemetria.
- **NC-5 → manter o chip.** O clique do usuário continua sendo o consentimento; `start_session` fica fora.
- **NC-3** fica sem efeito no modo auto (ver a SPIKE-1). Nos outros modos, o custo é um clique de aprovação.

**SPIKE-1, medida em 2026-09-30 às ~11:45** (mãe = sessão de orquestração, opus · max, modo auto; chip com a linha
`Roteamento: haiku · low (busca-leve)`):
- **Nascimento:** a filha nasceu **opus · max**, o nível exato da mãe; a linha de roteamento do prompt não teve efeito.
  Outro caso no mesmo dia: "Harden harness_paths/_escopo", filha de uma sessão que estava em sonnet · medium,
  nasceu sonnet · medium. **A herança do nível da mãe no instante do clique fica confirmada.**
- **A mãe roteia a filha:** `set_session_effort(filha, low)` e `set_session_model(filha, claude-haiku-4-5-20251001)`
  foram aplicados na hora, sem aprovação, e o `get_session` confirmou haiku · low. Isso vale para o modo auto da mãe.
- **Mensagem entre sessões dispara o `UserPromptSubmit` de quem recebe:** observado na sessão de orquestração a cada
  mensagem de outra sessão.
- **Falta medir:** se o `PreToolUse` dispara para `mcp__ccd_session__spawn_task`. Vai para o primeiro teste do TDD
  (o hook registrado e um chip real).

## [NEEDS CLARIFICATION]

- **NC-1:** chip de trabalho que não cabe em nenhum dos 4 tipos (conversa, planejamento com o usuário). Opções: um tipo `livre`, que o gate aceita sem mexer em nada; ou obrigar a escolher um dos 4.
- **NC-2:** filha sem mãe (arquivada ou sem resposta). Opções: (a) avisar e **parar** até o usuário ajustar no menu e dizer "segue"; (b) avisar e seguir no nível herdado.
- **NC-3:** se a SPIKE-1 mostrar que até baixar o esforço pede aprovação (chip não é "iniciado pela mãe"), o ganho vira trocar o ajuste manual por um clique de aprovação. Isso ainda vale a pena, ou só se usar `start_session` (NC-5)?
- **NC-4:** falta medir o valor de **nascimento**. Os JSONL em `~/.claude/projects/` registram o `model` por mensagem, mas talvez não o esforço. Opção: o hook do REQ-3 grava `get_session("self")` no nascimento, como telemetria.
- **NC-5:** `start_session`, que cria a sessão "linked" e devolve o id na hora, dispensaria a filha pedir. Só que ele cria a sessão sem o clique do usuário, que hoje é o consentimento do chip. Fica fora até o usuário decidir.

## Tamanho estimado da implementação

- **5 arquivos:**
  - `scripts/roteamento.py` (parse, tabela lida dos agentes, jsonl, sha)
  - `hooks/harness-roteamento.py`
  - `hooks/harness-roteamento.sh` (pré-filtro)
  - `hooks/hooks.json` (+2 entradas)
  - `tests/test_roteamento.py`
- **Cerca de 10 testes:** 6 ACs, mais o controle `=0`, mais 3 de parse e leitura do cabeçalho dos agentes.
- **Uma spike manual** antes de tudo.
