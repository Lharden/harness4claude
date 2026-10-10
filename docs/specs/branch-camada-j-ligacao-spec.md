# Spec: Camada J do Branch Keeper em modo sombra

**Status**: Aprovada pelo usuário em 2026-10-10, nesta janela, sobre a v3 (`ae14140`). Grill: 2 rodadas.
**Emenda v4** (2026-10-10, decisão do usuário na escalada da verify-multimodel, depois de 2 rodadas de gap-closure):
a âncora vem do `SessionStart` e não mais da âncora do sensor (AC-1.0, AC-1.2, AC-1.3, ASSUMPTION-013,
~~ASSUMPTION-008~~); dados pessoais na varredura (REQ-007, ASSUMPTION-014); exclusão e purga sem recriar arquivo
(AC-5.1, AC-5.2) e retenção depois da purga no `mh saude` (AC-5.4). Itens marcados `(v4)`.
**Created**: 2026-10-10
**Branch**: harness4claude `spec/camada-j-sombra`. No master-harness, o ramo sai do `main` **depois** do merge de
`feat/avaliacao-camada-j` (até `3ef033c`), que é feito pelo orquestrador.
**Author**: AI-generated, reviewed by Leonardo
**Task**: `t-20261010-164331842535` (L2-feature)
**Grill**: duas rodadas de 5 lentes em contexto limpo, nenhuma lente sem retorno:
- rodada 1: 70 perguntas, 8 bloqueantes;
- rodada 2, sobre a v2: 71 perguntas, 9 bloqueantes.

O destino de cada bloqueante está em [Grill](#grill).
**Antecessora**: [`branch-camada-j-spec-light.md`](branch-camada-j-spec-light.md), com veredito `avança` (precisão@25
de 0,36, exatamente no corte; `master-harness/docs/avaliacao/camada-j-resultado.md`).

---

## Executive Summary

A avaliação offline mostrou que o Jev separa *abertura de sessão nova* de *continuação* melhor que a camada A. Ela não
mostrou o que importa em produção: se o J acha ramo **no meio** de uma sessão.

Esta spec mede isso sem que o usuário veja nada:
- o usuário abre uma janela de 14 dias;
- em cada sessão que começa depois disso, o hook guarda localmente a âncora e os turnos humanos, sem rede, sem
  processo novo e sem latência perceptível;
- quando a janela fecha, um lote no `mh` pontua tudo de uma vez, pelo mesmo caminho medido na avaliação;
- o usuário rotula, sem saber qual é qual, até 40 turnos que o J sinalizou e 20 chamarizes que ele não sinalizou;
- um critério fixado aqui decide se vale escrever a spec que faz o J **oferecer** ramo.

## Context

**Hoje.**
- `scripts/branch_sensor.py::main` roda em `UserPromptSubmit` e em `Stop`, chamado pelo wrapper
  `hooks/harness-branch-sensor.sh`, que sai antes do Python quando `HARNESS_BRANCH=0`.
- No primeiro `UserPromptSubmit` substantivo de uma sessão, `main` fixa a âncora do sensor (`set_anchor`: texto
  cortado em 2 000 caracteres, por `state_dir(cwd)`, válida só para a mesma `session_id`) e retorna.
- A camada A (regex) é o portão de `ramo`, e a camada B está desligada.
- Na avaliação, a âncora foi o primeiro prompt **inteiro** da sessão (`harvest_branch_labels.py`).

**O que a avaliação deixou pronto** (master-harness, `feat/avaliacao-camada-j`):
- o molde `mh/avaliacao/moldes/camada_j_par.json`;
- a `PonteJev` do kit (redação, lista de bloqueio, truncagem, guarda da chave, log auditado na porta `C4`), pela
  `mh/avaliacao/camada_j.py`;
- o livro de gasto (`mh/hints/livro.py`) e a estatística do kit.

**Restrições herdadas:**
- **T2:** `mh/s1/` está congelado.
- **L-43:** redação local antes de qualquer envio.
- **L-44:** envelope de US$ 5 por mês.
- **L-45:** `jev-1.13.0` fixo, chave só no arquivo dela.
- **L-46:** o gateway nasce com o 2º consumidor de produção; ver [Gateway](#gateway-l-46).

### Files/Modules Impactados

- `harness4claude/scripts/branch_j_spool.py` (novo):
  - o filtro de turno humano e a escrita do spool, só com a biblioteca padrão;
  - nada de rede, nada de processo, nada de chave;
  - toda exceção do caminho é engolida.
- `harness4claude/scripts/branch_sensor.py::main` (v4): chama o spool uma vez por `UserPromptSubmit`, antes de
  qualquer saída antecipada, sem olhar a âncora do sensor; a decisão de `ramo` e a saída do hook não mudam.
- `harness4claude/hooks/branch_j_inicio.py` (novo, v4) e a entrada no `SessionStart` do `hooks/hooks.json`, com
  `matcher: startup|clear`: o marcador `<sid>.nova` do AC-1.0.
- `harness4claude/scripts/branch_config.py`: ganha a flag `HARNESS_BRANCH_LAYER_J` (BOOL, padrão `False`).
- `master-harness/mh/avaliacao/camada_j_sombra.py` (novo): verbos `abrir`, `status`, `excluir-sessao`, `seco`,
  `ver-amostra`, `rodar`, `rotular`, `medir` e `purgar`. O design-doc decide o que da `camada_j.py` vira função
  compartilhada.
- `master-harness/mh/avaliacao/cli_avaliacao.py`: registra os verbos novos.
- `master-harness/mh/saude.py`: ganha o aviso `spool_com_texto_retido` (AC-5.4), lendo o `janela.json` e só os
  **nomes** dos arquivos da pasta (v4), nunca o conteúdo.

### Dependências (quem garante cada uma)

- **D1. Merge de `feat/avaliacao-camada-j` no `main` do master-harness.** Garante o orquestrador; a implementação
  espera.
- **D2. Release e deploy do harness4claude com o spool no cache do plugin.** Garante o orquestrador, com OK do
  usuário, porque o deploy muda o hook de todas as sessões. O `abrir` confere, no cache apontado por
  `~/.claude/harness/plugin-root`, que existem o `branch_j_spool.py`, a chamada a ele no `branch_sensor.py` e (v4) o
  `hooks/branch_j_inicio.py` registrado no `hooks.json`. Lê o filtro do spool como texto (AST), sem executar código
  do plugin no processo que tem a chave.
- **D3. `HARNESS_BRANCH_LAYER_J=1` no `env` do `~/.claude/settings.json`, e `HARNESS_BRANCH` diferente de `0`.**
  Garante o usuário. O `abrir` lê o arquivo e recusa `flag_ausente` ou `branch_keeper_desligado`. O `env` é lido
  quando a sessão começa, e só sessões novas gravam (AC-1.2).
- **D4. A chave do Jev e o `jev-1.13.0` servido no dia do lote.** O `abrir` recusa `chave_ausente`. Se o modelo for
  retirado, a sonda recusa e a janela fecha por `purgar --abandonar --motivo modelo_retirado`.

---

## User Stories

### US-0: Abrir a janela e provar que ela está viva (Priority: P1)

- **AC-0.1**: Given D2, D3 e D4 satisfeitas e nenhuma janela anterior, When `mh avaliar camada-j sombra abrir` roda
  num terminal humano (REQ-010), Then:
  - nasce `<casa>/avaliacao/camada-j/sombra/`, com ACL só do usuário, herdável (`icacls … /inheritance:r /grant:r
    <usuário>:(OI)(CI)F`; `0700` fora do Windows);
  - grava-se `janela.json` com `{estado: "aberta", inicio, fim = inicio + 14 × 24 h, tau: 0.77, semente: 20261010,
    sha_filtro, spec_commit}`, instantes em ISO-8601 UTC;
  - `sha_filtro` é o sha256 da lista de prefixos e do MIN_LEN do REQ-003, como o código do hook os calcula;
  - `spec_commit` é o SHA do commit da spec aprovada, constante no código.
- **AC-0.2**: Given a casa do `mh` diferente de `~/.master-harness`, When `abrir`, Then recusa `casa_nao_padrao`.
  Hook e `mh` resolvem a mesma casa só porque os dois usam o padrão.
- **AC-0.3**: Given um `janela.json` existente, em qualquer estado, When `abrir`, Then recusa `janela_ja_existe`.
- **AC-0.4**: Given D2, D3 ou D4 faltando, When `abrir`, Then recusa com a causa: `plugin_sem_spool`,
  `flag_ausente`, `branch_keeper_desligado` ou `chave_ausente`.
- **AC-0.5**: Given a janela aberta, When `sombra status`, Then aparecem, sem texto:
  - estado, início e fim;
  - quantas sessões têm spool, quantas linhas cada uma tem e as contagens do hook;
  - o tamanho da pasta.

  O `abrir` termina mandando abrir uma sessão nova, mandar um prompt e rodar `status`. Esse é o teste de vida do dia 0.

### US-1: Guardar o turno sem custo no prompt (Priority: P1) — MVP

- **AC-1.1**: Given a flag desligada, ou `janela.json` ausente ou ilegível, ou `estado` ≠ `aberta`, ou o instante
  ≥ `fim`, ou o `sha_filtro` do código ≠ o do `janela.json`, When chega um `UserPromptSubmit`, Then nada é escrito na
  pasta do spool, e a saída do hook é idêntica byte a byte à de hoje.
- **AC-1.0** (v4): Given a janela aberta e a flag ligada, When chega um `SessionStart` com `source` `startup` ou
  `clear` e `session_id` válido, Then nasce o marcador vazio `sombra/<session_id>.nova`: a sessão nasceu dentro da
  janela. Com `source` `resume` ou `compact` nada nasce. Sem rede, sem processo, nunca levanta (o mesmo contrato do
  AC-1.6).
- **AC-1.2** (v4): Given o marcador `<session_id>.nova` e nenhum `<session_id>.jsonl`, When chega o primeiro
  `UserPromptSubmit` humano (REQ-003) da sessão, Then nasce `sombra/<session_id>.jsonl` com a linha `{tipo:
  "ancora", turno_idx: 0, instante, sessao, projeto, texto}` e o marcador é apagado:
  - `texto` é o prompt cru, como chegou, e inteiro;
  - `projeto` é o slug de `harness_paths` para o cwd;
  - um prompt não humano antes dele (slash command, aviso do host) é contado e não consome o marcador.
- **AC-1.3** (v4): Given um `<session_id>.jsonl` existente, When chega um `UserPromptSubmit` humano dessa sessão, Then
  uma linha `{tipo: "turno", turno_idx, instante, sessao, projeto, texto}` é acrescentada **sem criar o arquivo**
  (`O_APPEND` sem `O_CREAT`). `turno_idx` = número de linhas já no arquivo. Sem o arquivo e sem o marcador, nada é
  gravado nem contado: sessões abertas antes do `abrir`, retomadas ou compactadas ficam fora da amostra. A âncora do
  sensor (`branch-anchor.json`) não participa dessa decisão.
- **AC-1.4**: Given um turno que, depois de `lstrip()`, começa por um prefixo do REQ-003 (`startswith` literal, com
  maiúsculas e minúsculas como estão), ou que tem menos de 12 code points depois de `strip()`, When o hook roda, Then
  ele não é gravado, e sobe o contador do prefixo ou o `curto`. Só sessões da amostra (com marcador ou spool) contam.
- **AC-1.5**: Given um `session_id` fora de `[A-Za-z0-9-]{8,64}`, um prompt ausente ou não string, ou um `Stop`, When
  o hook roda, Then nada é gravado (sem contador, porque não há arquivo de sessão válido).
- **AC-1.6**: Given qualquer exceção no caminho do spool, When o hook roda, Then ela é engolida e a saída do hook
  fica idêntica à de hoje. Sobe `falha_spool` no arquivo de contagens da sessão; se essa escrita também falhar,
  nada mais é tentado.
- **AC-1.7** (v4): Given os `*.jsonl` da pasta somando ≥ 200 × 2^20 bytes (o tamanho sai de um `os.scandir` da pasta,
  medido dentro do p95 do AC-1.8 com 300 sessões), When o hook roda, Then nada é gravado e sobe `spool_cheio`.
- **AC-1.8**: Given a flag ligada, When o hook roda 1 000 vezes num banco de medida, Then o p95 do tempo a mais sobre
  o hook de hoje fica ≤ 20 ms, sem rede, sem processo e sem ler a chave.

Os contadores ficam em `sombra/<session_id>.contagens.json`, um arquivo por sessão e com um só escritor.

### US-2: Pontuar a janela pelo caminho medido (Priority: P1)

- **AC-2.1**: Given o instante < `fim`, When `sombra seco`, Then recusa `janela_aberta`.
- **AC-2.2**: Given a janela fechada, When `sombra seco`, Then:
  - sessões com linha 0 que não é `ancora`, linha corrompida ou `turno_idx` repetido são descartadas inteiras e
    contadas;
  - cada `turno` válido é montado com o par `{ancora da sua sessão, turno}` pela `PonteJev`, dentro da `captura()`,
    sem rede e com o molde `camada_j_par.json`;
  - os corpos redigidos vão a `sombra/seco.jsonl`;
  - grava-se `seco_meta.json` com os sha256 de `redacao.py`, `jev.py` e da lista de bloqueio, e com o maior `mtime`
    dos spools;
  - roda a varredura de resíduo (REQ-007);
  - saem a estimativa (a fórmula `jev.estimativa` sobre os itens a pontuar, mais a sonda), o gasto da janela, o gasto
    do mês e o código da amostra.
- **AC-2.3**: Given a varredura com resíduo num corpo, When `seco`, Then esse item sai da pontuação com
  `residuo_excluido`, contado no relatório, e nunca é enviado. O resto segue.
- **AC-2.4**: Given o seco, When o código da amostra é gerado, Then:
  - a amostra são 20 itens sorteados com `random.Random(20261010).sample` sobre os ids enviáveis, em ordem
    (sessão, `turno_idx` numérico), ou todos, se houver menos de 20;
  - o código é `sha256(json([sha256(seco.jsonl), ids]))[:12]`: o mesmo spool dá o mesmo código, e um seco diferente dá
    outro;
  - `ver-amostra` mostra os 20 corpos redigidos, exatamente como sairiam.
- **AC-2.5**: Given o código revisado, When `sombra rodar --confirmo <codigo>`, Then:
  - recusa `kit_mudou` se os sha de `seco_meta.json` divergem dos atuais;
  - recusa `spool_mudou` se algum spool tem `mtime` maior que o registrado;
  - recusa `fila_montada` se `fila.json` existe;
  - senão, cada turno é pontuado na porta `C4`, com log auditado e com o `sha_enviado` conferido contra o seco
    (`envio_divergente`);
  - só números e shas vão a `escores.jsonl`;
  - item já pontuado é pulado;
  - `--refazer-falhas` repontua só `rede`, `timeout`, `http_408`, `http_429` e `http_5xx`;
  - as outras falhas são definitivas e contadas.
- **AC-2.6**: Given a estimativa dos itens que ainda faltam, somada ao gasto da janela (linhas do livro com `lote`
  começando por `camada-j-sombra-<inicio compacto AAAAMMDDTHHMMSSZ>`), acima de US$ 0,50, ou somada ao gasto do mês
  UTC (`livro.gasto_do_mes`, todos os corpora) acima de US$ 5, When `rodar`, Then recusa antes da sonda
  (`teto_da_janela_excedido` ou `envelope_excedido`). Não há override.
- **AC-2.7**: Given a sonda com `model` ≠ `jev-1.13.0`, sem resposta, ou `vazamento_pendente` no kit, When `rodar`,
  Then recusa com a causa.

### US-3: Rotular às cegas (Priority: P1)

- **AC-3.1**: Given infra pendente em `escores.jsonl` (falha retomável não repontuada), When `sombra rotular`, Then
  recusa `escores_incompletos`.
- **AC-3.2**: Given os escores completos, When `rotular` roda pela primeira vez, Then a fila é montada uma vez e
  gravada em `fila.json`:
  - **sinalizados**: P ≥ 0,77, P cru. Até 40, sorteados com `random.Random(20261010)` sobre os ids em ordem
    (sessão, `turno_idx` numérico);
  - **chamarizes**: até 20 com P < 0,77, sorteados com o mesmo gerador, na mesma ordem;
  - se um grupo tem menos itens que o teto, entram todos;
  - **ordem**: os itens são embaralhados com o mesmo gerador.
- **AC-3.3**: Given a fila, When um item aparece, Then são mostrados a âncora e o turno do spool local, e nada mais:
  nem P, nem grupo, nem posição. As respostas são `s`, `n` e `pular`, e `pular` conta como resposta.
- **AC-3.4**: Given rótulos gravados e nenhum veredito, When `rotular` roda de novo, Then retoma do primeiro item sem
  resposta. `--corrigir <id>` regrava, e vale a última linha por id. `rotulos.jsonl` guarda `{id, rotulo, instante}`,
  sem texto.

### US-4: Decidir pelo critério fixado (Priority: P1)

- **AC-4.1**: Given algum item sem resposta, When `sombra medir`, Then recusa `fila_incompleta`.
- **AC-4.2**: Given a fila completa, When `medir`, Then, **nesta ordem**:
  1. **inconclusivo**, se a fila de sinalizados está vazia, se os sinalizados têm s + n < 20, ou se os chamarizes têm
     s + n < 10;
  2. senão, calcula-se:
     - `prec_J` = s / (s + n) dos sinalizados;
     - `base` = s / (s + n) dos chamarizes;
     - Δ = `prec_J` − `base`, com IC 95 % por bootstrap de sessões (B = 10 000, semente `20261010`, IC percentil).
       Cada réplica reamostra sessões com reposição e recalcula as duas razões; réplica com 0/0 num grupo vale NaN e
       é descartada;
     - se mais de 5 % das réplicas forem NaN, o veredito é **inconclusivo** (`bootstrap_degenerado`);
  3. **avança**, se `prec_J` ≥ 0,35 **e** o limite inferior do IC de Δ > 0;
  4. **não avança**, nos outros casos.
- **AC-4.3**: Given a medida, When o veredito é gravado (`veredito.json` e
  `master-harness/docs/avaliacao/camada-j-sombra-resultado.md`), Then o relatório traz:
  - o veredito; `prec_J`, `base` e Δ com IC; a fração de réplicas descartadas;
  - os itens sinalizados (total e na fila), os chamarizes, os pulados e os excluídos por causa;
  - a taxa de disparo (sinalizados ÷ turnos pontuados), ao lado dos ~3,7 % da avaliação, só para leitura;
  - as contagens do hook, as sessões com spool e descartadas, e o gasto;
  - nenhum texto.
- **AC-4.4**: Given `veredito.json` existente, When `rotular --corrigir`, `rotular`, `medir`, `rodar` ou `seco`, Then
  recusa `veredito_gravado`. Há uma medida só.
- **AC-4.5**: Given qualquer veredito, When ele é gravado, Then nenhuma configuração ou código de produção muda. Os
  próximos passos (desligar a flag, abrir a spec de oferta, `purgar`) são impressos para o usuário fazer.

### US-5: Exclusão, limpeza e retenção (Priority: P2)

- **AC-5.1** (v4): Given a janela aberta, When `sombra excluir-sessao <id>` roda num terminal humano, Then o
  `<id>.jsonl` é renomeado e apagado (o hook acrescenta sem `O_CREAT`, então não o recria), e nasce o marcador sem
  texto `<id>.excluida`. Arquivo em uso no Windows recusa `arquivo_em_uso` (tente de novo).
- **AC-5.2** (v4): Given `veredito.json`, When `sombra purgar` roda num terminal humano, Then, nesta ordem:
  1. `janela.json` passa a `estado: "encerrada"`;
  2. os `<session_id>.jsonl`, os marcadores `.nova` e o `seco.jsonl` são apagados; um arquivo que não apaga é
     contado, os outros seguem, e o verbo sai 3 dizendo quantos ficaram;
  3. escores, rótulos, fila, contagens, metas e relatório ficam.
- **AC-5.3**: Given nenhum veredito, When `purgar`, Then recusa `sem_veredito`. A saída é
  `purgar --abandonar --motivo <texto>`, que faz o mesmo que o AC-5.2 e grava `veredito.json` com
  `{veredito: "inconclusivo", abandonada: true, motivo}`.
- **AC-5.4** (v4): Given `estado: "aberta"` 30 dias depois de `fim`, ou `estado: "encerrada"` com algum
  `<session_id>.jsonl` ou `seco.jsonl` ainda na pasta, When `mh saude`, Then aparece o aviso
  `spool_com_texto_retido`.

---

## Requirements

- **REQ-001**: o hook grava num spool local a âncora e os turnos humanos das sessões que começam com a janela aberta,
  atrás de `HARNESS_BRANCH_LAYER_J`. Sem rede, sem processo novo, sem ler a chave, sem mudar a saída do hook e com
  p95 a mais ≤ 20 ms. `consumidor: o lote do REQ-004`.
- **REQ-002**: com a flag desligada, ou a janela não aberta, nada muda e nada é escrito.
- **REQ-003**: a lista fixa de prefixos não humanos, no código do `branch_j_spool.py` e comparada por `startswith`
  literal depois de `lstrip()`:
  - `<task-notification>`
  - `<command-`
  - `<cross-session-message`
  - `<local-command`
  - `Base directory for this skill:`
  - `Caveat: The messages below`
  - `This session is being continued`
  - `[Request interrupted`
  - `Continue from where you left off.`

  O MIN_LEN é 12 code points depois de `strip()`. O sha da lista e do MIN_LEN é conferido contra o do `janela.json`
  (AC-1.1). `consumidor: REQ-001`.
- **REQ-004**: os verbos `mh avaliar camada-j sombra {abrir, status, excluir-sessao, seco, ver-amostra, rodar,
  rotular, medir, purgar}`, no master-harness e fora de `mh/s1/`. `consumidor: o usuário`.
- **REQ-005**: τ = 0,77 exato, comparado com o P cru. É o P do 25º par em ordem decrescente na avaliação offline
  (`3ef033c`).
- **REQ-006**: teto de US$ 0,50 por janela, sem override, e envelope de US$ 5 por mês UTC, somando todos os corpora.
  O gasto vai ao livro com `corpus: "camada-j-sombra"` e `lote: "camada-j-sombra-<inicio>-<instante do rodar>"`, os
  dois no formato compacto `AAAAMMDDTHHMMSSZ`.
- **REQ-007**: a varredura de resíduo é código novo, com teste de duas metades. Resíduo é qualquer ocorrência destes
  padrões no estado redigido:
  - chave Anthropic, OpenAI, GitHub, AWS, Google ou Slack;
  - JWT;
  - bloco PEM de chave privada;
  - `senha|password|passwd|pwd` seguido de `:` ou `=` e de um valor;
  - `Bearer <token>`;
  - credencial em URL;
  - e-mail;
  - (v4) CPF (formatado, ou 11 dígitos depois de "CPF"), número de cartão (13 a 19 dígitos que passam no Luhn) e
    telefone brasileiro com DDD e separador.

  Item com resíduo é excluído, nunca enviado (AC-2.3).
- **REQ-008**: no `abrir` ficam congelados: τ, semente, tamanhos da fila (40 + 20), critério, janela, filtro (pelo
  sha), molde `camada_j_par.json`, teto e `spec_commit`. A redação e a lista de bloqueio são as do kit no dia do seco,
  e o `rodar` recusa se mudarem entre o seco e ele (AC-2.5).
- **REQ-009**: o veredito é consumido pelo usuário. Com `avança`, abre-se a spec de **oferta**, que leva a decisão do
  gateway. Com `não avança` ou `inconclusivo`, o usuário desliga a flag, e a C4 fica registrada no
  `jev-integracao.md`. `consumidor: decisão do usuário`.
- **REQ-010**: `abrir`, `excluir-sessao`, `ver-amostra`, `rodar`, `rotular` e `purgar` só rodam num terminal humano:
  stdin é TTY e `CLAUDECODE` não está no ambiente. Em qualquer outro caso recusam `terminal_nao_humano`. É o que
  impede um agente de revisar, confirmar ou rotular no lugar do usuário, e impede que o texto do spool caia num
  transcript.

---

## Gateway (L-46)

**Decisão do usuário, 2026-10-10, no grill rodada 2.** O modo sombra é medida, não consumo de produção: nenhuma
decisão usa o J durante a janela, e quem chama o Jev é um lote Python do `mh`, da mesma classe da Onda 6. Por isso ele
não dispara a decisão B2. O gatilho fica na spec de oferta, quando o J passaria a decidir no hook. Essa é uma
interpretação explícita da L-46 e vai registrada no `jev-integracao.md`, junto da C4.

---

## Boundaries

### ALWAYS
- Redação, lista de bloqueio e guarda da chave pela `PonteJev` antes de qualquer envio, mais a varredura de resíduo.
- Revisão de amostra e rótulo só pelo usuário, em terminal humano (REQ-010).
- Pasta `sombra/` na casa padrão do `mh`, com ACL herdável só do usuário e fora do git.
- O relatório diz quantos itens ficaram de fora e por quê.

### NEVER
- Oferecer ramo, injetar contexto ou mostrar qualquer coisa ao usuário a partir do J durante a janela.
- Rede, processo novo, leitura da chave ou criação de diretório dentro do hook. O hook só escreve se a pasta e o
  `janela.json` já existirem.
- Ler a pasta `sombra/` com qualquer coisa além do hook (escrita), dos verbos `sombra` e do `mh saude` (só o
  `janela.json` e os nomes dos arquivos, v4). Ela não entra em vault, grafo, `signals.json`, `emissions.jsonl`, semente de ramo, transcript de
  sessão nem backup do harness.
- Editar `mh/s1/` ou `mh/replay/`.
- Mudar o que o REQ-008 congela; abrir uma segunda janela nesta spec; medir mais de uma vez.
- Passar do teto da janela ou do envelope.
- Mudar configuração ou código de produção a partir do veredito.

### ASK
- Deploy do harness4claude com o spool (D2): muda o hook de todas as sessões.
- `abrir` e ligar a flag.
- `rodar`, com o código da amostra.
- `excluir-sessao` e `purgar`.

---

## Nível de garantia

**A entrega pode ser afirmada como:** "a precisão do J nos turnos do meio de sessão que ele sinalizaria, contra uma
linha de base de turnos não sinalizados, as duas por rótulo cego do usuário, em sessões abertas numa janela de 14
dias, com IC por sessão".

**Não está incluído:**
- o recall: os chamarizes dão só um indício;
- a latência de produção do J;
- qualquer efeito no usuário;
- generalização para além deste usuário e desta janela;
- sessões abertas antes do `abrir`, retomadas ou compactadas.

---

## Suposições

- **ASSUMPTION-001** (usuário, 2026-10-10): spool com lote, fora do hook. Justifica: REQ-001, REQ-004, Gateway.
- **ASSUMPTION-002** (usuário, 2026-10-10): todos os projetos podem ir ao Jev, incluindo o `slb-mestrado-projeto`
  (L-43). Justifica: REQ-001, REQ-004.
- **ASSUMPTION-003** (usuário, 2026-10-10): janela de 14 dias, τ = 0,77, teto de 40 sinalizados. Justifica: REQ-005,
  REQ-008, AC-0.1, AC-3.2.
- **ASSUMPTION-004** (usuário, 2026-10-10): teto de US$ 0,50 por janela. Justifica: REQ-006, AC-2.6.
- **ASSUMPTION-005** (usuário, 2026-10-10, grill 1): "40 sinais" limita a fila de rótulo, não a coleta. Justifica:
  AC-2.1, AC-3.2.
- **ASSUMPTION-006** (usuário, 2026-10-10, grill 1): rótulo cego com 20 chamarizes; critério contra essa linha de
  base. Justifica: AC-3.2, AC-3.3, AC-4.2.
- **ASSUMPTION-007** (usuário, 2026-10-10, grill 2): o modo sombra não dispara o gateway (L-46). Justifica: Gateway,
  REQ-009.
- ~~**ASSUMPTION-008** (inferência): a âncora do spool é o mesmo prompt em que o sensor fixa a dele, só que inteiro, e
  só quando é humano.~~ **FALSA em 2026-10-10** (verify-multimodel, rodadas 1 e 2): a âncora do sensor é por
  worktree e é refeita quando sessões se alternam no mesmo cwd ou quando o branch-out reancora sem `session_id`. O
  remendo pelo transcript contou slash commands como prompts anteriores. Substituída pela ASSUMPTION-013.
- **ASSUMPTION-009** (inferência): os prefixos do REQ-003 cobrem os turnos não humanos vistos na avaliação. Prefixo
  novo grava como humano e aparece na rotulagem, onde se responde `pular`. Justifica: REQ-003.
- **ASSUMPTION-010** (inferência): ~165 prompts por dia, um teto que inclui texto injetado. Projeta ~US$ 0,16 e uns 80
  sinalizados. O pior caso, com todo turno truncado em 24 000 code points, cabe no teto até ~1 800 turnos. Volume ou
  taxa muito diferentes levam a `teto_da_janela_excedido` ou a `inconclusivo`, nunca a um veredito errado. Justifica:
  REQ-006, AC-4.2.
- **ASSUMPTION-011** (inferência): guardar o prompt cru por até 14 dias, mais o tempo até o `purgar`, não cria classe
  nova de exposição. O mesmo texto já está nos transcripts de `~/.claude/projects`, no mesmo perfil e sob a mesma
  política de backup da máquina. A pasta fica fora dos leitores do harness (NEVER). Justifica: AC-1.2, NEVER.
- **ASSUMPTION-012** (inferência): o `janela.json` editado à mão não é detectado. A proteção é contra acidente, não
  contra o próprio usuário. Justifica: REQ-008.
- **ASSUMPTION-013** (usuário, 2026-10-10, escalada): uma sessão pertence à amostra se o `SessionStart` dela (`source`
  `startup` ou `clear`) aconteceu com a janela aberta; a âncora é o primeiro prompt humano depois disso. É a
  definição da avaliação (primeiro prompt humano da sessão), sem depender da âncora do sensor nem do transcript.
  Justifica: AC-1.0, AC-1.2, AC-1.3.
- **ASSUMPTION-014** (usuário, 2026-10-10, escalada): CPF, cartão e telefone que escapam da redação do kit excluem o
  turno do envio (a redação do kit fica como está, T2). Justifica: REQ-007.

---

## Grill

### Rodada 1 (v1 → v2): 8 bloqueantes

| # | Pergunta, em resumo | Destino |
|---|---|---|
| 1 | onde começa a janela | `abrir` grava `janela.json`; o fim é início + 14 × 24 h UTC |
| 2 | vazamento por outro caminho | pasta na casa do `mh`; NEVER de leitores |
| 3 | `purgar` reabre a janela | `encerrada`; NEVER de segunda janela |
| 4–5 | âncora da sessão e na forma da avaliação | ASSUMPTION-008 |
| 6 | ordem do deploy | D2, conferido no `abrir` |
| 7 | ACL sem processo no hook | ACL herdável, aplicada no `abrir` |
| 8 | a varredura não existia | REQ-007, código novo |

### Rodada 2 (v2 → v3): 9 bloqueantes

| # | Pergunta, em resumo | Destino |
|---|---|---|
| 1 | onde ficam os prefixos | no código; o sha é conferido contra o `janela.json` (AC-1.1) |
| 2 | `pular` é resposta? | sim (AC-3.3) |
| 3 | precedência dos vereditos | ordem fixa no AC-4.2 |
| 4 | geração do código e `ver-amostra` | AC-2.4 |
| 5 | agente rotulando ou confirmando | REQ-010: terminal humano, sem `CLAUDECODE` |
| 6 | corrigir e medir de novo depois do veredito | AC-4.4: `veredito_gravado` |
| 7 | hook e `mh` em casas diferentes | AC-0.2: só a casa padrão |
| 8 | um resíduo travava a janela | AC-2.3: o item é excluído |
| 9 | a flag nunca chega ao hook | D3 conferida no `abrir`; teste de vida no dia 0 com `status` (AC-0.5) |

Altas e médias da rodada 2, resolvidas no texto:
- comparação de prefixo com caixa literal;
- contadores sem sessão válida;
- ordem numérica dos ids;
- falhas de infraestrutura nomeadas;
- estimativa do restante;
- a dependência do `HARNESS_BRANCH`;
- sessões anteriores, retomadas e compactadas fora (AC-1.3);
- `--abandonar` apaga o texto e registra;
- exclusão de sessão durante a janela;
- NaN no bootstrap;
- `rotular` bloqueado com infra pendente;
- fila menor que o teto;
- kit ou spool que mudam entre seco e rodar;
- `mh saude` nos arquivos impactados;
- chave verificada no `abrir`;
- teto de latência numérico.

Ficaram como limite declarado:
- edição manual do `janela.json` (ASSUMPTION-012);
- corrida de dois lotes de corpora diferentes sobre o envelope, já que um lote roda por vez, por regra de
  orquestração;
- preço fixo do kit (L-44 é projeção);
- deriva de calibração do J no meio de sessão: é o que a janela mede, e a taxa de disparo vai ao relatório.

---

## Out of Scope

- Fazer o J oferecer ramo no hook (spec de oferta) e o gateway.
- Mudar a camada A ou ligar a camada B.
- Pontuar durante a janela.
- Leque, `criteria` e outros moldes.
