# Spec-light — Ramo visível no índice de sessões

**Task:** t-20260903-025351273585 · L1-feature · Fase 5 do plano de canal vivo + referência cross-sessão

## Objetivo

A busca cross-sessão devolve nós soltos. Quando uma sessão é ramo de outra, o
vínculo existe em `branches.json` e não chega ao índice — quem procura "o que
decidimos sobre X" recebe mãe e filho como se fossem conversas sem relação.

Fazer o índice ler o registro de ramos e anotar o vínculo, para a busca mostrar
a árvore.

## Contexto medido (2026-09-03)

- `branches.json`: **zero arquivos** em 35 buckets. Nenhum ramo foi aceito ainda.
  A feature precisa funcionar com o registro ausente, que hoje é o caso normal.
- `build_sessions_index.py` já grava `git_branch` (branch do git), que é outra
  coisa e não deve ser confundida com ramo de sessão.
- `render_seed` já escreve irmãs e o comando de consulta à mãe; `parked_block` já
  devolve a conclusão do ramo ao pai uma vez. Este é o item que falta da Fase 5.

## Requisitos

- **REQ-1** — `branch_links(harness_root)` varre os buckets, lê cada
  `branches.json` e devolve `{session_id: {"branch_of": <uuid|None>,
  "branches": [<uuid>, ...]}}`.
- **REQ-2** — o catálogo (`sessions-catalog.json`) ganha `branch_of` e `branches`
  por sessão.
- **REQ-3** — cada chunk do índice ganha `branch_of`, para o resultado da busca
  poder dizer de quem aquela sessão é ramo sem uma segunda leitura.
- **REQ-4** — registro ausente, ilegível ou com JSON quebrado resulta em mapa
  vazio; a construção do índice nunca falha por causa do estado de ramos.
- **REQ-5** — ramo sem `session_id` (ainda `pending`, janela nunca aberta) não
  entra no mapa: ele não corresponde a sessão nenhuma que a busca possa devolver.

## Acceptance criteria

- **AC-1** — Dado um bucket com `branches.json` contendo `parent_session: P` e um
  ramo com `session_id: F`, quando `branch_links` roda, então o mapa traz
  `F.branch_of == P` e `P.branches == [F]`.
- **AC-2** — Dado nenhum `branches.json` em lugar nenhum, quando `branch_links`
  roda, então devolve `{}` e não levanta.
- **AC-3** — Dado um `branches.json` com JSON inválido, quando `branch_links`
  roda, então esse bucket é ignorado e os demais continuam valendo.
- **AC-4** — Dado um ramo com `session_id` nulo, quando `branch_links` roda,
  então ele não aparece nem como filho nem em `branches` do pai.
- **AC-5** — Dado o catálogo construído com o mapa acima, quando uma linha é de
  sessão-filha, então ela traz `branch_of` preenchido; quando é de sessão sem
  vínculo, traz `branch_of: null` e `branches: []`.
- **AC-6** — Dado dois ramos do mesmo pai, quando o catálogo é construído, então
  `branches` do pai traz os dois, em ordem estável.

## Boundaries

- **ALWAYS** — degradar em silêncio: estado de ramo ausente é o caso normal hoje.
- **ALWAYS** — manter `git_branch` intocado; é branch de git, não ramo de sessão.
- **NEVER** — fazer o build do índice falhar por causa de `branches.json`.
- **NEVER** — inventar vínculo a partir de heurística (título parecido, mesmo
  cwd). O vínculo ou está registrado ou não existe.

## Fora de escopo

- Renderizar a árvore em `session_query.py` (consome o campo; é outro passo).
- Reconstruir vínculo de ramos já abertos antes desta feature — não há nenhum.
