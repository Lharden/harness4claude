# Skill Router (v3.3) — operação e tuning

Design completo: `docs/specs/skill-router-design.md`. Este doc cobre operação.

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
| EMBED_TIMEOUT | 1.2s | acima disso degrada p/ Camada A |
| HARNESS_OLLAMA_URL (env) | http://localhost:11434 | override do endpoint |
| HARNESS_SKILLS_INDEX (env) | ~/.claude/harness/skills-index | override do índice (testes) |

## Medições desta máquina

Máquina: RTX 5000 Ada, Windows, Ollama `nomic-embed-text-v2-moe`. Índice: 276 skills, dim 768.
Todos os números abaixo foram medidos e confirmados de forma independente.

- **Acurácia:** golden set top-3 hit rate **93.3% (14/15)** — gate era ≥80% → **PASS**.
  Único MISS conhecido: `"help me debug this failing test..."` (retorna zero hits; nenhum
  alias/skill cadastrado casa com essa frase — candidato a novo alias).
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
  nenhuma exceção escapa do hook, exit 0 sempre. No Windows uma porta morta bloqueia até
  `EMBED_TIMEOUT` (1.2s) em vez de recusar a conexão na hora, então o pior caso é
  ~1.7s antes de degradar (a correção se mantém — só não é instantânea).
- **Supressão por guard:** o router fica silencioso durante um pipeline harness ativo
  (por design — quem está roteando ali é o harness-workflow) e em prompts triviais/curtos.

## Operação
- Rebuild manual: `python scripts/build_skills_index.py` (`--no-embed` sem Ollama; `--check-stale` p/ diagnosticar).
- Aliases: `scripts/skill-aliases.json` → rebuild após editar. É o remédio nº 1 para MISS no golden set.
- Logs: `~/.claude/harness/router/debug-router.log` e `shim-errors.log`.
- **Pipeline ativo suprime dicas** (por design). Um state.json com task abandonada `status: active` silencia o router — concluir/limpar a task pendente do harness resolve.
- Rebuild em background pode morrer com o hook (MSYS): o marker `.stale` fica e o próximo SessionStart retenta; índice velho continua servível. (Deviação deliberada do design: o router NÃO retenta spawn no hot path.)

## Rollback
1. Remover os 2 blocos novos (router em UserPromptSubmit, warmup em SessionStart) de `hooks/hooks.json`.
2. Opcional: apagar `~/.claude/harness/skills-index/` e `~/.claude/harness/router/` (dados inertes).

## Ship p/ a cópia ativa
O Claude Code carrega a cópia de CACHE (`plugins/cache/harness4claude/...`), não este clone.
Caminho oficial: commit + push p/ GitHub (`Lharden/harness4claude`) + `/plugin update harness4claude`.
Teste local rápido (descartável): copiar `hooks/` + `scripts/` por cima da cópia de cache e abrir
sessão nova; a cópia de cache será sobrescrita no próximo update — nunca editar só nela.
