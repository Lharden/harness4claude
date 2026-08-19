# Skill Router (v3.3) — operação e tuning

Design completo: `docs/specs/skill-router-design.md`. Este doc cobre operação.

## Ligar o router (opt-in desde 2026-07-28)

O router está **desligado por padrão**. Ambos os hooks (`harness-skill-router.sh`
e `harness-router-warmup.sh`) saem imediatamente sem `HARNESS_ROUTER=1`.

```bash
export HARNESS_ROUTER=1
```

Motivo (auditoria 2026-07-28): a Camada B depende de um Ollama local. Sem ele,
`router/debug-router.log` acumulou **88 falhas consecutivas — 100%
`TimeoutError`, zero sucessos** — pagando `EMBED_TIMEOUT` a cada prompt e nunca
consultando o índice de 276 skills que o warmup mantinha atualizado. Com o
opt-in, quem não tem Ollama não paga nada; quem tem, liga a variável.

Ligado, a Camada B ainda tem disjuntor: após `BREAKER_THRESHOLD` (3) falhas
seguidas ela entra em cooldown de `BREAKER_COOLDOWN_S` (900s) sem nem tentar, e
mensagens de erro idênticas só voltam ao log a cada `DBG_REPEAT_WINDOW_S` (1h).
Um sucesso zera o disjuntor. Estado em `~/.claude/harness/router/layer-b-breaker.json`.

## Política de disparo (Camada A → Camada B)

Resolve a decisão em aberto #2 do design doc. Implementado no commit `1b42240`
(`feat(router): Camada B (embed) so dispara quando Camada A nao acha nada`),
substituindo a recomendação original do design ("sempre em prompt elegível").

**Camada B (embed Ollama, ~900ms) só dispara quando a Camada A (match de nome/alias)
não encontra nada.** Ou seja, há **duas categorias de latência**, não um único gate
"<500ms" — ver medições abaixo. Efeito prático:

- Prompt bate um nome/alias curado (`skill-aliases.json`) → resposta rápida, Ollama nunca é chamado.
- Prompt não bate nada na Camada A → cai para a Camada B (embed semântico), mais lento
  mas cobre frases livres/paráfrases que não têm alias cadastrado.
- Isso também é o motivo pelo qual **aliases são o remédio nº 1 para MISS no golden set**
  (ver seção Operação): cada MISS resolvido por alias vira caminho rápido permanente,
  em vez de depender do embed toda vez.

## Knobs (hooks/skill_router.py)
| Constante | Default | Efeito |
|---|---|---|
| TOP_K | 3 | máx. skills por dica |
| MIN_COS | 0.45 | piso absoluto da Camada B |
| MIN_MARGIN | 0.05 | exigência acima da mediana dos cosines |
| DISABLED_MIN / DISABLED_LEAD | 0.60 / 0.08 | bar p/ sugerir skill de plugin desabilitado |
| MAX_OFFERS_PER_SKILL | 2 | dedupe por sessão |
| CONNECT_TIMEOUT | 0.15s | pre-check TCP: porta morta falha aqui, sem pagar o teto de leitura |
| EMBED_TIMEOUT | 3.0s | teto de leitura; acima disso degrada p/ Camada A |
| HARNESS_OLLAMA_URL (env) | http://127.0.0.1:11434 | override do endpoint — **IP literal, não hostname** |
| HARNESS_SKILLS_INDEX (env) | ~/.claude/harness/skills-index | override do índice (testes) |

### Por que o endpoint é IP e não `localhost`

`localhost` resolve `::1` antes de `127.0.0.1`, e o Ollama escuta só em IPv4. O
`urllib` não tem happy-eyeballs: ele espera o SYN em `::1` estourar antes de cair
para o IPv4. Medido em 2026-08-19, Ollama no ar e modelo quente:

| chamada | `localhost` | `127.0.0.1` |
|---|---|---|
| `ollama_reachable` | 157 ms | 16 ms |
| `embed_query` | 2283 ms | 220 ms |

São ~2,06s pagos antes de o request sair, contra um `EMBED_TIMEOUT` de 3,0s —
sobravam 700ms para o embed inteiro. É o gerador das 88 falhas consecutivas
(100% TimeoutError) que puseram o router atrás de `HARNESS_ROUTER=1`. Camada B
ponta a ponta caiu de ~2,75s para ~0,60s com a troca.

O `test_ollama_endpoint.py` trava isso: nenhuma fonte viva em `hooks/`,
`scripts/` ou `tools/` pode voltar a usar hostname. O
`test_router_reachability.py` não pegava porque monta seus sockets em
`127.0.0.1` literal — ele exercita o relógio, nunca a resolução de nome.

## Medições desta máquina

Máquina: RTX 5000 Ada, Windows, Ollama `nomic-embed-text-v2-moe`. Índice: 276 skills, dim 768.
Todos os números abaixo foram medidos e confirmados de forma independente.

- **Acurácia:** golden set top-3 hit rate **100% (15/15)** — gate ≥80% → **PASS**.
  Medido em 2026-08-12, 3 rodadas idênticas, índice reconstruído (246 skills, dim 768).

  Histórico da medição, toda ela determinística (3 rodadas por cenário):

  | Índice | Aliases | hit@3 | MISS |
  |---|---|---|---|
  | 2026-07-24, 276 skills | 8 entradas | 93,3% (14/15) | `"help me debug this failing test…"` |
  | 2026-08-12, 246 skills | 8 entradas | 93,3% (14/15) | o mesmo |
  | 2026-08-12, 246 skills | +`systematic-debugging` | **100% (15/15)** | — |

  O índice encolheu de 276 para 246 skills entre julho e agosto (plugins desabilitados ou
  removidos) **sem mexer no hit rate** — a acurácia não era artefato do corpus antigo.
  O MISS único, que o doc marcava como "candidato a novo alias", foi fechado exatamente
  assim: 3 aliases em `skill-aliases.json` o levaram de zero hits para Camada A, ou seja,
  caminho rápido (~437ms) em vez de depender do embed.

  > ✅ **Causa raiz do 47% fechada (2026-08-12, issue #13).** O `TEST_MATRIX.md`
  > registrava este teste como known-failure medindo **47%** contra os 93,3%, com
  > causa em aberto e hipótese de contaminação via `state.json`. Reproduzido:
  > **os 47% são a Camada A sozinha.** Varrendo o `EMBED_TIMEOUT` contra o golden
  > set, o hit rate fica em 93% até 0.30s e cai para exatamente **46,7% (8/15
  > vazios)** em 0.05s — o valor em que nenhum embed completa. Não é contaminação:
  > o teste chama `route()` direto e nunca toca `passes_guards`, a única função que
  > lê `state.json`.
  >
  > Repro:
  > ```python
  > import json, sys; sys.path.insert(0, "hooks")
  > import skill_router as sr
  > data = json.load(open("tests/data/golden-prompts.json", encoding="utf-8"))
  > index, vecs = sr.load_index()
  > sr.EMBED_TIMEOUT = 0.05
  > print(sum(any(e in [h["id"] for h in sr.route(c["prompt"], index["skills"], vecs)]
  >               for e in c["expect_any"]) for c in data["positives"]) / 15)  # 0.4666
  > ```
  >
  > **Implicação de projeto, agora demonstrada:** 47% era o *piso* do modo degradado, e
  > o piso é elevável. Adicionar os aliases de `systematic-debugging` subiu o piso de
  > **46,7% para 53,3%** — mesma medição, `EMBED_TIMEOUT=0.05`. Cada MISS resolvido em
  > `skill-aliases.json` sobe o piso e vira caminho rápido permanente.
- **Latência — Camada A (fast path, alias/nome bateu, embed pulado):**
  p50 ~437ms · **p95 ~470–535ms** — abaixo da meta de ~600ms. É o caso comum para
  prompts que casam com palavra-chave/alias.
- **Latência — Camada B (semantic path, Camada A vazia, embed roda):**
  p50 ~1.3s · **p95 ~1.4–1.5s**. Composição: round-trip do embed no Ollama ~900ms +
  spawn do processo Python no Windows ~390ms + carga do índice ~6ms. A lógica própria
  do router (layer_a/layer_b/pick) é sub-milissegundo — o custo é I/O externo, não CPU.
- **Concorrência:** roda em **paralelo com o harness-classify** (hooks de `UserPromptSubmit`
  disparam concorrentemente; a latência observada é o `max()` das duas, não a soma),
  respeitando o timeout de 5000ms do hook.
- **Ollama fora do ar:** degradação graciosa — a Camada A (aliases) continua respondendo,
  nenhuma exceção escapa do hook, exit 0 sempre. Desde 2026-08-12 um pre-check TCP
  (`CONNECT_TIMEOUT` 0.15s) separa "porta morta" de "modelo ocupado": porta morta custa
  ~150ms em vez de bloquear até o teto de leitura, e o `EMBED_TIMEOUT` pôde subir para
  3.0s sem encarecer esse caso. Histórico — antes disso o pior caso era
  ~1.7s antes de degradar (a correção se mantém — só não é instantânea).
- **Supressão por guard:** o router fica silencioso durante um pipeline harness ativo
  (por design — quem está roteando ali é o harness-workflow) e em prompts triviais/curtos.

## Operação
- Rebuild manual: `python scripts/build_skills_index.py` (`--no-embed` sem Ollama; `--check-stale` p/ diagnosticar).
- Aliases: `scripts/skill-aliases.json` → rebuild após editar. É o remédio nº 1 para MISS no golden set.
- Logs: `~/.claude/harness/router/debug-router.log` e `shim-errors.log`.
- **Pipeline ativo suprime dicas** (por design). Um state.json com task abandonada `status: active` silencia o router — concluir/limpar a task pendente do harness resolve.
- Rebuild em background pode morrer com o hook (MSYS): o marker `.stale` fica e o próximo SessionStart retenta; índice velho continua servível. (Deviação deliberada do design: o router NÃO retenta spawn no hot path.)

## Riscos conhecidos

**Race de leitura no `state.json` (Windows).** O router abre
`~/.claude/harness/state.json` em modo leitura (`passes_guards`) a cada prompt
elegível, concorrentemente com `harness-classify.sh`, que grava o mesmo arquivo
via `os.replace` (escrita atômica) sem retry. No Windows, `os.replace` sobre um
arquivo aberto por outro processo pode falhar com uma sharing violation
(`PermissionError`/`WinError 32`) em vez de suceder silenciosamente como no
POSIX — isto é uma característica do filesystem do Windows, não um bug de
lógica. Em teoria isto pode, raramente, fazer a escrita atômica de
`harness-classify` falhar.

Mitigação em escopo nesta branch: apenas esta documentação — o router já é
read-only e tolera qualquer falha de leitura (`OSError`/`ValueError` viram
"sem pipeline ativo", nunca uma exceção que escape do hook). Endurecer
`_atomic_write_json` do classify com um retry loop é um follow-up fora de
escopo aqui: `harness-classify.sh` é arquivo protegido e não deve ser tocado
por esta branch.

## Rollback
1. Remover os 2 blocos novos (router em UserPromptSubmit, warmup em SessionStart) de `hooks/hooks.json`.
2. Opcional: apagar `~/.claude/harness/skills-index/` e `~/.claude/harness/router/` (dados inertes).

## Ship p/ a cópia ativa
O Claude Code carrega a cópia de CACHE (`plugins/cache/harness4claude/...`), não este clone.
Caminho oficial: commit + push p/ GitHub (`Lharden/harness4claude`) + `/plugin update harness4claude`.
Teste local rápido (descartável): copiar `hooks/` + `scripts/` por cima da cópia de cache e abrir
sessão nova; a cópia de cache será sobrescrita no próximo update — nunca editar só nela.
