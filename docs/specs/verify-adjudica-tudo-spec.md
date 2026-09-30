# Spec: o `pass` do verify não sai verde com achado aberto

**Status**: Draft
**Created**: 2026-09-30
**Updated**: 2026-09-30
**Branch**: `claude/verify-adjudica-tudo` (base `8cd6884`)
**Task**: `t-20260930-181933591685` (L2-architecture)
**Author**: AI-generated, reviewed by Leonardo

Insumos: [`CONTEXT.md`](../CONTEXT.md) (bloco "o `pass` do verify", L-01 a L-06),
[medição](verify-adjudica-tudo-medicao.md), [graph context](verify-adjudica-tudo-graph-context.md).

---

## Executive Summary

A fase de verificação dos pipelines L2 roda o Workflow `wf-verify-multimodel` e
vai para DONE quando ele responde `pass: true`. Em três rodadas seguidas sobre o
mesmo diff ele respondeu `pass: true` com 49 achados abertos, dos quais 25 eram
defeito real — dois deles caminhos de evidência verde forjada. O `pass` só conta
crítico/alto confirmado, e nenhum revisor rotulou nada de crítico/alto. Esta
spec muda três coisas: o `pass` passa a ser `false` enquanto houver achado não
adjudicado; o DONE passa a exigir destino escrito para cada um, conferido por
script; e os revisores passam a atribuir severidade pela consequência do defeito
— esta última só entra se uma rodada de medição mostrar que ela muda a
severidade dos defeitos conhecidos.

## Context

### Como é hoje

- `scripts/workflows/wf-verify-multimodel.js` abre cinco revisores em paralelo
  (`:62-88`), deduplica (`:116-127`), adjudica só `critical`/`high` com
  `juiz-alto-risco` (`:142-166`) e devolve
  `pass: criticals.length === 0 && nosMortos.length === 0` (`:183`).
- Medium/low saem com `adjudicado: false` e contam em `nao_adjudicados`
  (`:186`, `:197-205`), sem efeito no `pass`.
- Já antes do corte de adjudicação (`tests/data/wf-verify-multimodel-antes-de-d017a6b.js:151-158`)
  o `pass` contava só crítico/alto confirmado.
- Só o prompt de `spec-coverage` fala de severidade ("P1 ausente=critical",
  `:66`). Os outros quatro não têm critério.
- `skills/harness-workflow/SKILL.md:329`: "Se `pass: true` (sem findings
  critical/high) → pipeline completo, ir para DONE".

### Files/Modules Impactados

- `scripts/workflows/wf-verify-multimodel.js`: fórmula do `pass`, rubrica nos
  prompts, `repo_root`, `n` em cada achado, `summary`.
- `scripts/checar_triagem.py` (**novo**): confere destino de cada achado não
  adjudicado.
- `skills/harness-workflow/SKILL.md`: contrato do Workflow (`:180`), exemplo de
  invocação (`:174`), "Verify + gap-closure" (`:251-257`) e "verify (com
  gap-closure)" (`:324-347`).
- `tests/wf_run_harness.cjs`: grava o prompt de cada `agent()`.
- `tests/test_verify_por_tabela.py`: inverte `test_pass_invariante_lote_so_medium_low`.
- `tests/test_verify_pass_honesto.py` e `tests/test_checar_triagem.py` (**novos**).
- `tests/data/wf-verify-multimodel-antes-de-verify-adjudica-tudo.js` (**novo**):
  blob de hoje, metade-controle.
- `C:/Users/LHarden2/.claude/CLAUDE.md` (global, fora do repo): item "Fan-out
  (Workflow)".
- `skills/write-spec/templates/spec-template.md:193`,
  `docs/specs/verify-por-tabela-spec-light.md` (AC-3), `README.md:38-40`.

### Dependências

- Host do Claude Code: formato do `.output` do Workflow (`{summary, agentCount,
  logs, result, workflowProgress}`), medido nas três rodadas.
- `tools/orfaos.py`: guarda de função pública sem raiz; um `SKILL.md` que manda
  rodar o script conta como raiz.

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
- **AC-1.2**: Given o mesmo lote, When roda o código de hoje (blob fixado em `tests/data`), Then `pass` é `true` — metade-controle que prova que AC-1.1 muda comportamento.
- **AC-1.3**: Given nenhum achado e nenhum nó morto, When o Workflow calcula o retorno, Then `pass` é `true`.
- **AC-1.4**: Given só achados `high` e todos refutados pelo adjudicador, When o Workflow calcula o retorno, Then `pass` é `true` e `nao_adjudicados` é 0.
- **AC-1.5**: Given achados medium/low, When o Workflow roda, Then nenhum `agent()` de adjudicação abre para eles (L-03, o corte fica).
- **AC-1.6**: Given qualquer lote, When o Workflow devolve `findings`, Then cada achado traz `n` igual à sua posição (1, 2, 3…) na lista.
- **AC-1.7**: Given `nao_adjudicados > 0`, When o Workflow monta o `summary`, Then o texto diz quantos achados aguardam triagem.

**Edge Cases**:
- Nó morto e achado não adjudicado juntos: `pass: false`, e `nos_mortos` continua listado.
- Achado `high` confirmado junto com medium/low: `pass: false` pelos dois motivos; o caminho é bloqueio.

---

### US-2: DONE exige destino escrito, conferido por script (Priority: P1) — MVP

**Como** agente que fecha a fase de verificação
**Quero** um checador que reprove listando os achados sem destino
**Para que** o DONE só aconteça quando cada achado não adjudicado tiver sido decidido por escrito

**Why this priority**: sem isto, US-1 só troca o `pass` de lugar — o agente leria `false` e não saberia sair do laço (L-05).

**Independence**: o checador é testável sozinho, com arquivos de entrada sintéticos.

**Acceptance Criteria**:

- **AC-2.1**: Given uma saída do Workflow com os achados não adjudicados 3, 4 e 7 e um `closure-plan.md` com linha válida para os três, When roda `checar_triagem.py`, Then sai 0.
- **AC-2.2**: Given a mesma saída e um `closure-plan.md` sem a linha do 7, When roda o checador, Then sai 1, lista o 7 com o título do achado e imprime a linha de tabela pronta para copiar.
- **AC-2.3**: Given uma linha com destino fora de {`consertado`, `cobertura`, `limite`, `nao-defeito`}, When roda o checador, Then sai 1 e diz qual linha e qual valor.
- **AC-2.4**: Given uma linha com referência vazia ou ainda com o marcador `<...>`, When roda o checador, Then sai 1.
- **AC-2.5**: Given uma linha para um `n` que não é achado não adjudicado daquela rodada, When roda o checador, Then sai 1 (número digitado errado não passa em silêncio).
- **AC-2.6**: Given duas linhas para o mesmo `(rodada, n)` com destinos diferentes, When roda o checador, Then sai 1.
- **AC-2.7**: Given o arquivo de saída ausente, ou um JSON sem `findings`, When roda o checador, Then sai 2 com a causa.
- **AC-2.8**: Given a saída no formato do host (`{result: {findings}}`) ou no formato do retorno (`{findings}`), When roda o checador, Then as duas são lidas.
- **AC-2.9**: Given uma saída de rodada antiga sem o campo `n`, When roda o checador, Then `n` é a posição do achado na lista.
- **AC-2.10**: Given o `SKILL.md` do `harness-workflow`, When se lê a seção de verify, Then ela descreve os caminhos bloqueio e triagem, manda copiar a saída do host para `docs/specs/<slug>-verify-<rodada>.json` e manda rodar o checador antes do `complete`, com o comando literal.
- **AC-2.11**: Given o `CLAUDE.md` global, When se lê o item "Fan-out (Workflow)", Then ele mantém "adjudica só achados críticos e altos" e acrescenta que achado não adjudicado não libera o `pass` e sai com destino escrito (L-03).

**Edge Cases**:
- Rodada sem achado não adjudicado: o checador sai 0 sem exigir tabela.
- Linha de outra rodada no mesmo `closure-plan.md`: ignorada para esta rodada.

---

### US-3: rubrica de severidade por consequência (Priority: P1, condicionada a US-4)

**Como** revisor do Workflow
**Quero** um critério de severidade pela consequência do defeito
**Para que** defeito que forja evidência ou aceita o que devia recusar chegue ao adjudicador e bloqueie

**Why this priority**: ataca a razão de a adjudicação nunca abrir — 0 `high` em 25 defeitos reais (L-02). Entra só se US-4 passar (L-06).

**Acceptance Criteria**:

- **AC-3.1**: Given o Workflow rodando, When os cinco revisores são chamados, Then o prompt de cada um contém a mesma rubrica de severidade.
- **AC-3.2**: Given o blob de hoje (metade-controle), When os cinco revisores são chamados, Then nenhum prompt contém a rubrica.
- **AC-3.3**: Given o prompt do adjudicador, When se procura `rationale` ou "justificativa", Then não há ocorrência (`test_adjudicador_nao_recebe_rationale` segue verde).
- **AC-3.4**: Given `spec-coverage`, When o prompt é montado, Then a regra "P1 ausente=critical" continua lá, junto com a rubrica.

---

### US-4: medir a rubrica antes de ela entrar (Priority: P1)

**Como** usuário que aprovou B condicionada
**Quero** uma rodada do Workflow novo sobre o código exato da rodada 1
**Para que** a rubrica entre por dado, não por expectativa (L-04, L-06)

**Acceptance Criteria**:

- **AC-4.1**: Given um worktree destacado no commit que a rodada 1 revisou, When o Workflow novo roda com os mesmos cinco arquivos, a mesma spec e `repo_root` apontando para esse worktree, Then a saída é copiada para `tests/data/verify-rodadas-2026-09-30/medicao-b/`.
- **AC-4.2**: Given a saída, When cada achado é casado com a lista de consertos G1–G6, E1–E5, F1–F7 e com os limites, Then um CSV registra achado → defeito conhecido (ou nenhum) e severidade.
- **AC-4.3**: Given o CSV, When se conta, Then se pelo menos metade dos defeitos conhecidos distintos re-achados sai `high`/`critical`, a rubrica fica; senão, a rubrica sai do script e o resultado fica escrito na medição.
- **AC-4.4**: Given a rodada, When ela termina, Then a medição registra o custo (tokens do host e número de adjudicadores abertos).

---

### US-5: documentação coerente com o contrato (Priority: P2)

**Como** leitor futuro do harness
**Quero** que template, README e spec antiga digam o contrato novo
**Para que** nenhum documento continue afirmando o comportamento de antes

**Acceptance Criteria**:

- **AC-5.1**: Given `spec-template.md:193`, When se lê o critério de sucesso do Workflow, Then ele diz "`pass: true`, ou todo achado não adjudicado com destino (`checar_triagem.py` saindo 0)".
- **AC-5.2**: Given `verify-por-tabela-spec-light.md`, When se lê a AC-3, Then ela está marcada como superada por esta spec, sem ser apagada.
- **AC-5.3**: Given `README.md:38-40`, When se lê a descrição do Workflow, Then ela não afirma que só crítico/alto conta.

---

## Current System

### Entry Points
- Tool `Workflow` com `scriptPath` do plugin, chamada pela fase `verify-multimodel` da skill.
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
- `.js` dos workflows sem CR (`.gitattributes` com `eol=lf`).
- Só testes afetados, um processo por vez; suíte completa só no merge.

---

## Requirements

### Functional

- [ ] **REQ-F1**: `pass` é `true` só com zero crítico/alto confirmado, zero nó morto e zero achado não adjudicado; o texto `nosMortos.length === 0` continua na fórmula. [traces: US-1]
- [ ] **REQ-F2**: medium/low continuam fora da adjudicação, com `adjudicado: false` e contados em `nao_adjudicados`. [traces: US-1]
- [ ] **REQ-F3**: cada achado devolvido traz `n`, sua posição 1-based em `findings`. [traces: US-1, US-2]
  consumidor: a tabela de destino do `closure-plan.md` e `checar_triagem.py`.
- [ ] **REQ-F4**: o `summary` diz quantos achados aguardam triagem quando `nao_adjudicados > 0`. [traces: US-1]
- [ ] **REQ-F5**: argumento opcional `repo_root`; quando presente, o prompt de cada revisor diz que os caminhos são relativos a ele e que git, leitura e testes rodam lá. Sem ele, o prompt é o de hoje. [traces: US-4]
  consumidor: a invocação do Workflow na seção de verify do `SKILL.md` (passa a raiz do worktree da task) e a rodada de medição de US-4.
- [ ] **REQ-F6**: `scripts/checar_triagem.py --saida <json> --rodada <rótulo> <closure-plan.md>` confere, para cada achado não adjudicado da saída, uma linha de tabela `| <rótulo> | <n> | <destino> | <referência> |` com destino em {`consertado`, `cobertura`, `limite`, `nao-defeito`} e referência preenchida. Sai 0 (tudo decidido), 1 (falta ou inválido, com a lista e as linhas prontas para copiar) ou 2 (entrada ilegível). [traces: US-2]
  consumidor: a seção de verify do `skills/harness-workflow/SKILL.md`, que manda rodá-lo antes do `complete`.
- [ ] **REQ-F7**: a seção de verify do `SKILL.md` descreve dois caminhos: **bloqueio** (crítico/alto confirmado ou nó morto → gap-closure de hoje) e **triagem** (só não adjudicados → destino por achado; conserto de código faz nova rodada e conta como iteração, máximo 2; na última iteração a triagem fecha sem rodada nova). DONE = última rodada sem bloqueio e checador saindo 0 para ela. [traces: US-2]
- [ ] **REQ-F8**: a skill manda copiar a saída do host da rodada para `docs/specs/<slug>-verify-<rodada>.json` e rodar o checador sobre a cópia. [traces: US-2]
- [ ] **REQ-F9**: o item "Fan-out (Workflow)" do `CLAUDE.md` global ganha a frase de L-03, com a origem datada. [traces: US-2]
- [ ] **REQ-F10**: uma constante de rubrica é anexada ao prompt dos cinco revisores; ela classifica pela consequência se o defeito acontecer: forjar verificação, evidência ou aprovação, perder ou corromper dado persistido → `critical`; aceitar o que devia recusar ou recusar em definitivo o que devia aceitar, mudar estado num caminho de recusa ou erro, gravar resultado errado sem aviso, derrubar hook, gate ou fluxo principal → `high`; comportamento errado em caso de borda plausível, visível e recuperável → `medium`; lacuna de teste sem defeito demonstrado, desempenho sem impacto medido, manutenção → `low`; lacuna de teste que esconde defeito demonstrado leva a severidade do defeito. [traces: US-3]
- [ ] **REQ-F11**: `tests/wf_run_harness.cjs` grava o prompt de cada chamada de revisão e de adjudicação. [traces: US-3]
  consumidor: `tests/test_verify_pass_honesto.py` (infra de teste; o harness é código de teste, não capacidade de produção).
- [ ] **REQ-F12**: a rodada de medição de US-4 segue o procedimento da seção "Medição de B" e decide a permanência de REQ-F10 pelo critério de L-06. [traces: US-4]
- [ ] **REQ-F13**: template, spec antiga e README atualizados conforme US-5. [traces: US-5]

### Non-Functional

- [ ] **REQ-NF1 (Custo)**: US-1 e US-2 não abrem nenhum `agent()` a mais por rodada. O custo extra de US-3 é só o dos achados promovidos a `high`, medido em US-4.
- [ ] **REQ-NF2 (Descontaminação)**: a rubrica entra no prompt do revisor; o do adjudicador não recebe `rationale` nem a rubrica.
- [ ] **REQ-NF3 (Invocabilidade)**: o `.js` editado continua sem CR.

### Medição de B (procedimento de REQ-F12)

1. Fixar o commit da rodada 1 com uma ref local (`git branch medicao/verify-rodada1 578e0bc`), para ele não ser coletado (ASSUMPTION-008).
2. `git worktree add --detach .claude/worktrees/medicao-rodada1 578e0bc`.
3. Rodar o Workflow do ramo com `changed_files` = os cinco da rodada 1
   (`scripts/evidencia_em_segundo_plano.py`, `scripts/transactional_state.py`,
   `hooks/harness-transactional.py`, `tests/test_evidencia_em_segundo_plano.py`,
   `.gitattributes`), `spec_path: docs/specs/evidencia-em-segundo-plano-spec.md`
   e `repo_root` = o worktree do passo 2.
4. Copiar a saída do host para `tests/data/verify-rodadas-2026-09-30/medicao-b/`.
5. Classificar cada achado em `medicao-b/destinos.csv` (achado → G/E/F ou limite
   ou nenhum, com severidade).
6. Contar defeitos conhecidos distintos re-achados e quantos saíram `high`/`critical`.
7. Escrever o resultado e o custo na seção 7 de `verify-adjudica-tudo-medicao.md`
   e aplicar a decisão de L-06.

---

## Boundaries

### ALWAYS
- Manter a aresta do adjudicador descontaminada.
- Manter `censoNos` em todo `parallel(`.
- Metade-controle com o código de hoje fixado por hash de blob em `tests/data`, nunca `git show main:`.
- Rodar a função de produção (o script, o Workflow) para produzir número.

### NEVER
- Abrir adjudicação para medium/low (L-03).
- Tocar em `scripts/state_cli.py`, `scripts/transactional_state.py` ou hooks (L-05).
- Mudar `FINDINGS_SCHEMA` ou `VERDICT_SCHEMA`.
- Suprimir na rodada seguinte achado já declarado limite (medição §3.4).
- Deploy no cache do plugin sem merge e sem OK do usuário.

### ASK
- Se a rodada de medição der resultado na fronteira de L-06 (exatamente metade, ou menos de dois defeitos re-achados), antes de decidir.
- Se o formato do `.output` do host divergir do medido.

---

## Nível de garantia

**Esta feature entrega:** um `pass` que só sai verde sem achado aberto, e um DONE
que exige, conferido por script, uma decisão escrita para cada achado não
adjudicado.

**E não cobre:**
- A **verdade** do destino. O checador confere que cada achado tem destino e
  referência; não confere que um "limite" é mesmo limite. A medição §3.3 mostra a
  triagem errando quatro vezes nesse sentido.
- A **obrigatoriedade** fora da skill. O `state_cli complete` não chama o
  checador; quem não segue a seção de verify fecha a task sem ele.
- A **cobertura** da revisão. A rubrica muda o rótulo do que os revisores acham;
  não faz achar o que não acharam.
- Adjudicação de medium/low: continua só para crítico/alto.

---

## [NEEDS CLARIFICATION]

- [x] ~~**CLARIF-1**: L-04 fixou a medição em `5d2086b`. O código exato que a
  rodada 1 revisou (12:52) é o `578e0bc` — o merge de 12:47:40 no ramo da mãe,
  antes do rebase. O `5d2086b` é a mesma feature reaplicada sobre `db42807`, com o
  `transactional_state.py` de outra base. Recomendação: medir em `578e0bc`, que é
  a única comparação que isola a rubrica.~~ → ASSUMPTION-008.

---

## Suposições

- **ASSUMPTION-001**: medium/low continuam fora da adjudicação · decidido 2026-09-30 por usuário (L-03) · justifica: REQ-F2, AC-1.5
- **ASSUMPTION-002**: o `pass` falso por triagem não pode depender de uma rodada nova sair verde, porque toda rodada medida trouxe 11–23 achados · decidido 2026-09-30 por inferência · justifica: REQ-F7
- **ASSUMPTION-003**: a saída do host da rodada ainda existe quando a triagem começa (retenção medida em `evidencia-em-segundo-plano-spec.md`), e copiá-la para o repo é barato (22–33 kB por rodada nas medidas) · decidido 2026-09-30 por inferência · justifica: REQ-F8, AC-2.10
- **ASSUMPTION-004**: sem `repo_root`, os revisores leem os caminhos relativos no diretório da sessão, que nesta medição é o worktree da mãe com os consertos já aplicados · decidido 2026-09-30 por inferência (o prompt da rodada 1 só lista caminhos relativos) · justifica: REQ-F5, AC-4.1
- **ASSUMPTION-005**: numerar os achados pela posição na lista basta para a tabela de destino, porque a lista de uma rodada não muda depois de devolvida · decidido 2026-09-30 por inferência · justifica: REQ-F3, AC-2.9
- **ASSUMPTION-006**: a garantia de DONE é de protocolo mais checador, sem trava no `state_cli` · decidido 2026-09-30 por usuário (L-05) · justifica: REQ-F6, REQ-F7
- **ASSUMPTION-007**: critério de entrada da rubrica é metade dos defeitos conhecidos re-achados em `high`/`critical` · decidido 2026-09-30 por usuário (L-06) · justifica: REQ-F12, AC-4.3
- **ASSUMPTION-008**: a rodada 1 (`wf_6bd9e71a-c4f`, início 12:52 -0300) revisou o `578e0bc` (merge das 12:47:40 no ramo da mãe, antes do rebase; próximo commit às 13:12), e a medição usa esse commit · decidido 2026-09-30 por usuário (CLARIF-1) · justifica: REQ-F12, AC-4.1

---

## Success Criteria

- [ ] AC de US-1 a US-4 passando em testes automatizados (US-4: a medição escrita e o CSV no repo)
- [ ] `checar_triagem.py` com teste para cada código de saída
- [ ] Metade-controle vermelha no código de hoje para AC-1.2 e AC-3.2
- [ ] `tools/orfaos.py` sem acusar `checar_triagem.py`
- [ ] `wf-verify-multimodel` desta task com `pass: true`, ou checador saindo 0 para a última rodada
- [ ] `CLAUDE.md` global com a frase de L-03
- [x] CLARIF-1 resolvida e registrada como suposição (ASSUMPTION-008)

---

## Spec Metadata (machine-readable)

```json
{
  "spec_id": "verify-adjudica-tudo",
  "version": 1,
  "harness_version": "v3",
  "generated_by": "write-spec skill",
  "generated_at": "2026-09-30T21:10:00-03:00",
  "priorities": ["P1", "P2"],
  "requirement_count": 16,
  "user_story_count": 5,
  "needs_clarification_count": 0,
  "assumption_count": 8,
  "assumptions_by_inference": 4
}
```
