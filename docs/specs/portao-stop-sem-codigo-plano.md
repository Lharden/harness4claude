# Portão de Stop sem código — plano de conserto

Causa raiz e medições: `docs/specs/portao-stop-sem-codigo-diagnostico.md`.
Pipeline L2-bug, task `t-20260925-022237730816`.
Grill: 1 rodada, 63 perguntas de 5/5 lentes (`wf-grill`, run `wf_229e954d-35d`),
12 bloqueantes. Três viraram decisão do usuário (D1–D3); as demais estão
respondidas abaixo, com a pergunta de origem entre colchetes.

## Objetivo

Um pipeline de docs passa a ter uma evidência que ele consegue produzir
honestamente, cobrada na fase que a produz, e leitura pura de shell deixa de
invalidar evidência. Nenhum consumidor de `verified` ganha exceção própria:
muda **qual evidência liga `verified`** e **em que fase o Stop a cobra**, num
lugar só (`scripts/transactional_state.py`), lido pelos consumidores.

## Decisões do usuário (grill, 2026-09-25)

- **D1. O Stop só cobra a verificação de docs na fase final** do pipeline
  (`verify` em L1-docs, `verify-against-spec` em L2-docs). Antes dela, o Stop
  não bloqueia task de docs. Pipelines de código não mudam. [1, 5, 8, 9, 12]
- **D2. Task de docs que edita código: declarado, não tratado.** A verificação
  de docs cobre afirmações. A skill `documentation` passa a dizer que mexer em
  código além do texto faz da task uma task de código, que roda teste. [4, 7, 19]
- **D3. A evidência de docs é ancorada num relatório em disco.** `state_cli
  evidence --type docs` recusa quando `--command-text` não é um arquivo que
  existe e grava o sha256 dele; a régua de docs exige o hash. [2, 3, 6, 10, 35]

## Parte A — a evidência vem do pipeline (pergunta 1)

- **REQ-1.** O tipo de evidência que liga `verified` depende do `kind`:
  `docs` exige `evidence_type='docs'`; todo outro `kind` continua exigindo
  `'test'`. `tasks.kind` vale `docs` para L1-docs e L2-docs (split de
  `tier-kind`, conferido no `harness.db` do incidente); `kind` nulo ou
  desconhecido cai em `test`. [11, 53]
- **REQ-2.** Uma régua só, para os dois tipos: `exit_code = 0`,
  `tests_collected > 0`, `tests_passed > 0`, `tests_skipped >= 0` (nulo conta
  0), `tests_passed + tests_skipped = tests_collected`. O `>= 0` é novo e vale
  também para teste: `3 = 5 + (-2)` fechava a soma. [56] Para docs, soma-se
  `output_hash IS NOT NULL` (D3).
- **REQ-2b. O que os números de docs significam** (`skills/documentation`
  §Verificação): `collected` = afirmações da tabela de fontes conferidas,
  `passed` = confirmadas na doc, `skipped` = descartadas com motivo, que fica
  escrito no relatório (D3) — não em coluna nova [42]. `exit_code = 0` só
  quando os itens 2 a 4 da §Verificação valem: todo exemplo roda, nenhum
  `[NEEDS CLARIFICATION]` pendente, nenhum caminho citado sumiu. [15] Doc sem
  nenhuma afirmação conferível não se verifica por esta régua (`collected =
  0`): não é pipeline de docs. [31, 51]
- **REQ-3.** Evidência de tipo diferente do exigido não mexe em `verified` —
  nem liga, nem desliga. [41: o teste grava direto no banco]
- **REQ-4.** `complete` exige evidência fresca do tipo exigido
  (`_has_fresh_test_evidence` vira `_has_fresh_evidence`).
- **REQ-5.** Stop: task de docs fora da fase final não bloqueia (D1); na fase
  final, a mensagem descreve a régua de docs, imprime a receita com `--type
  docs` e `--command-text "<relatorio>"` (placeholders que quem copia
  substitui — a receita nunca traz número preenchido [10]), e não fala em
  pytest. A mensagem de escalonamento já não cita teste. [23, 55]
  `register_stop_continuation` usa a mesma regra de fase, para o banco e o
  hook não discordarem.
- **REQ-6.** Editar a doc depois de verificada sobe `code_revision` e zera
  `verified`. Qualquer escrita conta, não só a doc [25, 30]: a ordem é
  escrever o relatório, gravar a evidência, fechar. Leitura por interpretador
  depois da evidência também invalida [28] — o mesmo custo dos pipelines de
  código, declarado.
- **REQ-7.** `skills/documentation/SKILL.md` §Verificação diz como registrar a
  evidência de docs e D2; `skills/verify-against-spec/SKILL.md` diz que, num
  pipeline de docs, o fechamento é essa evidência, não pytest. [14, 20, 24, 36]
  Sem bloco `bash` novo: a linha literal (caminho e balde) é a que o portão
  imprime.
- **REQ-8. Troca de `kind` zera `verified`** — já é o comportamento de
  `confirm_classification` (`transactional_state.py:402`); travado por teste.
  Evidência de um `kind` não vale para o outro. [16, 26, 29]
- **REQ-9. O mapa `kind -> evidência` é ligado ao contrato por teste:** todo
  `kind` cujos pipelines em `contract/pipelines.json` não têm fase `tdd` ou está
  no mapa, ou está na lista declarada de não mapeados (`question`, `review`).
  Pipeline novo sem `tdd` reprova o teste até alguém decidir. [52, 62]

## Parte B — leitura não sobe revisão (pergunta 2)

- **REQ-10.** `cd` é leitura (`_SOMENTE_LEITURA`).
- **REQ-11.** `_segmentos` consome o alvo de todo `>`/`>>` em vez de abrir
  segmento com ele. [17] A recusa de destino que não é nulo passa a ser
  explícita, em `is_read_only` **e** em `nao_muda_a_arvore`: arquivo, `$OUT`.
  [39] `>` sem alvo é erro de sintaxe no bash — não escreve, e não é tratado
  como escrita. Destino nulo = token exato em `_DESTINOS_NULOS` (já
  existente) [18, 57]; `2>&1` continua sendo duplicação de descritor, não
  redirecionamento [32, 49]; `>` entre aspas não é operador, porque
  `_tokenize` respeita aspas [33, 38].
- **REQ-12.** `find` é leitura quando nenhum argumento é, por token exato,
  `-delete`, `-exec`, `-execdir`, `-ok`, `-okdir`, `-fprint`, `-fprint0`,
  `-fprintf` ou `-fls`. [40]
- **REQ-13.** Continuam contando, travado por teste: interpretadores, `awk` e
  laço `for/do/done`. [44] Os 59 comandos do incidente eram todos da
  ferramenta Bash; vocabulário de PowerShell (`Get-Content`, `$null`) não foi
  medido e fica fora. [27, 45, 60]

## Critérios de aceite (1 AC = 1 teste, em `tests/test_portao_de_docs.py`)

- **AC-1 (reprodução).** Task `L2-docs` numa pasta sem `.git` nem suíte. Na
  fase 1, depois das leituras medidas e de um `python -c` de leitura, o Stop
  **não** bloqueia. Na fase final ele bloqueia pedindo `--type docs`; o
  relatório é escrito, a receita impressa (placeholders substituídos) grava a
  evidência, o Stop libera e `complete` fecha. **Falha antes do conserto.**
- **AC-2.** Na fase final, sem evidência, o bloqueio cita `--type docs` e não
  cita `pytest`.
- **AC-3.** Numa task de docs, pytest verde não liga `verified`; pytest
  vermelho não desliga.
- **AC-4 (controle).** Numa task `bug`, evidência `docs` não liga `verified` e
  o Stop continua pedindo teste em qualquer fase.
- **AC-5.** Doc verificada e depois editada (`sed -i`) perde `verified`.
- **AC-6.** Evidência `docs` fora da régua não verifica: exit ≠ 0, nada
  confirmado, nada conferido, soma que não fecha, sem exit, sem hash,
  `skipped` negativo.
- **AC-7.** Leituras medidas são leitura; o que escreve ou pode escrever
  continua contando (lista do REQ-11/12/13); `nao_muda_a_arvore("git log >
  $OUT")` é `False`; `grep "a > b" x` é leitura.
- **AC-8.** A receita de docs é isenta (não sobe a revisão) e recusa
  relatório inexistente com saída 2, sem gravar nada.
- **AC-9.** Reclassificar uma task de docs verificada para `feature` zera
  `verified`.
- **AC-10.** O mapa `kind -> evidência` cobre o contrato (REQ-9).
- **AC-11.** Evidência `test` com `skipped` negativo não verifica.

## Fora do escopo, declarado

- `kind` `review`: também sem fase `tdd`; declarado no teste do REQ-9, dono:
  sessão pai.
- harness4codex: mesmo desenho (só `test` liga `verified`); não compartilha
  `harness.db` nem `skills/` com este repositório. Paridade: sessão pai. [47, 61]
- Tasks de docs abertas no deploy com `verified=1` ligado por pytest: o Stop
  as libera, e `complete` recusa até haver evidência `docs` fresca. A janela
  fecha no primeiro toque ou na evidência nova. [46, 54, 59]
- Edição por ferramenta que o PostToolUse não vê (MCP, vault) não invalida
  evidência. [50]
- Classificação pelo regex (pergunta 3): não causou este incidente.
- Nenhuma mudança em `contract/`, em `scripts/vault_sync.py` nem no bloco do
  CLAUDE.md (o snippet não afirma que a evidência é teste). [63]
