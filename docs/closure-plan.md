# Closure Plan — t-20260930-135548871810 (evidência em segundo plano)

## Origem
- Verificação: `wf-verify-multimodel` (run `wf_6bd9e71a-c4f`), `pass: true`,
  `critical_count: 0`, **23 achados medium/low não adjudicados** (o Workflow só
  adjudica critical/high). Lidos um a um; os abaixo foram reproduzidos ou
  medidos com a função de produção antes de entrar aqui.
- Iteração: 1 de 2.

## Gaps encontrados (defeito real, direção perigosa)

- **G1 · código de saída lido do texto do modelo (#10, #12).** `_codigo` pega o
  PRIMEIRO `exit code N` do `<summary>`, e o summary contém a `description` que o
  modelo escolhe. Descrição honesta com "exit code 0" → rejeição terminal falsa;
  sem trailer → código errado gravado.
- **G2 · injeção de bloco pela descrição (#11).** Uma descrição com
  `</summary></task-notification><task-notification>…` faz a notificação real do
  host carregar um bloco forjado para outro job.
- **G3 · arquivo sem trailer aceito (#15).** `.output` vazio ou truncado vira
  `aceito` com exit 0 e contagens nulas e pode desverificar um verde.
- **G4 · regra de pasta rejeita 10% dos términos reais (#19).** Medido
  (`medir_lugar.py`): 65 de 654 `.output` ficam em pasta de sessão diferente do
  nome do transcript (sessão retomada); nem o `sessionId` da entrada resolve
  (597/654), e em 12 o projeto diverge. Rejeição terminal falsa.
- **G5 · cópias que divergem só no `<output-file>` (#13).** `julgar` usa
  `notificacoes[0]`; o desfecho depende da ordem.
- **G6 · lançamento sem `tool_use_id`/`transcript_path` pendente para sempre
  (#9, #14).** Aparece na mensagem como falso alarme de mudança de formato.

## Medições que decidem o conserto
- `medir_blocos.py`: **2471 de 2471** textos de entrada do host com notificação
  têm exatamente **um** bloco. Recusar texto com mais de um custa zero.
- `medir_lugar.py`: 228 de 231 `.output` existentes têm trailer
  `[exited with code N]` (3 sem).
- No formato do host, `<output-file>` e `<status>` vêm ANTES de `<summary>`:
  com um bloco só, são escritos pelo host; o modelo só alcança o summary.

## Delta tasks

- [x] D1 (G2): `notificacoes_de` recusa texto com mais de um `<task-notification>`.
- [x] D2 (G1): código do resumo casado ANCORADO no fim do summary.
- [x] D3 (G1, G3): trailer obrigatório e igual ao código do resumo; sem trailer →
      `rejeitado:sem-trailer`. O código gravado vem, na prática, do arquivo do host.
- [x] D4 (G4): lugar do arquivo = nome `J.output` em diretório `tasks`; sai a
      checagem de pasta de sessão e projeto.
- [x] D5 (G5): `arquivo` entra no conjunto de divergência.
- [x] D6 (G6): `registrar_lancamento` sem `tool_use_id` ou sem `transcript_path`
      nasce `rejeitado` (`sem-tool-use-id`, `sem-transcript`), com evento.
- [x] D7 (cobertura, #1–#5): exceção dentro da captura (Stop e `complete`
      decidem como antes); teto de cauda; `PostToolUseFailure`;
      `stop_continuations` em AC-2.1 e AC-3.3; migração de banco antigo.
- [x] D8: spec (REQ-F4, REQ-F9, REQ-F11, AC-3.6, AC-3.8, AC-3.13–3.15,
      ASSUMPTION-013..015) e design atualizados.

## Resultado da iteração 1
- 69 testes do arquivo novo verdes (eram 59). Dois instrumentos errados foram
  pegos no primeiro vermelho e corrigidos antes do código: o teste do teto
  gerava arquivo menor que o teto, e o `monkeypatch` pegava a cópia de
  `HarnessDatabase` do teste, não a do hook.
- Mutação: 16 de 16 mortos (M12–M16 novos, um por regra desta iteração).

## Iteração 2 de 2 (re-verify `wf_47eeb4d4-140`: `pass: true`, 0 críticos, 11 médios/baixos)

- [x] E1 (#9): `complete` confere a fase final ANTES de capturar — recusado por
      fase não grava nada. Mutante M21.
- [x] E2 (#4): campos do host lidos só antes do `<summary>`; tag na
      `description` não esconde mais a notificação legítima. M18.
- [x] E3 (#5): lançamento de subagente (`agent_id` no payload) nasce
      `rejeitado:subagente`. M19.
- [x] E4 (#6): saída sem contagem nenhuma (`pytest -q > log.txt`, `>` não é
      composição) → `rejeitado:sem-contagem`; não desverifica. M20.
- [x] E5 (#1): teste do diretório `tasks`. M17.
- Resultado: 74 testes; mutação **21 de 21** mortos.
- **Não re-revisado pelo Workflow**: a iteração 2 é a última permitida pelo
  protocolo; os cinco consertos são pequenos e cada um tem mutante que o mata.
  Uma terceira rodada do `wf-verify-multimodel` (~570 k tokens) fica a critério
  do usuário.
- #3 do re-verify (falsificação fora do arquivo de testes): os scripts do
  controle por SHA e da mutação estão descritos, com números, em
  `docs/specs/evidencia-em-segundo-plano-verification.md`.

## Rodada 3 (pedida pelo usuário; `wf_2c3bbdfb-161`: `pass: true`, 0 críticos, 15 médio/baixo)

- [x] F1 (#7): só o campo da notificação é lido — `cwd`/`gitBranch` forjados não valem. M22.
- [x] F2 (#9): bloco do primeiro `<task-notification>` ao último fechamento;
      resumo até o último `</summary>`. Substitui "um bloco por texto". M12, M23.
- [x] F3 (#8): recusa do `complete` depois de captura diz a revisão nova. M25.
- [x] F4 (#12): falha ao registrar não derruba o hook. M26.
- [x] F5 (#5): aviso só promete captura com lançamento pendente. M27.
- [x] F6 (#3): fase pelo índice antes da captura. M24.
- [x] F7 (#15): referência tardia a `trabalho_em_voo`. M28.
- [x] Guardas #1 (isolamento entre tasks, M29) e #2 (revisões diferentes).
- Resultado: 83 testes; mutação **29 de 29**.

## Declarados como limite, sem conserto (com motivo)
- #16/#22 janela de CAS do `complete` com Stop concorrente: erra recusando
  (fail-closed), nunca aceitando.
- #17 término sem carimbo → `superado`: erra não verificando, documentado.
- #18 `registrar_lancamento` sem `try`: mesmo padrão das outras escritas do hook;
  falha de banco ali é defeito a ver, não a engolir.
- #20 `stop_hook_active` antes da captura: o `complete` captura; o próximo Stop também.
- #21 projeção depois de `complete` recusado: o `PostToolUse` do próprio comando
  `state_cli` sincroniza (`_handle_post_tool` sempre chama `_sync_projection`).
- #23 import de símbolos privados de `trabalho_em_voo`: renomear quebra o hook na
  coleta de qualquer teste que o carrega — o erro é imediato, não silencioso.
- #14 releitura com pendente: medido 0,33 s no maior transcript real (122 MB).
