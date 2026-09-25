# Portão de Stop sem código — verificação

Plano: `docs/specs/portao-stop-sem-codigo-plano.md`. Diagnóstico:
`docs/specs/portao-stop-sem-codigo-diagnostico.md`. Testes:
`tests/test_portao_de_docs.py` (45 casos).

## Suíte completa, comparada por node id

`python -m pytest -n 2 -p no:cacheprovider -q --junitxml ... tests`, com
`VAULT_PATH` e `AI_BRAIN_PATH` num diretório temporário do scratchpad.

| | passed | skipped | failed | node ids |
|---|---|---|---|---|
| Antes (`27c0002`, só docs novas no worktree) | 1555 | 1 | 0 | 1556 |
| Depois | 1600 | 1 | 0 | 1601 |

Falhas novas: **0**. Node ids que sumiram: **0**. Node ids que mudaram de
desfecho: **0**. Novos: 45, todos de `tests/test_portao_de_docs.py`.
`ruff check` nos arquivos alterados: limpo, exceto um `RUF100` pré-existente em
`scripts/state_cli.py:22` (linha não tocada).

## RED antes do conserto

Com o código de `27c0002`: 24 de 46 casos falharam, cada um pelo motivo
esperado — leitura subindo `code_revision`, `docs` sem ligar `verified`, API
nova ausente, régua aceitando descarte negativo. Um caso do controle estava
errado e saiu: `cat x >` é erro de sintaxe no bash e não escreve nada.

## Falsificação — cada guarda desligada derruba o teste dela

Cada mutação aplicada sozinha ao código consertado; o original foi restaurado
byte a byte depois de cada uma (`scratchpad/falsificar.py`).

| Guarda desligada | Testes que reprovaram |
|---|---|
| `cd` fora de `_SOMENTE_LEITURA` | 5 |
| `_segmentos` volta a abrir segmento no alvo de `>` | 8 |
| `is_read_only` sem `_redireciona_para_arquivo` | 1 |
| `nao_muda_a_arvore` sem `_redireciona_para_arquivo` | 1 |
| `find` ignora `_FIND_QUE_ESCREVE` | 4 |
| `EVIDENCIA_DO_KIND = {}` (docs volta a teste) | 8 |
| Stop cobra docs em qualquer fase | 1 |
| Régua aceita `tests_skipped` negativo | 2 |
| Régua de docs sem `output_hash` | 1 |
| Evidência de outro tipo volta a desverificar | 1 |
| CLI aceita `--type docs` sem relatório | 3 |

11 de 11 pegam. Restaurado: 45 passam.

## Remedição do incidente com as funções consertadas

Os 59 toques `shell-placeholder` da task `t-20260925-013929224445`, casados com
o comando do transcript e reavaliados com `is_read_only` de produção
(`scratchpad/mapear_toques.py`): **24 agora isentos**, 35 ainda contam. Os 35 =
30 interpretadores + 2 escritas reais + 3 leituras declaradas fora (`awk` ×2,
laço `for` ×1). Batem com as classes do diagnóstico.

## Requisitos

| REQ | Evidência | Estado |
|---|---|---|
| REQ-1 `kind` escolhe a evidência | `transactional_state.tipo_de_evidencia`; AC-3, AC-4 | COBERTO |
| REQ-2 régua única, `skipped >= 0`, hash em docs | `REGRA_EVIDENCIA_VALIDA`, `regra_da_evidencia`; AC-6 (7 casos), AC-11 | COBERTO |
| REQ-2b significado dos números | `skills/documentation/SKILL.md` "Registrar a verificação"; mensagem do portão | COBERTO |
| REQ-3 outro tipo não mexe em `verified` | `record_evidence` (`elif evidence_type == exigido`); AC-3 | COBERTO |
| REQ-4 `complete` exige o tipo do `kind` | `_has_fresh_evidence`; AC-1 (`complete` fecha task de docs) | COBERTO |
| REQ-5 Stop por fase e por tipo | `cobra_evidencia_nesta_fase` no hook e em `register_stop_continuation`; AC-1, AC-2 | COBERTO |
| REQ-6 editar a doc invalida | AC-5 | COBERTO |
| REQ-7 skills | `skills/documentation/SKILL.md`, `skills/verify-against-spec/SKILL.md`; nenhum bloco `bash` novo | COBERTO |
| REQ-8 troca de `kind` zera `verified` | AC-9 | COBERTO |
| REQ-9 mapa ligado ao contrato | AC-10 (`review`, `question` declarados no teste) | COBERTO |
| REQ-10 `cd` é leitura | AC-7; falsificação | COBERTO |
| REQ-11 alvo de redirecionamento | `_segmentos`, `_redireciona_para_arquivo`; AC-7 | COBERTO |
| REQ-12 `find` sem ação | `_FIND_QUE_ESCREVE`; AC-7 | COBERTO |
| REQ-13 interpretador, `awk`, laço contam | AC-7 CONTROLE; `test_is_read_only_recusa_o_que_escreve_ou_pode_escrever` | COBERTO |

## Limites declarados

- `review` também não tem fase `tdd` e continua verificado por teste —
  declarado em `SEM_TDD_DECLARADOS` do teste AC-10. Dono: sessão pai.
- harness4codex tem o mesmo desenho; paridade é decisão da sessão pai.
- Os números da evidência de docs são declarados por quem verificou; o que os
  torna auditáveis é o relatório em disco e o hash dele (D3).
- Task de docs que edita código não é barrada pelo portão (D2): está escrito na
  skill `documentation`.
- PowerShell (`Get-Content`, `$null`) não medido; os 59 comandos do incidente
  eram todos da ferramenta Bash.
- Leitura por interpretador depois da evidência a invalida, como em código.
