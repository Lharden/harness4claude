# Spec: Camada J do Branch Keeper em modo sombra

**Status**: Draft (aguarda grill-me e approve-spec)
**Created**: 2026-10-10
**Branch**: harness4claude `spec/branch-camada-j`; master-harness, ramo a abrir a partir do `feat/avaliacao-camada-j`
**Author**: AI-generated, reviewed by Leonardo
**Task**: `t-20261010-164331842535` (L2-feature)
**Antecessora**: [`branch-camada-j-spec-light.md`](branch-camada-j-spec-light.md), com veredito `avança`
([`branch-camada-j-verification.md`](branch-camada-j-verification.md); relatório em
`master-harness/docs/avaliacao/camada-j-resultado.md`, `3ef033c`). A antecessora reservou para esta spec o nome
`…-ligacao-spec-light.md`; ela saiu como spec completa (L2) e o nome mudou junto.

---

## Executive Summary

A avaliação offline mostrou que o Jev separa *abertura de sessão nova* de *continuação* melhor que a camada A:
9 acertos nos 25 primeiros contra 2. Ela não mostrou o que importa em produção, que é achar ramo **no meio** de uma
sessão. Esta spec mede isso sem tocar no que o usuário vê. Enquanto a flag estiver ligada, o hook guarda localmente a
âncora e o prompt de cada turno humano, sem rede e sem latência. Fechada a janela, um lote no `mh` pontua tudo de uma
vez pelo mesmo caminho medido. O usuário rotula só os prompts que o J sinalizou, e um critério fixado aqui decide se
vale escrever a spec que faz o J **oferecer** ramo. Nada é oferecido, injetado ou mostrado durante a janela.

## Context

**Hoje.** `scripts/branch_sensor.py::evaluate` roda em `UserPromptSubmit` e em `Stop`. A camada A (regex) é o portão
de `ramo` (`verdict()` exige `hit_a`), e a camada B está desligada (`HARNESS_BRANCH_LAYER_B=0`). A âncora da sessão
fica em `branch-anchor.json`, no `state_dir` do projeto (`branch_sensor.load_anchor`). O texto do turno vem de
`text_from_payload`. A camada A tem precisão de 0,080 nos 703 pares; na produção foram 211 ofertas degradadas.

**O que a avaliação deixou pronto** (master-harness, `feat/avaliacao-camada-j`):
- o molde `mh/avaliacao/moldes/camada_j_par.json` (pergunta fixada, estado `{ancora, turno}`);
- a `PonteJev` com redação, lista de bloqueio, truncagem, guarda da chave e log auditado na porta `C4`;
- o livro de gasto (`mh/hints/livro.py`) e a estatística do kit.

**Restrições herdadas:**
- **T2:** `mh/s1/` está congelado.
- **L-43:** dado real só sai com redação local antes do envio.
- **L-44:** envelope de US$ 5 por mês.
- **L-45:** `jev-1.13.0` fixo, chave só no arquivo dela.
- **L-46:** o gateway nasce com o 2º consumidor de produção.
- **ASSUMPTION-004 da antecessora:** as condições do modo sombra impostas pelo usuário.

### Files/Modules Impactados

- `harness4claude/scripts/branch_sensor.py`: ganha a gravação do turno no spool, atrás da flag. A decisão de `ramo`
  não muda.
- `harness4claude/scripts/branch_config.py`: ganha a flag `HARNESS_BRANCH_LAYER_J` (BOOL, padrão `False`).
- `harness4claude/scripts/branch_j_spool.py` (novo): o filtro de turno humano e a escrita do spool, só com a biblioteca
  padrão.
- `master-harness/mh/avaliacao/camada_j_sombra.py` (novo): lote, rotulagem e medida, com os verbos de
  `mh avaliar camada-j sombra …`.
- `master-harness/mh/avaliacao/cli_avaliacao.py`: registra os verbos novos.

### Dependências

- Avaliação offline mesclada, ou pelo menos o ramo `feat/avaliacao-camada-j`: molde, ponte e funções de
  `camada_j.py`.
- Chave do Jev em `~/.master-harness/segredos/typesafe.key` (já existe).

---

## User Stories

### US-1: Guardar o turno sem custo no prompt (Priority: P1) — MVP

**Como** usuário do Claude Code
**Quero** que, com a flag ligada, cada prompt meu seja guardado localmente com a âncora da sessão
**Para que** o J possa ser medido sobre o meio de sessão sem que eu sinta nada

**Why this priority**: sem o spool não há o que medir; e o spool é a única parte que roda dentro do hook.

**Independence**: testável sozinha. Com a flag ligada, o spool cresce; com ela desligada, nada muda.

**Acceptance Criteria**:
- **AC-1.1**: Given `HARNESS_BRANCH_LAYER_J` desligada (o padrão), When chega um `UserPromptSubmit`, Then nenhum
  arquivo do spool é criado ou escrito, e a saída do hook é idêntica byte a byte à de hoje.
- **AC-1.2**: Given a flag ligada e um prompt humano com ≥ 12 caracteres, When chega um `UserPromptSubmit`, Then uma
  linha `{instante, sessao, projeto, turno_idx, ancora, turno}` é acrescentada a
  `~/.claude/harness/branch-j/sombra/<session_id>.jsonl`. O arquivo é um por sessão, então não há dois escritores no
  mesmo arquivo.
- **AC-1.3**: Given a flag ligada, When o turno começa com um dos prefixos não humanos fixados (REQ-003), Then ele não
  é gravado e um contador por prefixo sobe no mesmo diretório.
- **AC-1.4**: Given a flag ligada, When chega um `Stop` (texto do assistente), Then nada é gravado: só o turno do
  usuário foi medido.
- **AC-1.5**: Given qualquer erro na gravação (disco, permissão, JSON), When o hook roda, Then o erro é engolido, o
  hook sai como sairia sem a flag, e um contador `falha_spool` sobe. O contrato de `evaluate` ("nunca levanta")
  continua valendo.
- **AC-1.6**: Given a flag ligada, When o hook roda, Then nenhuma conexão de rede é aberta e nenhum processo é lançado
  pelo caminho do spool.

**Edge Cases**:
- Sessão sem âncora: grava com `ancora: null`, e o lote pontua só os turnos que têm âncora.
- Prompt gigante: grava inteiro. A truncagem de 24 000 code points acontece no lote, pela ponte.

### US-2: Pontuar a janela pelo caminho medido (Priority: P1)

**Como** dono do harness
**Quero** pontuar o spool da janela com o mesmo molde, a mesma redação e a mesma ponte da avaliação
**Para que** o modo sombra meça exatamente o que foi avaliado

**Why this priority**: sem escore não há sinal para rotular.

**Acceptance Criteria**:
- **AC-2.1**: Given a janela fechada, When `mh avaliar camada-j sombra seco`, Then os corpos são montados pela
  `PonteJev` dentro da `captura()`, sem rede e com o molde `camada_j_par.json`. Os corpos redigidos vão a
  `<casa>/avaliacao/camada-j/sombra/seco.jsonl`, com ACL só do usuário. A varredura automática de credencial residual
  roda e imprime contagens. Saem também a estimativa, o gasto do mês e o código de uma amostra de 20.
- **AC-2.2**: Given a varredura com ≥ 1 resíduo, When `sombra rodar`, Then a rodada recusa `residuo_de_credencial`
  antes de qualquer chamada.
- **AC-2.3**: Given o código da amostra revisada pelo usuário, When `sombra rodar --confirmo <codigo>`, Then cada
  turno é pontuado na porta `C4` com log auditado. Cada `sha_enviado` é conferido contra o do seco
  (`envio_divergente`), e só números e shas vão ao arquivo de escores.
- **AC-2.4**: Given uma estimativa que, somada ao gasto já feito na janela, passa de US$ 0,50, ou ao gasto do mês
  passa de US$ 5, When `sombra rodar`, Then a rodada recusa (`teto_da_janela_excedido` ou `envelope_excedido`) antes
  da sonda.
- **AC-2.5**: Given a sonda com `model` ≠ `jev-1.13.0`, When `sombra rodar`, Then a rodada recusa
  `modelo_remoto_divergente`.

### US-3: Rotular só o que o J sinalizou (Priority: P1)

**Como** usuário
**Quero** responder "isto merecia sessão própria?" só para os turnos que o J marcou
**Para que** o custo de rotular fique pequeno e a precisão saia de rótulo humano, não de supervisão distante

**Acceptance Criteria**:
- **AC-3.1**: Given os escores da janela, When `sombra rotular`, Then entram na fila os turnos com P ≥ τ = 0,77. Se
  forem mais de 40, entra uma amostra de 40, sorteada com a semente `20261010` antes de qualquer rótulo. Cada item
  mostra a âncora e o turno do spool local, nunca o corpo redigido, e aceita `s`, `n` ou `pular`.
- **AC-3.2**: Given rótulos já gravados, When `sombra rotular` roda de novo, Then ele retoma do primeiro item sem
  rótulo e não reapresenta os rotulados.
- **AC-3.3**: Given um item rotulado, When o rótulo é gravado, Then `{id, rotulo, instante}` vai a `rotulos.jsonl` na
  pasta restrita, sem texto.

### US-4: Decidir pelo critério fixado (Priority: P1)

**Como** dono do harness
**Quero** um veredito automático contra um critério escrito antes de olhar
**Para que** a decisão de fazer o J oferecer não dependa de leitura favorável dos números

**Acceptance Criteria**:
- **AC-4.1**: Given ≥ 20 itens rotulados com `s` ou `n`, When `sombra medir`, Then a precisão é s / (s + n), com IC
  95 % de Wilson. O veredito é `avança` se precisão ≥ 0,35 **e** limite inferior > 0,08, e `não avança` nos outros
  casos.
- **AC-4.2**: Given < 20 itens rotulados com `s` ou `n`, When `sombra medir`, Then o veredito é `inconclusivo`.
- **AC-4.3**: Given a medida, When o relatório é escrito, Then ele traz:
  - o veredito e a precisão com o IC;
  - n sinalizados, n rotulados e n pulados;
  - a taxa de disparo (sinalizados ÷ turnos pontuados), comparada aos ~3,7 % da avaliação;
  - os turnos filtrados por prefixo;
  - o gasto;
  - nenhum texto de turno.

### US-5: Fim de janela e limpeza (Priority: P2)

**Como** usuário
**Quero** que a janela tenha fim e que o texto bruto não fique para sempre
**Para que** o spool não vire um segundo histórico de conversa

**Acceptance Criteria**:
- **AC-5.1**: Given a flag ligada há 14 dias (contados do primeiro registro do spool), When o hook roda, Then ele para
  de gravar e sobe o contador `janela_fechada`, mesmo que a flag continue ligada.
- **AC-5.2**: Given o veredito gravado, When o usuário roda `sombra purgar`, Then o spool e o seco (os textos) são
  apagados, e escores, rótulos e relatório (sem texto) ficam. Nada é apagado sem esse comando.

---

## Requirements

- **REQ-001**: o hook grava o turno humano e a âncora num spool local por sessão, atrás de `HARNESS_BRANCH_LAYER_J`
  (padrão desligado), sem rede, sem processo novo e sem mudar a saída do hook.
  `consumidor: o lote do REQ-004 (mh avaliar camada-j sombra seco/rodar)`.
- **REQ-002**: a flag desligada deixa o comportamento de hoje intacto, verificado pela saída do hook e pela ausência do
  diretório do spool.
- **REQ-003**: o filtro de turno humano é uma lista fixa de prefixos, aplicada no hook:
  - `<task-notification>`
  - `<command-`
  - `<cross-session-message`
  - `Base directory for this skill:`
  - `Caveat: The messages below`
  - `This session is being continued`
  - `[Request interrupted`
  - `Continue from where you left off.`

  Mais o `MIN_LEN` = 12 do sensor. `consumidor: REQ-001`.
- **REQ-004**: verbos `mh avaliar camada-j sombra {seco, ver-amostra, rodar, rotular, medir, purgar}` no master-harness,
  fora de `mh/s1/`, reusando `camada_j` (molde, ponte, livro, varredura). `consumidor: o usuário, que roda o lote
  depois que a janela fecha`.
- **REQ-005**: τ = 0,77, o P do 25º par da avaliação offline, ou seja, o mesmo volume de disparos da camada A
  (`camada-j-resultado.md`, escores de `3ef033c`). Fica fixo durante a janela.
- **REQ-006**: teto de US$ 0,50 por janela, somando todos os lotes dela, e o envelope de US$ 5 por mês. O gasto vai ao
  livro com `corpus: "camada-j-sombra"`.
- **REQ-007**: antes de toda rodada, a revisão de amostra pelo usuário (código) e a varredura automática de credencial
  com zero resíduos.
- **REQ-008**: o critério US-4 e a janela US-5 ficam fixados nesta spec e não mudam depois que o primeiro turno entra
  no spool.
- **REQ-009**: a saída da medida é o consumidor final. Com `avança`, abre-se a spec de **oferta** (o J decide ramo no
  hook), e é ela que leva a decisão do gateway (L-46). Com `não avança` ou `inconclusivo`, a flag volta a desligada e a
  C4 fica registrada com o resultado. `consumidor: decisão do usuário sobre a spec de oferta`.

---

## Gateway (L-46)

O modo sombra **não** é o 2º consumidor de produção do Jev, e por isso não dispara o gateway. Quem chama o Jev é um
lote Python no venv do `mh`, da mesma classe da Onda 6, que importa o kit sem editá-lo. O hook não chama o Jev e não
abre conexão. Decisão do usuário de 2026-10-10 (ASSUMPTION-001). O gatilho vai para a spec de oferta, que é quando o
hook precisaria do julgamento em tempo de prompt. Pesam ali os fatos já medidos: hook em processo novo, TLS frio de
0,6 a 8 s, livro de gasto hoje só da Onda 6.

---

## Boundaries

### ALWAYS
- Redação, lista de bloqueio e guarda da chave pela `PonteJev` antes de qualquer envio.
- Revisão de amostra pelo usuário e varredura de resíduo antes de cada `rodar`.
- Spool, seco e rótulos com ACL só do usuário e fora do git.
- O veredito sai do critério escrito aqui, e o relatório diz quantos itens ficaram de fora e por quê.

### NEVER
- Oferecer ramo, injetar contexto ou mostrar qualquer coisa ao usuário a partir do J durante a janela.
- Chamar a rede, lançar processo ou ler a chave dentro do hook.
- Editar `mh/s1/` ou `mh/replay/`.
- Mudar τ, a janela ou o critério depois do primeiro registro.
- Apagar texto do spool sem o comando `sombra purgar`.

### ASK
- Ligar a flag. É o usuário que liga, e isso marca o início da janela.
- Rodar o lote real (código da amostra).
- Qualquer gasto acima de US$ 0,50 na janela.

---

## Nível de garantia

**A entrega pode ser afirmada como:** "a precisão do J nos prompts do meio de sessão que ele sinalizaria, medida por
rótulo do usuário numa janela de até 14 dias, com IC".

**Não está incluído:**
- o recall: o que o J deixou de sinalizar não é rotulado;
- a latência de produção, porque o lote usa conexão quente;
- qualquer efeito no usuário, porque nada é oferecido;
- generalização para além do usuário e da janela medidos.

---

## Suposições

- **ASSUMPTION-001** (usuário, 2026-10-10): o J roda num lote com spool, fora do hook. O hook só grava, e o lote no
  `mh` pontua. Justifica: REQ-001, REQ-004, Gateway.
- **ASSUMPTION-002** (usuário, 2026-10-10): prompts de todos os projetos podem ir ao Jev, incluindo o
  `slb-mestrado-projeto` com conteúdo interno da SLB (L-43, nenhum projeto excluído). Justifica: REQ-001, REQ-004.
- **ASSUMPTION-003** (usuário, 2026-10-10): a janela é de 14 dias ou 40 sinais rotulados; o critério é precisão
  ≥ 0,35 com Wilson inferior > 0,08; o limiar é τ = 0,77. Justifica: REQ-005, REQ-008, US-3, US-4, US-5.
- **ASSUMPTION-004** (usuário, 2026-10-10): o teto é de US$ 0,50 por janela. Justifica: REQ-006, AC-2.4.
- **ASSUMPTION-005** (inferência): "40 sinais" limita a fila de rótulo (amostra de 40 sorteada antes do primeiro
  rótulo), não a coleta. A coleta dura os 14 dias, e o lote roda uma vez, depois que a janela fecha. Assim, a revisão
  de amostra é uma só, e não uma por dia. Justifica: AC-3.1, AC-5.1, REQ-007.
- **ASSUMPTION-006** (inferência): os prefixos do REQ-003 cobrem os turnos não humanos vistos na amostra revisada da
  avaliação. Prefixo novo que aparecer na janela entra no relatório (contagem) e não é corrigido no meio. Justifica:
  REQ-003, AC-1.3.
- **ASSUMPTION-007** (inferência): ~165 prompts por dia, um teto contado nos transcripts que inclui texto injetado,
  a 1 657 tokens em média. Isso projeta ~US$ 0,16 em 14 dias, abaixo do teto. Justifica: REQ-006.
- **ASSUMPTION-008** (inferência): um arquivo por sessão evita escrita concorrente. Escritor único por arquivo dispensa
  trava, o que importa porque o hook não pode esperar. Justifica: AC-1.2.

---

## Out of Scope

- Fazer o J oferecer ramo no hook (spec de oferta) e o gateway.
- Mudar a camada A ou ligar a camada B.
- Rótulo do que o J **não** sinalizou (recall).
- Leque, `criteria` e outros moldes: a produção roda exatamente o molde medido.
