# Spec: Camada J do Branch Keeper em modo sombra

**Status**: Draft v2 (grill-me rodada 1 incorporada; aguarda approve-spec)
**Created**: 2026-10-10
**Branch**: harness4claude `spec/camada-j-sombra`. No master-harness, o ramo sai do `main` **depois** do merge de
`feat/avaliacao-camada-j`, que é feito pelo orquestrador.
**Author**: AI-generated, reviewed by Leonardo
**Task**: `t-20261010-164331842535` (L2-feature)
**Grill**: rodada 1, 2026-10-10, com 5 lentes em contexto limpo: 70 perguntas, 8 bloqueantes, nenhuma lente sem
retorno. A seção [Grill](#grill--rodada-1) traz o destino de cada bloqueante.
**Antecessora**: [`branch-camada-j-spec-light.md`](branch-camada-j-spec-light.md), com veredito `avança` (precisão@25
de 0,36, exatamente no corte; `master-harness/docs/avaliacao/camada-j-resultado.md`). Ela reservou para esta spec o
nome `…-ligacao-spec-light.md`; a spec saiu completa (L2).

---

## Executive Summary

A avaliação offline mostrou que o Jev separa *abertura de sessão nova* de *continuação* melhor que a camada A. Ela não
mostrou o que importa em produção: se o J acha ramo **no meio** de uma sessão.

Esta spec mede isso sem que o usuário veja nada:
- o usuário abre uma janela de 14 dias;
- enquanto a janela está aberta e a flag ligada, o hook guarda localmente cada turno humano, sem rede, sem processo
  novo e sem latência perceptível;
- quando a janela fecha, um lote no `mh` pontua tudo de uma vez, pelo mesmo caminho medido na avaliação;
- o usuário rotula, sem saber qual é qual, até 40 turnos que o J sinalizou e 20 chamarizes que ele não sinalizou;
- um critério fixado aqui decide se vale escrever a spec que faz o J **oferecer** ramo.

## Context

**Hoje.**
- `scripts/branch_sensor.py::main` roda em `UserPromptSubmit` e em `Stop`.
- A camada A (regex) é o portão de `ramo` (`verdict()` exige `hit_a`), e a camada B está desligada.
- A âncora do sensor (`set_anchor`, `branch_sensor.py:194`) é o primeiro prompt substantivo da sessão, cortado em
  2 000 caracteres. Ela fica num arquivo por `state_dir(cwd)` e só vale para a mesma `session_id` (`load_anchor`).
- Na avaliação, a âncora foi o primeiro prompt **inteiro** da sessão (`harvest_branch_labels.py`), com o filtro
  `ABERTURA_NAO_HUMANA`.

**O que a avaliação deixou pronto** (master-harness, `feat/avaliacao-camada-j`):
- o molde `mh/avaliacao/moldes/camada_j_par.json`;
- a `PonteJev` (redação, lista de bloqueio, truncagem, guarda da chave, log auditado na porta `C4`), pela
  `mh/avaliacao/camada_j.py`;
- o livro de gasto (`mh/hints/livro.py`) e a estatística do kit.

**Restrições herdadas:**
- **T2:** `mh/s1/` está congelado.
- **L-43:** redação local antes de qualquer envio.
- **L-44:** envelope de US$ 5 por mês.
- **L-45:** `jev-1.13.0` fixo, chave só no arquivo dela.
- **L-46:** o gateway nasce com o 2º consumidor de produção.

### Files/Modules Impactados

- `harness4claude/scripts/branch_j_spool.py` (novo): filtro de turno humano e escrita do spool, só com a biblioteca
  padrão. Nada de rede e nada de processo.
- `harness4claude/scripts/branch_sensor.py::main`: chama o spool em `UserPromptSubmit`, atrás da flag, antes de
  qualquer saída antecipada. A decisão de `ramo` e a saída do hook não mudam.
- `harness4claude/scripts/branch_config.py`: ganha a flag `HARNESS_BRANCH_LAYER_J` (BOOL, padrão `False`).
- `master-harness/mh/avaliacao/camada_j_sombra.py` (novo): verbos `abrir`, `seco`, `ver-amostra`, `rodar`, `rotular`,
  `medir` e `purgar`. O design-doc decide o que da `camada_j.py` vira função compartilhada.
- `master-harness/mh/avaliacao/cli_avaliacao.py`: registra os verbos novos.

### Dependências (quem garante cada uma)

- **D1. Merge de `feat/avaliacao-camada-j` no `main` do master-harness.** Garante o orquestrador. A implementação
  desta spec espera esse merge.
- **D2. Release do harness4claude com o `branch_j_spool.py`, instalado no cache do plugin.** Garante o deploy do
  harness4claude. O `sombra abrir` confere, pelo `~/.claude/harness/plugin-root`, que o cache tem o módulo, e recusa
  `plugin_sem_spool` se não tiver.
- **D3. A flag no `env` do `~/.claude/settings.json`**, o bloco global que todo processo de hook herda. Garante o
  usuário. Sessões abertas antes da mudança não gravam, e o relatório conta as sessões que gravaram.
- **D4. A chave do Jev e o `jev-1.13.0` servido no dia do lote.** Ninguém garante. A sonda recusa
  `modelo_remoto_divergente` e a janela vira `inconclusivo`.

---

## User Stories

### US-0: Abrir a janela (Priority: P1)

**Como** usuário
**Quero** um comando explícito que abre a janela de 14 dias
**Para que** o início da coleta seja um ato meu, registrado, e não um efeito colateral de ligar uma variável

**Acceptance Criteria**:
- **AC-0.1**: Given nenhuma janela anterior, o plugin instalado com o spool e a chave presente, When
  `mh avaliar camada-j sombra abrir`, Then:
  - nasce a pasta `<casa>/avaliacao/camada-j/sombra/`, onde `<casa>` é `MASTER_HARNESS_HOME`, senão
    `~/.master-harness`;
  - a pasta recebe ACL só do usuário, pela mesma rotina da avaliação (`icacls` no Windows; roda no `mh`, nunca no
    hook);
  - grava-se `janela.json` com `{estado: "aberta", inicio, fim = inicio + 14 × 24 h (UTC, ISO-8601), tau: 0.77,
    semente: 20261010, prefixos, sha256 desta spec}`.
- **AC-0.2**: Given um `janela.json` existente, em qualquer estado, When `sombra abrir`, Then recusa
  `janela_ja_existe`. Uma janela por spec; outra janela é outra spec.
- **AC-0.3**: Given o cache do plugin sem `branch_j_spool.py`, When `sombra abrir`, Then recusa `plugin_sem_spool`.

### US-1: Guardar o turno sem custo no prompt (Priority: P1) — MVP

**Como** usuário do Claude Code
**Quero** que, com a janela aberta e a flag ligada, cada prompt meu seja guardado localmente com a âncora da sessão
**Para que** o J possa ser medido sobre o meio de sessão sem que eu sinta nada

**Acceptance Criteria**:
- **AC-1.1**: Given a flag desligada, ou `janela.json` ausente, ou `estado` ≠ `aberta`, ou o instante ≥ `fim`, When
  chega um `UserPromptSubmit`, Then nada é escrito na pasta do spool, e a saída do hook é idêntica byte a byte à de
  hoje.
- **AC-1.2**: Given a janela aberta, a flag ligada e um turno humano (REQ-003), When chega um `UserPromptSubmit`,
  Then uma linha `{tipo, instante (ISO-8601 UTC), sessao, projeto (slug do cwd), turno_idx, texto}` é acrescentada a
  `sombra/<session_id>.jsonl`:
  - o primeiro turno humano gravado da sessão tem `tipo: "ancora"` e `turno_idx` 0, e é a âncora, com o texto
    inteiro, como na avaliação;
  - os seguintes têm `tipo: "turno"` e `turno_idx` 1, 2, …, contados pelas linhas já gravadas no arquivo.
- **AC-1.3**: Given um `session_id` fora de `[A-Za-z0-9-]{8,64}`, When o hook roda, Then nada é gravado e sobe o
  contador `sessao_invalida`.
- **AC-1.4**: Given um turno que, depois de `lstrip()`, começa por um prefixo do REQ-003 (comparação com
  maiúsculas e minúsculas), ou que tem menos de 12 code points depois de `strip()`, When o hook roda, Then ele não é
  gravado, e sobe o contador do prefixo ou o `curto`.
- **AC-1.5**: Given um `Stop`, When o hook roda, Then nada é gravado.
- **AC-1.6**: Given qualquer erro de escrita, When o hook roda, Then o erro é engolido, o hook sai como sairia sem a
  flag, e sobe `falha_spool` no arquivo de contagens da sessão (se esse arquivo também falhar, nada mais é tentado).
- **AC-1.7**: Given a pasta do spool com ≥ 200 MB, When o hook roda, Then nada é gravado e sobe `spool_cheio`.
- **AC-1.8**: Given a flag ligada, When o hook roda, Then nenhuma conexão de rede é aberta, nenhum processo é lançado
  e a chave não é lida.

Os contadores ficam em `sombra/<session_id>.contagens.json`, um arquivo por sessão e com um só escritor.

### US-2: Pontuar a janela pelo caminho medido (Priority: P1)

**Acceptance Criteria**:
- **AC-2.1**: Given o instante < `fim`, When `sombra seco`, Then recusa `janela_aberta`. Depois de `fim`, o hook não
  grava mais, o spool fica congelado e os shas ficam estáveis.
- **AC-2.2**: Given a janela fechada, When `sombra seco`, Then:
  - cada `turno` (nunca a `ancora`) é montado com o par `{ancora da sua sessão, turno}` pela `PonteJev`, dentro da
    `captura()`, sem rede e com o molde `camada_j_par.json`;
  - os corpos redigidos vão a `sombra/seco.jsonl`;
  - roda a varredura de resíduo (REQ-007);
  - saem a estimativa, o gasto da janela, o gasto do mês e o código de uma amostra de 20.

  Um seco novo substitui o código anterior.
- **AC-2.3**: Given a varredura com ≥ 1 resíduo, When `sombra rodar`, Then recusa `residuo_de_credencial` antes de
  qualquer chamada.
- **AC-2.4**: Given o código da amostra que o usuário revisou, When `sombra rodar --confirmo <codigo>`, Then cada
  turno é pontuado na porta `C4`, com log auditado. Cada `sha_enviado` é conferido contra o do seco
  (`envio_divergente`). Só números e shas vão a `sombra/escores.jsonl`. Item já pontuado é pulado, e
  `--refazer-falhas` repontua só as falhas de infraestrutura, com o mesmo código.
- **AC-2.5**: Given uma estimativa que, somada ao gasto da janela (linhas do livro com `lote` começando por
  `camada-j-sombra-<inicio>`), passa de US$ 0,50, ou que, somada ao gasto do mês UTC (`livro.gasto_do_mes`, todos os
  corpora), passa de US$ 5, When `sombra rodar`, Then recusa antes da sonda (`teto_da_janela_excedido` ou
  `envelope_excedido`). Não há override.
- **AC-2.6**: Given a sonda com `model` ≠ `jev-1.13.0`, ou sem resposta, When `sombra rodar`, Then recusa
  `modelo_remoto_divergente` ou `sonda_sem_resposta`.

### US-3: Rotular às cegas (Priority: P1)

**Acceptance Criteria**:
- **AC-3.1**: Given os escores, When `sombra rotular` roda pela primeira vez, Then a fila é montada uma vez e gravada
  em `fila.json`:
  - **sinalizados**: os turnos com P ≥ 0,77, comparando o P cru (sem arredondar). Se forem mais de 40, entram 40,
    sorteados com `random.Random(20261010).sample` sobre os ids ordenados;
  - **chamarizes**: 20 turnos com P < 0,77, sorteados na mesma sequência do gerador;
  - **ordem**: os 60 itens são embaralhados com o mesmo gerador;
  - o id de um item é `<sessao>:<turno_idx>`.
- **AC-3.2**: Given a fila, When um item aparece, Then são mostrados a âncora e o turno, lidos do spool local, e
  nada mais: nem P, nem grupo, nem posição no grupo. As respostas aceitas são `s`, `n` e `pular`.
- **AC-3.3**: Given rótulos já gravados, When `sombra rotular` roda de novo, Then ele retoma do primeiro item sem
  rótulo. `sombra rotular --corrigir <id>` regrava um rótulo, e vale a última linha por id. `rotulos.jsonl` guarda
  `{id, rotulo, instante}`, sem texto.

### US-4: Decidir pelo critério fixado (Priority: P1)

**Acceptance Criteria**:
- **AC-4.1**: Given algum item da fila sem rótulo, When `sombra medir`, Then recusa `fila_incompleta`. Há uma medida
  só, com a fila inteira: nada de parada opcional.
- **AC-4.2**: Given a fila completa, When `sombra medir`, Then:
  - `prec_J` = s / (s + n) entre os sinalizados;
  - `base` = s / (s + n) entre os chamarizes;
  - o Δ = `prec_J` − `base` ganha IC 95 % por bootstrap de sessões (`estatistica.bootstrap_pareado`, B = 10 000,
    semente `20261010`, IC percentil);
  - **avança** se `prec_J` ≥ 0,35 **e** o limite inferior do IC de Δ > 0;
  - **inconclusivo** se os sinalizados têm s + n < 20, ou os chamarizes têm s + n < 10, ou a fila de sinalizados está
    vazia;
  - **não avança** nos outros casos.
- **AC-4.3**: Given a medida, When o relatório é escrito em `master-harness/docs/avaliacao/camada-j-sombra-resultado.md`,
  Then ele traz:
  - o veredito, `prec_J` com IC, `base` com IC e Δ com IC;
  - quantos itens foram sinalizados, rotulados e pulados, e quantos sinalizados houve no total;
  - a taxa de disparo (sinalizados ÷ turnos pontuados, sem âncoras) ao lado dos ~3,7 % da avaliação, só para leitura;
  - as contagens do hook somadas (por prefixo, `curto`, `sessao_invalida`, `falha_spool`, `spool_cheio`);
  - o número de sessões com spool e o gasto;
  - nenhum texto de turno.
- **AC-4.4**: Given qualquer veredito, When ele é gravado, Then nenhuma configuração ou código de produção muda. Os
  próximos passos (desligar a flag, abrir a spec de oferta) são impressos para o usuário fazer.

### US-5: Limpeza (Priority: P2)

**Acceptance Criteria**:
- **AC-5.1**: Given o veredito gravado, When o usuário roda `sombra purgar`, Then:
  - o texto é apagado: os `<session_id>.jsonl` e o `seco.jsonl`;
  - `escores.jsonl`, `rotulos.jsonl`, `fila.json`, as contagens e o relatório ficam;
  - `janela.json` passa a `estado: "encerrada"`, e o hook nunca mais grava nesta spec.
- **AC-5.2**: Given nenhum veredito, When `sombra purgar`, Then recusa `sem_veredito`. A saída é
  `sombra purgar --abandonar`, que encerra a janela sem veredito e o registra.
- **AC-5.3**: Given `estado: "aberta"` 30 dias depois de `fim`, When `mh saude` roda, Then aparece o aviso
  `spool_com_texto_retido`.

---

## Requirements

- **REQ-001**: o hook grava o turno humano e a âncora da própria sessão num spool local, um arquivo por sessão, atrás
  de `HARNESS_BRANCH_LAYER_J` (padrão desligado) e de uma janela aberta. Sem rede, sem processo novo, sem ler a chave,
  sem mudar a saída do hook. `consumidor: o lote do REQ-004`.
- **REQ-002**: flag desligada, ou janela não aberta, deixa o comportamento de hoje intacto: nada é escrito na pasta do
  spool.
- **REQ-003**: a lista fixa de prefixos não humanos é aplicada no hook depois de `lstrip()`, com o MIN_LEN de 12 code
  points depois de `strip()`. Os prefixos:
  - `<task-notification>`
  - `<command-`
  - `<cross-session-message`
  - `<local-command`
  - `Base directory for this skill:`
  - `Caveat: The messages below`
  - `This session is being continued`
  - `[Request interrupted`
  - `Continue from where you left off.`

  `consumidor: REQ-001`.
- **REQ-004**: os verbos `mh avaliar camada-j sombra {abrir, seco, ver-amostra, rodar, rotular, medir, purgar}`, no
  master-harness e fora de `mh/s1/`. `consumidor: o usuário, que roda abrir no início e o resto depois do fim`.
- **REQ-005**: τ = 0,77 exato, comparado com o P cru. É o P do 25º par em ordem decrescente na avaliação offline
  (`escores.jsonl` de `3ef033c`; 26 pares têm P ≥ 0,77).
- **REQ-006**: teto de US$ 0,50 por janela, sem override, e envelope de US$ 5 por mês UTC, com todos os corpora do
  livro. O gasto vai ao livro com `corpus: "camada-j-sombra"` e `lote: "camada-j-sombra-<inicio>-<instante>"`.
- **REQ-007**: antes de cada `rodar`, a revisão de amostra pelo usuário (código) e a varredura de resíduo com zero
  achados. A varredura é código novo no `camada_j_sombra.py`, com teste de duas metades. Resíduo é qualquer
  ocorrência, no estado redigido, destes padrões:
  - chave Anthropic, OpenAI, GitHub, AWS, Google ou Slack;
  - JWT;
  - bloco PEM de chave privada;
  - `senha|password|passwd|pwd` seguido de `:` ou `=` e de um valor;
  - `Bearer <token>`;
  - credencial em URL (`usuario:senha@`);
  - e-mail.
- **REQ-008**: no `abrir`, ficam congelados, com o sha da spec gravado em `janela.json`: τ, semente, tamanhos da fila
  (40 + 20), critério, janela, prefixos e MIN_LEN, molde `camada_j_par.json` e teto. A redação e a lista de bloqueio
  são as do kit no dia do lote; o sha da lista vai a cada escore.
- **REQ-009**: o veredito é consumido pelo usuário. Com `avança`, abre-se a spec de **oferta** (o J decide ramo no
  hook), e é ela que leva a decisão do gateway (L-46). Com `não avança` ou `inconclusivo`, o usuário desliga a flag, e
  a C4 fica registrada com o resultado no `jev-integracao.md`. `consumidor: decisão do usuário sobre a spec de oferta`.

---

## Gateway (L-46)

O modo sombra **não** é o 2º consumidor de produção do Jev, e por isso não dispara o gateway. Quem chama o Jev é um
lote Python no venv do `mh`, da mesma classe da Onda 6. O hook não chama o Jev, não abre conexão e não lê a chave.
Decisão do usuário de 2026-10-10 (ASSUMPTION-001). O gatilho vai para a spec de oferta, que é quando o hook precisaria
do julgamento em tempo de prompt.

---

## Boundaries

### ALWAYS
- Redação, lista de bloqueio e guarda da chave pela `PonteJev` antes de qualquer envio; varredura de resíduo e revisão
  de amostra pelo usuário antes de cada `rodar`.
- Spool, seco, fila e rótulos na pasta `sombra/` da casa do `mh`, com ACL só do usuário (aplicada pelo `abrir`) e fora
  do git.
- O relatório diz quantos itens ficaram de fora e por quê.

### NEVER
- Oferecer ramo, injetar contexto ou mostrar qualquer coisa ao usuário a partir do J durante a janela.
- Rede, processo novo ou leitura da chave dentro do hook.
- Ler a pasta `sombra/` com qualquer coisa além do hook (escrita), dos verbos `sombra` (leitura) e do `mh saude`
  (só `janela.json`). Ela não entra em vault, grafo, `signals.json`, `emissions.jsonl`, semente de ramo nem backup do
  harness.
- Editar `mh/s1/` ou `mh/replay/`.
- Mudar qualquer item do REQ-008 depois do `abrir`; abrir uma segunda janela nesta spec; medir com a fila incompleta.
- Passar do teto da janela ou do envelope.
- Mudar configuração ou código de produção a partir do veredito.

### ASK
- `sombra abrir`, que começa a janela, e ligar a flag.
- `sombra rodar`, com o código da amostra.
- `sombra purgar`, que é irreversível.

---

## Nível de garantia

**A entrega pode ser afirmada como:** "a precisão do J nos turnos do meio de sessão que ele sinalizaria, contra uma
linha de base de turnos não sinalizados, as duas por rótulo cego do usuário, numa janela de 14 dias, com IC por
sessão".

**Não está incluído:**
- o recall: os chamarizes dão só um indício;
- a latência de produção;
- qualquer efeito no usuário;
- generalização para além deste usuário e desta janela;
- sessões abertas antes da flag.

---

## Suposições

- **ASSUMPTION-001** (usuário, 2026-10-10): spool com lote, fora do hook. Justifica: REQ-001, REQ-004, Gateway.
- **ASSUMPTION-002** (usuário, 2026-10-10): todos os projetos podem ir ao Jev, incluindo o `slb-mestrado-projeto`
  com conteúdo interno da SLB (L-43). Justifica: REQ-001, REQ-004.
- **ASSUMPTION-003** (usuário, 2026-10-10): janela de 14 dias, τ = 0,77, teto de rótulo de 40 sinalizados.
  Justifica: REQ-005, REQ-008, AC-0.1, AC-3.1.
- **ASSUMPTION-004** (usuário, 2026-10-10): teto de US$ 0,50 por janela. Justifica: REQ-006, AC-2.5.
- **ASSUMPTION-005** (usuário, 2026-10-10, confirmada no grill): "40 sinais" limita a fila de rótulo, não a coleta.
  A coleta dura os 14 dias, e o lote roda uma vez, depois do fim. Justifica: AC-2.1, AC-3.1.
- **ASSUMPTION-006** (usuário, 2026-10-10, no grill): rótulo cego com 20 chamarizes, e o critério compara os
  sinalizados com essa linha de base humana, não com o 0,08 da supervisão distante. Justifica: AC-3.1, AC-3.2,
  AC-4.2.
- **ASSUMPTION-007** (inferência): a âncora do spool é o primeiro turno humano gravado da sessão, inteiro, no próprio
  arquivo da sessão. É a mesma definição da avaliação, e não depende de `branch-anchor.json` (cortado em 2 000 e
  compartilhado no `state_dir`). Justifica: AC-1.2, AC-2.2, REQ-005.
- **ASSUMPTION-008** (inferência): os prefixos do REQ-003 cobrem os turnos não humanos vistos na amostra revisada da
  avaliação. Prefixo novo grava como humano e aparece para o usuário na rotulagem; ali ele pode responder `pular`.
  Justifica: REQ-003.
- **ASSUMPTION-009** (inferência): ~165 prompts por dia, um teto que inclui texto injetado. Projeta ~US$ 0,16 em 14
  dias e uns 80 sinalizados, a ~3,7 %. Se a taxa no meio de sessão for muito menor, o veredito pode sair
  `inconclusivo`. Justifica: REQ-006, AC-4.2.
- **ASSUMPTION-010** (inferência): a pasta `sombra/` fica na casa do `mh`, e não em `~/.claude/harness`, porque a
  casa do `mh` não é lida pelos espelhamentos do harness. O hook resolve o caminho pela mesma regra de
  `redacao.pasta_dos_segredos` (`MASTER_HARNESS_HOME`, senão `~/.master-harness`). Justifica: NEVER (leitores),
  AC-0.1.

---

## Grill — rodada 1

Bloqueantes e destino:

| # | Pergunta, em resumo | Destino |
|---|---|---|
| 1 | onde começa a janela | US-0: `abrir` grava `janela.json`; o fim é `inicio` + 14 × 24 h UTC |
| 2 | o spool pode vazar por outro caminho do ecossistema | pasta na casa do `mh` (ASSUMPTION-010) e NEVER de leitores |
| 3 | `purgar` reabre a janela em silêncio | `janela.json` passa a `encerrada`; NEVER de segunda janela; `purgar` é ASK e exige veredito |
| 4–5 | a âncora é da sessão e tem a forma da avaliação | âncora no próprio arquivo da sessão, inteira (ASSUMPTION-007) |
| 6 | ordem do deploy do plugin | D2; o `abrir` recusa `plugin_sem_spool` |
| 7 | ACL sem processo no hook | a ACL é aplicada pelo `abrir`, no `mh`; o hook herda |
| 8 | a varredura de resíduo não existe | é código novo, com os padrões definidos no REQ-007 |

As altas e médias foram resolvidas nos ACs:
- contadores com um arquivo por sessão;
- `session_id` validado;
- MIN_LEN, `lstrip` e τ exato;
- id `<sessao>:<turno_idx>`;
- sorteio e ordem da fila;
- medida única com a fila completa;
- teto sem override;
- gasto por janela pelo `lote`;
- retomada do `rodar`;
- primeiro turno = âncora, nunca pontuado;
- flag no `settings.json`;
- base do master-harness depois do merge;
- IC por sessão no lugar de Wilson;
- veredito sem efeito em produção;
- prazo de retenção com aviso no `mh saude`.

Ficaram como limite declarado:
- sessões não interativas que passem no filtro são gravadas, aparecem na rotulagem e podem ser puladas;
- a retirada do `jev-1.13.0` durante a janela;
- a política de dados da empresa, que é decisão do usuário (ASSUMPTION-002).

---

## Out of Scope

- Fazer o J oferecer ramo no hook (spec de oferta) e o gateway.
- Mudar a camada A ou ligar a camada B.
- Pontuar durante a janela.
- Leque, `criteria` e outros moldes.
