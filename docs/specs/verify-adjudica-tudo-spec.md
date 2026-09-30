# Spec: o `pass` do verify não sai verde com achado aberto

**Status**: Grilled (rodada 1)
**Created**: 2026-09-30
**Updated**: 2026-09-30
**Branch**: `claude/verify-adjudica-tudo` (base `8cd6884`)
**Task**: `t-20260930-181933591685` (L2-architecture)
**Author**: AI-generated, reviewed by Leonardo

Insumos: [`CONTEXT.md`](../CONTEXT.md) (bloco "o `pass` do verify", L-01 a L-06),
[medição](verify-adjudica-tudo-medicao.md), [graph context](verify-adjudica-tudo-graph-context.md).
Grill: `wf-grill` `wf_88d4f13b-860` (5/5 lentes, 90 perguntas, 9 bloqueantes) —
respostas na seção "Grill".

---

## Executive Summary

A fase de verificação dos pipelines L2 roda o Workflow `wf-verify-multimodel` e
vai para DONE quando ele responde `pass: true`. Em três rodadas seguidas sobre o
mesmo diff ele respondeu `pass: true` com 49 achados abertos, dos quais 25 eram
defeito real — dois deles caminhos de evidência verde forjada. O `pass` só conta
crítico/alto confirmado, e nenhum revisor rotulou nada de crítico/alto. Esta
spec muda três coisas: o `pass` passa a ser `false` enquanto houver achado não
adjudicado; o DONE passa a exigir destino escrito para cada achado não
adjudicado de cada rodada, conferido por script; e os revisores passam a
atribuir severidade pela consequência do defeito — esta última só entra se uma
rodada de medição mostrar que ela muda a severidade dos defeitos conhecidos.

## Context

### Como é hoje

- `scripts/workflows/wf-verify-multimodel.js` abre cinco revisores em paralelo
  (`:62-88`), deduplica (`:116-127`), adjudica só `critical`/`high` com
  `juiz-alto-risco` (`:142-166`) e devolve
  `pass: criticals.length === 0 && nosMortos.length === 0` (`:183`).
- Medium/low saem com `adjudicado: false` e contam em `nao_adjudicados`
  (`:186`, `:197-205`), sem efeito no `pass`. O filtro é "não é `critical` nem
  `high`" (`:147`): severidade fora do conjunto também cai em não adjudicado.
- Já antes do corte de adjudicação (`tests/data/wf-verify-multimodel-antes-de-d017a6b.js:151-158`)
  o `pass` contava só crítico/alto confirmado.
- Só o prompt de `spec-coverage` fala de severidade ("P1 ausente=critical",
  `:66`). Os outros quatro não têm critério.
- O prompt do adjudicador (`:159-162`) manda refutar a alegação e mostra a
  "Severidade alegada", sem dizer se severidade errada é motivo de refutação.
- `skills/harness-workflow/SKILL.md:329`: "Se `pass: true` (sem findings
  critical/high) → pipeline completo, ir para DONE".
- Único leitor de `pass` em código: a seção de verify do `SKILL.md`. No
  `master-harness` o Workflow aparece só em texto de spec herdado do template
  ("Zero findings críticos"), sem código que leia o retorno (busca de
  2026-09-30).

### Files/Modules Impactados

- `scripts/workflows/wf-verify-multimodel.js`: fórmula do `pass`, rubrica nos
  prompts, `repo_root`, `n` em cada achado, `summary`, instrução ao adjudicador.
- `scripts/checar_triagem.py` (**novo**): confere destino de cada achado não
  adjudicado e ausência de bloqueio na última rodada.
- `skills/harness-workflow/SKILL.md`: contrato do Workflow (`:180`), exemplo de
  invocação (`:174`), "Verify + gap-closure" (`:251-257`) e "verify (com
  gap-closure)" (`:324-347`).
- `tests/wf_run_harness.cjs`: grava o prompt de cada `agent()`.
- `tests/test_verify_por_tabela.py`: inverte `test_pass_invariante_lote_so_medium_low`.
- `tests/test_verify_pass_honesto.py` e `tests/test_checar_triagem.py` (**novos**).
- `tests/data/wf-verify-multimodel-antes-de-verify-adjudica-tudo.js` (**novo**):
  blob de `wf-verify-multimodel.js` em `8cd6884`, metade-controle.
- `tests/data/verify-rodadas-2026-09-30/medicao-b/` (**novo**): lista
  pré-registrada, saída e casamento da medição de B.
- `C:/Users/LHarden2/.claude/CLAUDE.md` (global, fora do repo): item "Fan-out
  (Workflow)", só no deploy.
- `skills/write-spec/templates/spec-template.md:193`,
  `docs/specs/verify-por-tabela-spec-light.md` (AC-3), `README.md:38-40`.

### Dependências

- Host do Claude Code, formato do `.output` do Workflow: `{summary, agentCount,
  logs, result, workflowProgress}`, com `workflowProgress[]` trazendo `tokens`,
  `startedAt` e `agentType` por agente (medido nas três rodadas).
- Tool `Workflow`: `scriptPath` só aceita arquivo no diretório da sessão ou em
  diretório adicionado; recusou o plugin em cache e o worktree deste ramo
  (sondado em 2026-09-30). O parâmetro `script` aceita o conteúdo inline.
- `tools/orfaos.py`: guarda de função pública sem raiz; um `SKILL.md` que manda
  rodar o script conta como raiz.
- Balde da task: `python "<PR>/scripts/harness_paths.py" --cwd … --session-id …`
  devolve o diretório durável da sessão (`<STATE_DIR>`).

---

## User Stories

### US-1: `pass` honesto (Priority: P1) — MVP

**Como** orquestrador do pipeline (`harness-workflow`)
**Quero** que o Workflow responda `pass: false` enquanto houver achado não adjudicado
**Para que** um `pass: true` signifique "nada aberto", e não "nada que alguém rotulou de crítico"

**Why this priority**: é a causa do defeito medido (L-01); sem isto nada mais muda o DONE.

**Independence**: testável sozinha pelo harness de comportamento do Workflow.

**Acceptance Criteria**:

- **AC-1.1**: Given um lote só com achados medium/low, sem nó morto, When o Workflow calcula o retorno, Then `pass` é `false`, `critical_count` é 0 e `nao_adjudicados` é o número desses achados.
- **AC-1.2** (metade-controle): Given o mesmo lote, When roda o blob de `8cd6884` fixado em `tests/data`, Then `pass` é `true`. É um teste verde que afirma o comportamento antigo; com AC-1.1, prova que a mudança muda comportamento.
- **AC-1.3**: Given nenhum achado e nenhum nó morto, When o Workflow calcula o retorno, Then `pass` é `true`.
- **AC-1.4**: Given só achados `high` e todos refutados pelo adjudicador, When o Workflow calcula o retorno, Then `pass` é `true` e `nao_adjudicados` é 0.
- **AC-1.5**: Given achados medium/low, When o Workflow roda, Then nenhum `agent()` de adjudicação abre para eles (L-03, o corte fica).
- **AC-1.6**: Given qualquer lote, When o Workflow devolve `findings`, Then cada achado traz `n` igual à sua posição (1, 2, 3…) na lista.
- **AC-1.7**: Given `nao_adjudicados > 0`, When o Workflow monta o `summary`, Then o texto diz quantos achados aguardam triagem e nomeia `checar_triagem.py` e a seção de verify do `harness-workflow` — uma sessão com o texto antigo da skill no contexto lê no próprio retorno o que fazer.
- **AC-1.8**: Given um achado com severidade fora de {`critical`, `high`}, When o Workflow separa os achados, Then ele conta como não adjudicado (erra bloqueando).
- **AC-1.9**: Given o prompt do adjudicador, When é montado, Then diz que severidade alegada errada não é motivo de refutação — refuta-se só o defeito que não existe.

**Edge Cases**:
- Nó morto e achado não adjudicado juntos: `pass: false`, e `nos_mortos` continua listado.
- Achado `high` confirmado junto com medium/low: `pass: false` pelos dois motivos; a rodada é de bloqueio e os medium/low dela também recebem destino.

---

### US-2: DONE exige destino escrito, conferido por script (Priority: P1) — MVP

**Como** agente que fecha a fase de verificação
**Quero** um checador que reprove listando os achados sem destino
**Para que** o DONE só aconteça quando cada achado não adjudicado de cada rodada tiver sido decidido por escrito

**Why this priority**: sem isto, US-1 só troca o `pass` de lugar — o agente leria `false` e não saberia sair do laço (L-05).

**Independence**: o checador é testável sozinho, com arquivos de entrada sintéticos.

**Formato da tabela** (seção `## Destino dos achados não adjudicados` do
`closure-plan.md`, até o próximo cabeçalho `## `, fora de bloco de código):

```
| Rodada | # | Achado | Destino | Referência |
|---|---|---|---|---|
| wf_6bd9e71a-c4f | 10 | Exit code is read from the first | consertado | G1/D2, test_codigo_ancorado_no_fim |
```

- **Rodada**: o id do run do Workflow (`wf_…`), igual ao nome do arquivo da cópia.
- **#**: o `n` do achado.
- **Achado**: início do título (pelo menos 20 caracteres, ou o título inteiro se for menor), comparado sem diferença de caixa e de espaço.
- **Destino**: `consertado`, `limite` ou `nao-defeito` (aceita `não-defeito`), os três de L-01.
- **Referência**: pelo menos 7 caracteres visíveis; não pode ser o marcador que o checador imprime nem `n/a`, `-`, `?`, `ok`, `tbd`, `todo`.

**Acceptance Criteria**:

- **AC-2.1**: Given uma rodada com os não adjudicados 3, 4 e 7, sem bloqueio, e a tabela com linha válida para os três, When roda o checador, Then sai 0.
- **AC-2.2**: Given a mesma rodada sem a linha do 7, When roda o checador, Then sai 1, lista o 7 com o título e imprime a linha pronta para copiar, com destino `<consertado|limite|nao-defeito>` e referência `<teste, commit ou motivo>`.
- **AC-2.3**: Given a linha impressa em AC-2.2 colada sem edição, When roda o checador, Then sai 1.
- **AC-2.4**: Given uma linha com destino fora dos três, When roda o checador, Then sai 1 e diz qual linha e qual valor.
- **AC-2.5**: Given uma referência vazia, curta demais ou da lista proibida, When roda o checador, Then sai 1.
- **AC-2.6**: Given uma linha cujo `#` não é achado não adjudicado da rodada, When roda o checador, Then sai 1.
- **AC-2.7**: Given uma linha cujo início de título não bate com o título do achado daquele `#`, When roda o checador, Then sai 1 (tabela de outra execução não passa).
- **AC-2.8**: Given duas linhas para o mesmo `(rodada, #)`, When roda o checador, Then sai 1.
- **AC-2.9**: Given uma linha de tabela fora da seção, ou dentro de bloco de código, When roda o checador, Then ela é ignorada.
- **AC-2.10**: Given a última rodada com `critical_count > 0` ou `nos_mortos` não vazio, When roda o checador, Then sai 1 e diz que o bloqueio se resolve pelo gap-closure.
- **AC-2.11**: Given várias rodadas no diretório de cópias, When roda o checador, Then confere a tabela de todas e o bloqueio só da última, a de maior `workflowProgress[].startedAt` (na falta, a de mtime maior).
- **AC-2.12**: Given o `closure-plan.md` ausente, When há achado pendente, Then sai 1 com todas as linhas prontas; When não há achado pendente nem bloqueio, Then sai 0.
- **AC-2.13**: Given uma cópia ilegível, sem `findings`, ou com `n` diferente da posição, When roda o checador, Then sai 2 com a causa.
- **AC-2.14**: Given um achado sem o campo `adjudicado`, When o checador separa os pendentes, Then ele conta como não adjudicado.
- **AC-2.15**: Given a saída no formato do host (`{result: {findings}}`) ou no formato do retorno (`{findings}`), When roda o checador, Then as duas são lidas.
- **AC-2.16**: Given o `SKILL.md` do `harness-workflow`, When se lê a seção de verify, Then ela descreve o protocolo de REQ-F7 e REQ-F8 com os comandos literais de cópia e do checador.
- **AC-2.17** (verificado por leitura, fora da suíte): Given o deploy aprovado pelo usuário, When o `CLAUDE.md` global é editado, Then o item "Fan-out (Workflow)" mantém "adjudica só achados críticos e altos" e acrescenta a frase de L-03.

**Edge Cases**:
- Limite que reaparece numa rodada seguinte com outro `#`: ganha linha nova; a referência pode apontar a decisão anterior (`ver wf_… #4`).
- Rodada sem achado não adjudicado e sem bloqueio: não exige tabela.

---

### US-3: rubrica de severidade por consequência (Priority: P1, condicionada a US-4)

**Como** revisor do Workflow
**Quero** um critério de severidade pela consequência do defeito
**Para que** defeito que forja evidência ou aceita o que devia recusar chegue ao adjudicador e bloqueie

**Why this priority**: ataca a razão de a adjudicação nunca abrir — 0 `high` em 25 defeitos reais (L-02). Entra só se US-4 passar (L-06).

**Acceptance Criteria**:

- **AC-3.1**: Given o Workflow rodando, When os cinco revisores são chamados, Then o prompt de cada um contém a constante da rubrica, inteira.
- **AC-3.2** (metade-controle): Given o blob de `8cd6884`, When os cinco revisores são chamados, Then nenhum prompt contém a primeira linha da constante da rubrica.
- **AC-3.3**: Given o prompt do adjudicador, When se procura `rationale`, "justificativa" ou a rubrica, Then não há ocorrência.
- **AC-3.4**: Given `spec-coverage`, When o prompt é montado, Then a regra "P1 ausente=critical" continua, e a rubrica diz que ela vale para requisito sem implementação, e que requisito implementado sem teste é `low` salvo defeito demonstrado.

---

### US-4: medir a rubrica antes de ela entrar (Priority: P1)

**Como** usuário que aprovou B condicionada
**Quero** uma rodada do Workflow novo sobre o código exato da rodada 1
**Para que** a rubrica entre por dado, não por expectativa (L-04, L-06)

**Acceptance Criteria**:

- **AC-4.1**: Given a lista de defeitos conhecidos pré-registrada e commitada antes da rodada, When a rodada roda, Then a lista não muda depois.
- **AC-4.2**: Given um worktree destacado em `578e0bc`, When o script do ramo roda inline com os cinco arquivos da rodada 1, `spec_path: docs/specs/evidencia-em-segundo-plano-spec.md` e `repo_root` apontando para o worktree, Then a saída é copiada para `medicao-b/` com o sha256 do script usado.
- **AC-4.3**: Given a saída, When um `juiz-alto-risco` em janela limpa casa cada achado com a lista sem ver a severidade, Then o CSV registra achado → defeito conhecido (ou nenhum) e marca achado que cita código ausente em `578e0bc` (contaminação).
- **AC-4.4**: Given o CSV, When se conta, Then um defeito conhecido "saiu `high`/`critical`" se o maior rótulo de revisor entre os achados casados com ele for `high` ou `critical`; se pelo menos metade dos defeitos conhecidos distintos re-achados saiu assim, a rubrica fica; senão, sai.
- **AC-4.5**: Given a rodada, When termina, Then a medição registra o custo (soma de `workflowProgress[].tokens` e número de adjudicadores) e, à parte, quantos dos promovidos o adjudicador confirmou.

---

### US-5: documentação coerente com o contrato (Priority: P2)

**Como** leitor futuro do harness
**Quero** que template, README e spec antiga digam o contrato novo
**Para que** nenhum documento continue afirmando o comportamento de antes

**Acceptance Criteria**:

- **AC-5.1**: Given `spec-template.md:193`, When se lê o critério de sucesso do Workflow, Then ele diz "`wf-verify-multimodel` com `pass: true`, ou `checar_triagem.py` saindo 0 sobre todas as rodadas da task".
- **AC-5.2**: Given `verify-por-tabela-spec-light.md`, When se lê a AC-3, Then ela está marcada como superada por esta spec, sem ser apagada.
- **AC-5.3**: Given `README.md:38-40`, When se lê a descrição do Workflow, Then ela diz que a adjudicação cobre crítico/alto e que nenhum achado fica sem destino.

---

## Current System

### Entry Points
- Em produção: tool `Workflow` com `scriptPath` do plugin, chamado pela fase `verify-multimodel` da skill.
- Neste ramo, antes do merge: tool `Workflow` com o script do ramo passado inline (`script`).
- `SKILL.md` lê `pass`, e só `pass`, para decidir DONE.

### Data Flow

```
revisores (5) ──findings──▶ dedupe ──┬── critical/high ──▶ adjudicador ──▶ confirmados ──▶ criticals ──┐
                                     └── medium/low ───────────────────────────────▶ nao_adjudicados   │
                                                                                                        ▼
                                                              pass = criticals == 0 && nosMortos == 0
```

### Constraints Existentes
- Aresta do adjudicador descontaminada (alegação + local + critério).
- Censo de nós em todo `parallel(`.
- `.js` sem CR (`.gitattributes`: `*.js` com `eol=lf`, vale para `tests/data/` também).
- Só testes afetados, um processo por vez; suíte completa só no merge.
- Qualquer escrita de arquivo depois da evidência de teste a torna histórico (memória "fechar task tem duas corridas"); `python` conta como escrita para o hook.

---

## Requirements

### Functional

- [ ] **REQ-F1**: `pass` é `true` só com zero crítico/alto confirmado, zero nó morto e zero achado não adjudicado; o texto `nosMortos.length === 0` continua na fórmula. [traces: US-1]
- [ ] **REQ-F2**: medium/low (e qualquer severidade fora de {`critical`, `high`}) continuam fora da adjudicação, com `adjudicado: false` e contados em `nao_adjudicados`. [traces: US-1]
- [ ] **REQ-F3**: cada achado devolvido traz `n`, sua posição 1-based em `findings`. [traces: US-1, US-2]
  consumidor: a coluna `#` da tabela de destino e `checar_triagem.py`.
- [ ] **REQ-F4**: o `summary`, com `nao_adjudicados > 0`, diz quantos aguardam triagem e nomeia o checador e a seção de verify. [traces: US-1]
- [ ] **REQ-F5**: argumento opcional `repo_root`; presente, o prompt dos revisores **e do adjudicador** diz que os caminhos são relativos a ele, que leitura e git rodam lá e que o escopo são os arquivos listados, sem comparar com outro ramo. Ausente, o prompt não tem essa frase. [traces: US-4]
  consumidor: a invocação do Workflow na seção de verify do `SKILL.md` (passa a raiz do worktree da task) e a rodada de medição de US-4.
- [ ] **REQ-F6**: o adjudicador é instruído a refutar só defeito inexistente, nunca por severidade alegada. `VERDICT_SCHEMA` não muda. [traces: US-1, US-3]
- [ ] **REQ-F7**: `scripts/checar_triagem.py --plano <closure-plan.md> --saidas <arquivo ou diretório>…` implementa o formato e as AC-2.1 a AC-2.15. Sai 0 (tudo decidido e sem bloqueio na última rodada), 1 (pendente, inválido ou bloqueio, com a lista e as linhas prontas) ou 2 (entrada ilegível). A rodada é o nome do arquivo sem extensão. [traces: US-2]
  consumidor: a seção de verify do `skills/harness-workflow/SKILL.md`, que manda rodá-lo antes de cada rodada nova e antes do `complete`.
- [ ] **REQ-F8**: protocolo de rodadas na seção de verify do `SKILL.md` [traces: US-2]:
  1. Rodada = uma execução do Workflow. No máximo 3 por task (a inicial e mais 2), contador único para bloqueio e triagem.
  2. Logo depois da rodada, copiar o `.output` do host (caminho da notificação) para `<STATE_DIR>/verify/<run id>.json`.
  3. Bloqueio (crítico/alto confirmado ou nó morto) → gap-closure de hoje. Todo não adjudicado da rodada, com ou sem bloqueio, recebe linha na tabela.
  4. Conserto de código (bloqueio ou triagem) → nova rodada obrigatória, se ainda houver rodada; antes dela, o checador não pode apontar pendente de triagem.
  5. Nova rodada só por nó morto, sem código mudado, não conta no limite; um segundo nó morto seguido vai ao gate `escalation`.
  6. Na 3ª rodada: bloqueio → gate `escalation`; conserto pedido pela triagem fecha com teste que pegue o defeito, e o relatório de verificação lista esses "consertos sem rodada de revisão".
  7. Ordem no fim: tabela → checador saindo 0 → evidência de teste final → `complete`.
  8. No DONE, a tabela de destinos é mostrada ao usuário.
- [ ] **REQ-F9**: o item "Fan-out (Workflow)" do `CLAUDE.md` global ganha a frase de L-03, com a origem datada, no deploy e com OK do usuário. [traces: US-2]
- [ ] **REQ-F10**: uma constante de rubrica é anexada ao prompt dos cinco revisores. Julga-se pela consequência se o defeito acontecer, não pela chance nem pelo tamanho do conserto; vale a faixa mais alta cuja cláusula se aplica. `critical`: forjar verificação, evidência ou aprovação; perder ou corromper dado persistido. `high`: aceitar o que devia recusar ou recusar em definitivo o que devia aceitar; mudar estado num caminho de recusa ou erro; gravar resultado errado sem aviso; derrubar hook, gate ou fluxo principal. `medium`: comportamento errado em caso de borda, visível e recuperável. `low`: lacuna de teste sem defeito demonstrado, desempenho sem impacto medido, manutenção. Lacuna de teste que esconde defeito demonstrado leva a severidade do defeito; requisito P1 sem implementação é `critical`, implementado sem teste é `low` salvo defeito demonstrado. [traces: US-3]
- [ ] **REQ-F11**: `tests/wf_run_harness.cjs` grava o prompt de cada chamada de revisão e de adjudicação. [traces: US-1, US-3]
  consumidor: `tests/test_verify_pass_honesto.py` (infra de teste, não capacidade de produção); os testes de REQ-F5 e REQ-F6 o usam mesmo se a rubrica sair.
- [ ] **REQ-F12**: a rodada de medição de US-4 segue a seção "Medição de B" e decide a permanência de REQ-F10 por L-06. Se a rubrica sair: a constante e os testes de AC-3.1, AC-3.2 e AC-3.4 são removidos no mesmo commit, US-3 fica marcada "não entrou" com o número medido, e AC-3.3 continua. [traces: US-4]
- [ ] **REQ-F13**: template, spec antiga e README atualizados conforme US-5. [traces: US-5]

### Non-Functional

- [ ] **REQ-NF1 (Custo)**: US-1 e US-2 não abrem `agent()` a mais **por rodada**; abrem rodadas: tarefa com conserto passa a ter até 3 rodadas (~1,1 a 1,7 M tokens de verify), decisão do usuário no grill. O custo extra de US-3 por rodada é o dos promovidos a `high`, medido em US-4.
- [ ] **REQ-NF2 (Descontaminação)**: a rubrica entra no prompt do revisor; o do adjudicador não recebe `rationale` nem a rubrica.
- [ ] **REQ-NF3 (Invocabilidade)**: o `.js` editado continua sem CR.
- [ ] **REQ-NF4 (Direção do erro)**: toda dúvida do checador (campo ausente, formato estranho) erra reprovando.

### Medição de B (procedimento de REQ-F12)

Congelado antes da rodada: a rubrica (sha256 do script), a lista de defeitos e a
regra de casamento. Uma rodada só; nada é refeito depois de ver severidades.

1. `578e0bc` já está fixado pela ref local `medicao/verify-rodada1` (criada em 2026-09-30; sem push).
2. Pré-registrar `medicao-b/conhecidos.csv`: cada defeito consertado em G1–G6, E1–E5 ou F1–F7 cujo código defeituoso existe em `578e0bc` (conferido lendo `git show 578e0bc:<arquivo>`), com mecanismo, local e a severidade que a rubrica daria. Limites não entram. Commitar.
3. `git worktree add --detach C:/Users/LHarden2/Documents/projects/harness4claude/.claude/worktrees/medicao-rodada1 578e0bc`.
4. Rodar o script do ramo inline, com `changed_files` = `scripts/evidencia_em_segundo_plano.py`, `scripts/transactional_state.py`, `hooks/harness-transactional.py`, `tests/test_evidencia_em_segundo_plano.py`, `.gitattributes`; `spec_path: docs/specs/evidencia-em-segundo-plano-spec.md`; `repo_root` = o worktree do passo 3.
5. Copiar a saída do host e o sha256 do script para `medicao-b/`.
6. Casamento por `juiz-alto-risco` em janela limpa: recebe `conhecidos.csv` e os achados sem severidade; devolve achado → defeito (ou nenhum) e contaminação. Gravar `medicao-b/casamento.csv`.
7. Juntar severidades, contar pela regra de AC-4.4, escrever a seção 7 de `verify-adjudica-tudo-medicao.md` com resultado, custo e o número de confirmados pelo adjudicador; aplicar L-06.
8. Remover o worktree do passo 3; a ref fica.

---

## Boundaries

### ALWAYS
- Manter a aresta do adjudicador descontaminada.
- Manter `censoNos` em todo `parallel(`.
- Metade-controle com o blob de `8cd6884` fixado por hash em `tests/data`, nunca `git show main:`.
- Rodar a função de produção (o script, o Workflow) para produzir número.
- Rodar a medição e o verify desta task com o script do ramo, inline.

### NEVER
- Abrir adjudicação para medium/low (L-03).
- Tocar em `scripts/state_cli.py`, `scripts/transactional_state.py` ou hooks (L-05).
- Mudar `FINDINGS_SCHEMA` ou `VERDICT_SCHEMA`.
- Filtrar na rodada seguinte, no Workflow ou na tabela, achado já declarado limite (medição §3.4).
- Deploy no cache do plugin, ou edição do `CLAUDE.md` global, sem merge e sem OK do usuário.
- Rodar a medição de B ou o verify desta task com o script do cache.
- Editar a cópia da saída de uma rodada.
- Refazer a medição de B, mudar a rubrica depois de congelada, ou refazer o casamento depois de ver severidades.
- Push da ref `medicao/verify-rodada1`.

### ASK
- Resultado da medição na fronteira de L-06: exatamente metade, ou menos de dois defeitos conhecidos re-achados.
- Nó morto na rodada de medição.
- Host recusando o script inline, ou `.output` com formato diferente do medido.

---

## Nível de garantia

**Esta feature entrega:** um `pass` que só sai verde sem achado aberto, e um DONE
que exige, conferido por script, uma decisão escrita para cada achado não
adjudicado de cada rodada da task, e nenhum bloqueio na última.

**E não cobre:**
- A **verdade** do destino. O checador confere que cada achado tem destino e
  referência; não confere que um "limite" é mesmo limite. A medição §3.3 mostra a
  triagem errando quatro vezes nesse sentido; a tabela vai ao usuário no DONE,
  sem gate.
- A **integridade da cópia**. O checador amarra cada linha ao achado pelo id da
  rodada e pelo início do título, mas confia que o arquivo no balde é a saída do
  host sem edição: o host não assina a saída.
- A **obrigatoriedade** fora da skill. O `state_cli complete` não chama o
  checador; quem não segue a seção de verify fecha a task sem ele.
- A **cobertura** da revisão. A rubrica muda o rótulo do que os revisores acham;
  não faz achar o que não acharam.
- A revisão dos consertos da 3ª rodada: têm teste, não têm rodada; ficam listados.
- Adjudicação de medium/low: continua só para crítico/alto.

---

## [NEEDS CLARIFICATION]

- [x] ~~**CLARIF-1**: L-04 fixou a medição em `5d2086b`. O código exato que a
  rodada 1 revisou (12:52) é o `578e0bc` — o merge de 12:47:40 no ramo da mãe,
  antes do rebase. O `5d2086b` é a mesma feature reaplicada sobre `db42807`, com o
  `transactional_state.py` de outra base. Recomendação: medir em `578e0bc`, que é
  a única comparação que isola a rubrica.~~ → ASSUMPTION-008.
- [x] ~~**CLARIF-2** (grill #10, #66): conserto de código obriga nova rodada?~~ → ASSUMPTION-009.
- [x] ~~**CLARIF-3** (grill #22, #27, #35): conserto pedido na última rodada, sem rodada que o revise?~~ → ASSUMPTION-010.
- [x] ~~**CLARIF-4** (grill #6, #58, #62, #69): onde fica a cópia da saída de cada rodada?~~ → ASSUMPTION-011.

---

## Suposições

- **ASSUMPTION-001**: medium/low continuam fora da adjudicação · decidido 2026-09-30 por usuário (L-03) · justifica: REQ-F2, AC-1.5
- **ASSUMPTION-002**: o `pass` falso por triagem não pode depender de uma rodada nova sair verde, porque toda rodada medida trouxe 11–23 achados · decidido 2026-09-30 por inferência · justifica: REQ-F7, REQ-F8
- **ASSUMPTION-003**: o `.output` do Workflow existe durante a sessão que o gerou (as três rodadas da mãe ainda estavam no disco horas depois); a cópia para o balde é feita logo após a rodada, sem depender da retenção · decidido 2026-09-30 por inferência · justifica: REQ-F8 (passo 2)
- **ASSUMPTION-004**: sem `repo_root`, revisores e adjudicador leem os caminhos relativos no diretório da sessão, que nesta medição é o worktree da mãe com os consertos já aplicados · decidido 2026-09-30 por inferência (o prompt da rodada 1 só lista caminhos relativos) · justifica: REQ-F5, AC-4.2
- **ASSUMPTION-005**: numerar os achados pela posição na lista basta, porque a lista de uma rodada não muda depois de devolvida; o início do título amarra a linha à execução · decidido 2026-09-30 por inferência · justifica: REQ-F3, AC-2.7, AC-2.13
- **ASSUMPTION-006**: a garantia de DONE é de protocolo mais checador, sem trava no `state_cli` · decidido 2026-09-30 por usuário (L-05) · justifica: REQ-F7, REQ-F8
- **ASSUMPTION-007**: critério de entrada da rubrica é metade dos defeitos conhecidos re-achados em `high`/`critical` · decidido 2026-09-30 por usuário (L-06) · justifica: REQ-F12, AC-4.4
- **ASSUMPTION-008**: a rodada 1 (`wf_6bd9e71a-c4f`, 12:52–12:57 -0300) revisou o `578e0bc` (merge das 12:47:40 no ramo da mãe, antes do rebase; próximo commit às 13:12). Conferido: as linhas citadas em r1 #10, #11, #19 e #21 batem com `578e0bc` · decidido 2026-09-30 por usuário (CLARIF-1) · justifica: REQ-F12, AC-4.2
- **ASSUMPTION-009**: conserto de código obriga nova rodada, até 3 por task, contador único — as rodadas 2 e 3 da mãe acharam 5 e 9 defeitos reais novos · decidido 2026-09-30 por usuário (grill) · justifica: REQ-F8, REQ-NF1
- **ASSUMPTION-010**: conserto pedido pela triagem da 3ª rodada fecha com teste e fica listado como "conserto sem rodada de revisão", sem gate · decidido 2026-09-30 por usuário (grill) · justifica: REQ-F8 (passo 6)
- **ASSUMPTION-011**: a cópia da saída de cada rodada fica no balde da task (`<STATE_DIR>/verify/<run id>.json`), fora de qualquer repo; a tabela fica no `closure-plan.md` · decidido 2026-09-30 por usuário (grill) · justifica: REQ-F7, REQ-F8
- **ASSUMPTION-012**: um defeito conhecido conta como `high`/`critical` pelo maior rótulo de revisor entre os achados casados com ele — é esse rótulo que decide se ele chega ao adjudicador · decidido 2026-09-30 por inferência · justifica: AC-4.4
- **ASSUMPTION-013**: os subagentes do Workflow leem arquivos fora do diretório da sessão (as cinco lentes do grill leram a spec no worktree deste ramo) · decidido 2026-09-30 por inferência · justifica: REQ-F5, AC-4.2

---

## Grill (rodada 1, `wf_88d4f13b-860`)

Resolvidas por decisão do usuário: CLARIF-2 a 4 (acima). Resolvidas por fato ou
por inferência registrada, com a pergunta de origem:

| Tema | Perguntas | Resolução |
|---|---|---|
| Qual script roda | #5, #9, #30, #37, #56 | Tool recusa `scriptPath` fora do cwd (sondado); script do ramo inline; NEVER com o do cache |
| Checador liberando com bloqueio | #7, #12 | Checador sai 1 com bloqueio na última rodada (AC-2.10); AC-5.1 reescrita |
| Rodadas anteriores sem destino | #8, #10 | Toda rodada é triada; o checador lê todas (AC-2.11) |
| Laço e última rodada | #3, #22, #27, #35, #65 | REQ-F8 passos 1, 5 e 6 |
| Entrada do checador | #6, #26, #34, #48 | Rodada = nome do arquivo; coluna do título (AC-2.7); limite declarado no Nível de garantia |
| Destino `cobertura` | #13, #60 | Sai; ficam os três de L-01; tabela vai ao usuário no DONE |
| Linha pronta que passa colada | #20 | AC-2.3 |
| Âncora da tabela, código de saída, duplicata, referência | #46–#50, #76–#79 | Formato da tabela, AC-2.8, AC-2.9, AC-2.12, AC-2.13 |
| Campo ausente ou severidade estranha | #67, #68 | AC-1.8, AC-2.14, REQ-NF4 |
| Adjudicador e severidade | #24, #33 | REQ-F6, AC-1.9 |
| `repo_root` no adjudicador | #15, #25 | REQ-F5 |
| Universo, desempate e casamento da medição | #1, #2, #17, #23, #28, #82, #85 | Lista pré-registrada (passo 2), ASSUMPTION-012, juiz cego (passo 6), congelamento |
| Árvore da rodada 1 | #29, #39 | ASSUMPTION-008 com as linhas conferidas |
| Base do diff, contaminação | #16, #71, #83 | Prompt diz que o escopo são os arquivos listados; juiz marca contaminação |
| Commit alcançável | #38, #59 | Ref local criada; worktree removido no fim; sem push |
| Fonte do custo | #57, #90 | `workflowProgress[].tokens` (Dependências) |
| `CLAUDE.md` global | #4, #32, #44, #54, #80 | Só no deploy, com OK; AC-2.17 por leitura, fora da suíte |
| Evidência e ordem | #42, #63, #72, #84 | REQ-F8 passo 7 |
| Rubrica sem entrar | #18, #36, #74 | REQ-F12 |
| Metade-controle | #19, #81 | AC-1.2 e AC-3.2 reescritas |
| Precedência na rubrica, regra P1 | #31, #55 | REQ-F10, AC-3.4 |
| Outros leitores do `pass` | #45, #64, #73 | Busca feita (Context); `summary` autoexplicativo (AC-1.7) |
| Blob de controle | #53, #88 | `8cd6884`; `*.js` com `eol=lf` cobre `tests/data` |
| Custo por task | #66 | REQ-NF1 declara até 3 rodadas |

Ficam como limite declarado, sem mudança: estabilidade de uma rodada só (#75 —
a decisão do usuário foi uma rodada; o ASK cobre a fronteira); revisores rodando
testes em paralelo no `repo_root` e hooks da sessão vendo esses testes (#21,
#70, #89 — já acontece em todo verify de hoje, e mexer nos hooks é NEVER);
modelo dos agentes igual ao das 12:52 (#86 — não controlável; os `agentType`
são os mesmos e ficam no CSV). Adiado: teto de adjudicadores por rodada (#61),
decidido depois da medição, com o número de promovidos na mão.

---

## Success Criteria

- [ ] AC de US-1, US-2 (menos AC-2.17), US-3 e US-4 verificados: testes automatizados para US-1 a US-3; CSV e medição escrita para US-4
- [ ] `checar_triagem.py` com teste para cada código de saída
- [ ] Metades-controle AC-1.2 e AC-3.2 verdes contra o blob de `8cd6884`
- [ ] `tools/orfaos.py` sem acusar `checar_triagem.py`
- [ ] Verify desta task com o script do ramo: `pass: true`, ou checador saindo 0 sobre todas as rodadas
- [ ] AC-2.17 conferida por leitura no deploy
- [x] CLARIF-1 a 4 resolvidas e registradas como suposição

---

## Spec Metadata (machine-readable)

```json
{
  "spec_id": "verify-adjudica-tudo",
  "version": 2,
  "harness_version": "v3",
  "generated_by": "write-spec skill",
  "generated_at": "2026-09-30T21:10:00-03:00",
  "updated_at": "2026-09-30T22:40:00-03:00",
  "priorities": ["P1", "P2"],
  "requirement_count": 17,
  "user_story_count": 5,
  "needs_clarification_count": 0,
  "assumption_count": 13,
  "assumptions_by_inference": 6,
  "grilled": true,
  "grill_rounds": 1
}
```
