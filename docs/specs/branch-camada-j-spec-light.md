# Camada J do Branch Keeper: o Jev julga o ramo (avaliação offline)

**Status:** aprovada pelo usuário em 2026-10-10 (confirmada na janela desta sessão às 08:41) · 2026-10-09 · task `t-20261009-142619139824` (L1-feature)
**Origem:** item C4 de `master-harness/docs/specs/jev-integracao.md:89`, cujo gatilho ("depois do selo-1 de P1")
disparou em 2026-10-01 (`fe86ed2`) sem dono. É a R2 da auditoria do uso do Jev de 2026-10-09, aprovada pelo usuário.
**Plano de avaliação:** [`branch-camada-j-avaliacao-plano.md`](branch-camada-j-avaliacao-plano.md)

## Objetivo

A camada A (regex) é hoje o portão de `ramo` no `branch_sensor`, e nos 703 pares rotulados ela acerta 2 de 25
disparos: precisão 0,080, recall 0,050 e AUC-PR 0,058, contra 0,057 do acaso. Esta spec mede, **offline e antes de
qualquer ligação**, se uma pergunta `noul` ao Jev separa ramo de continuação melhor que a camada A. O critério fica
fixado aqui, antes de existir qualquer escore.

## Requisitos

- **REQ-001: consumidor nomeado.** O consumidor é `scripts/branch_sensor.py::evaluate`, atrás da flag
  `HARNESS_BRANCH_LAYER_J`, desligada por padrão. Esta spec só produz o veredito que decide se essa ligação será
  escrita. O avaliador `mh/avaliacao/camada_j.py` (master-harness, fora de `mh/s1/`) tem um consumidor só: o gate de
  decisão do usuário. Se o veredito for `avança`, ele vai também à spec de ligação, a escrever
  (`branch-camada-j-ligacao-spec-light.md`).
- **REQ-002: a pergunta**, um `noul` com o texto fixado abaixo, num molde próprio e fora do kit. O estado é
  `{"ancora", "turno"}`, exatamente o que o sensor tem em `UserPromptSubmit`: a âncora da sessão e o prompt atual.
  > The state holds the opening message of a working session (`ancora`) and a later message from the same user
  > (`turno`). Does `turno` start a new line of work with a life of its own — something that could begin in a fresh
  > session without the context accumulated in this one? Answer no if `turno` continues, refines, corrects, approves,
  > or asks about the work in `ancora`.
- **REQ-003: redação antes do envio.** Toda chamada passa pela `PonteJev` do kit, importada sem edição, como faz a
  Onda 6. Ela aplica, nesta ordem, a `redacao.redigir`, a lista de bloqueio, a truncagem em 24 000 code points e a
  guarda da chave. Também grava o corpo redigido no log local auditado `enviados-C4.jsonl`. O modelo é fixo,
  `jev-1.13.0`, e o TLS é o padrão do Python. Antes da rodada real, o usuário revisa uma amostra dos corpos que o modo
  `seco` capturou sem rede. Na rodada real, o `sha_enviado` de cada item tem de bater com o do seco.
- **REQ-004: quarentena.** A pergunta é outra, diferente das duas de P1, e roda depois do selo-1 de P1. O princípio 2 e
  o L-43 permitem isso, mesmo que a população dos prompts se sobreponha à de P1. O avaliador não lê nem escreve
  nenhum destes: goldset, casa, selo, log `enviados-P1`/`-P5`, olhada ou `politica.json` de P1. Também não chama
  `jev.causas_da_fase`, que cobra o sentinela selado de P1/P5. As pré-condições são a sonda com o ID fixado, nenhum
  vazamento pendente e a revisão de `C4` em dia.
- **REQ-005: custo com teto.** O seco imprime a estimativa pela `jev.estimativa` e o gasto do mês (somando o livro da
  Onda 6). A rodada real recusa se a estimativa passar de **US$ 0,15** ou se o gasto do mês somado a ela passar do
  envelope de US$ 5 (L-44).

## Critério de sucesso (fixado antes da avaliação)

Arquivo `~/.claude/harness/calib/branch-labels.json`, sha256 `f2ec1909…ec14943`: 40 positivos e 663 negativos.
Camada A pelos `LAYER_A_PATTERNS` de `bd58ac4`. O escore J é o P(sim) do molde `par`. Seja k o número de disparos da
camada A no mesmo arquivo (hoje 25).

| # | Condição | Corte |
|---|---|---|
| S0 | cobertura | ≥ 95 % dos 703 pares com escore; senão, `inconclusivo` |
| S1 | precisão@k do J (os k pares de maior P) | ≥ 0,35, o `MIN_PRECISAO` já declarado em `calibrate_branch_layer_a.py`, ou seja, ≥ 9 de 25 |
| S2 | Δ precisão@k (J − A) | limite inferior do IC 95 % > 0 |
| S3 | Δ AUC-PR (J − controle de comprimento do turno) | limite inferior do IC 95 % > 0. O controle hoje dá 0,073 |

O IC é um bootstrap por sessão (`estatistica.bootstrap_pareado`, B = 10 000, semente `20261009`, IC percentil). Os
negativos vêm de 53 sessões, até 25 por sessão. O veredito é `avança` se S0–S3 passam, `não avança` se S0 passa e
algum de S1–S3 falha, e `inconclusivo` se S0 falha. Recall@k, AUC, Brier e ECE vão ao relatório e não decidem nada.
**Ablação, também relatada e sem decidir:** o molde `turno` (estado só com o turno). Se o IC do Δ AUC-PR (par − turno)
contiver 0, o J está julgando o estilo do turno e não a relação com a âncora. Isso vira ressalva obrigatória na spec de
ligação.

## Acceptance Criteria

- **AC-001** (REQ-003): Given um par com um segredo plantado, When roda o `seco`, Then o corpo capturado não contém o
  valor, a contagem da regra sobe e nenhuma conexão abre. O par sem segredo sai igual byte a byte.
- **AC-002** (REQ-003): Given o seco feito e o código da amostra, When `rodar --confirmo` recebe um código errado, ou
  algum `sha_enviado` difere do sha do seco, Then a rodada recusa (2) ou o item vira `envio_divergente`.
- **AC-003** (REQ-004): Given a avaliação inteira, When ela termina, Then `mh/s1/` não tem diff e nada na casa de P1 foi
  escrito. A única escrita do lado do kit é o log `enviados-C4` com a revisão dele.
- **AC-004** (REQ-005): Given uma estimativa acima de US$ 0,15, ou um gasto do mês somado à estimativa acima de US$ 5,
  When `rodar`, Then a rodada recusa antes da sonda.
- **AC-005** (critério): Given escores sintéticos construídos para cada caso, When `medir`, Then sai `avança`,
  `não avança` ou `inconclusivo` conforme S0–S3, de forma reprodutível pela semente, e sem texto de par nas saídas.

## Custo esperado

- **Avaliação**, pela `jev.estimativa` do kit: o molde `par` dá 1 165 026 tokens (US$ 0,049; 37 pares truncados) e
  o molde `turno` dá 734 170 tokens (US$ 0,031). Com a sonda, a rodada fica em ~US$ 0,08, sob o teto de US$ 0,15. O
  teto de chamadas é de 2 109 por molde.
- **Produção projetada** (não faz parte desta spec): até 5 105 prompts em 30 dias, um teto contado nos transcripts que
  inclui mensagens injetadas. A 1 657 tokens por par, a média do arquivo, isso dá ~US$ 0,36 por mês. No pior caso,
  todo prompt truncado em 24 000 code points, ~US$ 1,39 por mês. Os dois cabem no envelope junto com o Science.

## Gateway (L-46)

A avaliação offline não é consumidor de produção e não dispara o gateway. O gatilho é a spec de ligação: ela só pode ir
a approve-spec com a decisão B2 tomada, ou seja, `mh juizo servir` construído ou uma exceção declarada com causa, ação
e prazo. Fatos medidos que essa decisão tem de pesar:

- o `branch_sensor` roda no Python do sistema, não no venv do `mh`;
- cada hook é um processo novo, então todo prompt pagaria o TLS frio (641 ms a 8 s) em vez de 259 ms quente;
- o livro de gasto hoje é só da Onda 6 (`mh/hints/livro.py`);
- a auditoria é por porta.

## Boundaries

- **In:** esta spec, o plano de avaliação e, depois do OK, o avaliador em `mh/avaliacao/` com os testes dele. A rodada
  real espera um OK separado do usuário.
- **Out:** qualquer mudança no `branch_sensor`, no hook ou em `mh/s1/`; a escolha do limiar de produção, que fica para a
  spec de ligação, em modo sombra; leque e `criteria`.
- **ENTREGA:** um veredito com IC sobre rótulos de supervisão distante. Ele diz se o J separa *abertura de sessão nova*
  de *continuação*. Não prova que o J acha ramos no meio de uma sessão: os positivos são primeiros prompts escritos em
  contexto limpo (`branch_sensor.py:132-137`).

## Suposições

- **ASSUMPTION-001** (inferência): a instrução vai em inglês, a língua em que o fornecedor declara a melhor acurácia, e
  o estado fica em português. É um candidato só, sem escolha entre variantes. Justifica: REQ-002, S1–S3.
- **ASSUMPTION-002** (inferência): o avaliador mora em `mh/avaliacao/`, o lugar que o F1 reserva ao avaliador em código
  (`fabrica-plano.md:75`), e importa `mh.s1.estatistica` sem editar o kit. Justifica: REQ-001, REQ-004.
- **ASSUMPTION-003** (inferência): k é o volume de disparos da camada A, e não um limiar escolhido sobre o J. A
  comparação fica no mesmo volume de interrupções, sem ajustar nada aos dados. Justifica: S1, S2.
- **ASSUMPTION-004** (usuário, 2026-10-10, resolve a CLARIF-1): com `avança`, a spec de ligação começa em modo
  sombra. O J pontua e registra os prompts do meio de sessão, sem oferecer nada, e o rótulo manual vale só para os
  itens que ele sinalizar. Condições do usuário:
  - a chamada fica fora do caminho do hook, sem latência no prompt;
  - a flag começa desligada;
  - a redação roda antes do envio;
  - há teto de gasto por janela;
  - o critério e o tamanho da janela são fixados antes de olhar;
  - a âncora e o prompt ficam guardados localmente, para julgar depois.

  Justifica: REQ-001, ENTREGA, Gateway.
- **ASSUMPTION-005** (usuário, 2026-10-10): a rodada real sobre os 703 pares está liberada, a ~US$ 0,08 com teto de
  US$ 0,15. A revisão da amostra do seco, que o REQ-003 exige antes do envio, continua valendo. Justifica: REQ-003,
  REQ-005.
