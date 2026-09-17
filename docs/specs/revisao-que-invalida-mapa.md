# Mapa — o que invalida a evidência, medido

**Data:** 2026-09-17 · **Branch:** `claude/revisao-que-invalida` · **Base:** `6e7f4e5`
**Escopo:** mapeamento. Nenhuma linha de código de produção foi alterada nesta sessão.

Este documento responde a uma pergunta só: *quando o portão declara a evidência velha, o que mudou de verdade?* A resposta medida é **quase nunca o código**.

---

## 0. Fontes

| Afirmação | Fonte que manda | Nível | Nota |
|---|---|---|---|
| O que entra em `files` e quando `code_revision` sobe | `scripts/transactional_state.py:911-945` (`touch_files`) | 1 | não filtra nada; todo filtro é do chamador |
| Quem extrai caminho de um comando de shell | `hooks/harness-transactional.py:341-388` (`shell_write_targets`) | 1 | e `_tokenize` em `:181-230` |
| Quem decide se um `Edit`/`Write` conta | `hooks/harness-reclassify.sh:135` → `scripts/post_tool_policy.py:86` | 1 | já tem filtro de raiz |
| A régua de evidência fresca | `scripts/transactional_state.py:48-54` + `:1118-1131` | 1 | `REGRA_TESTE_VALIDO` + `_has_fresh_test_evidence` |
| Por que `complete` recusa | `scripts/transactional_state.py:1067-1072` | 1 | exige `verified` **e** evidência na revisão atual |
| A isenção do `state_cli` | `hooks/harness-transactional.py:152-173` | 1 | estreita de propósito, e a estreiteza é o problema |
| Quem mais lê a tabela `files` | `scripts/record_signal.py:68` → `actual_level` | 1 | segundo consumidor, danificado junto |
| Os números da sessão de coordenação | `harness.db` de `86459dbf-…-06f60408` | 1 | lido em modo `ro` |
| Os números desta sessão | `harness.db` de `022e090a-…-c72485c9` | 1 | verdade-base: eu sei o que rodei |

**Discordância registrada:** a `SEMENTE.md` (nível 7) afirma quatro coisas que o nível 1 contradiz. Estão nomeadas em §6.

---

## 1. O número do autor está certo, e o denominador dele não

A régua do autor — *"sem separador e sem extensão não parece caminho"* — reproduzida sobre a mesma tabela:

| | autor (2026-09-17, mais cedo) | reproduzido agora |
|---|---:|---:|
| linhas em `files` | 466 | **467** |
| não parecem caminho | 122 = 26,2% | **121 = 25,9%** |

O banco cresceu uma linha entre as duas leituras. **A medição dele reproduz.**

O que não reproduz é a conta de revisões. O autor usou `count(distinct first_seen_code_revision)` e obteve 268. Mas `code_revision` é **por task**, e as dez tasks deste banco reusam os mesmos inteiros: a revisão 22 da task de 09-09 e a revisão 22 da task de 09-12 colapsam numa só. Contando o par `(task_id, first_seen_code_revision)`, que é a unidade real:

| | autor | correto |
|---|---:|---:|
| revisões distintas | 268 | **366** |
| criadas só por entradas que não são caminho | 80 = **29,9%** | 67 = **18,3%** |

**`[superado: 29,9% — denominador colapsado entre tasks]`.** Pela régua do próprio autor o número é **18,3%**, não 30%.

Isso não absolve o sistema. Absolve só aquela régua.

---

## 2. A régua do autor subestima o lixo pela metade

A régua dele aceita como caminho legítimo qualquer coisa com separador **ou** extensão. Isso deixa passar 113 entradas que **não podem ser um arquivo neste sistema de arquivos** — variável de shell nunca expandida, componente terminando em `:`, corpo de documento inteiro:

```
'$TEMP\claude\orfaos.py'        '$D\portao-de-stop.seed.md'      '$S\f_$n.out'
'$SH\docs\closure\README.md'    '$LOCK\dono'                     '$P\$f'
'\dev\null) main 2>\dev\null && echo SIM || echo '?')'
'$repo\n& subst $($letra): $repo 2>&1 | Out-Null\nif (-not (Test-Path …
```

Régua usada aqui, declarada para poder ser contestada: **não é que seja um caminho improvável — é que não pode SER um caminho.** Caractere ilegal em nome no Windows (`< > " | ? *`, quebra de linha); `$var` / `${` / `$(` não expandido; nenhum alfanumérico; componente terminando em `.`, espaço ou `:` que não seja letra de drive; dispositivo nulo; e, sem separador, forma que não é nome de arquivo.

| régua | linhas | % de 467 |
|---|---:|---:|
| do autor (sem separador e sem extensão) | 121 | 25,9% |
| esta (não pode ser um caminho) | **233** | **49,9%** |
| só esta pega (o autor deixou passar) | 113 | |
| só o autor pega (falso positivo dele) | 1 | |

**Erro do meu próprio instrumento, registrado em vez de apagado:** a primeira versão desta régua classificou `'0.75'`, `'0.99999'` e `'F4.0'` como *caminhos válidos fora da raiz*, porque a regex de extensão era `\.[A-Za-z0-9]{1,6}$` e `.75` casa. O número saiu plausível — inflava uma classe em 3 — e por isso quase passou. Corrigido exigindo que a extensão comece por letra. É a mesma doença que a semente avisa: número plausível esconde erro de régua.

---

## 3. A pergunta que importa não é "é um caminho" — é "é código sob teste"

Classificando as 467 linhas contra a raiz do projeto daquela sessão (`…\projects\science-harness`):

| classe | linhas | % |
|---|---:|---:|
| **A** — não pode ser um caminho | 233 | 49,9% |
| **B** — caminho válido, **fora** da raiz do projeto | 133 | 28,5% |
| **C** — dentro da raiz, não é código (`.md`, `settings.local.json`) | 9 | 1,9% |
| **D** — **código sob teste** (`.py`, `.sh`, `.ps1`, `.toml`…) | **92** | **19,7%** |

A classe B é inteira de escrita real, e nenhuma delas muda o que a suíte mede: `%TEMP%\claude\*.py` (scripts de diagnóstico), `.claude\plans\*.md`, `.claude\harness\projects\*\branches\*.seed.md`, `.claude\projects\*\memory\*.md`.

Por revisão, a conta que responde à pergunta do portão:

| revisões que… | n | % de 366 |
|---|---:|---:|
| são 100% lixo pela régua do autor | 67 | 18,3% |
| são 100% lixo por esta régua | 142 | 38,8% |
| **não contêm NENHUM código sob teste** | **274** | **74,9%** |

Por task, a proporção de revisões sem nenhum código sob teste:

| task | revisões com rastro | sem código |
|---|---:|---:|
| `t-20260909-061833822991` | 51 | 62,7% |
| `t-20260910-134640724724` | 55 | 60,0% |
| `t-20260912-020007618177` | 97 | 68,0% |
| `t-20260914-193735299237` | 61 | 70,5% |
| `t-20260916-113513958619` | 88 | **100,0%** |
| `t-20260917-135651643831` | 14 | 85,7% |

---

## 4. E o banco não consegue responder pelas outras 85%

`touch_files` usa `INSERT OR IGNORE`. Tocar de novo um caminho já visto **não cria linha** — mas **sobe `code_revision` do mesmo jeito**. Somando o `code_revision` final das seis tasks: **2 594 subidas**. Delas, apenas **366 (14,1%)** deixaram rastro em `files`.

**As outras 2 228 invalidações são invisíveis.** Não há tabela que diga o que as causou; a informação foi descartada na escrita. Qualquer percentual calculado sobre as 366 é um **limite inferior sobre uma amostra enviesada** — enviesada para o lado de *ter* nomeado um arquivo novo, que é o caso mais favorável ao portão. `[NEEDS CLARIFICATION: nenhuma fonte deste banco responde o que causou as 2 228 restantes.]`

---

## 5. A medição de ouro: esta sessão, com verdade-base

Esta sessão de mapeamento não alterou uma linha de código de produção. Banco próprio, no fim da sessão:

```
task t-20260917-181421930426 · status=done · code_revision=24 · verified=1
files:  rev= 1  'shell-command'
        rev= 7  '…\022e090a-…\scratchpad\files_dump.txt'
        rev=16  '…\docs\specs\revisao-que-invalida-mapa.md'
        rev=17  '…\docs\specs\revisao-que-invalida-plano.md'
        rev=20  '…\docs\specs\revisao-que-invalida-verification.md'
        rev=21  'MSGEOF'
evidence: (24, exit 0, 1329 collected, 1329 passed, 0 skipped)
```

**Vinte e quatro invalidações. Zero arquivos de código.** Nenhum `.py`, `.sh` ou `.ps1` na lista. Dezoito delas não deixaram linha em `files` porque o caminho já estava lá — o mecanismo da §4, ao vivo.

Na leitura intermediária, com `code_revision = 9`, havia duas linhas. A atribuição naquele ponto: 8 invalidações pelo placeholder `shell-command` (toda invocação de interpretador), 1 pelo redirecionamento para o scratchpad, 0 por mudança de código.

**A linha `rev=21` é o achado inteiro se fechando sobre si.** `'MSGEOF'` é o delimitador do heredoc de `git commit -F - <<'MSGEOF'` — o commit **deste mapa**. Ele virou "arquivo" porque a última linha da mensagem é a de atribuição, `Co-Authored-By: … <noreply@anthropic.com>`, e o `>` que fecha o endereço de e-mail é um operador de redirecionamento para `_tokenize`. O token seguinte é o delimitador de fechamento. O mecanismo descrito na §6.2 disparou no ato de documentá-lo.

E a linha `rev=7` é a assimetria nua: `files_dump.txt` está no **scratchpad**, fora do repositório. Criado com `Write`, **não** contaria. Criado com `>`, contou.

E a linha da revisão 7 é o achado inteiro numa linha só: `files_dump.txt` está no **scratchpad**, fora do repositório. Se eu tivesse criado o mesmo arquivo com a ferramenta `Write`, ele **não** contaria. Criei com `>` e contou.

Medido com as funções de produção, sem reimplementar nenhuma:

| mesmo arquivo, mesmo lugar | veredito |
|---|---|
| `Write` → `counts_as_modified_file(tool, path, raiz)` | **False** — não conta |
| `echo … > <mesmo caminho>` → `shell_write_targets` + `touch_files` | **True** — conta e sobe `code_revision` |

`post_tool_policy.py:45-96` tem o filtro de raiz e ele funciona. `hooks/harness-transactional.py:580-582` chama `touch_files` **sem passar por ele**. O conserto de `b771b6b` chegou a um caminho e não ao outro.

### 5.1 O laço do Achado 5 não existe na forma atômica — medido no fim desta sessão

Fechar esta task exigiu sete chamadas de `state_cli.py` seguidas: uma de `evidence`, três de `transition`, três de `artifact`, e o `complete`. Todas na forma atômica — caminho literal, sem `;` e sem `$( )`.

`code_revision` ficou em **24 nas sete**. `verified` ficou em `1`. A evidência gravada na revisão 24 continuou fresca até o `complete`, que devolveu `status: "done"`.

É a falsificação de R4 executada por inteiro, sem nenhuma mudança de código: **o laço é da forma composta, não do desenho.** A isenção de `is_state_management` já funciona; ela só nunca é alcançada por quem copia o manual.

---

## 6. Quatro afirmações da semente que o código contradiz

### 6.1 `[superado]` — "são dois relógios" (Achado 4)

A semente diz que `files.first_seen_code_revision` chega a 647 enquanto a task corrente lê 109, e pergunta se o portão compara relógios diferentes. **Não são dois relógios.** É um contador por task, e o 647 é de outra task:

| task | max(`first_seen`) | `tasks.code_revision` |
|---|---:|---:|
| `t-20260909-…` | 392 | 393 |
| `t-20260910-…` | 563 | 565 |
| `t-20260912-…` | **647** | **671** |
| `t-20260914-…` | 329 | 331 |
| `t-20260916-…` | 509 | 513 |
| `t-20260917-…` | 116 | 121 |

`max(first_seen) ≤ code_revision` nas seis. A comparação do portão (`evidence.code_revision = tasks.code_revision`, `transactional_state.py:1122`) é dentro da mesma task e está correta. **Achado 4 encerrado: não há defeito aqui.**

### 6.2 `[superado]` — o mecanismo do lixo não é o que a semente descreve

A semente diz que o extrator *"está pegando tokens do shell — variáveis, fatias de array, fechamentos de JSON"*. Rodando `shell_write_targets` sobre exatamente esses casos:

```
python -c "print(1 if x>0.75 else 0)"          -> []
awk '{if ($3>30]) print}' f.txt                -> []
jq '.a' f.json | python -c "…read()>'x'"       -> []
```

**`_tokenize` respeita aspas** (`hooks/harness-transactional.py:184-185`, e `tests/test_transactional_hook.py:482` trava isso). A hipótese como escrita está **refutada**.

**A causa real:** `_tokenize` é um tokenizador de shell POSIX aplicado a uma string que muitas vezes **não é um comando POSIX** — ela contém corpo de heredoc, código de programa e sintaxe de PowerShell. Dentro dessas regiões o rastreio de aspas não significa nada, e todo `>` vira alvo. Reproduzido:

```
python - <<'PY'                                -> ['0.75:']
if riqueza>0.75:
PY
cat > nota.md <<'EOF'                          -> ['nota.md', 'Passar']
> Passar o titulo calcularia outra chave.
EOF
python - <<'PY'                                -> ['b:']
print("score:", x)
if a>b: pass
PY
$LOG = 'x.log'; Get-Content a.txt > $LOG       -> ['$LOG']
```

Tokenização nua do primeiro: `['python', '-', '<<PY', 'if', 'riqueza', '>', '0.75:', 'print(alto)', 'PY']`.

**`'0.75:'` é literalmente a linha `rev=230` da task `t-20260909`**, e acabou de ser regenerada de um heredoc. O segundo caso explica as duas outras assinaturas de uma vez: um heredoc que escreve markdown com citação em bloco devolve o arquivo legítimo **e** a primeira palavra da prosa — que é como um parágrafo inteiro de docstring virou "arquivo" na revisão 19.

Cinco de seis casos não-controle produzem alvo espúrio; os dois controles se comportam.

### 6.3 `[superado]` — a duração do Achado 3

Doze linhas de `evidence`, todas da mesma task. As **nove últimas** registram o número idêntico `2 230 collected / 2 191 passed / 39 skipped / exit 0`:

```
rev=57  15:51:27    rev=87   17:01:52    rev=107  17:52:58
rev=71  16:34:07    rev=92   17:36:12    rev=109  17:56:09
rev=84  16:48:48    rev=101  17:42:22    rev=117  18:13:46
```

A semente diz *"8 das últimas 9 … ao longo de 1h10"*. A contagem estava certa no momento dela (havia 11 linhas; a nona de trás para frente, `rev=24`, difere). **A duração não:** `rev=57 → rev=109` são **2h04min41s**, não 1h10. Agora, com a décima segunda linha, são **nove idênticas em 2h22min19s**. Nove execuções da suíte, zero informação nova.

### 6.4 `[superado]` — a causa do laço de `complete` (Achado 5)

A semente diz: *"Registrar evidência é uma escrita; a escrita avança `code_revision`."* Isso **já foi consertado em 2026-09-02**. `is_state_management` (`hooks/harness-transactional.py:152-173`) isenta `state_cli.py`, `branch_state.py` e `confirm_classification.py`, e a docstring nomeia o incidente.

A isenção tem uma condição: `not _has_unquoted_shell_composition(command)`. E a receita que a própria skill `harness-workflow` manda copiar é composta — `;`, `$( )`, aspas:

| comando | isento? | composição? |
|---|---|---|
| a receita da skill, literal | **False** | True |
| a mesma chamada, forma atômica | **True** | False |

**A receita documentada derrota a isenção que existe para fazê-la funcionar.** Três sessões chegaram ao laço por caminhos independentes porque as três seguiram o manual. O defeito não está na régua nem no `complete`: está na distância entre a condição da isenção e a forma que o manual ensina.

---

## 7. O segundo consumidor, danificado junto

`scripts/record_signal.py:68` lê a **mesma tabela** para derivar `actual_level` (`:44-47`: 0-1 → L0, 2-3 → L1, 4+ → L2). Ele filtra só o placeholder `shell-command` (`:71`). Todo o resto do lixo conta como "arquivo alterado":

| task | linhas − placeholder | só código | nível hoje | nível limpo |
|---|---:|---:|---|---|
| `t-20260909-…` | 53 | 19 | L2 | L2 |
| `t-20260910-…` | 71 | 22 | L2 | L2 |
| `t-20260912-…` | 111 | 31 | L2 | L2 |
| `t-20260914-…` | 75 | 18 | L2 | L2 |
| `t-20260916-…` | 133 | **0** | **L2** | **L0** |
| `t-20260917-…` | 18 | 2 | **L2** | **L1** |

**Dois de seis rótulos mudam, e o erro é sempre na mesma direção: inflado para L2.** `t-20260916` é uma task de orquestração — sementes, launchers, `uuid-*.txt` — que tocou **zero** arquivo de código e está rotulada L2.

`actual_level` é o lado "observado" de `proxy_regex_vs_observado`, o número que o `CLAUDE.md` cita como **0,297** para justificar o protocolo de confirmação semântica. **Esse número é medido contra um rótulo corrompido.** Não estou afirmando que ele está errado — estou afirmando que ninguém sabe, porque a verdade-base contra a qual ele é calculado contém 49,9% de lixo.

Ressalva na direção contrária, para não superestimar: `t-20260916` trabalhou em `harness4claude` enquanto o `cwd` era `science-harness`, então "fora da raiz" é verdade para aquele balde e ainda assim havia trabalho real — em outro repositório.

**Sétimo ponto, produzido por esta sessão:** `record_signal.py` fechou esta task com `level=L2, files=5`. As cinco linhas são um arquivo no scratchpad, três `.md` de documentação e o delimitador de heredoc `MSGEOF`. **Zero arquivos de código.** É a sétima task medida e o sétimo rótulo inflado para L2, agora com verdade-base completa — eu sei exatamente o que esta sessão escreveu, porque fui eu.

---

## 8. Onde o portão está certo

Nada aqui é argumento para afrouxá-lo, e três coisas que ele já faz bem precisam ficar escritas para não serem removidas por engano:

1. **`REGRA_TESTE_VALIDO` é um texto só** (`transactional_state.py:48-54`), avaliado pelo mesmo motor na escrita e na leitura. `tests_passed > 0` impede que uma suíte inteiramente pulada passe por verificada.
2. **`is_read_only` e `nao_muda_a_arvore` já funcionam** para o que cobrem. Medido: `grep -rn`, `git status`, `git add -A` e `git commit -m x` **não** sobem `code_revision`.
3. **`inside_root` é fail-closed e a docstring já carrega um `[superado:]` próprio** (`post_tool_policy.py:59-70`). Na dúvida conta como dentro; o erro caro é o oposto.

O que sobe sem dever: **todo `python`, todo `node`, toda invocação de interpretador** — porque um interpretador pode escrever, e a lista `_SOMENTE_LEITURA` (`:240-244`) deliberadamente não os inclui. A decisão está certa para um programa arbitrário e é o custo residual honesto. Ela é responsável por 8 das 9 invalidações desta sessão.

---

## 9. Resumo do diagnóstico

O portão pergunta *"você provou que o código está verde?"* e mede *"alguma coisa foi escrita desde a última prova?"*. As duas perguntas são a mesma variável, e por isso:

1. Um tokenizador POSIX lê strings que não são POSIX e inventa arquivos (§6.2).
2. Arquivo inventado ou fora do repositório sobe `code_revision` pelo caminho do shell, mas não pelo caminho do `Write` (§5).
3. `INSERT OR IGNORE` apaga o rastro de 85,9% das invalidações (§4).
4. O mesmo lixo corrompe o rótulo `actual_level` da telemetria (§7).
5. O laço de `complete` não é o que a semente descreve — é a receita do manual derrotando a isenção que existe para ela (§6.4).

**Reprodução:** `scratchpad/medir.py` (contagens), `scratchpad/falsificar.py` e `scratchpad/falsificar2.py` (falsificação com as funções de produção). Nenhum deles reimplementa lógica de produção; todos importam `hooks/harness-transactional.py` e `scripts/post_tool_policy.py` e chamam as funções reais.
