# Portão de Stop e subagentes fora do checkout — verificação

Ramo `fix/portao-subagente-fora-da-raiz`, task `t-20260928-171727153953`,
pipeline L1-bug (`systematic-debugging -> tdd -> verify`). Diagnóstico em
[`portao-subagente-fora-da-raiz-diagnostico.md`](portao-subagente-fora-da-raiz-diagnostico.md).

## O pedido, item a item

| # | Pedido | Evidência | Estado |
|---|---|---|---|
| 1 | Achar a causa raiz | Diagnóstico §"Causa raiz": o hook localiza o comando por `payload.cwd` e ignora o `cd` inicial da linha. Reproduzido com as funções de produção sobre os 1 000 comandos reais | feito |
| 2 | Comando de subagente conta contra o pai? | Sim, pela mesma régua do pai — onde pode escrever, não quem emitiu. `roda_fora_da_raiz` não lê `agent_id` | decidido |
| 3 | Comando com cwd fora do repositório conta? | Não, quando o local declarado é conhecido, disjunto do checkout e nada na linha o alcança; em dúvida conta | decidido |
| 4 | Como o hook sabe quem emitiu? | Sonda `claude -p` no Claude Code 2.1.283: subagente chega com o `session_id` do pai, `agent_id`/`agent_type` preenchidos e o `cwd` do pai. Agora gravado em `recusas.jsonl` (`_emissor`) | medido |
| 5 | Teste que falha reproduzindo a invalidação | RED abaixo | feito |
| 6 | Conserto | `hooks/harness-transactional.py`: `diretorio_declarado`, `roda_fora_da_raiz`, `_apenas_dentro_da_raiz(base=)`, `_emissor` | feito |
| 7 | Teste passando, as duas metades | GREEN e mutação abaixo | feito |
| 8 | Não afrouxar para edição real no repositório | 22 guardas + mutante M9 ("ignora subagente") morto com 25 falhas | feito |

## RED — antes do conserto

Contra o hook de `main` (`7795a5a`), a seção nova de
`tests/test_transactional_hook.py`:

- as 10 reproduções (`test_comando_que_roda_fora_do_checkout_nao_invalida_evidencia`,
  5 formas × {subagente, principal}) falharam com `assert 1 == 0` e toque
  `{'path': 'shell-command', 'origem': 'shell-placeholder'}` — o incidente;
- `test_alvo_relativo_e_resolvido_no_diretorio_do_cd` falhou com
  `['sim_334.py'] == []` — o arquivo escrito em `%TEMP%` atribuído ao checkout;
- as funções novas falharam por `AttributeError` (não existiam);
- as guardas da outra metade **passaram** — é o esperado: elas descrevem o que
  já contava e tem de continuar contando.

Durante o GREEN, a mutação e a revisão acharam três buracos da primeira versão,
cada um com guarda escrita e vista vermelha antes do conserto: `popd` e shell
aninhado (`bash -c "cd ../../repo && ..."`), `os.chdir` dentro do programa, e
atribuição de variável (`SAIDA=../../repo/x.py python gera.py`, `assert 0 > 0`).

## GREEN — depois do conserto

| Rodada | Resultado |
|---|---|
| `tests/test_transactional_hook.py` | **142 passed** (86 de antes + 56 novos) |
| Suíte inteira, `-n 8 --dist loadscope` | **1 656 passed, 0 failed**, 6 subtests |
| Suíte inteira, `-n auto` | 1 654 passed, 2 failed — ver abaixo |

As 2 falhas do `-n auto` não carregam o hook (nenhum dos dois arquivos importa
`harness-transactional`) e passam em série (24 de 24):

- `test_hermeticity_enforcement.py::TestScopeA::test_sees_own_marker` depende de
  `test_writes_marker` rodar antes no **mesmo** worker; o `--dist load` padrão
  pode separar os dois. `--dist loadscope` mantém a classe junta.
- `test_state_lock.py::TestConcurrency::test_two_concurrent_acquires_serialize`
  tem teto de 3 s de relógio para dois `bash` concorrentes; com ~20 workers
  mediu 4 s.

Causa conhecida, fora deste ramo; ficam registradas aqui em vez de escondidas.

Os 14 `ModuleNotFoundError: No module named 'tools'` da primeira rodada foram de
invocação: o pytest rodou de fora da raiz, e `tools/` só entra no `sys.path`
com o `cwd` na raiz. Resolvido com `-o pythonpath=<raiz>`.

## Falsificação por mutação

Cada cláusula da regra foi removida, uma por vez, numa cópia do repositório, e a
seção nova rodou contra o mutante. Todos morreram:

| Mutante | O que tira | Guardas que falharam |
|---|---|---|
| M1 | checagem de ancestral | `ancestral-do-checkout` |
| M2 | busca da raiz no texto | `heredoc-citando-a-raiz`, `fora-citando-a-raiz-no-programa` |
| M3 | token resolvido para dentro | `fora-subindo-por-ponto-ponto`, `fora-flag-com-caminho-relativo`, `git-C-para-dentro` (+1) |
| M4 | variável no resto da linha | `variavel-no-resto` |
| M5 | conversão MSYS (junta `/c/...` ao drive do cwd) | `msys-para-dentro[pelo-ancestral]` |
| M6 | árvore de trabalho da sessão | `sessao_em_worktree_fora_do_dono` |
| M7 | exigência de `&&`/`;` depois do `cd` | `cd-com-ou`, `cd-e-pipe` (+3) |
| M8 | contagem de trocas de diretório no texto | `popd`, `shell-aninhado`, `programa-muda-de-diretorio`, `cd-menos` (+4) |
| M9 | "ignorar todo comando com `agent_id`" | 25 guardas, `sed-no-checkout` e `cd-para-dentro` entre elas |
| M10 | alvo relativo resolvido no `cwd` | `alvo_relativo_e_resolvido_no_diretorio_do_cd` |
| M11 | valor depois do `=` só em flag | `atribuicao-de-variavel-para-dentro` |

M5 **sobreviveu** na primeira rodada: a busca no texto já pegava `cd /c/.../repo`
direto, então a guarda não media a conversão. Ganhou a forma `pelo-ancestral`
(`cd /c/<pai> && cd repo/docs`), que só a conversão pega, e morreu.

M9 é a resposta executável à pergunta 2: a implementação ingênua — "subagente
não conta" — derruba 25 guardas.

## Medição com a função de produção

`handle_payload` de `main` e do ramo sobre os 1 000 comandos de shell dos
transcripts da `44b0dfb5`, com o payload real (`cwd` = checkout do
master-harness, `agent_id` nos comandos de subagente):

| | `main` | ramo |
|---|---|---|
| subidas de `code_revision`, principal | 72 | 52 |
| subidas de `code_revision`, subagente | 342 | 184 |
| **total** | **414** | **236** |
| recusas `roda-fora-da-raiz` | 0 | 178 (158 com `agent_id`) |

As 178 que deixam de contar foram revisadas por forma: todas operam em caminho
relativo do worktree em `%TEMP%` (`git add/commit/merge`, `python -m pytest`,
`cat > docs/...`, `sed -i` em arquivo do worktree). Nenhuma cita o checkout.

Das invalidações reais de evidência atribuíveis por carimbo de tempo, o conserto
evita **10 de 16** — todas `cd "%TEMP%\...\wt-*" &&` de subagente. As 6 que
restam têm causa própria, registrada no diagnóstico: indireção por variável
(`F=...`, `WT=...`), `git -C <dir> show` lido como escrita, laço `until` de
espera, `tr '\n'` (token que só o shell resolveria, e a régua conta na dúvida) e
um `python - <<PY` do pai no checkout — este último conta com razão. Uma 17ª
linha casou por engano com `echo still-waiting`, que não conta nem em `main`, e
ficou fora da conta.

## Riscos que continuam, declarados

1. Programa lançado de fora do checkout que escreve dentro por caminho que não
   está na linha (arquivo-ponteiro como `~/.master-harness/mh-root`, variável de
   ambiente herdada, `subprocess(cwd=...)`) deixa de contar. Só o digesto da
   árvore fecha isto; custo medido de `git status --porcelain=v1 -z -uall` +
   `git rev-parse HEAD` no master-harness: 0,225 s.
2. Evidência sobre código de outro checkout (gravada à mão) não é modelada pelo
   portão — nem antes nem depois.
3. Sessão aberta em worktree fora do diretório do dono: o caminho `Edit`/`Write`
   (`counts_as_modified_file` com raiz colapsada) segue julgando o próprio
   worktree como fora. Anterior a este ramo; o shell não herda o defeito.

## O que NÃO foi feito

- **Deploy.** O cache instalado (`~/.claude/plugins/cache/harness4claude/.../4.0.0`)
  segue rodando o hook de `main`, e segue invalidando a evidência de quem trabalha
  em worktree fora — inclusive a da sessão que escreveu este relatório. Deploy é
  consequência de merge.
- Push e merge.
