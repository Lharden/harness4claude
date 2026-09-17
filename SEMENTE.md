# A revisão que invalida — 30% das invalidações de evidência vêm de coisas que não são arquivo

## Origem

**Sessão pai:** `86459dbf-2585-482f-814e-7b3dca136264` — coordenação, **aberta**. O autor está **na máquina agora**; pergunta que precise dele pode ser feita na hora.
**Projeto:** `harness4claude`, worktree `.claude/worktrees/revisao-que-invalida`, branch `claude/revisao-que-invalida`, base `6e7f4e5`.
**Autorizado pelo autor** em 2026-09-17: *"abra uma sessão de mapeamento e depois planejamento para ver o que podemos fazer, em uma branch própria dentro do Harness."*

**Isto é MAPEAMENTO e depois PLANEJAMENTO. Não é para consertar hoje.** Entregue o mapa medido e o plano; a execução é decisão do autor depois de ler.

## A diretriz core, e ela é do autor

**Ou entregamos tudo direito, ou não entregamos.** Causa raiz, não sintoma. Lean e PMBOK. **Vermelho conhecido não se mescla** · **conserto exige falsificação** · **capacidade construída precisa de consumidor nomeado** · **medir com a função de produção, não com a reimplementação dela**.

E uma que vale especialmente aqui: **o portão existe para impedir que alguém declare pronto sem evidência.** Nada no que segue é argumento para enfraquecê-lo. O problema não é o portão ser exigente — é ele estar medindo a coisa errada.

## O que eu observei, com número

Rodei uma sessão de coordenação de ~8 horas sobre três repositórios. O portão de verificação disparou em praticamente todo turno. Os dados estão em `~/.claude/harness/projects/science-harness-f34c6792/sessions/86459dbf-2585-482f-814e-7b3dca136264-06f60408/harness.db`.

### Achado 1 — a tabela `files` está poluída, e é a causa raiz

`files` tem 3 colunas: `task_id`, `normalized_path`, `first_seen_code_revision`. Medido:

| | |
|---|---:|
| linhas em `files` | **466** |
| **não parecem caminho** (sem separador e sem extensão) | **122 = 26,2%** |
| revisões distintas no total | 268 |
| **revisões criadas por essas entradas** | **80 = 29,9%** |

Amostra do que entrou como "arquivo":

```
rev=1     'shell-command'
rev=22    '$L'
rev=101   'riqueza(b)'
rev=147   '3:'
rev=188   'MSG'
rev=225   '$LOG'
rev=230   '0.75:'
rev=253   '30]'
rev=265   'score:'
rev=275   '}}]}}'
rev=19    '.pdf)`. Passar o título — como faz o caminho do E1 — calcularia outra\n    chave e leria o cache errado, ou nenhum, em silêncio.'
```

São **fragmentos de linha de comando e prosa**. O extrator que descobre "quais arquivos este comando tocou" está pegando tokens do shell — variáveis, fatias de array, fechamentos de JSON — e às vezes um parágrafo inteiro de docstring.

**Consequência direta:** `code_revision` avança por escrita que não existiu. E como o portão exige evidência **na revisão atual**, quase um terço das invalidações não tem causa real.

### Achado 2 — o portão pede evidência de TESTE para invalidação por ARQUIVO

A régua é: `exit 0`, `tests_passed > 0`, `passed + skipped == collected`, **na `code_revision` atual**.

Mas `code_revision` avança com **qualquer** arquivo tocado — incluindo `.md`, semente, launcher, arquivo em `%TEMP%`. Medido nesta sessão: escrevi sementes, documentos de verificação e scripts de diagnóstico fora do repositório, e cada um invalidou a evidência de que **o código** estava verde.

**São duas grandezas com um nome só.** *O código sob teste mudou?* e *algum arquivo foi escrito?* — e esta é literalmente a distinção que `counts_as_modified_file` já aprendeu em `3ae4dbd`, em outro lugar do mesmo sistema: `git add` e `git commit` escrevem em `.git/` e **não mudam a árvore que a suíte mede**. O conserto existe e não chegou aqui.

### Achado 3 — o custo, medido

`evidence` tem 11 linhas nesta task. **8 das últimas 9 registram o número IDÊNTICO** — `2 191 passed / 39 skipped / exit 0` — ao longo de 1h10:

```
rev=109  2191/39   17:56       rev=87   2191/39   17:01
rev=107  2191/39   17:52       rev=84   2191/39   16:48
rev=101  2191/39   17:42       rev=71   2191/39   16:34
rev=92   2191/39   17:36       rev=57   2191/39   15:51
```

Oito execuções da suíte inteira (~45 s cada) e oito turnos de cota, **produzindo zero informação nova**. Nenhuma delas podia falhar: a árvore estava idêntica, carimbada com `git rev-parse HEAD` e `git status --porcelain` antes e depois.

### Achado 4 — dois contadores com o mesmo nome

`files.first_seen_code_revision` chega a **647**. A task corrente lê `code_revision = 109`. São escopos diferentes com o mesmo nome de coluna, e eu não sei qual manda no portão. **Descubra** — se forem dois relógios, o portão pode estar comparando um com o outro.

### Achado 5 — fechar a task não converge, e três sessões independentes bateram nisso

`state_cli complete` recusa com *"task requires fresh verification evidence"*. Registrar evidência é uma escrita; a escrita avança `code_revision`; a evidência recém-registrada fica velha. Três sessões chegaram a isso hoje **por caminhos independentes**:

- a Fase 5 ficou em laço e parou quando avisada;
- a sessão do vault saiu fazendo **o próprio script de fechamento se apagar antes de rodar**;
- a Fase 6 registrou: *"`state_cli complete` não passa, porque cada chamada mexe em `code_revision` e vence a própria evidência"*.

Três descobertas independentes do mesmo laço é sinal de causa estrutural, não de azar.

## A minha opinião, e é para você contestar

**O portão está certo na intenção e errado na grandeza.** Ele pergunta *"você provou que o código está verde?"* e mede *"alguma coisa foi escrita desde a última prova?"*. Enquanto essas duas forem a mesma variável, ele vai disparar por motivos que não existem — e o risco de médio prazo é o pior de todos: **quem vê o portão disparar sem causa para de acreditar nele**, e aí ele deixa de pegar a vez em que a causa é real. *Teste que reprova por construção morre por uso*, e isso vale para portão também.

**O que eu NÃO recomendo:** relaxar a régua, aumentar um TTL, ou dar um jeito de pular. O problema não é rigor demais.

**O que eu recomendo investigar, em ordem de força:**

1. **Consertar o extrator de caminhos.** 26% de lixo é a causa raiz de 30% das invalidações. Um caminho que não é caminho não deveria entrar em `files` — e a recusa deve ser **declarada**, não silenciosa, senão troca-se ruído por cegueira.
2. **Separar as duas perguntas**, reusando o que já existe: `counts_as_modified_file` de `scripts/post_tool_policy.py` já distingue *escreveu algo* de *mudou o código sob teste*, e já sabe que `git add`/`git commit` não contam. Arquivo fora da raiz do projeto, `.md`, semente e launcher provavelmente caem no mesmo balde.
3. **Deixar a evidência sobreviver a escrita que não é código.** Se `code_revision` só avançar quando o código sob teste mudar, o laço do Achado 5 desaparece sozinho — sem tocar na régua.
4. **Dar ao portão uma saída honesta para o turno que não mexe em código.** Hoje um turno que só lê transcript e responde precisa rodar a suíte inteira. Isso não é rigor: é o portão medindo o turno errado.

**Ordem que eu seguiria:** 1 e 2 são causa raiz e independentes entre si. 3 é consequência de 2. 4 só faz sentido depois de 2, porque sem separar as grandezas não há como dizer o que é "turno que não mexe em código".

## Primeira ação

**Reproduza o Achado 1 por conta própria antes de acreditar em mim.** Abra o banco, rode a mesma contagem, e **confira o meu critério de "parece caminho"** — ele é meu e é grosseiro (separador ou extensão). Se o seu critério der outro número, o seu vale mais, e o meu vira `[superado: …]`.

Depois **ache o extrator** — quem escreve em `files` — e leia a função que decide o que é caminho. É lá que o mapa começa.

## Escopo

**ENTRA:** o mapeamento medido e o plano escrito.
**NÃO ENTRA:** conserto, mudança de régua, mudança de config do harness. O autor lê o plano e decide.

Se durante o mapeamento você achar um conserto de **uma linha, de causa raiz e falsificável**, proponha-o no plano com a falsificação desenhada — mas **não aplique**.

## Verificação

```bash
python -m pytest -q -p no:cacheprovider
```

**Base em `6e7f4e5`: `1 329 passed`, zero falhas.** A suíte deste repo leva **~13 minutos** — rode em background e não a repita sem motivo, que é literalmente o assunto desta sessão.

## Regras que valem aqui

- **O que for construído aqui serve a todos os projetos.** Nada específico de HCE, de mestrado ou de um repositório.
- **`exit_code == 0` não é aceite.** Leia a saída.
- **Número aposentado permanece escrito**, com `[superado: por quê]`.
- **Um parágrafo é uma linha.**
- **Quinze erros de instrumento na coordenação destes dois dias**, e a assinatura é sempre a mesma: **o número errado era plausível porque os erros se cancelavam em parte**. Os números acima são meus e podem ter essa doença — trate-os como hipótese com evidência, não como fato.

## Duas sessões irmãs, sem interseção

`vetores-que-cabem` (science-harness) e `text-cache-vazio` (slb) rodam agora. Nenhuma toca `harness4claude`. Interseção medida: **nenhuma**.

## Como reportar

Commite no branch. **Não mescle em `main` e não empurre.** Entregue dois documentos: `docs/specs/revisao-que-invalida-mapa.md` (o que É, medido) e `docs/specs/revisao-que-invalida-plano.md` (o que FAZER, ranqueado, com a falsificação de cada item desenhada). **Termine com um resumo de 10 linhas na sua última mensagem.**
