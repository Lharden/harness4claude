---
applies_to:
  - hooks/harness-branch-sensor.sh
  - hooks/hooks.json
  - scripts/branch_state.py
  - scripts/branch_seed.py
  - scripts/branch_sensor.py
  - scripts/vault_sync.py
  - scripts/health-check.sh
  - skills/branch-out/**
---

# Design — Branch Keeper (ramificação passiva)

## O problema

Conversa longa perde de três jeitos, e nenhum deles é anunciado:

1. **Deriva** — o fio escorrega do objetivo original.
2. **Ramos órfãos** — nascem várias ideias com mérito, uma é desenvolvida, as
   outras evaporam.
3. **Desperdício de contexto** — o assunto abandonado continua ocupando janela.

Quem deveria perceber é quem está dentro da conversa, e é exatamente quem perde
a perspectiva. Daí a solução ser um sensor externo, não uma regra de conduta.

## Arquitetura

```
┌─ Sensor (passivo) ──────────────────────────────────────────┐
│ UserPromptSubmit → tangente que o usuário jogou              │
│ Stop             → tangente que o modelo levantou            │
│                                                              │
│ Camada A: regex PT/EN  ──┐                                   │
│ Camada B: cos(turno, âncora) via nomic-embed ──┐             │
│                          A E B → RAMO ─────────┤             │
│                          B sustentada → DERIVA ─┤            │
│                          A sem B → RAMO degradado            │
└──────────────────────────┬───────────────────────────────────┘
                           │ systemMessage: BRANCH SIGNAL
┌─ Executor (skill branch-out) ────────────────────────────────┐
│ offer → nomeia, justifica, AskUserQuestion                   │
│ open  → semente (.md) + launcher (.ps1) + wt/PS7 + parking   │
│ list / recall / close / drift                                │
└──────────────────────────┬───────────────────────────────────┘
                           │
┌─ Estado (por projeto) ───┴───────────────────────────────────┐
│ branches.json · branch-anchor.json · branch-sensor.json      │
│ branches/<slug>.seed.md + <slug>.launch.ps1                  │
│ espelho: vault/wiki/branches · telemetria: signals.json      │
└──────────────────────────────────────────────────────────────┘
```

Sensor e executor são separados pela mesma razão que `harness-classify.sh` é
separado de `harness-workflow`: o hook enxerga um turno de texto, o modelo
enxerga a conversa. Semente escrita por hook seria semente sem contexto.

## Decisões e o porquê

| Decisão | Alternativa descartada | Razão |
|---|---|---|
| Ramo exige A **e** B | A sozinha sempre | Marcador de tangente no mesmo assunto é comum; sem B, o sensor viraria ruído |
| A sozinha vale quando B cai | Silêncio sem Ollama | Silêncio é indistinguível de "não havia ramo" — a falha invisível |
| Deriva ≠ ramo | Um veredicto só | Ramo abre janela (errar custa foco); deriva emite frase (errar custa uma linha) |
| `session_id` no nascimento | uuid na abertura | `--session-id` deixa o pai gravar o endereço do filho; ramo `pending` já é `--resume`-ável |
| Launcher em `.ps1` | String inline no `wt` | `wt → pwsh → claude → prompt multilinha` com `Program Files` no caminho: quoting quebra só na hora de abrir |
| `-w -1` (janela) | Aba nova | Aba nasce escondida atrás da atual — o mesmo esquecimento que a feature combate |
| Recusa **parkeia** | Recusa descarta | "Agora não" é o instante exato em que a ideia se perde |
| Parking soft | Bloqueio duro | Parking errado não pode travar assunto legítimo; uma palavra desfaz |
| Reusar breaker do router | Embed próprio | Um serviço, um disjuntor. Dois divergiriam na primeira troca de modelo |
| Dedupe lexical (Jaccard) | Dedupe por embedding | Roda no caminho quente; a camada semântica já está no sensor |

## Orçamento de ruído

Falso positivo aqui **é** o problema que a feature resolve. Por isso o orçamento
tem mais lógica que a detecção:

- 2 ofertas por sessão (`HARNESS_BRANCH_MAX_OFFERS`)
- cooldown de 8 chamadas de hook entre ofertas (`HARNESS_BRANCH_COOLDOWN_TURNS`). Uma troca completa gera duas chamadas, entao 8 vale ~4 trocas
- dedupe contra temas já registrados
- teto de 3 ramos abertos (`HARNESS_BRANCH_MAX_OPEN`)
- bloco de parking limitado a 5 itens, tema truncado em 80 chars

E há um orçamento de **latência**, não só de perguntas: a Camada B custa ~1s
(p95 medido 1049ms). Ela só roda quando há o que decidir — marcador da Camada A,
ou amostragem periódica para deriva (`HARNESS_BRANCH_DRIFT_SAMPLE`, default 2).
Pagar embed em todo prompt seria cobrar do foco para proteger o foco.

## Estado

`~/.claude/harness/projects/<slug>/`:

- `branches.json` — registro (`schema_version: 1`), autômato
  `pending → open → closed` / `open → recalled`, terminais fechados
- `branch-anchor.json` — objetivo da sessão + embedding; âncora de outra sessão
  é ignorada
- `branch-sensor.json` — orçamento, streak de deriva, contador de turno
- `branches/` — sementes e launchers

Escrita sob lock por diretório, mesmo protocolo de `scripts/state-lock.sh`.
Telemetria no bloco `branch` de `signals.json` (raiz): `created`, `open`,
`closed`, `recalled`, `discarded`, `offered_ramo`, `offered_ramo_degradado`,
`offered_deriva`. A razão `discarded / offered_*` é o que calibra os pisos —
mesmo loop que `aggregates.classify` fechou para a classificação.

## Testes

`tests/test_branch_state.py` (21) · `tests/test_branch_seed.py` (16) ·
`tests/test_branch_sensor.py` (38) · `test_vault_sync.py` (espelho) ·
`test_hook_liveness.py` (heartbeat do `Stop`) · smoke no `health-check.sh`
(âncora nasce, tangente dispara, payload vazio não quebra).

## Limites conhecidos

- `claude-cli://` existe registrado mas a gramática da URI não é pública: o host
  é Windows Terminal + PS7, sem tentativa de abrir no app desktop.
- Pisos (`0.55` / `0.35`) são chutes iniciais até a telemetria acumular.
- `Stop` é o evento mais novo do contrato do host; o heartbeat existe para que
  a morte dele apareça em vez de virar "nenhum ramo detectado".
