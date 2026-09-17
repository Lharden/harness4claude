# Spec: Guarda de órfão

**Status**: Draft
**Created**: 2026-09-16
**Updated**: 2026-09-16
**Branch**: `claude/guarda-de-orfao`
**Author**: AI-generated, reviewed by Leonardo
**Upstream**: [`CONTEXT.md`](../CONTEXT.md) · [`medicao.md`](guarda-de-orfao-medicao.md) · [`graph-context.md`](guarda-de-orfao-graph-context.md)

---

## Executive Summary

Um teste que falha quando nasce função pública sem caminho até uma raiz que o host
executa. Quem quiser deixar uma sem chamador escreve o nome numa lista, **com o
motivo**. Isso converte "esquecido" em "declarado", que é a única diferença
mensurável entre as 38 peças abandonadas e as 29 ferramentas de mão que este
repositório tem hoje — hoje as duas classes são indistinguíveis por qualquer
indicador existente: suíte verde, revisão feita, cobertura alta.

A medição que justifica: **419 funções públicas de topo, 67 sem raiz externa, 38
esquecidas contra 29 declaradas.** A fronteira entre os dois grupos é uma frase
escrita num documento, e nada mais.

---

## Context

### Como o repositório verifica saúde hoje

Existe uma família de validadores com contrato comum — `graph_lint.py`,
`arsenal.py check`, `compendium.py check`, `wiki_lint.py`, `impact.py`,
`design_scope.py`, `vault_sync_doctor.py`: **JSON no stdout, booleano `ready`,
exit 1, reporta e nunca corrige**. `scripts/health-check.sh` consome essa forma.

`scripts/check_hook_liveness.py` é o precedente direto desta spec. Ele responde
"os hooks continuam sendo chamados?" com três propriedades que esta spec herda:
grava **antes** de qualquer guard, confronta com **sinal independente** (os
transcripts do host), e dá **nome à ausência**. Seu docstring é o enunciado do
problema: *"código presente ≠ código rodando"*.

E existem testes-de-conjunto — `tests/test_pipeline_phases_are_real.py`,
`tests/test_arsenal.py`, `tests/test_branch_config.py` — que não testam uma peça:
testam o **conjunto**, e por isso pegam a próxima peça esquecida sem ninguém
lembrar dela. Esta spec produz mais um.

### O que falta

Nada pergunta se uma função tem chamador. `analyze_wiki()` tem 18 testes e o grafo
lhe dá comunidade própria feita só de teste. `hooks/harness_lite_adapter.py` prova
a capacidade `integration.harness-lite`, declarada `"level": "required"` no
contrato — e a prova é um nó de pytest, como são as 44 provas de
`contract/behavioral-probes.json`, sem exceção.

### Files/Modules Impactados

- `tools/orfaos.py` **(novo)**: o scanner. Herda o contrato da família de linters.
- `tools/orfaos.json` **(novo)**: a allowlist. Só julgamento; o fato vem do disco.
- `tests/test_orfaos.py` **(novo)**: o guarda propriamente dito, e as regressões do instrumento.
- `scripts/health-check.sh`: ganha a chamada ao scanner — segundo chamador independente.
- `tools/README.md`: ganha a linha do scanner na tabela — e um parágrafo nomeando `wiki_lint.py` e `wiki_moc.py` como ausentes dela, **sem acrescentá-las**: declarar uma peça esquecida é decisão de quem mantém o repositório, não de quem passou medindo.
- `skills/write-spec/SKILL.md`, `skills/write-spec-light/SKILL.md`: regra do consumidor nomeado (parte C).

### Dependências

- **stdlib apenas** (`ast`, `json`, `re`, `pathlib`): é a regra de todo hook e script deste repositório.
- **Python 3.10**: a matriz de CI inclui 3.10, e `tomllib` é 3.11+. Por isso a allowlist é JSON, não TOML. *(Observação colhida na medição, fora do escopo desta spec: `tools/arsenal.py:60` importa `tomllib` sem guarda, e a matriz roda 3.10.)*

---

## Current System

### Entry Points
- `scripts/health-check.sh`: agrega os linters; já tem seção `--- Orphaned artifacts ---`, hoje sobre estado (`ralph-loop.local.md`, task velha), não sobre código.
- `.github/workflows/ci.yml`: roda `pytest -q`, `ruff check .`, `bandit`.

### Data Flow atual
```
hook .sh / health-check.sh / CI  ──>  script .py  ──>  função
                                             (sem ninguém medindo se a seta existe)
```

### Constraints existentes
- Namespace plano: os módulos se importam por basename, via `sys.path.insert(0, dirname(__file__))`.
- Hooks `.sh` embutem heredoc Python e importam por nome de módulo, às vezes com ` as `.
- `hooks/harness-lifecycle.py` carrega módulos por `importlib.util.spec_from_file_location` e chama por atributo.
- A suíte leva ~16 min; referência **1 272 passed**, zero falhas, em `5ca9d4e`.

---

## User Stories

### US-1: O guarda reprova órfão não declarado (Priority: P1) — MVP

**Como** autor do harness
**Quero** que a suíte falhe quando nasce função pública sem caminho até uma raiz que o host executa
**Para que** peça esquecida pare de ser indistinguível de peça viva

**Why this priority**: é a feature. Sem ela não há nada.

**Independence**: testável sozinha, contra fixtures sintéticas.

**Acceptance Criteria**:

- **AC-1**: Given um módulo de produção com função pública `f` referenciada por um hook `.sh`, When o scanner roda, Then `f` é `viva` e o guarda passa.
- **AC-2**: Given uma função pública `f` que nenhuma raiz externa alcança e que **não** está na allowlist, When o guarda roda, Then ele **falha**, e a mensagem nomeia `arquivo:linha`, o nome da função e o que faltou.
- **AC-3**: Given a mesma `f` **com** linha na allowlist, When o guarda roda, Then ele passa e a linha não é contada como falha.
- **AC-4**: Given uma função referenciada **apenas** por arquivos sob `tests/`, When o scanner roda, Then ela **não** é `viva` — teste não é chamador.
- **AC-5**: Given uma função que só se chama a si mesma (recursão), When o scanner roda, Then ela **não** é `viva` por causa disso.
- **AC-6**: Given uma função alcançável só através de outra função que também é órfã, When o scanner roda, Then ambas são reportadas — alcançabilidade é transitiva a partir da raiz, não da referência.

**Edge Cases**:
- Arquivo com `SyntaxError`: reportado como não-analisável e **nomeado**, nunca omitido em silêncio.
- Função definida duas vezes no mesmo módulo: vale a primeira, e a colisão é reportada.
- Repositório sem `hooks/` nem `.github/`: zero raízes é resultado suspeito, não normal — o scanner reprova com "nenhuma raiz encontrada" em vez de aprovar tudo como órfão.

---

### US-2: O guarda tem de ser chamado, e a prova não depende dele (Priority: P1) — MVP

**Como** autor
**Quero** provar que o guarda roda, por evidência que não venha do próprio guarda
**Para que** ele não vire o problema que resolve

**Why this priority**: é a ironia do seed elevada a critério de aceite. Um guarda de órfão que ninguém roda é um órfão, e **o guarda não pode ser sua própria testemunha** — um registro que se confirma sozinho prova consistência, não vida.

**Acceptance Criteria**:

- **AC-1**: Given o scanner instalado, When `pytest` roda, Then `tests/test_orfaos.py` executa — e portanto o CI, que já roda `pytest -q`, também.
- **AC-2**: Given o scanner instalado, When `scripts/health-check.sh` roda, Then ele invoca `tools/orfaos.py` e reporta `[OK]`/`[FAIL]`, no mesmo formato de `graph_lint.py`.
- **AC-3**: Given uma função órfã plantada e **não** declarada, When a suíte roda, Then ela falha — e a verificação registra **a saída colada da execução**, não a afirmação de que falharia. O veredito é do `pytest`, não do guarda: é essa a evidência independente. *(G5)*
- **AC-4**: Given `tools/orfaos.py`, When o próprio scanner roda, Then as funções dele aparecem como `viva`. **Corroboração, não prova** — o scanner atestando a si mesmo é registro se autoconfirmando, que mede consistência e não vida. *(G5)*

---

### US-3: A allowlist guarda julgamento, e julgamento tem motivo (Priority: P1) — MVP

**Como** autor
**Quero** que cada linha da allowlist tenha categoria e motivo escrito
**Para que** a lista seja o inventário que nunca existiu, e não uma lista de silenciamentos

**Why this priority**: sem isto, a allowlist vira `# noqa` em massa e o guarda deixa de discriminar no primeiro mês.

**Acceptance Criteria**:

- **AC-1**: Given uma entrada de allowlist sem `motivo`, ou com `motivo` vazio, When o guarda roda, Then ele **falha** apontando a entrada.
- **AC-2**: Given uma entrada cujo `motivo` é apenas o nome da função repetido, When o guarda roda, Then ele **falha** — motivo que não informa não é motivo.
- **AC-3**: Given uma entrada com `categoria` fora do vocabulário fechado, When o guarda roda, Then ele **falha** e lista o vocabulário aceito.
- **AC-4**: Given a allowlist e o código, When o guarda roda, Then vale a **dupla inclusão**: toda não-viva está na lista, e toda linha da lista aponta para uma não-viva. *(G4: o 67 é linha de base medida, não invariante — `assert len == 67` quebraria a suíte de quem escreve função nova, o defeito que G1 nomeou.)*
- **AC-5**: Given a allowlist, When alguém a lê, Then as 29 declaradas e as 38 esquecidas são distinguíveis pela `categoria` — a distinção é o produto, não um detalhe.

**Vocabulário fechado de `categoria`**:

| categoria | significado |
|---|---|
| `FERRAMENTA_DE_MAO` | humano roda sob demanda; o `motivo` cita onde está documentado o como e o quando |
| `RESERVA_DECLARADA` | construído para um degrau que ainda não foi ligado; o `motivo` cita a spec ou o desenho |
| `API_EXTERNA` | consumida por outro projeto ou host, fora deste repositório |
| `ORFAO` | **ninguém sabe por que existe.** Não é exceção: é dívida com nome |

**Edge Case**: allowlist com nome que **não existe mais** no código, ou cuja função **ganhou** chamador — ver CLARIF-1.

---

### US-4: Os erros do instrumento viram regressão (Priority: P2)

**Como** autor
**Quero** que os três erros medidos na construção do instrumento tenham teste
**Para que** a próxima volta não os reintroduza silenciosamente

**Why this priority**: P2 porque o guarda funciona sem eles — mas foram três leituras erradas e plausíveis em uma sessão, e a terceira era o próprio defeito que o guarda caça.

**Acceptance Criteria**:

- **AC-1**: Given um hook `.sh` com `from M import f as _g`, When o scanner roda, Then `M.f` é `viva` — o ` as ` não pode esconder o import.
- **AC-2**: Given um módulo carregado por `spec_from_file_location("M", ...)` e chamado por `var.f()`, When o scanner roda, Then `M.f` é `viva`.
- **AC-3**: Given `tests/test_M.py` e nenhuma outra menção a `M`, When o scanner roda, Then `M` **não** ganha raiz por substring.
- **AC-4**: Given `scripts/build_M.py` citado num hook e nenhuma menção a `M`, When o scanner roda, Then `M` **não** ganha raiz — prefixo também é substring.
- **AC-5**: Given um módulo `M` e um arquivo que contém `outroM.py`, When o scanner roda, Then não há casamento. *(G2: os três são o mesmo defeito, medido três vezes em um dia.)*

---

### US-5: Capacidade declarada exige consumidor nomeado (Priority: P2)

**Como** autor
**Quero** que uma spec que diz *"X existe"* tenha de dizer *"e Y o chama"*
**Para que** o problema pare de nascer

**Why this priority**: P2 porque não conserta o que já existe — impede o próximo. A medição mostrou que o alvo é maior que specs: são as 22 capacidades `required` do contrato, provadas só por teste. Mas retrofit do contrato depende da parte A e está **Deferred**.

**Acceptance Criteria**:

- **AC-1**: Given `skills/write-spec/SKILL.md`, When alguém gera uma spec, Then o template exige, para cada REQ que cria capacidade nova, a linha **`consumidor:`** nomeando quem a invocará em produção.
- **AC-2**: Given um REQ cujo único `consumidor:` é um teste, When a spec é escrita, Then isso é registrado como `[NEEDS CLARIFICATION]`, não aceito como consumidor.
- **AC-3**: Given `skills/write-spec-light/SKILL.md`, When uma spec-light é gerada, Then a mesma exigência vale, em uma linha — o overhead humano de ~2 min não pode crescer.
- **AC-4**: Given a regra instalada, When `tests/test_packaging.py` (ou equivalente) roda, Then ele verifica que as duas SKILL.md contêm a exigência — a regra não pode ficar só no texto que ninguém confere.

---

### US-7: Linha obsoleta reprova, e a mensagem traz o conserto (Priority: P1) — MVP

**Como** autor
**Quero** que o guarda falhe quando uma linha da allowlist deixou de ser verdade, **e que a falha imprima o comando copiável que corrige**
**Para que** a lista não vire sedimento, sem que corrigir um órfão puna quem corrigiu

**Why this priority**: P1 porque sem isto a allowlist envelhece e o guarda deixa de discriminar — uma allowlist com linha morta é um órfão de segunda ordem, o guarda contra abandono sendo abandonado. E o caso que mais precisa sair da lista é justamente o que representa progresso: a função que **ganhou** chamador.

**Acceptance Criteria**:

- **AC-1**: Given uma entrada de allowlist cuja função **ganhou** chamador de produção, When o guarda roda, Then ele **falha**, dizendo que a linha ficou obsoleta.
- **AC-2**: Given uma entrada cujo nome **não existe mais** no código, When o guarda roda, Then ele **falha**, dizendo que o alvo sumiu.
- **AC-3**: Given qualquer falha do guarda, When a mensagem é impressa, Then ela **diz qual dos dois casos é** e contém uma linha de comando copiável — e só promete desfecho verde no caso de linha obsoleta. *(G1: o comando não pode consertar órfão novo sozinho; falta o julgamento humano, que é o produto. Prometer o contrário repetiria o defeito que a mensagem existe para corrigir.)*
- **AC-4**: Given a mensagem de falha, When o teste roda, Then ele **extrai a linha de comando da própria mensagem e a executa**, e o comando sai com exit 0 — seguindo `5ca9d4e` (*"o comando que a mensagem imprime tem de rodar"*): quem verificar esta mensagem no futuro executa em vez de ler.
- **AC-5**: Given o comando de sincronização, When ele roda, Then ele **remove** linhas obsoletas e **acrescenta** as novas com `categoria: "ORFAO"` e `motivo` vazio — e o guarda continua falhando até alguém escrever o motivo. Sincronizar não é aprovar.

---

### US-6: Relatório legível (Priority: P3 — nice to have)

**Como** autor
**Quero** `python tools/orfaos.py --report`
**Para que** eu leia o inventário sem abrir JSON

**Acceptance Criteria**:
- **AC-1**: Given `--report`, When o scanner roda, Then a saída agrupa por arquivo e por categoria, e separa declaradas de esquecidas.
- **AC-2**: Given nenhuma flag, When o scanner roda, Then a saída é JSON com `ready`, e o exit é 1 quando `ready` é falso.

---

## Requirements

### Functional
- [ ] **REQ-F1**: O scanner resolve funções públicas de topo em `scripts/`, `hooks/`, `tools/`, `skills/` e computa alcançabilidade dirigida a partir de raízes externas. [traces: US-1]
- [ ] **REQ-F2**: Raiz externa = módulo cujo caminho ou nome de import aparece em `hooks/*.sh`, `hooks/hooks.json`, `scripts/*.sh`, `scripts/workflows/*.js`, `.github/workflows/*.yml`, `skills/*/SKILL.md` ou `README.md` — **nunca** sob `tests/`. O casamento exige **fronteira à esquerda**: início de linha, separador de caminho ou delimitador. [traces: US-1, US-4] *(G2: a armadilha de substring disparou três vezes na medição de hoje — `tests/test_harness_paths.py`→`harness_paths`, `build_wiki_index.py`→`wiki_index`, e um `grep -l` que me fez crer que uma SKILL.md declarava `wiki_lint`.)*
- [ ] **REQ-F3**: A propagação cobre código de topo, corpo de classe, chamada, callback, registro em dict, decorator, `getattr` com literal e módulo carregado por `spec_from_file_location`. [traces: US-1, US-4]
- [ ] **REQ-F4**: Auto-referência (recursão) não torna função viva. [traces: US-1]
- [ ] **REQ-F5**: O guarda falha listando cada função não-viva ausente da allowlist, com `arquivo:linha`. [traces: US-1]
- [ ] **REQ-F6**: A allowlist valida `categoria` contra vocabulário fechado e `motivo` contra vazio e contra repetição do nome. [traces: US-3]
- [ ] **REQ-F7**: `scripts/health-check.sh` invoca o scanner e reporta no formato da família de linters. [traces: US-2]
- [ ] **REQ-F8**: `write-spec` e `write-spec-light` exigem `consumidor:` para capacidade nova, e um teste verifica que a exigência está nas SKILL.md. [traces: US-5]
- [ ] **REQ-F9**: Arquivo de produção que não parseia **reprova**, com o nome — não basta mencionar: sem reprovar, as funções dele somem da conta e o total cai sem ninguém notar. [traces: US-1] *(G7)*
- [ ] **REQ-F10**: Zero raízes encontradas é reprovação, não aprovação. [traces: US-1]
- [ ] **REQ-F11**: Entrada de allowlist obsoleta — função que ganhou chamador, ou nome que sumiu do código — **reprova**. [traces: US-7]
- [ ] **REQ-F12**: Toda mensagem de falha contém uma linha de comando copiável que sincroniza a allowlist; um teste extrai essa linha da mensagem e a executa. [traces: US-7]
- [ ] **REQ-F13**: O comando de sincronização acrescenta órfão novo com `categoria: "ORFAO"` e `motivo` vazio, e o guarda segue reprovando até o motivo ser escrito — sincronizar não é aprovar. [traces: US-7, US-3]
- [ ] **REQ-F14**: A sincronização **nunca apaga motivo escrito**. Linha cujo alvo sumiu é marcada obsoleta e só sai por ação explícita; o comando imprime os motivos afetados para recolagem. [traces: US-7] *(G6: a allowlist é o produto desta feature, e um `git mv` não pode destruí-la.)*

### Non-Functional
- [ ] **REQ-NF1 (Performance)**: O scanner completo abaixo de 5 s sobre este repositório — ele roda em toda suíte e em todo health-check. [traces: all]
- [ ] **REQ-NF2 (Portabilidade)**: stdlib apenas; Python 3.10+; sem `tomllib`. [traces: all]
- [ ] **REQ-NF3 (Contrato de saída)**: JSON no stdout com `ready`, exit 1 quando falso, igual a `graph_lint.py`. [traces: US-2, US-6]
- [ ] **REQ-NF4 (Determinismo)**: mesma árvore de trabalho ⇒ mesma saída, ordenada. [traces: US-2]
- [ ] **REQ-NF5 (Honestidade)**: o scanner reporta o que **não** conseguiu decidir; ausência de decisão nunca vira aprovação. [traces: US-1, US-3]

---

## Boundaries

### ALWAYS
- Rodar a função de produção para produzir número, nunca uma reimplementação dela.
- Ler a saída: `exit_code == 0` não é aceite.
- Número aposentado permanece escrito, com `[superado: por quê]`.
- Nomear a ausência: "não analisado" e "sem órfão" são resultados diferentes.

### NEVER
- Apagar função por estar órfã, nesta volta.
- Aceitar teste como prova de que uma capacidade é usada, ou como raiz de produção.
- Deixar linha de allowlist sem motivo, ou com motivo que repita o nome.
- Depender de `graphify-out/` — o grafo é não-direcionado e não responde alcançabilidade.

### ASK
- Se a allowlist precisar passar de 67 linhas para a suíte passar: a medição errou, e o número tem de ser refeito antes do guarda.
- Se o scanner exigir dependência fora da stdlib.

---

## Nível de garantia

**Esta feature entrega:** um **inventário verificado de funções públicas sem
caminho estático até uma raiz que o host executa**, com a decisão sobre cada uma
escrita, e um teste que falha quando aparece uma não escrita.

**E não cobre:**
- **Não diz se uma ferramenta de mão foi executada alguma vez.** Para as 29 declaradas o caminho termina no humano; alcançabilidade estática não distingue "rodo toda semana" de "ninguém rodou desde que foi escrita". Essa é a parte A, e é outro ramo.
- **Não cobre métodos de classe** — são **18 classes com 38 métodos públicos**, ~9% da superfície. Isso é também uma **rota de evasão**: mover uma função órfã para dentro de uma classe a tira do radar sem mudar sua natureza, e pode acontecer sem intenção, numa refatoração. *(G3)*
- **Não prova que uma função viva faz algo útil.** Ter chamador não é ter valor.
- **Não retrofita o contrato.** As 22 capacidades `required` provadas só por teste continuam assim; a regra nova (US-5) vale para spec nova.
- **Não detecta dispatch que o AST não vê** — `getattr` com nome computado, import por string montada em runtime, invocação via variável de ambiente. Esses aparecem como órfãos e vão para a allowlist com `motivo`, que é o desfecho correto: declaração explícita em vez de detecção mágica.

---

## [NEEDS CLARIFICATION]

- [x] ~~**CLARIF-1**~~: Quando uma função **ganha** chamador de produção, ou some do código, e a linha dela continua na allowlist — o guarda deve **falhar** por linha obsoleta, ou apenas avisar? → **Resolvida 2026-09-16: falha, e a mensagem traz o comando que corrige.** Ver ASSUMPTION-006 e US-7.
- [x] ~~**CLARIF-2**~~: A exigência de `consumidor:` em `write-spec` é bloqueante ou advertência? → **Resolvida 2026-09-16: bloqueante, nas duas skills.** Ver ASSUMPTION-007.

---

## Suposições

- **ASSUMPTION-001** · A medição de 2026-09-16 (67 não-vivas, 29 declaradas, 38 esquecidas) está correta e é a linha de base da allowlist · decidido 2026-09-16 por usuário · justifica: REQ-F5, AC US-3/AC-4
- **ASSUMPTION-002** · Uma função nomeada num `skills/*/SKILL.md` que manda rodá-la conta como alcançável, porque o agente é o executor · decidido 2026-09-16 por inferência · justifica: REQ-F2
- **ASSUMPTION-003** · O CI que roda `pytest -q` é chamador suficiente para satisfazer US-2, sem precisar de job próprio · decidido 2026-09-16 por inferência · justifica: REQ-F7, AC US-2/AC-1
- **ASSUMPTION-004** · Funções públicas de topo são amostra representativa; métodos de classe não mudariam a proporção 29:38 · decidido 2026-09-16 por inferência · justifica: o recorte de REQ-F1 · **DÍVIDA MEDIDA: são 18 classes de topo com 38 métodos públicos, ~9% da superfície, e o guarda nunca os viu.** Não há base para afirmar que a proporção se manteria. *(G3)*
- **ASSUMPTION-005** · JSON basta para a allowlist apesar de os motivos serem prosa, porque `tomllib` quebraria o job de Python 3.10 do CI · decidido 2026-09-16 por inferência · justifica: REQ-NF2
- **ASSUMPTION-006** · Falhar por linha obsoleta não cria resistência a consertar órfão, **porque a mensagem traz o comando copiável que sincroniza a lista** · decidido 2026-09-16 por usuário · justifica: REQ-F11, REQ-F12, US-7
  - *Raciocínio do autor, preservado:* "aviso que ninguém lê é a mesma doença. Uma allowlist com linha morta é um órfão de segunda ordem — o guarda contra abandono sendo abandonado." E sobre o meio-termo: *"'função ganhou chamador → só avisa' é justamente o caso que mais precisa sair da lista, porque é o único que representa progresso. Deixar o progresso virar sedimento é pior que deixar o erro."* O custo real — punir quem conserta — é desarmado pelo comando na mensagem.
- **ASSUMPTION-007** · O gate de `consumidor:` bloqueante nas duas skills não estoura o overhead de ~2 min da spec-light, porque na light ele é **uma linha** · decidido 2026-09-16 por usuário (bloqueante) e por inferência (o custo de uma linha) · justifica: REQ-F8, AC US-5/AC-3

---

## Success Criteria

- [ ] Todos os AC de P1 (US-1, US-2, US-3) passando em teste automatizado
- [ ] Allowlist inicial com 67 entradas, cada uma com `categoria` e `motivo` não-vazio
- [ ] `tools/orfaos.py` aparece como `viva` no próprio scanner (US-2/AC-4)
- [ ] Suíte completa verde: **1 272 + novos**, zero falhas
- [ ] Evidência externa de US-2/AC-3 registrada: execução em que a suíte falha por órfão plantado
- [ ] `tools/README.md` atualizado com o scanner, e a ausência de `wiki_lint.py`/`wiki_moc.py` nomeada sem ser resolvida (CONTEXT L3: nenhuma é ligada)
- [ ] Zero findings críticos em `wf-verify-multimodel`
- [ ] Todos os `[NEEDS CLARIFICATION]` resolvidos e convertidos em `ASSUMPTION-nnn`
