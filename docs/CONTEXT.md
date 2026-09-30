# CONTEXT — Incorporação Graphify + Obsidian ao Harness (t-20260611-204025)

> Gerado em modo autônomo (usuário ausente). Decisões Discretion são revisáveis — nenhuma é irreversível.

## Locked (decididas pelo usuário no prompt)

- L1. Harness local deve estar sincronizado com `github.com/Lharden/harness4claude` (**feito**: ff 7a8a47a → 2074d5b).
- L2. Aplicar nesta máquina o setup do doc `Downloads/obsidian-replicacao.md` (Local REST API + MCP nativo HTTP), direcionando o que não puder ser automatizado.
- L3. Incorporar o Graphify (graphify.net / safishamsi/graphify) ao harness de workflow.
- L4. Usar o Obsidian como camada de estruturação/aceleração do gerenciamento de contexto e conhecimento do Claude.

## Discretion (decididas pelo agente, justificadas)

- D1. **Coexistência MCP, não substituição**: `obsidian` → MCP nativo REST (como manda o doc) e mcpvault renomeado para `obsidian-fs` como fallback headless. Motivo: vault-bridge e sessões cron dependem de acesso com o app fechado (mcpvault é filesystem); o REST nativo exige Obsidian aberto.
- D2. **Pacote oficial `graphifyy`** (PyPI, duplo y) — alerta de typosquatting do próprio README. Instalação via pip global (Python 3.11.9 ✓).
- D3. **Hook PreToolUse do graphify ativado** (`graphify claude install`): alinhado ao objetivo "acelerar contexto" (grafo consultado antes de Glob/Grep). Reversível com `graphify claude uninstall`.
- D4. **Convenção de export Obsidian**: `AI-Brain/wiki/graphs/{repo-slug}/` via `--obsidian-dir`. Grafos de código viram parte do segundo cérebro (wikilinks + graph view).
- D5. **Fase de contexto do pipeline L2**: skill `graph-context` consulta `graphify-out/` primeiro; `wf-context-scan` vira fallback quando não há grafo.

## Deferred (registradas, NÃO fazer agora)

- DF1. `python -m graphify.serve` como MCP server de grafo — adiar até haver demanda de queries repetidas (já há muitos MCP servers ativos).
- DF2. Migrar vault-bridge para nomes de tools do REST nativo — só após a migração Camada B ser concluída e validada pelo usuário.
- DF3. Advisor Strategy / execução não-interativa (scope F6) — fora deste escopo.

## ASK (exigem o usuário — direcionamentos)

- A1. **Instalar plugin `obsidian-local-rest-api` no Obsidian** (Settings → Community plugins → Browse). Download automatizado foi negado pelo classificador de segurança (código executável de terceiro) — instalação via UI oficial é a rota correta. Depois: rodar `bash ~/.claude/obsidian-config/finish-obsidian-migration.sh`.
- A2. Revisar decisões D1–D5 e os diffs em `~/.claude/settings.json` / `CLAUDE.md` feitos por `graphify claude install`.

## Achados da auditoria (contexto)

- Health-check harness: ALL PASSED. Working tree limpo. Classificação regex deste prompt corrigida semanticamente (L2-bug → L2-feature, `agreed: false`).
- Vault desta máquina: `C:/Users/Leonardo/Documents/Obsidian Vault` (23 plugins, sync remotely-save/github-sync) — **sem** `obsidian-local-rest-api`; porta 27124 morta; Obsidian rodando.
- Plugin context-mode: hooks ativos (bloqueiam WebFetch/orientam sandbox) mas servidor MCP **não conectado** nesta sessão — tools `ctx_*` inexistentes. Inconsistência a reportar.
- firecrawl CLI v1.12.2 presente porém **não autenticado** (sem FIRECRAWL_API_KEY).

---

# CONTEXT — Mãe por ramo no Branch Keeper (t-20260909-195611274295, L2-bug)

> Acrescentado, não substituído: o sync espelha este arquivo para uma página
> única por projeto (`{slug}-context.md`), então sobrescrever apagaria o registro
> de junho. Pipeline de bug não tem fase `discuss`, logo não produz CONTEXT.md —
> e a camada de decisão do vault ficaria vazia justamente na sessão que mais
> decidiu. Ver [[../specs/parent-session-por-ramo-riscos|mapa de riscos]].

## Locked (decididas pelo usuário)

- **L1. Mapa de riscos antes de implementar.** *"Não podemos mais tolerar
  arrumar uma coisa e quebrar 3."* Quatro lentes adversariais em janela limpa
  precederam a primeira linha de código.
- **L2. `parent_session_id` guarda a intenção; o dono de disco é derivado.**
  Revisão de uma decisão anterior do próprio usuário (dois campos separados),
  feita depois de a medição mostrar onde o perigo se concentrava.
- **L3. Auto-cura só na escrita.** Sem ação `migrate`; registro legado responde
  pelo fallback.
- **L4. Fase 0 (teste) antes de qualquer conserto**, como pré-requisito e não
  como parte da camada.

## Recusadas — não reabrir sem dado novo

- **R1. Gravar o bucket de disco (`origin_bucket`) no registro.** Concentra três
  falhas: reentrância no `_Lock` (todo caminho que aciona a varredura já está
  dentro dele), slug × uuid (bucket é `uuid-sha8`, leitor recebe uuid cru), e
  dono errado carimbado é **durável e auto-confirmante**. Medido: a varredura já
  deriva o dono em 69 ms no pior caso real (239 buckets, 41 com banco).
- **R2. Carimbar em `parked_block`.** Caminho quente de todo `UserPromptSubmit`;
  as docstrings travam que ele não pode escrever nem levantar.
- **R3. Bumpar `SCHEMA_VERSION` de `branches.json`.** Campo aditivo sem bump é o
  padrão estabelecido do arquivo (`conclusion_delivered`, `origin_turn`,
  `launcher_path` entraram assim). Bumpar é que seria a novidade.
- **R4. Editar `contract/schemas/`.** A árvore efetivamente validada é a do
  `master-harness` — `contract_adapter.py:171` chama `load_contract()` sem
  `root`, então o lock deste repo é inerte localmente e uma edição só aqui
  passaria despercebida pela suíte.

## Regras que saíram desta sessão

- **Nunca marcar entrega num caminho que não emite.** Fecha os dois bugs de
  conclusão perdida: 2026-09-04 (sessão errada lendo) e 2026-09-09 (evento
  errado da sessão certa).
- **Escrita exige lock; leitura e contador seguem fail-open.** Ramo não
  registrado é recuperável; ramo fantasma no teto do `can_open` não é.
- **Na dúvida, o parking entrega.** Chamador anônimo e ramo órfão recebem: um
  bloco a mais custa cinco linhas, um bloco a menos é o ramo sumindo em silêncio.
- **Verde não é evidência; verde que já foi visto vermelho é.** Dois testes
  escritos nesta sessão passaram sem exercitar nada, e os dois só foram pegos
  porque o vermelho foi procurado de propósito.

## Deferred (registradas, NÃO fazer agora)

- **DF4. Ramo nasce no projeto da mãe, não no projeto dono.** Este ramo é a
  prova: aberto no `RSL_Project` para consertar `harness4claude`. A Fase 1 era
  pré-requisito e está pronta. Ver
  [[../specs/ramo-nasce-no-projeto-dono-spec-light]].
- **DF5. Um `ok` virou `L1-feature` e travou o Stop por três turnos.** Promoção
  L0→L1 lê o contador do projeto para decidir o nível de um turno; e
  `expire_stale_pipeline.py` trabalha sobre a projeção enquanto o banco manda.
  Ver [[../specs/ok-nao-pode-virar-l1-spec-light]].
- **DF6. Ferramenta de inspeção do parking consome a entrega.** Ler para
  verificar marca como visto; vale um `--peek` que não marca.

## Achados medidos (contexto)

- Suíte 1215 → **1234 passed** (+19 testes). Nove commits, todos deployados.
- A suíte ficava **verde** com cinco das seis camadas do plano sendo no-op —
  foi o que motivou a Fase 0.
- A varredura de `4b9a670` **encobria** o candidato `parent_session` em vez de
  cobri-lo: removê-lo inteiro deixava 174 passed.
- `required=True` no `_Lock` derrubou **34 testes de uma vez** por um efeito não
  previsto (o diretório do bucket nasce dentro do lock). Sem a rede da Fase 0,
  isso teria ido para o cache.

---

# CONTEXT — guarda de órfão

> Acrescentado, não substituído — e eu sobrescrevi antes de perceber. O aviso
> estava escrito no bloco de setembro, três parágrafos acima, e mesmo assim um
> `Write` apagou 113 linhas de duas tasks. Só apareceu porque o `git diff --stat`
> acusou 186 linhas trocadas num arquivo que eu achava novo. **Antes de escrever
> em `docs/CONTEXT.md`, leia o que já está lá.**

**Task:** `t-20260916-215220419949` · **Ramo:** `claude/guarda-de-orfao` · **Base:** `5ca9d4e`
**Fase:** `discuss` · **Decidido em:** 2026-09-16
**Medição que sustenta:** [`guarda-de-orfao-medicao.md`](specs/guarda-de-orfao-medicao.md)

---

## Locked — decidido, não relitigar

### L1 · O guarda reprova função pública de topo sem raiz externa

Uma função é **viva** quando existe caminho até ela a partir de uma raiz que o
host executa: hook (`hooks/*.sh`, `hooks/hooks.json`), `scripts/health-check.sh`,
`.github/workflows/ci.yml`, um `skills/*/SKILL.md` que manda rodá-la, ou outra
função viva. Não sendo, o guarda falha — **a menos que o nome esteja na
allowlist, com motivo escrito**.

Escopo: funções públicas de topo de módulo em `scripts/`, `hooks/`, `tools/`,
`skills/`. Métodos ficam de fora nesta volta, por comparabilidade com a medição.

### L2 · A allowlist é o artefato, e ela nasce com 67 linhas

29 decisões declaradas + 38 esquecidas. Não é lista de exceções a esvaziar: é o
inventário que nunca existiu. O critério de qualidade é **cada linha ter motivo
verdadeiro**, não a lista encolher.

### L3 · As 38 esquecidas entram como `ORFAO` e nada mais acontece com elas

Nenhuma é apagada, nenhuma é ligada, nenhum comportamento muda. O motivo de cada
linha registra o que se sabe hoje, inclusive "não sei por que isto existe".

**Consequência aceita:** `skills/branch-out/SKILL.md:142` continua afirmando que
"o renderizador recusa semente incompleta" enquanto `render_seed` não roda. A
afirmação falsa fica registrada na allowlist e na medição, não corrigida aqui.

### L4 · A parte A (liveness de capacidade em execução) vira ramo próprio

B responde *"existe caminho?"* estaticamente e é decidível agora. A responde *"foi
chamada de verdade?"* e precisa de sinal de execução por capacidade — a pergunta
que o seed diz não ter resposta óbvia. Misturar trava o fechamento de B.

Este ramo entrega **B (guarda) e C (consumidor nomeado em spec)**. A abre depois,
com a medição como base.

### L5 · O guarda tem de ser chamado, e a prova não pode depender dele

A ironia do seed é critério de aceite, não observação. Antes de fechar: o guarda
roda na suíte (`pytest`), e portanto no CI, que já roda `pytest -q`. A prova de
que é chamado é o CI verde sobre um commit onde ele falha de propósito — evidência
externa ao guarda.

### L6 · O instrumento vai para produção com os três erros documentados

`alcance.py` (scratchpad) vira código do repositório. Os três erros próprios
medidos hoje — ` as ` no heredoc, `module_from_spec`, e `tests/test_X.py` casando
como raiz de `X` por substring — viram **teste de regressão do guarda**, cada um.
O terceiro especialmente: é o teste certificando produção dentro do detector de
teste-certificando-produção.

---

## Deferred — fica para depois, com endereço

| item | por quê | onde |
|---|---|---|
| Liveness de capacidade em execução (parte A) | pergunta diferente, sinal diferente | ramo próprio, L4 |
| As 22 capacidades `required` provadas só por teste | C cobre specs novas; o contrato existente é retrofit | depende de A |
| `ListAgents` listando sessão sem processo | superfície do host, fora deste repositório | reportado pela sessão irmã; anotado |
| `branch_state.open_branches()` contar ramo morto como aberto | candidato real dentro deste repo, mas é outra medição | depois de A |
| Métodos de classe no escopo do guarda | medição foi feita sobre funções de topo | próxima volta |
| Correção de `SKILL.md:142` e ligação de `render_seed` | muda comportamento, não registro | L3 |

---

## Discretion — decido eu, sem perguntar

- Formato da allowlist (arquivo, sintaxe, onde mora).
- Nome do módulo e do teste.
- Como o guarda descobre raízes, e quais extensões varre.
- Granularidade das categorias de motivo na allowlist.
- Se o guarda é um teste, um script com teste, ou os dois — desde que L5 valha.
- Texto da regra que entra em `write-spec` / `write-spec-light` (parte C).

---

## Boundaries

**ALWAYS**
- Rodar a função de produção, nunca uma reimplementação dela, para produzir número.
- `exit_code == 0` não é aceite: ler a saída.
- Número aposentado permanece escrito, com `[superado: por quê]`.

**NEVER**
- Apagar função por estar órfã, nesta volta.
- Deixar linha de allowlist sem motivo, ou com motivo que seja o nome repetido.
- Aceitar teste como prova de que uma capacidade é usada.

**ASK**
- Se a allowlist inicial precisar passar de 67 linhas para a suíte passar — significa
  que a medição errou e o número tem de ser refeito antes do guarda.

---

# CONTEXT — evidência automática de suíte em segundo plano

> Acrescentado, não substituído (ver o aviso do bloco "guarda de órfão").

**Task:** `t-20260930-135548871810` · **Ramo:** `claude/vibrant-montalcini-9868a6` · **Base:** `18ec682`
**Fase:** `discuss` · **Decidido em:** 2026-09-30
**Origem:** decisão D4 de `docs/specs/portao-stop-em-voo-diagnostico.md` (ramo
`claude/magical-shamir-b49924`): a captura automática da evidência em segundo
plano é ramo próprio.

## O problema, em uma frase

`_handle_post_tool` só grava evidência de verificação confiável em PRIMEIRO
plano. Suíte que vai para segundo plano — pedida (`run_in_background`) ou
empurrada pelo host após o timeout do Bash (`backgroundTaskId` +
`timedOutAfterMs` em `tool_response`) — não produz evidência; o modelo lê a
saída e registra à mão pela receita de `comando_de_evidencia`. A suíte do
harness4claude leva ~21 min e por isso nunca se autoverifica.

## Locked — decidido pelo usuário, não relitigar

- **L-01 · Consumidor nomeado:** a suíte completa do harness4claude (~21 min,
  empurrada para o fundo pelo timeout de 10 min do Bash).
- **L-02 · O lançamento é registrado no `harness.db`:** id do job
  (`tool_response.backgroundTaskId`), comando, e a `code_revision` logo DEPOIS
  do toque do próprio lançamento.
- **L-03 · O sinal de término vem do transcript** (`transcript_path` do
  payload): blocos `<task-notification>` com `<task-id>`, `<status>`,
  `<output-file>` e o resumo com o código de saída. As formas terminais medidas
  em `jobs_em_voo` (TaskStop sem notificação, Monitor expirado, resumo órfão na
  retomada, entradas `queue-operation`) são o ponto de partida.
- **L-04 · Evidência na `code_revision` do LANÇAMENTO.** Se a revisão mudou
  desde o lançamento, a linha é histórico, não verificação. Hoje
  `record_evidence` grava sempre na revisão corrente; isso muda.
- **L-05 · Contagens saem do arquivo de saída pela lógica de produção de
  `_test_counts`; o código de saída sai da notificação.** Nada de
  reimplementação do parser.
- **L-06 · Retenção do arquivo de saída é medida,** não suposta: o arquivo
  existe quando o hook o lê?
- **L-07 · Falsificação nas duas metades:** suíte verde em segundo plano com a
  revisão inalterada verifica; com a revisão alterada, não verifica.
- **L-08 · Sequência de ramos (decidido 2026-09-30):** spec, grill-me, design e
  validate-plan agora, lendo o código NÃO commitado da `magical-shamir` como
  base. O `tdd` só começa depois que `magical-shamir` entrar em `main` e `main`
  for trazido para cá. Nada é copiado de lá antes disso. **Cumprido em
  2026-09-30:** `magical-shamir` entrou em `main` como `d3fc851`; este ramo
  avançou por fast-forward. Regras de coordenação recebidas da sessão "Plano e
  implementação System One Models": só testes afetados, um processo por vez;
  suíte completa só no merge; nenhum deploy sem OK do usuário; não mexer no
  caminho do classificador; este ramo é o 7º na ordem de merge sugerida.
- **L-09 · A captura roda nos leitores que DECIDEM sobre `verified`
  (brainstorming, 2026-09-30):** uma função só, que precisa apenas do banco (a
  linha do lançamento guarda `transcript_path`), chamada no Stop antes do
  portão e no `complete` antes de recusar. `continuation_policy.task_viva` fica
  de fora: é sonda somente-leitura por contrato ("nunca escreve no banco"), e
  todo turno termina num Stop antes do prompt seguinte.

## Deferred — fora do escopo

| item | por quê |
|---|---|
| Monitor, Workflow e Agent assíncrono como evidência | não produzem saída de teste; só Bash/PowerShell passam por `is_trusted_verification` |
| Job em segundo plano que escreve na árvore depois da evidência, sem toque | lacuna anterior (grill #52 da `magical-shamir`), do mesmo tipo que `shell-placeholder` |
| Digesto de árvore para `shell-placeholder` | `portao-mede-atividade` §3 |
| Editar `contract/schemas/` | R4 do bloco "Mãe por ramo": a árvore validada é a do `master-harness` |

## Discretion — Claude decide

- Nome e forma da tabela de lançamentos; migração no padrão de `_ensure_schema`.
- Como a notificação é autenticada como do host, e a régua de aceitação do
  arquivo de saída.
- Texto dos avisos.

## Constraints técnicas

- Função de produção, nunca reimplementação, para contar (`_test_counts`) e
  para ler o transcript (módulo `trabalho_em_voo`).
- Hook que falha não pode derrubar o Stop: toda leitura de transcript e de
  arquivo degrada para "sem captura" — o comportamento de hoje.
- Erro de captura tem direção obrigatória: na dúvida, NÃO verifica.
- Leitura do transcript com pré-filtro por substring (timeout do Stop: 15 s;
  maior transcript medido: 129 MB).
- Comando atômico para `state_cli` (isenção do contador).
