# Medição — o que o `wf-verify-multimodel` deixa de adjudicar

Ramo `verify-adjudica-tudo` (sessão `6baae6ca`), aberto da sessão `6b0a4567`
(evidência de suíte em segundo plano). Data: 2026-09-30.

## 1. Pergunta e resposta

**Pergunta.** Dos achados `medium`/`low` que o Workflow devolve com
`adjudicado: false` e que não pesam no `pass`, quantos eram defeito real?

**Resposta.** **25 de 49 (51%)** viraram conserto de código. Somando as 8
lacunas de teste fechadas, **33 de 49 (67%)** pediram ação. As três rodadas
responderam `pass: true, critical_count: 0`.

| Destino | Achados |
|---|---|
| Defeito real, consertado na rodada | 21 |
| Declarado limite pela triagem, consertado numa rodada seguinte | 4 |
| Lacuna de cobertura fechada com teste (código já certo) | 8 |
| Limite mantido, com motivo escrito | 15 |
| Não é defeito do diff | 1 |
| **Total** | **49** |

## 2. Dados

- Saídas e journals das rodadas `wf_6bd9e71a-c4f` (23 achados),
  `wf_47eeb4d4-140` (11) e `wf_2c3bbdfb-161` (15), copiados do host em
  `tests/data/verify-rodadas-2026-09-30/` antes da limpeza de `%TEMP%`
  (commit `47d07b9`).
- Destino de cada achado: `tests/data/verify-rodadas-2026-09-30/destinos.csv`,
  classificado contra `docs/closure-plan.md` e as seções 4, 5 e 8 de
  `docs/specs/evidencia-em-segundo-plano-verification.md`. A coluna `ref`
  aponta o item de conserto (G1–G6, E1–E5, F1–F7) ou o parágrafo do limite.
- A contagem sai por script, que antes confere severidade e quantidade de cada
  linha do CSV contra o `workflow.output` da rodada. Nenhuma linha divergiu.
- Dimensão de cada achado: casada pelo título com o evento `result` do
  `journal.jsonl` de cada revisor.

## 3. Resultado

### 3.1 Por severidade

| Severidade | Achados | Defeito real | Acionáveis (defeito + cobertura) | Limite |
|---|---|---|---|---|
| medium | 15 | 10 (67%) | 13 (87%) | 2 |
| low | 34 | 15 (44%) | 20 (59%) | 13 |
| high / critical | **0** | — | — | — |

Nenhum dos 49 achados foi rotulado `high` ou `critical`, inclusive os 25
defeitos reais. Como a adjudicação só abre para `high`/`critical`, ela não
abriu nenhuma vez nas três rodadas. A severidade também não separa defeito de
ruído: 44% dos `low` eram defeito real.

### 3.2 Por dimensão do revisor

| Dimensão (agente) | Achados | Defeito real | Cobertura | Limite |
|---|---|---|---|---|
| correctness (`analise-complexa`) | 7 | **7** | 0 | 0 |
| security (`juiz-alto-risco`) | 3 | **3** | 0 | 0 |
| regressions (`analise-complexa`) | 10 | 6 | 0 | 3 |
| edge-cases (`analise-complexa`) | 17 | 9 | 0 | 8 |
| spec-coverage (`analise-complexa`) | 12 | 0 | 8 | 4 |

Os dois caminhos de **evidência verde forjada** vieram do revisor de segurança
(`juiz-alto-risco`, opus · high) e saíram com severidade baixa:

- rodada 1 #11, `medium`: bloco de `<task-notification>` forjado pela
  descrição do job (conserto G2/D1);
- rodada 3 #7, **`low`**: bloco forjado no `cwd`/`gitBranch` da entrada do host
  vira evidência verde (conserto F1, mutante M22).

### 3.3 A triagem sem adjudicação também errou

A sessão-mãe triou os não adjudicados à mão, um por um. Quatro que ela declarou
limite foram consertados numa rodada seguinte, quando outro revisor levantou o
mesmo ponto de novo:

| Achado | Declarado limite em | Consertado em |
|---|---|---|
| r1 #18 `registrar_lancamento` sem proteção; aviso promete captura | iteração 1 | F4 + F5 (rodada 3) |
| r1 #21 `complete` que falha pode ter gravado dados | iteração 1 | E1 (rodada 2) |
| r1 #23 import de símbolos privados de `trabalho_em_voo` | iteração 1 | F7 (rodada 3) |
| r2 #11 o mesmo import | iteração 2 | F7 (rodada 3) |

### 3.4 Repetição e duplicata

- Limites já declarados voltaram na rodada seguinte: 5 dos 11 da rodada 2 e 4
  dos 15 da rodada 3. Um deles (o import privado) era defeito: suprimir limite
  declarado teria escondido o conserto F7.
- O dedupe do Workflow (arquivo + 40 primeiros caracteres do título) não pegou
  duplicatas semânticas: r1 #8 = #13, r1 #10 = #12, r3 #4 = #8, r3 #6 = #9,
  r3 #11 = #14. Achados distintos: 44.

## 4. Custo medido

Duas métricas, cada uma usada só para o que mede:

- **`tokens` do host** (campo de `workflowProgress` no `.output`): 557 382,
  573 890 e 573 528 por rodada — cerca de 111–115 mil por revisor. É o número
  que a sessão-mãe citou.
- **Tokens novos por transcript** (`input` + `cache_creation` + `output`,
  por mensagem de assistente, sem `cache_read`): a única disponível para
  adjudicadores, porque nenhuma das três rodadas abriu um. Nesta métrica os
  revisores das três rodadas custaram de 76 a 120 mil (um, 281 mil).

Adjudicadores já rodados na máquina: **352**, em 8 runs, todos opus.

| Medida (tokens novos por adjudicador) | Mediana | Média |
|---|---|---|
| `cache_creation` (carga de contexto) | 74 024 | 81 595 |
| `output` | 9 808 | 10 096 |
| total | 84 924 | 91 721 |

- Um adjudicador custa perto de um revisor. O piso é a carga de contexto
  (87% da mediana), que não depende do modelo.
- Veredictos: `is_real = true` em **289 de 321** (90%). Como filtro de falso
  positivo, a adjudicação derruba cerca de 1 em 10.
- `is_real` não separa "consertar" de "aceitar como limite": os 15 limites
  mantidos são comportamentos reais que a triagem decidiu não mudar.

## 5. Opções, com custo sobre as mesmas três rodadas

Custo em tokens novos, com o piso medido de ~85 mil por agente.

| # | Opção | Tokens extras nas 3 rodadas | Muda a regra do `CLAUDE.md`? | O que resolve | O que não resolve |
|---|---|---|---|---|---|
| A | **`pass` honesto**: `pass: true` só com zero achado não adjudicado; a skill só vai a DONE quando cada não adjudicado tem destino escrito (consertado, limite com motivo, não-defeito) | 0 (a triagem fica no laço principal, como a mãe já fez nas 3 rodadas) | Não | O `pass` deixa de dizer "nada relevante" quando há 23 achados abertos | A triagem do laço principal erra (§3.3) |
| B | **Rubrica de severidade** no prompt dos revisores, por consequência (aceitar o que devia recusar, evidência forjada, recusa que muda estado → `high`) | 0 direto; ~85 mil por achado promovido a `high` | Não | A escala colapsada (0 `high` em 25 defeitos reais), que impede a adjudicação de abrir | Quanto seria promovido não está medido: exige rerodar uma rodada com a rubrica |
| C | **Adjudicar medium/low em lote** (`juiz-alto-risco`, lotes de ~8, só alegação + local + critério) | 7 agentes (3 + 2 + 2): ~0,6 a 0,9 M, estimativa, porque o custo de ler 8 alegações num agente não foi medido | Sim | Tira a triagem do laço principal | Refuta ~10%; não decide limite × conserto |
| D | Adjudicar cada medium/low individualmente | 49 agentes: ~4,2 M (2,2× a 4,6× a revisão, por rodada) | Sim | O mesmo que C, sem lote | É o custo que a regra de 2026-09-24 existe para barrar |
| E | Adjudicar medium/low com modelo mais barato | Os mesmos ~4,2 M de D: o piso é carga de contexto; só o preço por token cai | Sim | — | Veredito está no degrau de risco alto da tabela de roteamento |

**Recomendação: A + B.** A conserta o contrato sem custo de token e sem mexer
na regra; B ataca a causa de a adjudicação nunca abrir. C vale só se o usuário
quiser tirar a triagem do laço principal, sabendo que o adjudicador confirma
90% do que recebe e não decide limite. D e E custam o que a regra proíbe.

Não recomendado: suprimir na rodada seguinte os limites já declarados (§3.4).

## 6. Limites desta medição

- A classificação de destino é leitura de `closure-plan.md` e do relatório de
  verificação da sessão-mãe; a sessão-mãe reproduziu cada defeito e matou um
  mutante por conserto (31 de 31), mas "limite" é decisão dela, não prova de
  ausência de defeito (§3.3 mostra quatro erros nesse sentido).
- Três rodadas sobre um único diff. Não há controle de outra feature.
- O custo de C é estimativa; o de B não foi medido.
