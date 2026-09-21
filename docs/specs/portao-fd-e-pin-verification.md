# Verificação — os dois defeitos do portão medidos em 2026-09-21

Branch `claude/portao-fd-e-pin`. Origem: a sessão `86459dbf` bloqueada seis vezes seguidas pelo gate de verificação, relatada com dois sintomas. **Os dois sintomas eram reais; nenhuma das duas hipóteses que os acompanhavam sobreviveu à medição.** O que este documento registra é o que foi medido, com a função de produção, e o que mudou por causa disso.

| Item | Estado |
|---|---|
| Causa do defeito 1 | **medida** — reproduzida byte a byte do registro de produção |
| Causa do defeito 2 | **medida** — o pin real replayado pelas duas versões |
| Falsificação, as duas metades | **medida** — tabela ANTES→DEPOIS, 10 casos |
| Suíte | 1 379 → 1 403 passando, 0 falhando |
| Hipóteses do enunciado | **duas refutadas**, escritas abaixo |

---

## 1. Defeito 1 — a causa não era a composição que o enunciado supunha

O enunciado lia: *"cada shell-command incrementa `code_revision`"*, e *"o `state_cli.py` é ele mesmo um shell-command, então grava em rev=N e imediatamente leva o contador a N+1"*.

A segunda metade está certa e já era conhecida: `state_cli evidence` grava em `row["code_revision"]` e o `PostToolUse` roda depois. Foi exatamente isso que o commit `e72c546` (2026-09-18) consertou, tornando a receita do portão **atômica** — sem `;`, sem `$( )` — para que ela caísse na isenção de `is_state_management` e nenhum toque nascesse atrás dela.

**A primeira metade está errada, e é ela que explica por que o conserto de 18-set não segurou.** Nem todo shell-command incrementa: há quatro isenções no arquivo. O que aconteceu foi que **todas as quatro estavam sendo derrubadas pela mesma coisa**.

### 1.1 O que o registro de produção diz

`recusas.jsonl` do balde `science-harness-f34c6792`, linha de `2026-09-21T09:04:48` — o instante exato do toque `rev=5` que órfãou a evidência gravada em `rev=4`:

```json
{"comando": "python \"C:/Users/LHarden2/.claude/plugins/cache/.../state_cli.py\" --home \"...\" ...",
 "motivo": "vazio-ou-operador", "candidato": "&"}
```

O comando começa com `python "` — é a forma atômica, a receita consertada. E mesmo assim gerou recusa e toque. O que o campo `candidato` denuncia é um `&` que o extrator de destinos de redirecionamento encontrou e não conseguiu tratar como caminho.

O `comando` fica truncado em 200 caracteres (`harness-transactional.py:827`), então o sufixo não aparece. A reprodução veio de rodar a **função de produção** sobre a receita literal mais cada sufixo candidato:

| comando | `is_state_management` | recusa produzida |
|---|---|---|
| receita atômica, literal | `True` | — |
| receita `+ 2>&1` | `False` | **`('&', 'vazio-ou-operador')`** |
| receita `+ > /dev/null` | `True` | `('/dev/null', 'destino-nulo')` |
| receita `+ \| tail -1` | `False` | — |
| receita com `cd ... &&` antes | `False` | — |

Só `2>&1` reproduz a assinatura exata do registro. **A causa é `2>&1`.**

### 1.2 Por que um redirecionamento de descritor virou composição

`_OPERADORES` (`harness-transactional.py:55`) inclui `&` sem olhar o contexto. `_scan_composition` percorre a linha e devolve no primeiro operador fora de aspas. Em `2>&1`, o `&` é duplicação de descritor: aponta o fd 2 para o fd 1, dentro do mesmo processo. **Não introduz comando nenhum, e não escreve em arquivo nenhum.**

Como a varredura é uma só e as quatro isenções dependem dela, `2>&1` derrubava as quatro de uma vez:

| guarda | com `2>&1`, antes | consequência |
|---|---|---|
| `is_state_management` | `False` | a receita do portão invalidava a própria evidência |
| `is_trusted_verification` | `False` | `python -m pytest -q 2>&1` **não gravava evidência** |
| `is_read_only` | `False` | `git status 2>&1` subia `code_revision` |
| `nao_muda_a_arvore` | `False` | `git commit 2>&1` idem |

A terceira e a quarta caem por outra porta, não pela varredura: `_segmentos` quebrava em `>` e em `&`, o fragmento `2` virava segmento próprio, e `2` não é binário conhecido. Duas portas, uma causa.

**Isto fecha o laço que o enunciado descreveu.** Com `is_trusted_verification` derrubado, a frase da mensagem — *"rodar a suíte em primeiro plano já grava sozinho"* — deixava de ser verdade, e a única saída restante era o caminho manual. Com `is_state_management` derrubado, o caminho manual também não fechava. A sessão não tinha saída nenhuma: era a condição impossível, não implementação ruim.

### 1.3 A escala

Contado em todos os baldes de `~/.claude/harness/projects/`:

| medida | valor |
|---|---|
| linhas de recusa registradas | 2 292 |
| **recusas de candidato `&`** | **1 062 (46,3%)**, em **649 comandos distintos** |
| segunda classe (`/dev/null`) | 489 (21,3%) |
| linhas de `evidence` gravadas | 246 |
| **invalidadas por um toque ≤3 s depois** | **101 (41,1%)** |
| tasks que gravaram evidência | 66 |
| **e mesmo assim terminaram `verified=0`** | **23 (34,8%)** |

Na task do relato, `t-20260921-090314300233`: **33 toques, 33 com origem `shell-placeholder`**, nenhum caminho atribuído em nenhum. **13 linhas de `evidence`, 13 invalidadas** por um toque 0–2 s depois. A receita do próprio portão aparece 17 vezes entre os comandos recusados.

### 1.4 Sobre `shell-placeholder`

O enunciado perguntava se `shell-placeholder` é *"um default preenchido quando o hook não consegue identificar o comando"*, da mesma família de `dict.get` com default.

**É um default, mas o dano não vinha dele.** Ele nasce em `harness-transactional.py:895`, `origem="shell" if alvos else "shell-placeholder"`, e é honesto: significa "houve comando de shell e eu não sei que arquivo ele tocou". O `path` vira a constante `shell-command`, e a tabela `touches` não tem coluna de comando — ela responde *quantas invalidações*, nunca *qual comando*. A pergunta "qual comando" só é respondível pelo `recusas.jsonl`, e foi por ele que esta investigação passou.

O que o placeholder faz de errado é ser **invisível como diagnóstico**: 33 linhas iguais não distinguem um `sed -i` real de um `git status 2>&1`. Consertar a causa reduz a população dele; **não** foi alterado o desenho de invalidar quando não se sabe. Invalidar por dúvida é fail-closed e está certo.

### 1.5 O conserto

`_duplicacao_de_fd(command, index)` decide se um `&` é parte de `2>&1`. A porta é estreita, e a estreiteza **é** a correção:

- exige `>` ou `<` **colado antes** — em `&&` o caractere anterior é espaço ou o próprio `&`, então `a && b` e `sleep 1 &` seguem compostos;
- exige dígito ou `-` **colado depois** — então `a &> saida.txt` segue composto, porque aquilo é redirecionamento para **arquivo**, escrita de verdade, e `shell_write_targets` tem de continuar vendo o alvo.

Consumido em dois pontos: `_scan_composition` (as duas primeiras isenções) e `_segmentos` (as duas últimas).

A mensagem do portão ganhou `AVISO_LINHA_SOZINHA`. Ela documentava um caminho sem documentar a condição dele: a receita é isenta **enquanto a linha não tem composição**, e um `cd ... &&` na frente a tira da isenção em silêncio — o CLI responde `verified: true` e o portão bloqueia de novo. Agora está escrito, com `2>&1` nomeado como permitido.

---

## 2. Defeito 2 — o balde não é de outro projeto; é do projeto de nove dias atrás

O enunciado levantava duas hipóteses, marcadas como não verificadas: *"additional working directories"* e *"worktree tem `.git` como ARQUIVO"*.

**As duas foram refutadas.** O resolvedor chamado com o `cwd` desta sessão devolve o slug certo, inclusive de dentro de um worktree:

```
harness_paths.py --cwd <worktree do slb> --session-id <esta sessão>
  -> ...\projects\slb-mestrado-projeto-7c89ab0c\sessions\...
```

A causa é o **pin de sessão** — o mecanismo introduzido para consertar o incidente de 2026-09-12, documentado no topo de `harness_paths.py`. O pin fixa o primeiro slug que a sessão cunha e o reusa mesmo com outro `cwd`. Ele não tinha validade.

Pin real de `86459dbf`, lido em 2026-09-21:

```json
{"project_slug": "science-harness-f34c6792",
 "pinned_at": "2026-09-12T08:12:52",
 "drifts": [{"slb-mestrado-projeto-7c89ab0c", "2026-09-12T16:03:22"},
            {"mainframe-2268cab9",            "2026-09-15T18:59:18"},
            {"harness4claude-7aba6948",       "2026-09-17T17:35:56"}]}
```

Nove dias, quatro projetos, e a atividade de 2026-09-21 (09:03 → 15:40) inteira em `slb-mestrado-projeto`. O estado continuou indo para o balde cunhado no primeiro dia.

O id da sessão sobrevive ao trecho de trabalho: o host retoma a sessão dias depois, o `cwd` é outro, e o pin não tem como saber disso. **O pin consertou 2026-09-12 e criou 2026-09-21.**

### 2.1 Quantas tasks foram parar no balde errado

Cruzando `~/.claude/harness/projects/*/sessions/*` com o `cwd` gravado **dentro** de cada transcript:

| medida | valor |
|---|---|
| sessões com balde e `cwd` registrado | 325 |
| **partidas em mais de um balde** | **5** |
| balde que não bate com nenhum `cwd` real | 3 (0,9%) |

Para `86459dbf` isoladamente: **110 tasks** no balde `science-harness` (82 não verificadas) contra **27** no balde `slb` (23 não verificadas), `code_revision` máximo 671.

> ⚠️ **Um número descartado, e por quê.** A primeira contagem deste cruzamento deu **312 de 329 erradas**. Era erro de instrumento: eu decodificava o `cwd` pelo nome da pasta do transcript, trocando hífen por barra, e `science-harness` virava `science/harness`. Número plausível, mecanismo errado. A contagem válida usa o campo `cwd` gravado nas linhas do transcript. `[superado 2026-09-21: 312 de 329 — instrumento, não achado.]`

### 2.2 O conserto

O pin passa a ter validade, e a régua exige as **duas** condições: ele só cede quando ficou **parado além do TTL** *e* o projeto corrente é **outro**.

- Ceder só por tempo repinaria uma sessão que voltou ao mesmo lugar.
- Ceder só por projeto é exatamente o incidente de 2026-09-12 de volta.

`last_seen_at` avança a cada resolução, **inclusive em deriva**, porque o que vence é a inatividade da **sessão**, não a do projeto: quem alterna entre dois repositórios a tarde inteira mantém um balde só. `HARNESS_PIN_TTL_H` (default 24 h, `0` desliga) é o botão. O balde anterior fica escrito em `repins` — trocar de balde em silêncio é como perder o trabalho guardado no lugar errado.

Data ilegível **não** vence o pin, de propósito: a direção do erro importa, e repinar por engano parte o estado em dois bancos.

---

## 3. Falsificação — as duas metades, medidas

### 3.1 Defeito 1: os mesmos guardas, antes e depois

| comando | guarda | antes | depois |
|---|---|---|---|
| `state_cli.py evidence ... 2>&1` | `is_state_management` | `False` | **`True`** |
| `python -m pytest -q 2>&1` | `is_trusted_verification` | `False` | **`True`** |
| `git status 2>&1` | `is_read_only` | `False` | **`True`** |
| `git commit -m x 2>&1` | `nao_muda_a_arvore` | `False` | **`True`** |
| `state_cli.py ... 2>&1 && sed -i s/a/b/ x.py` | `is_state_management` | `False` | `False` |
| `python -m pytest -q \| tail -5` | `is_trusted_verification` | `False` | `False` |
| `git status && rm -rf x` | `is_read_only` | `False` | `False` |
| `python gera.py > saida.py 2>&1` | `is_read_only` | `False` | `False` |
| `python x.py &> log.txt` | `is_state_management` | `False` | `False` |
| `sleep 1 &` | `is_read_only` | `False` | `False` |

Quatro mudaram — os quatro casos de duplicação de descritor. Seis não mudaram — toda composição real e toda escrita real. **Sem a metade de baixo, "consertei" e "afrouxei o guarda" dariam o mesmo verde.**

### 3.2 Defeito 2: o pin real, replayado pelas duas versões

Mesmo arquivo de pin, mesmo `cwd` de 2026-09-21, copiado para uma raiz temporária:

```
ANTES  -> balde = science-harness-f34c6792      (pin intacto)
DEPOIS -> balde = slb-mestrado-projeto-7c89ab0c (repins = ['science-harness-f34c6792'])
cwd real da sessão -> slb-mestrado-projeto-7c89ab0c
```

E a metade de baixo, em teste: `test_pin_vigente_em_outro_projeto_nao_cede` prova que o incidente de 2026-09-12 continua consertado. Sem ela, *"pin com validade"* e *"pin removido"* dariam o mesmo verde.

### 3.3 O portão inteiro, pelo `handle_payload` de produção

Tabela de guardas prova que a função mudou; ela não prova que o **portão** mudou. Este cenário roda a sequência inteira — criar task, emitir `PostToolUse`, emitir `Stop` — pelo ponto de entrada real, nas duas versões:

| cenário | ANTES | DEPOIS | esperado |
|---|---|---|---|
| A. suíte com `2>&1` — o caso do relato | BLOQUEOU | **LIBEROU** | LIBERAR ✔ |
| B. suíte atômica, sem redirecionamento | LIBEROU | LIBEROU | LIBERAR ✔ |
| C. nenhum teste rodado | BLOQUEOU | **BLOQUEOU** | BLOQUEAR ✔ |
| D. teste REPROVANDO (`1 failed`, exit 1) | BLOQUEOU | **BLOQUEOU** | BLOQUEAR ✔ |
| E. suíte verde e **depois** um `sed -i` real | BLOQUEOU | **BLOQUEOU** | BLOQUEAR ✔ |
| F. suíte verde e depois `git status 2>&1` | BLOQUEOU | **LIBEROU** | LIBERAR ✔ |

C, D e E são a metade que importa: **o portão continua pegando o que ele existe para pegar.** A e F são o defeito consertado. B prova que o caminho que já funcionava não regrediu.

> ⚠️ **A primeira versão deste cenário era inútil e dizia o contrário.** Ela emitia `UserPromptSubmit` no hook transacional esperando que isso criasse a task — não cria; quem cria é `harness-classify.sh`. Sem task ativa, `_handle_stop` retorna vazio na primeira linha, e **todos os seis cenários "LIBERARAM", inclusive "nenhum teste rodado" e "teste reprovando"**. O erro só apareceu porque os casos negativos estavam na tabela. Um cenário que só testa o caso positivo teria sido lido como conserto confirmado. `[superado 2026-09-21: "o portão liberou no smoke test" — o smoke test não tinha task.]`

### 3.4 Um teste que mudou de lado, e por quê

`test_comando_de_leitura_puro_continua_sem_subir_o_contador` afirmava `is_read_only("grep -rn alvo hooks 2>&1") is False`, com a ressalva escrita `[superado: "grep ... 2>&1 é read-only"] — não é, e nunca foi`.

A descrição estava **certa sobre o código** e errada como contrato. Aquilo era um limite descrito, nunca uma propriedade desejada — um falso positivo barato de aceitar enquanto ninguém tinha medido o preço. O preço foi medido: §1.3. A asserção foi invertida **com o motivo escrito no lugar da linha antiga**, e ganhou três asserções novas que impedem a leitura preguiçosa do parágrafo: `> achados.txt`, `> achados.txt 2>&1` e `2>&1 && sed -i` continuam não sendo leitura.

---

## 4. Suíte

| | |
|---|---|
| baseline em `main` (coleta anterior às edições) | **1 379 passando · 6 subtests · 912,9 s · exit 0** |
| neste branch, execução limpa | **1 410 passando · 6 subtests · 895,9 s · exit 0** |
| delta | **+31, nenhuma falha** |

Os 31 são 25 testes novos mais as parametrizações: 16 em `test_transactional_hook.py` (incluindo `2>&1`, `1>&2`, `>&2`, `2>&-`, `0<&-` e as dez formas de composição real), 9 em `test_harness_paths.py::TestPinVence`. Mais um teste existente com asserção invertida e justificada (§3.4).

> ⚠️ **A mensagem do commit `ec74271` diz "1 379 -> 1 403". O número está errado.** `[superado 2026-09-21: 1 403 — nunca foi medido.]` Eu o escrevi contando testes à mão enquanto a execução limpa ainda rodava, em vez de esperar o total. Fica registrado porque é o erro que este repositório mais persegue, cometido no commit que o persegue: **número plausível no lugar da medição**. O valor medido é 1 410.

A primeira execução de verificação foi descartada por contaminação: ela começou antes das últimas edições e foi interrompida em 40%. Um resultado de suíte cujo código mudou no meio não responde nada.

---

## 5. O que este conserto NÃO faz

- **Não corrige o estado já gravado.** As 110 tasks de `86459dbf` continuam no balde `science-harness`, e as 23 tasks com evidência e `verified=0` continuam assim. Migrar estado é decisão do autor, não consequência deste conserto.
- **Não está no ar.** O plugin roda de `~/.claude/plugins/cache/harness4claude/...`, que é cópia de `main`. Enquanto este branch não for mesclado e o cache não for atualizado, o comportamento medido em 2026-09-21 continua valendo em sessões novas. Verificado: a árvore do cache é idêntica à de `main` — a deriva aparente em 27 arquivos é **só fim de linha** (terceira vez que esse mesmo instrumento engana neste repositório).
- **Não limpa o `recusas.jsonl`.** Os 1 062 `&` continuam sendo registrados como recusa mesmo depois do conserto, porque `shell_write_targets` segue sem conseguir tratar `&1` como caminho — e está certo: aquilo não é caminho. A recusa é o registro de atribuição de escrita, não a decisão de isenção. O log fica com essa fração de ruído, declarada.
- **Não mexe em `_escopo.py`**, que é derivado e não se edita. O pin vive em `state_dir`, não em `project_slug`, exatamente como a nota de 2026-09-12 exige.
