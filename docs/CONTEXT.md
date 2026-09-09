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
