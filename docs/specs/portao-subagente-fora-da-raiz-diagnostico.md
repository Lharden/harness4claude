# Portão de Stop e subagentes fora do checkout — diagnóstico

Ramo `fix/portao-subagente-fora-da-raiz` (sessão `82a47c1d`), pipeline L1-bug
(regex sugeriu L2-bug; L1 por pedido explícito do usuário), task
`t-20260928-171727153953`. Base: `main` em `7795a5a`.

## O incidente

Sessão `44b0dfb5` (cwd `Documents/projects/master-harness`), task
`t-20260927-221153760510`, L2-architecture, fase `tdd`. A sessão-mãe despachou
subagentes em segundo plano que trabalhavam em worktrees criados à mão em
`%TEMP%\claude\...\44b0dfb5-...\scratchpad\wt-*` — fora do checkout. O portão de
Stop bloqueava todo fim de turno com `0 evidence on current code_revision`: a
evidência de teste recém-gravada era invalidada em segundos por toques
`shell-command (shell-placeholder)`.

Fonte: cópia só-leitura do `harness.db` do balde
`master-harness-5a8ec6a2/sessions/44b0dfb5-...-c8fb553b`, mais os transcripts do
pai e dos 24 subagentes em
`~/.claude/projects/C--Users-LHarden2-Documents-projects-master-harness/44b0dfb5-...`.

| Medida | Valor |
|---|---|
| toques na task | 394 (371 `shell-placeholder`, 23 `shell`) |
| toques emitidos por subagente | 330 (313 placeholder, 17 `shell`) |
| toques emitidos pelo pai | 60 (4 sem correlação) |
| linhas de `evidence` | 18, em 16 revisões; 17 invalidadas, em 15 revisões |
| revisões invalidadas com o comando atribuível | 14 (a 15ª casou com `echo still-waiting`, que não conta nem em `main`: erro da correlação) |
| invalidações causadas por comando de subagente | 12 das 14 |
| invalidações por comando que começa com `cd "<fora do checkout>" &&` | 10 das 14 (9 para `%TEMP%\...\wt-*`, 1 para `harness-lite`) |

A atribuição toque → comando é por carimbo de tempo: o `tool_result` entra no
transcript ~1 s depois do `PostToolUse` (o próprio hook atrasa a escrita), e
cada toque casa com o primeiro `tool_result` em `[0 s, 2,5 s]` depois dele.
`touches` não guarda o comando, então esta é a melhor atribuição disponível —
limite declarado, não medição exata. A sessão seguia viva durante a medição; a
verificação conta duas invalidações a mais, as das revisões 392 e 402.

## Como o hook sabe quem emitiu o comando — medido

Sonda `claude -p` (Claude Code 2.1.283) com um hook `PostToolUse` que só grava
o payload, pedindo ao principal um `echo`, a um subagente outro `echo`, e ao
principal um terceiro:

| Emissor | `session_id` | `agent_id` | `agent_type` | `cwd` |
|---|---|---|---|---|
| principal | S | ausente | ausente | cwd da sessão |
| subagente | **S (o mesmo)** | `adf27e37f442fae62` | `general-purpose` | **cwd da sessão** |
| principal | S | ausente | ausente | cwd da sessão |

Os 24 transcripts de subagente da `44b0dfb5` confirmam: `sessionId` = o do pai,
`cwd` = `...\master-harness`, mesmo quando o comando fazia `cd` para `%TEMP%`.

Três conclusões:

1. **Sessão**: `session_id` não distingue pai de subagente. Por isso todo
   comando de subagente cai no balde — e na task — do pai. É o desenho do
   Claude Code, não defeito do harness.
2. **Agente**: `agent_id`/`agent_type` existem só dentro de subagente (doc
   oficial, "Common input fields", e a sonda acima). O hook não lê nenhum dos
   dois hoje.
3. **Diretório**: `payload.cwd` é o diretório *persistente* da sessão. O Bash
   desta máquina volta a ele depois de toda chamada (`Shell cwd was reset to
   ...`), e o subagente herda o do pai. Então o agente declara onde roda *na
   própria linha*: `cd "<dir>" && ...`. **Quem quiser saber onde o comando
   rodou tem de ler o comando, não o `cwd`.**

## Causa raiz

`_handle_post_tool` (`hooks/harness-transactional.py:1037`) localiza todo
comando de shell por `payload.cwd` e ignora o `cd` com que a linha começa. Daí
saem duas falhas, pela mesma causa:

- **(a) O comando opaco é presumido dentro do checkout.** Sem alvo atribuível e
  sem ser leitura pura, vira `shell-placeholder` e sobe `code_revision` — mesmo
  que a linha diga `cd "%TEMP%\...\wt-s1-l5a" && python -m pytest ...`. O
  placeholder existe para o programa que escreve por dentro; ele supõe que o
  programa rodou no projeto.
- **(b) O alvo relativo é resolvido contra o diretório errado.**
  `_apenas_dentro_da_raiz` (`:998`) junta o alvo relativo ao `payload.cwd`.
  `cd "%TEMP%\...\l4" && cat > sim_334.py` foi gravado como `sim_334.py`
  *dentro* do master-harness.

Os subagentes não são a causa; são o multiplicador. Compartilham a sessão,
herdam o `cwd`, são muitos e rodam em paralelo — e são justamente os que mais
trabalham em worktree fora do checkout.

### Reprodução pelas funções de produção

Os 1 000 comandos de shell dos transcripts (102 do pai, 898 de subagentes)
passados por `shell_write_targets`, `_apenas_dentro_da_raiz`, `is_read_only`,
`nao_muda_a_arvore` e `is_state_management` de `main`, com o `cwd` real do
payload:

| Local declarado pela linha | placeholder | atribuído (`shell`) |
|---|---|---|
| `cd` para fora do checkout | **207** (205 sem citar a raiz) | **20** (todos escritos em `%TEMP%`) |
| sem `cd` inicial | 134 | 1 |
| `cd` para dentro do checkout | 45 | 0 |

A classe dominante do incidente é a primeira linha. As outras duas são
defeitos diferentes (lacunas da lista de leitura pura — `for`, `until`,
`git -C <dir> log` —, e indireção por variável `WT=...; cd "$WT"`) e ficam fora
deste conserto: ali o local é desconhecido ou é dentro, e contar é o certo pelo
que o hook sabe.

## Pergunta 1 — comando de subagente deve contar contra a revisão do pai?

**Sim, quando pode mudar o checkout do pai; e pela mesma régua do comando do
pai.** Identidade não é critério de veredito.

O subagente trabalha na mesma sessão e no mesmo diretório. Um `execucao-mecanica`
editando o checkout depois de o pai gravar evidência muda o código que a suíte
mediu. Ignorar o comando por `agent_id` deixaria o pai delegar a edição para
depois da evidência e passar no portão com número velho — afrouxar o guarda
para edição real dentro do repositório, que é o que este conserto não pode
fazer. O que decide é **onde o comando pode escrever**, e a pergunta vale igual
para os dois emissores.

`agent_id` fica registrado na recusa (ver abaixo), como proveniência para
auditoria — é o que teria dispensado a correlação por carimbo de tempo acima.

## Pergunta 2 — comando que roda fora do repositório da task deve contar?

**Não conta quando o local declarado pela linha é conhecido, disjunto do
checkout, e nada na linha resolve para dentro dele.** Em qualquer dúvida, conta
— o comportamento de hoje.

- **Local**: `cd`/`pushd`/`chdir`/`Set-Location`/`sl`/`Push-Location` no início
  da linha, com um argumento e seguido de `&&` ou `;` (repetível). `cd` no meio
  da linha, `||`, `|`, variável, `~`, `-`, ou caminho enraizado sem drive no
  Windows que não seja a forma MSYS `/c/...` → local desconhecido.
- **Disjunto**: nem dentro, nem ancestral da raiz do projeto
  (`find_repo_root(cwd)`, que colapsa worktree no dono) nem da árvore de
  trabalho da própria sessão (o `.git` mais próximo acima do `cwd`, sem
  colapso). Ancestral não é fora: de `projects/` qualquer `master-harness/x`
  relativo chega dentro. A árvore da sessão entra porque, numa sessão aberta
  num worktree fora do diretório do dono, o colapso diria que o próprio `cwd`
  está fora.
- **Nada resolve para dentro**: nenhum token (valor de flag `--x=...`
  incluído), resolvido contra o local, cai dentro de uma das duas raízes;
  nenhum token tem variável não expandida; e o texto inteiro da linha,
  corpo de heredoc incluído, não cita o caminho de nenhuma raiz (barra
  normalizada, forma MSYS, caixa ignorada).

O alvo relativo de `>`, `tee` e `sed -i` passa a ser resolvido contra o local
declarado quando ele é conhecido, o que fecha a falha (b).

### O que continua descoberto — declarado

Um programa opaco lançado de fora do checkout que escreve dentro dele por um
caminho que **não aparece na linha** — lido de arquivo-ponteiro, de variável de
ambiente, ou montado por `..` dentro do corpo do programa — deixa de contar.
Nesta máquina o mecanismo existe: `~/.master-harness/mh-root` aponta para o
checkout do master-harness. Nenhum comando do incidente usou esse caminho para
escrever, mas nada no texto de uma linha permite provar isso.

Só medir a árvore fecha esta classe: o digesto de
`portao-mede-atividade-verification.md` §3, que troca "o comando parece
escrever?" por "o checkout mudou?". `git status --porcelain=v1 -z -uall` +
`git rev-parse HEAD` custou 0,225 s no master-harness (medido 2026-09-28) — é a
resposta à pergunta de custo que aquele documento deixou aberta. Fica como
próximo passo, com dono no usuário; não entra neste ramo porque muda a
semântica de frescura do portão (e do `complete`) e é L2.

## Pergunta 3 — como o hook sabe qual sessão ou agente emitiu o comando?

Respondida acima, por medição: `session_id` identifica a sessão (e é
compartilhado com o subagente), `agent_id`/`agent_type` identificam o
subagente (ausentes no principal), e o `cwd` do payload é o da sessão — o
diretório do comando só está no texto do comando.

## Fora deste conserto, registrado

- Sessão aberta num worktree **fora** do diretório do dono: `find_repo_root`
  colapsa para o dono, e o `counts_as_modified_file` do `Edit`/`Write`
  (`harness-reclassify.sh:149`) passa a julgar a edição do próprio worktree
  como fora. Defeito anterior a este ramo, no outro caminho; este ramo só evita
  estendê-lo ao shell.
- Evidência sobre código de outro checkout (a `44b0dfb5` gravou à mão
  "pytest ... worktree wt-s1"): o portão modela o checkout da sessão, e só ele.
  Depois deste conserto, edição de subagente em `wt-s1` não invalida aquela
  linha — nunca invalidou por mérito, só pelo placeholder cego.
- `git -C <dir> log|status|diff` conta como escrita porque `is_read_only` lê
  `-C` como subcomando (28 placeholders no incidente). Defeito de parsing,
  causa própria.
