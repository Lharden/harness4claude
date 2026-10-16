# Verificação — o `pass` do verify não sai verde com achado aberto

Task `t-20260930-181933591685` · ramo `claude/verify-adjudica-tudo` · spec
[v2](verify-adjudica-tudo-spec.md) · triagem em
[closure-plan](verify-adjudica-tudo-closure-plan.md).

## 1. Resultado

| | |
|---|---|
| US-1 `pass` honesto | entregue (AC-1.1 a AC-1.9, metade-controle AC-1.2) |
| US-2 checador e protocolo | entregue (AC-2.1 a AC-2.16); AC-2.17 fica para o deploy |
| US-3 rubrica | **não entrou**: medida, 1 de 7 defeitos re-achados em `high` ([medição §7](verify-adjudica-tudo-medicao.md)) |
| US-4 medição | entregue: `medicao-b/` com lista pré-registrada, saída, casamento cego e contagem |
| US-5 documentação | entregue (template, README, spec antiga superada) |
| Fase verify | 3 rodadas do script do ramo sobre este diff; a 3ª trouxe bloqueio e foi ao gate `escalation`; decisão do usuário: consertar e fechar |

## 2. As três rodadas do verify desta task

O protocolo novo rodou sobre a própria mudança, com o script do ramo (cópia no
scratchpad, sha256 conferido) e `repo_root` neste worktree.

| Rodada | Script | `pass` | Bloqueio | Não adjudicados | Consertos | Limites | Não-defeito |
|---|---|---|---|---|---|---|---|
| `wf_d91bfd29-d22` | `feea44e` | false | 0 | 17 | 12 | 3 | 2 |
| `wf_e66b2c58-1e7` | `d5aff37` | false | 2 (o mesmo defeito) | 10 | 5 + bloqueio | 2 | 3 |
| `wf_69c2fd62-849` | `933f8bb` | false | 1 | 9 | 6 + bloqueio | 3 | 0 |

- O Workflow de antes do ramo teria respondido `pass: true` na rodada 1.
- Os dois bloqueios eram defeitos **anteriores ao ramo** do mesmo tipo do que a
  task fecha — achado que some sem destino: veredito `null` (adjudicador morto)
  e veredito "real" com confiança abaixo de 0,5. Cada um tem metade-controle no
  blob de `8cd6884` que reproduz o defeito antigo.
- Checador sobre as três cópias (`<STATE_DIR>/verify/`): 36 linhas de destino,
  nenhum `PENDENTE` nem `INVALIDA`; segue apontando o `BLOQUEIO` da 3ª rodada,
  liberado pela decisão do gate `escalation` (closure-plan, "Rodada 3").

## 3. Consertos sem rodada de revisão

Da 3ª rodada, por decisão do usuário (sem 4ª rodada). Cada um tem teste que o
pega e mutante que o derruba:

| Id | Conserto | Teste | Mutante |
|---|---|---|---|
| F1 | veredito inconclusivo vai para a triagem | `test_veredito_inconclusivo_vai_para_a_triagem` (+ controle) | M33 |
| F2 | linha pronta só para quem não tem linha | `test_achado_com_linha_invalida_aponta_a_linha_e_nao_manda_colar_outra` | M35 |
| F3 | cerca de código fecha só com o mesmo marcador | `test_cercas_de_tipos_diferentes_nao_se_fecham` | M36 |
| F4 | título vazio sai 2 | `test_titulo_vazio_sai_2` | M37 |
| F5 | prompt pede achado critical se a raiz não existir | `test_repo_root_pede_achado_critico_se_nao_existir` | M34 |
| F6 | testes de texto da skill, do template e do README | `test_skill_diz_…`, `test_template_e_readme_…` | — |

## 4. Falsificação

- Metades-controle no blob de `8cd6884` (`248189ec…`, fixado por hash):
  lote só medium/low aprovava; adjudicador morto apagava o crítico; veredito
  inconclusivo apagava o achado.
- Mutação (script de sessão, uma troca de texto por regra, restaura o arquivo):
  **34 de 34 mortos** sobre o código final — 10 no checador original, 4 no
  Workflow original, 9 da rodada 1, 6 da rodada 2, 5 da rodada 3. Os 3 mutantes
  da rubrica (M15–M17) saíram com ela.

## 5. Limites declarados

Os da spec ("Nível de garantia") e os da triagem, cada um com motivo na
tabela: verdade do destino; integridade da cópia; rodada sem cópia e sem linha
(registrar ids exige `state_cli`/hook, NEVER); ordem por mtime no formato de
retorno; texto injetado no título chegando ao adjudicador (anterior ao ramo);
raiz inexistente (mitigada por instrução, o Workflow não lê disco); dedupe que
erra duplicando, nunca perdendo.

## 6. Fora do ramo, depois do merge

- Deploy no cache do plugin e a frase de L-03 no `CLAUDE.md` global (REQ-F9,
  AC-2.17): só com merge e OK do usuário.
