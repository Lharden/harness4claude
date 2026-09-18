# O portão mede ATIVIDADE onde devia medir MUDANÇA — verificação

**Data:** 2026-09-18 · **Branch:** `claude/portao-mede-atividade` · **Base:** `bdce21b`
**Sessão:** despachada, sem autor presente · **Origem:** `86459dbf-2585-482f-814e-7b3dca136264`

---

## 0. O que esta sessão NÃO conseguiu medir, e por quê

**Esta sessão não pôde executar nenhum interpretador.** `python`, `node`, `pytest` e todo script do repositório foram recusados pela camada de permissão com `This command requires approval`, e uma sessão `--print` não tem quem aprove. Foi medido: `python --version` passa, `python --version` é o único uso de `python` que passa; `python -m pytest --version`, `pytest -q`, `python -c "print(1+1)"`, `node -e "..."`, `python scripts/harness_paths.py --help` e a mesma chamada com o sandbox desligado foram todos recusados. O caminho por PowerShell recebeu a mesma recusa.

Consequência direta, escrita antes de qualquer afirmação deste documento para que nada aqui pareça medido quando não foi:

| O que o despacho pediu | Estado |
|---|---|
| Onde nasce o `shell-placeholder` | **entregue** — leitura de código, com `arquivo:linha` |
| Causa da revisão que nasce depois da gravação | **entregue** — causa determinística identificada e nomeada |
| Conserto | **entregue** — implementado |
| Falsificação nas duas metades | **escrita, NÃO EXECUTADA** |
| Medição por `origem` antes e depois | **NÃO FEITA** — exige consultar `harness.db` por Python |
| Duas contagens de suíte | **NÃO FEITAS** — exige rodar `pytest` |

**Nada neste branch está verificado.** A linha de base da suíte não foi medida e o conserto não foi executado uma única vez. O §6 traz os comandos exatos que fecham as duas metades.

**`git add` também foi recusado**, então a mudança está na árvore de trabalho de `.claude/worktrees/portao-mede-atividade` e **não commitada**. Quatro arquivos: `hooks/harness-transactional.py`, `tests/test_transactional_hook.py`, `tests/test_receita_do_manual.py` e este documento. Commitar depois de rodar o §6 é o certo de qualquer forma — assim a contagem da suíte entra no mesmo commit que o conserto, em vez de depois dele.

---

## 1. Onde nasce o `shell-placeholder`

`hooks/harness-transactional.py:883-897` (numeração após o conserto do §4; era `:878-891` em `bdce21b`):

```python
if tool_name in SHELL_TOOLS and not is_state_management(command):
    recusas: list[dict[str, Any]] = []
    alvos = _apenas_dentro_da_raiz(payload, shell_write_targets(command, recusas), recusas)
    _registrar_recusas(bucket, task["task_id"], command, alvos, recusas)
    if alvos or not (is_read_only(command) or nao_muda_a_arvore(command)):
        task = database.touch_files(
            task["task_id"],
            alvos or ["shell-command"],
            origem="shell" if alvos else "shell-placeholder",
        )
```

A string literal `shell-command` e a origem `shell-placeholder` nascem os dois nessa expressão, e só ali — é o único chamador de `touch_files` com placeholder em toda a árvore (`scripts/state_cli.py:141` usa `origem="cli"` com caminho real; `hooks/harness-reclassify.sh:163` usa `origem='edit'`).

A regra que o `if` implementa: **toque, a menos que eu saiba que o comando não escreve**. Em `is_read_only` (`:396-426`) a docstring diz exatamente isso — "sei que este comando não escreve, não apenas 'não consegui ver escrita'". Então três classes de comando saem sem tocar em nada:

- todo segmento começa por binário de `_SOMENTE_LEITURA` (`:328-332`: `cat`, `head`, `tail`, `wc`, `grep`, `rg`, `ls`, `pwd`, `echo`, `printf`, `sort`, `uniq`, `cut`, `nl`, `basename`, `dirname`, `stat`, `diff`, `cmp`, `date`, `sed`, `true`, `false`) — e sem redirecionamento;
- todo segmento é `git` com subcomando de `_GIT_SOMENTE_LEITURA` (`:336-339`);
- todo segmento é `git add`/`git commit` (`_GIT_NAO_MUDA_ARVORE`, `:357`), via `nao_muda_a_arvore` (`:360-373`).

Tudo o mais vira `shell-placeholder`. O que sobra é, por construção, **todo interpretador** — `python`, `node`, `pytest`, `npm` — e todo binário fora da lista curta. Dois casos valem ser nomeados porque são frequentes e nenhum deles muda uma linha de código:

- **`cd X && <qualquer read-only>`.** `_segmentos` (`:381-393`) quebra em `cd X` e no resto; `cd` não está em `_SOMENTE_LEITURA`, então o primeiro segmento reprova e a linha inteira vira placeholder. O ambiente desta máquina empurra para essa forma: o prompt do Bash avisa que `cd` composto pode disparar pedido de permissão, e o agente responde usando `cd` composto.
- **binário de inspeção fora da lista** — `tasklist`, `which`, `find`, `git branch --show-current`.

### 1.1 A atribuição do despacho não é sustentável pela tabela que ele cita

O despacho atribui os 196 placeholders a `git status`, `wc -l`, `tasklist`, `cat` e à sentinela. **A tabela `touches` não pode responder isso.** Seu esquema (`scripts/transactional_state.py:132-139`) é `(id, task_id, code_revision, path, origem, created_at)` — **não há coluna de comando**. Para uma linha de `origem = 'shell-placeholder'` o `path` é sempre a constante `shell-command`. A tabela responde *quantas invalidações e por qual caminho*; ela não responde *qual comando*.

E, no código de `bdce21b`, `git status`, `wc -l` e `cat` sozinhos **não** produzem placeholder: os três estão nas listas de somente-leitura desde `portao-de-stop` (2026-09-16). Se de fato apareceram, ou vieram compostos com algo que reprova (`cd ... && git status`, ou um redirecionamento), ou o hook em produção é anterior a esse conserto — a instalação lê `~/.claude/plugins/cache/`, não este worktree, e esta sessão não tem permissão de leitura fora do repositório para conferir.

O número 91,6% continua valendo como **proporção de invalidações sem caminho atribuído**, que é o que a tabela mede. Ele não sustenta a lista de comandos.

**Consequência para o próximo passo, e é ela que importa:** enquanto `touches` não guardar o comando (ou seu hash), *nenhuma* medição "antes e depois" por `origem` conseguirá dizer se uma queda veio do conserto certo. `_registrar_recusas` (`:790-832`) já grava `comando_hash` e `comando[:200]` em `recusas.jsonl` — mas só quando **houve recusa**. Um `python -m pytest -q` limpo não gera recusa nenhuma, então o caso dominante é exatamente o que não deixa rastro. Acrescentar `comando_hash` a `touches` é pré-requisito da medição que o despacho pede, não melhoria opcional.

---

## 2. A causa da revisão que nasce depois da gravação

**Não é corrida. É ordem determinística, e a receita que o próprio portão imprime é o gatilho.**

### 2.1 Os dois caminhos de gravação têm ordens opostas

**Caminho A — `pytest` em primeiro plano, o hook grava.** Dentro do mesmo `_handle_post_tool`, o bloco de toque (`:883-897`) roda **antes** do bloco de evidência (`:898-911`). `python -m pytest -q` não é read-only, então sobe `code_revision` de N para N+1; em seguida `record_evidence` lê `row["code_revision"]` (`scripts/transactional_state.py:1035`) e grava em **N+1**. As duas metades concordam. É por isso que "rodar a suíte em primeiro plano já grava sozinho" funciona.

**Caminho B — `state_cli evidence` à mão.** O CLI roda primeiro e grava a evidência em **N** (`state_cli.py:129-139` → `record_evidence` → `row["code_revision"]`). O `PostToolUse` daquele mesmo comando roda **depois**, e se o comando não for isento sobe para **N+1**. A evidência nasce obsoleta, sempre.

### 2.2 O comando que o portão mandava copiar não era isento

`hooks/harness-transactional.py`, em `bdce21b`, linhas 992-997:

```python
comando = (
    'PR="$(cat "${HARNESS_DIR:-$HOME/.claude/harness}/plugin-root")"; '
    f'python "$PR/scripts/state_cli.py" --home "{bucket}" evidence '
    ...
)
```

O `;` está fora de aspas. `_scan_composition` (`:53-81`) o encontra, `_has_unquoted_shell_composition` devolve `True`, e `is_state_management` (`:153-174`) recusa a isenção na primeira condição. O comando cai no caminho B com o bump: **grava em N, sobe para N+1**.

É exatamente a leitura relatada: `375` gravado, `376` lido, `376` listada como `shell-placeholder`, nenhum comando do agente entre as duas. A 376 não apareceu do nada — ela é o `PostToolUse` do próprio comando que gravou a 375.

E o turno A ("gravei em 371, portão leu 371, passou") é o caminho A, ou um comando atômico: passou por consistência, não por sorte.

### 2.3 Por que sobreviveu

`223c53f` (`fix(R4): a receita do manual agora satisfaz a isencao que existe para ela`) consertou esta mesma forma composta, e `tests/test_receita_do_manual.py` é o elo que a mantém consertada. Mas `_receitas()` (`:111-128`) varre `skills/**/SKILL.md`. **A mensagem do portão é uma receita e não é um arquivo de doc**, então o scanner estruturalmente não a via. Um conserto chegou a um caminho e não ao outro — a mesma forma que `revisao-que-invalida-plano.md:62` descreve para `counts_as_modified_file`, e que a docstring de `touch_files` (`transactional_state.py:921-945`) descreve para o laço de arquivos.

---

## 3. A pergunta do item 2, respondida

> *Um comando que não se sabe classificar deve invalidar evidência, ou deve deixá-la de pé e registrar a dúvida?*

**Deve continuar invalidando enquanto a dúvida existir — mas a dúvida é evitável, e o sinal que a remove já está ao alcance do hook.**

Manter o fail-closed é certo: um `python - <<PY ... write_text(...) PY` escreve de verdade, e não há como saber lendo a linha de comando. Trocar isso por "na dúvida, deixa passar" faria número velho passar por fresco, que é a falha que o portão existe para impedir.

Só que a pergunta certa não é *este comando escreve?* — é *o código sob teste mudou?*. `nao_muda_a_arvore` (`:360-373`) já enuncia o critério: "o comando escreve, mas não no código que a suíte mede". E a árvore é **observável depois do fato**, sem adivinhar nada sobre o comando:

```
digesto = git rev-parse HEAD  +  git status --porcelain=v1 -z
```

Guardado no balde da sessão e comparado a cada `PostToolUse`:

- **digesto igual** ⇒ o comando não mudou o código que a suíte mede ⇒ não invalidar. Registrar o toque com `origem="shell-sem-mudanca"` e **sem** subir `code_revision` (exige método novo — `touch_files` sempre sobe, `transactional_state.py:961`).
- **digesto diferente** ⇒ invalidar, e agora com atribuição real: os caminhos alterados estão no próprio `git status`, então `shell-placeholder` some e vira caminho.
- **digesto não calculável** (não é repo git, `git` ausente, timeout de 5 s do hook) ⇒ comportamento de hoje. O fail-closed fica intacto.

Isso mede MUDANÇA onde hoje se mede ATIVIDADE, com a régua que o próprio arquivo já usa. **Não foi implementado aqui**, e a razão é de método, não de escopo: muda o veredito do portão, custa um subprocesso por comando de shell dentro de um timeout de 5 s, e nesta sessão não há como rodar um único teste. Um conserto de veredito entregue sem nenhuma medição seria indistinguível de afrouxamento — que é a coisa que o despacho proíbe em primeiro lugar.

**Pergunta para o autor, já que esta sessão não pode perguntar ao vivo:** o custo de um `git status` por `PostToolUse` de shell é aceitável nesta máquina? É a única incógnita real do desenho; o resto é mecânico.

---

## 4. O que foi consertado

**Alvo: §2, a gravação que invalida a si mesma.** Escolhido porque é o único dos três itens cujo conserto não muda o veredito do portão — ele muda um texto — e porque o item 3 do despacho diz que vale mesmo que o item 2 não tenha conserto.

`hooks/harness-transactional.py`:

- **`CLI_DE_ESTADO`** (`:18-21`): o `state_cli.py` que acompanha *este* hook, derivado de `SCRIPTS`, portanto de `__file__`, em `as_posix()`. É mais correto que `plugin-root`, que responde outra pergunta e já apontou para versão antiga (`tests/test_arsenal.py:853`). A barra normal é necessária: barra invertida dentro de aspas duplas é escape para `_scan_composition` (`:69`).
- **`comando_de_evidencia(bucket, task_id)`** (`:969-1002`): monta a linha em **uma chamada, caminho literal, sem composição nenhuma**. `is_state_management` passa a isentá-la, e o bloco de toque inteiro é pulado em `:883`.
- **`_motivo_do_gate`** passa a chamá-la (`:1033`).

Uma linha, nada mais: `python "<root>/scripts/state_cli.py" --home "<balde>" evidence --task <id> --type test --command-text "python -m pytest -q" --exit-code 0 --tests-collected <N> --tests-passed <P> --tests-skipped <S>`

---

## 5. A falsificação, nas duas metades

Escrita. **Não executada** — ver §0.

**Metade 1, o conserto para de invalidar no caso que não muda código.**
`tests/test_transactional_hook.py::test_receita_do_portao_nao_invalida_a_propria_evidencia`, parametrizado em `template` e `preenchido` (`<N>` e `1` tokenizam diferente, e quem copia substitui antes de rodar). Ele pega o texto **real** que a mensagem imprime — não uma reconstrução —, passa pelo `handle_payload` de produção, e exige `code_revision` inalterada e `verified` ainda `True`. A mensagem de falha imprime o comando e os três últimos toques, para que reprovar nomeie a causa.

**Metade 2, o guarda continua pegando o que existe para pegar.**
`tests/test_transactional_hook.py::test_receita_composta_continua_invalidando`. A forma composta antiga fica escrita no arquivo como constante e tem de **continuar** subindo `code_revision`; junto dela, `state_cli ... && sed -i s/a/b/ x.py`. Exige `+2` exatos e `verified is False`. Sem esta metade, "consertei a mensagem" e "afrouxei `is_state_management`" dariam o mesmo verde.

**O elo, na altura em que a regra é enunciada.**
`tests/test_receita_do_manual.py::test_a_receita_do_portao_tambem_e_receita`: a receita do portão é uma receita, e o scanner de `SKILL.md` não a alcança. Começa por um censo (o comando cita mesmo um CLI de estado) para não passar verde sobre nada — a mesma proteção de `test_o_scanner_acha_alguma_receita`.

### 5.1 Testes existentes conferidos por leitura

Nenhum foi executado. Cada um foi lido linha a linha contra a mudança:

| Teste | Por que sobrevive |
|---|---|
| `test_comando_que_a_mensagem_imprime_de_fato_roda` (`:1148`) | corta o `argv` no token que termina em `state_cli.py"`; o caminho literal também termina assim, e o subprocesso usa `ROOT`, não o caminho impresso |
| `test_mensagem_do_gate_cita_o_que_leu` (`:1058`) | exige `state_cli.py` e `--tests-skipped` na mensagem; ambos preservados |
| `test_inv4_no_unguarded_default_path_composition` | dispara só em linha com `expanduser`/`Path.home()`; a mudança **remove** a única menção a `.claude` dessa função |
| `test_composicao_continua_nao_isenta` (`test_receita_do_manual.py:206`) | não toca em `is_state_management`, que não mudou |

**Risco declarado:** conferência por leitura não é execução. É a diferença entre este documento e um verificado.

---

## 6. Como fechar — os comandos exatos

Numa sessão com permissão de executar, na raiz deste worktree:

```bash
# 1. Linha de base, ANTES de qualquer mudança (git stash ou checkout de bdce21b).
python -X utf8 -m pytest -q --tb=no

# 2. Com a mudança aplicada.
python -X utf8 -m pytest -q --tb=no

# 3. As duas metades, isoladas.
python -X utf8 -m pytest -q tests/test_transactional_hook.py -k receita
python -X utf8 -m pytest -q tests/test_receita_do_manual.py

# 4. Prova de que a metade 1 reprova sem o conserto: reverter comando_de_evidencia
#    para a forma composta e confirmar que test_receita_do_portao_... FALHA.
#    Teste que nunca foi visto vermelho não é guarda.
```

**Ler a contagem, não o `exit_code`.** As duas contagens do passo 1 e 2 entram aqui, com a diferença explicada linha a linha. `bdce21b` deveria ter ~1 375 testes segundo o despacho; a última contagem registrada em memória é `1329 passed` (2026-09-17, sessão `revisao-que-invalida`) e `docs/specs/portao-mede-a-arvore-verification.md` **não existe na árvore** — a sessão que mesclou `d9c6b2c` nunca escreveu a sua verificação. Nenhum dos dois números foi conferido aqui.

Medição por `origem`, depois que `touches` ganhar o comando (§1.1):

```sql
SELECT origem, COUNT(*) FROM touches WHERE task_id = ? GROUP BY origem;
SELECT comando_hash, COUNT(*) FROM touches
 WHERE task_id = ? AND origem = 'shell-placeholder'
 GROUP BY comando_hash ORDER BY 2 DESC LIMIT 10;
```

A segunda é a que separa "o ruído caiu" de "o guarda cegou".

---

## 6.1 Duas observações ao vivo, colhidas de produção nesta sessão

O portão bloqueou a resposta final desta sessão. A mensagem que ele devolveu é dado de produção, não reconstrução — e ela confirma duas coisas que o resto deste documento só tinha traçado por leitura.

**Primeira: o portão entregou exatamente o comando composto do §2.2.** Texto literal recebido:

```
Rodar a suite em primeiro plano ja grava sozinho. Para registrar a mao:
PR="$(cat "${HARNESS_DIR:-$HOME/.claude/harness}/plugin-root")"; python "$PR/scripts/state_cli.py" --home "..." evidence --task t-20260918-035336576310 --type test ...
```

O hook em produção ainda imprime a forma com `;`. §2.2 deixa de ser inferência: **o portão em uso manda copiar um comando que não é isento, e por isso sobe `code_revision` depois de o CLI gravar a evidência.** O conserto do §4 continua sem ter sido executado, mas o alvo dele está confirmado onde importa.

**Segunda: três `Edit` num `.md` produziram três invalidações.** Leitura do portão, verbatim:

```
code_revision=15 | verified=False | a tabela `evidence` desta task esta VAZIA (0 linhas)
Ultima(s) invalidacao(oes): rev=13 ...\docs\specs\portao-mede-atividade-verification.md (edit) ;
                           rev=14 ...\docs\specs\portao-mede-atividade-verification.md (edit) ;
                           rev=15 ...\docs\specs\portao-mede-atividade-verification.md (edit)
```

As três são o **mesmo arquivo**: este documento. `counts_as_modified_file` (`post_tool_policy.py:86-96`) conta toda escrita dentro do repositório, e de propósito — `tests/test_orfaos.py` lê `tools/README.md`, então um `.md` versionado pode mudar o resultado da suíte. A regra está certa pelo que sabe. O efeito colateral é que **escrever o documento de verificação de uma task invalida a evidência daquela task**, uma vez por `Edit`, e quem terminar de escrever tem de rodar a suíte de novo.

É a mesma forma do §2: o guarda mede ATIVIDADE (houve escrita) onde a pergunta é MUDANÇA (o que a suíte mede mudou). E é a mesma forma que `touch_files` já corrigiu uma camada abaixo — lá, N caminhos num `Edit` valem uma invalidação; aqui, N `Edit`s no mesmo caminho valem N. O digesto de árvore do §3 resolve os dois casos pela mesma régua, porque reescrever um `.md` **muda** a árvore e portanto continuaria contando — o que este parágrafo defende não é parar de contar `.md`, é contar uma vez o que mudou uma vez.

---

## 7. O que ficou aberto

1. **Medição por `origem`, antes e depois** — bloqueada por §0, e parcialmente bloqueada por §1.1 mesmo com execução: `touches` não guarda o comando.
2. **As duas contagens de suíte** — bloqueadas por §0.
3. **O digesto de árvore (§3)** — desenhado, não implementado, com uma pergunta aberta de custo para o autor.
4. **`comando_hash` em `touches`** — pré-requisito de (1).
5. **Qual versão do hook roda em produção** — o cache do plugin não é legível desta sessão; se ele for anterior a `portao-de-stop` (2026-09-16), parte dos 196 placeholders tem outra causa, já consertada.
6. **Esta task (`t-20260918-035336576310`) fica em vermelho declarado.** O portão bloqueou a resposta final por falta de evidência de teste, e a leitura dele está certa: `evidence` vazia, 0 linhas. Não há como satisfazê-lo desta sessão — `pytest` é recusado pela camada de permissão. **Causa:** sessão `--print` sem quem aprove execução de interpretador. **Ação:** rodar o §6 numa sessão com permissão e registrar a evidência então. **Dono:** o autor. Registrado aqui em vez de contornado, porque contorno silencioso é o que o próprio portão existe para impedir.
7. **`docs/specs/portao-mede-a-arvore-verification.md` ausente** — `d9c6b2c` mesclou cinco consertos (`R2`, `R3.1-R3.3`, `R4`, `R5`) sem o documento de verificação que o próprio `test_receita_do_manual.py:233` cita. Fora do escopo desta sessão; registrado porque some em silêncio.
