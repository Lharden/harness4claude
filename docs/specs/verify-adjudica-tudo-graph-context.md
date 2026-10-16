# Graph context — o `pass` do verify e os não adjudicados

Fase `graph-context` da task `t-20260930-181933591685` (ramo
`claude/verify-adjudica-tudo`, base `8cd6884`).

## Fonte

- `graphify-out/graph.json` existe só no checkout principal e é de
  **2026-07-28**; os cinco arquivos-alvo mudaram depois (último toque
  2026-09-30, `33c3708`). O grafo **não foi usado**: descreveria código que já
  não existe.
- Toda afirmação estrutural abaixo vem de busca direta (`Grep`/`Read`) na árvore
  do ramo, e cada uma cita arquivo e linha.

## Quem produz e quem consome o `pass`

| Papel | Onde | O que diz hoje |
|---|---|---|
| Produz | `scripts/workflows/wf-verify-multimodel.js:183` | `pass: criticals.length === 0 && nosMortos.length === 0` |
| Produz (sem achado) | `scripts/workflows/wf-verify-multimodel.js:129-139` | `pass: completo` quando não há achado |
| Corte de adjudicação | `scripts/workflows/wf-verify-multimodel.js:142-147` | medium/low vão para `naoAdjudicaveis` |
| Prompts dos revisores | `scripts/workflows/wf-verify-multimodel.js:62-88` | só `spec-coverage` fala de severidade ("P1 ausente=critical") |
| Consome (contrato) | `skills/harness-workflow/SKILL.md:180` | `{ pass, critical_count, findings[], nos_mortos[], summary }` |
| Consome (gap-closure) | `skills/harness-workflow/SKILL.md:251-257` | "pass: false → liste os findings bloqueantes (critical/high)" |
| Consome (DONE) | `skills/harness-workflow/SKILL.md:329` | "Se `pass: true` (sem findings critical/high) → DONE" |
| Consome (template) | `skills/write-spec/templates/spec-template.md:193` | "Zero findings críticos em `wf-verify-multimodel`" |
| Descreve | `README.md:38-40` | "adversarial adjudication pass" |
| Descreve | `skills/documentation/SKILL.md:76-77` | "5 dimensões em paralelo + adjudicação adversarial" — não fala do `pass`; sem mudança |
| Existência | `scripts/health-check.sh:280` | só confere que o arquivo existe; sem mudança |

`skills/source-selection/SKILL.md:106-107` cita `wf-verify-multimodel.js:57-78`
e `:148` num exemplo de tabela de fontes; as linhas já estão deslocadas hoje.
É exemplo didático, não contrato — fora do escopo.

## Testes que tocam o contrato

| Teste | Arquivo | Efeito da mudança |
|---|---|---|
| `test_pass_invariante_lote_so_medium_low` | `tests/test_verify_por_tabela.py:140` | **inverte**: lote só medium/low passa a dar `pass: false` |
| `test_medium_low_nunca_geram_agent_de_adjudicacao` | `tests/test_verify_por_tabela.py:127` | continua (L-03: o corte fica) |
| `test_no_morto_ainda_forca_pass_false` | `tests/test_verify_por_tabela.py:148` | continua |
| `test_CONTROLE_codigo_antigo_adjudicava_medium_low` | `tests/test_verify_por_tabela.py:206` | continua; o blob antigo é fixo |
| `test_verify_nao_aprova_com_cobertura_incompleta` | `tests/test_workflow_returns.py:106` | exige o texto `nosMortos.length === 0` e proíbe `pass: true,` — a fórmula nova tem de manter os dois |
| `test_adjudicador_nao_recebe_rationale` | `tests/test_workflow_returns.py:86` | continua; a rubrica entra no prompt do REVISOR, não no do adjudicador |
| `test_workflow_pode_ser_invocado_pela_ferramenta` | `tests/test_workflow_returns.py:142` | o `.js` editado precisa continuar sem CR |

O harness de comportamento `tests/wf_run_harness.cjs` grava `label` e
`agentType` de cada `agent()`, **não o prompt** (`:36`). Para provar que a
rubrica chega às cinco dimensões ele precisa gravar o prompt também.

## Precedente de metade-controle

`tests/test_verify_por_tabela.py:179-203` fixa o código antigo como arquivo em
`tests/data/` conferido pelo hash de blob do git, porque `git show main:`
reprovava por construção depois do merge e o CI faz checkout raso. A
metade-controle desta task segue o mesmo padrão com o blob atual de
`wf-verify-multimodel.js` (`8cd6884`).

## Riscos estruturais

1. **Laço de gap-closure sem saída.** Toda rodada medida trouxe 11–23 achados
   medium/low. Com `pass: false` para qualquer não adjudicado, uma nova rodada
   quase sempre volta `false`; se o DONE dependesse de uma rodada `true`, o
   pipeline iria sempre para `escalation`. Por isso o DONE é "última rodada sem
   bloqueio + todo não adjudicado com destino", não "uma rodada `pass: true`".
2. **Plugin em cache.** A skill que roda é a de
   `~/.claude/plugins/cache/harness4claude/harness4claude/4.0.0/`; a mudança só
   vale depois do merge e do deploy, que pede OK do usuário.
3. **`docs/closure-plan.md` é um arquivo só no repo**, reescrito a cada task. O
   checador lê o arquivo que a skill mandar, não um caminho fixo.
