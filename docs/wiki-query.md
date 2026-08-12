# Wiki Query — operação e tuning

A operação `query` do vault AI-Brain: busca semântica sobre a wiki, com citação de
página. Skill: `skills/wiki-query/`. Este doc cobre operação; o desenho está em
`docs/specs/` do vault (`AI-Brain/CLAUDE.md`, seção Operações canônicas).

## Por que existe

O padrão LLM Wiki (Karpathy) define três operações: `ingest`, **`query`**, `lint`. O
`AI-Brain/CLAUDE.md` v0.1.0 escreveu `ingest`, **`inbox`**, `lint`, `sync` — três que
escrevem, nenhuma que lê. Sem consulta, nada denuncia índice desatualizado ou link
quebrado, e o vault apodreceu por três meses (89 erros de lint no baseline de
2026-08-11). Esta é a operação que faltava.

## Arquitetura: reuso, não fork

| Peça | De onde vem |
|---|---|
| `layer_a` / `layer_b` / `pick` | `hooks/skill_router.py`, **importados** |
| `l2norm` / `pack_f16` / `ollama_embed` / `atomic_write` | `scripts/build_skills_index.py`, importados |
| Estrutura do golden set | `tests/data/golden-prompts.json` |
| Contrato de falha | router: "NUNCA bloqueia — qualquer falha ⇒ degrada, nunca exceção" |

Os registros de chunk carregam `enabled: True` e `usage_count: 0` de propósito: são os
campos que `layer_b`/`pick` exigem, neutros por construção, e é o que permite reusar as
funções de decisão do router sem fork.

Índice **separado** (`~/.claude/harness/wiki-index/`) — o skill-router em produção não é
tocado.

## Chunking por seção

Uma skill é "nome. descrição" e cabe num vetor. Uma página de wiki é prosa longa e
multi-assunto: embedada inteira vira um centroide, e pergunta sobre uma seção específica
perde para o assunto médio da página.

Medido no corpus real (67 páginas), com o piso do router (0.45):

| Indexação | hit@3 | falso-positivo |
|---|---|---|
| 1 vetor por página | 60% | 0 |
| 1 vetor por seção (512 chunks) | 80% | 0 |
| 1 vetor por seção + aliases curados | **93%** | 0 |

Seções menores que 80 caracteres são absorvidas pela seguinte. Tabelas entram achatadas
(`| a | b |` → `a b`) porque neste vault é nelas que moram as decisões — pulá-las
esvaziava justamente as páginas mais consultáveis.

## Duas bandas, ambas medidas

`scripts/calibrate_wiki_floor.py` varre o piso contra `tests/data/golden-wiki.json`.
Nenhum dos dois valores foi chutado.

| Knob | Valor | Papel |
|---|---|---|
| `MIN_COS` | 0.32 | **vale mostrar**. Acerto correto em rank #1 pontua 0.33–0.40; cortar em 0.45 descartaria resposta certa. |
| `CONFIDENT_COS` | 0.45 | **vale afirmar**. É o `MIN_COS` do router, reaproveitado: nenhuma pergunta fora do domínio do vault alcança esse patamar. |
| `OVERFETCH` | 4 | chunks buscados por página desejada, antes da dedupe por página |
| `EMBED_TIMEOUT` | 8.0s | folgado ante o 1.2s do router: aqui a consulta é deliberada, não caminho quente |
| `DEFAULT_TOP_K` | 5 | páginas devolvidas (a dedupe roda antes do corte) |

Varredura completa (top-3, corpus de 2026-08-11):

```
  piso    hit@3   falso+
  0.28     100%        1
  0.32     100%        1     <== MIN_COS
  0.38     100%        1
  0.40      93%        1
  0.45      93%        0     <== CONFIDENT_COS
```

O único falso-positivo abaixo de 0.45 é a classe "pergunta técnica fora do domínio"
(medido com *"como configurar um reverse proxy nginx"*, que casa 0.4488 com
`harness-lite/06 Componentes, Adaptadores e Integrações` — proxy é proxy). Ele aparece
marcado `(abaixo da barra)` e com o aviso de cobertura; a skill instrui a descartá-lo ao
ler a página. Nenhum negativo produz hit **confiante**.

## Golden set

`tests/data/golden-wiki.json` — 20 positives (pergunta + `expect_any`) e 3 negatives
(pergunta + `reason`). Gates em `tests/test_wiki_golden.py`:

- positives: alvo no top-3 em ≥ 80% — **medido 95% (19/20)**
- negatives: zero hits confiantes — **medido 0**
- alvos do golden existem no índice (falha aqui, não no hit rate, quando página é renomeada)

O alvo pode ser uma página (`area/pagina`) ou um verbete (`area/pagina#Termo`). No compêndio
a unidade de sentido é a seção: casar só a página daria o caso por certo sem provar que o
verbete certo veio.

MISS conhecido, mantido de propósito: *"o que fazer quando o serviço de embedding cai no
meio do pipeline"* não alcança `compendio/03 confiabilidade#Degradação graciosa`. O verbete
responde pelo nome (cos 0.5252), mas a paráfrase do problema perde para 640 chunks de prosa
de projeto. Reescrever o caso para ele passar apagaria a informação.

Pula automaticamente se o índice real ou o Ollama estiverem ausentes, como o
`test_router_golden.py`.

## Latência medida

Máquina LHarden2, Windows, RTX 5000 Ada, `nomic-embed-text-v2-moe` residente em GPU,
índice de 512 chunks / 588 KB. `scripts/bench_wiki.py`, n=12, subprocess real, GPU a
35–38% de utilização de base (desktop: Teams, Edge WebView, Obsidian):

- **Camada A** (alias curado, embed pulado): p50 **214ms** · p95 223ms
- **Camada B** (semântico, embed roda): p50 **853ms** · p95 887ms

Composição, medida camada a camada com subprocessos:

| Etapa | p50 acumulado | Delta |
|---|---|---|
| `python -c pass` (startup Windows) | 124ms | — |
| + import do `wiki_query` | 179ms | +55ms |
| + `load_index` (588 KB, 512 chunks) | 203ms | +24ms |
| Camada A completa | 206ms | +3ms |
| Camada B completa | 800ms | +597ms (embed) |

O embed domina a Camada B; índice e cosseno somam ~40ms, então crescer o vault não muda
o número. Embed cru medido em isolamento (POST direto em `/api/embed`, 12 amostras após
aquecimento): **577ms** p50 para query curta, 565ms para query de 1400 chars — o
comprimento da pergunta é irrelevante.

### Sobre a medição de 3023ms descartada

A primeira rodada deste bench registrou p50 de 3023ms para a Camada B, e isso chegou a
ser documentado como bloqueio para a Onda 3. Estava errado por dois motivos, ambos de
método:

1. A decomposição inicial cronometrou a **primeira** chamada de embed do processo, sem
   descartar aquecimento — 2685ms de amostra única.
2. Havia um processo pesado do Codex ocupando a GPU no momento (utilização em 82%,
   contra 35% na medição definitiva).

Repetida com aquecimento e sob carga normal, a Camada B mede 853ms — coerente com o
p50 de ~1.3s que `docs/router.md` registra para a mesma etapa no skill-router. **Não há
bloqueio de latência para a injeção automática no `discuss`.** A lição operacional fica:
medir embed sob contenção de GPU produz número que não representa o caminho quente, e
uma amostra sem aquecimento não é medida.

## Prior-art no pipeline (`tools/wiki_prior_art.py`)

Passo 0 da skill `discuss`, em pipelines L2. Responde "isto já foi decidido antes?".

Prior-art é uma **tarefa de busca diferente** da consulta livre: a descrição chega como
proposta ("quero adotar TLA+ para verificar as invariantes"), não como pergunta, e o
embedding responde com vizinhos temáticos. Medido: a página que recusa TLA+ cai para
rank **24/512** nessa frase, enquanto páginas sobre *invariantes* ocupam o topo. Daí duas
camadas:

1. **Literal** — nome próprio de técnica (`TLA+`, `pm4py`, `HNSW`, `qwen3.5`) é o sinal
   mais forte de "já falamos disso". Termos passam por filtro de discriminância: só vale
   o que aparece em ≤15% das páginas, senão `harness` casaria com tudo.
2. **Semântica** — hits do `wiki_query`, **só os confiantes**.

Ambas passam pelo mesmo filtro final: o achado só conta se estiver numa página
`type: decision` **ou** sob cabeçalho de decisão (`recusa|decis|adot|assimil|troca|
rejeit|substitu|escolh|descart`). Sem ele, uma tarefa sobre "parser de CSV" puxava três
specs que citam um `.csv` de passagem — prior-art é *decisão* sobre X, não *menção* de X.

Comportamento medido no vault real:

| Tarefa | Resultado |
|---|---|
| "adotar TLA+ para verificar as invariantes" | `decisions/assimilacoes-2026 › Recusas registradas` + `09 Autorreforma › Decisões estruturantes` |
| "usar HNSW no retrieval" | mesma dupla, via termo `HNSW` |
| "trocar o modelo de classificação para qwen3.5:9b" | a recusa de 2026-06-03 |
| "melhorar a legibilidade das mensagens de erro" | **silencioso** |
| "criar um parser de CSV" | 2 decisões que mencionam `.csv` (ruído residual, aceito) |

Latência p50: **915ms**. Sai 0 sempre — é passo de contexto, não gate.

## Digest no SessionStart

`tools/wiki_index.py --digest` emite ~**411 bytes**: cobertura por categoria, a lista de
`decisions/` (a superfície de prior-art) e como consultar. O `index.md` inteiro tem
10.8 KB — caro demais para toda sessão.

`hooks/harness-session-start.sh` junta esse bloco ao resume de pipeline num **único**
`systemMessage`. Hook completo: **915ms**. Sem vault, não emite nada.

## Operação

- Rebuild: `python scripts/build_wiki_index.py --root "$VAULT_PATH/AI-Brain"`
  (`--no-embed` sem Ollama; `--check-stale` sai 1 quando desatualizado).
- Aliases: `scripts/wiki-aliases.json` → rebuild após editar. **É o remédio nº 1 para
  MISS no golden set**, mesma lição do `skill-aliases.json`: cada MISS resolvido por
  alias vira caminho rápido permanente (207ms) em vez de depender do embed toda vez.
  Foram os aliases que levaram o hit@3 de 80% para 93% no piso de 0.45.
- Calibrar após mudança grande no corpus: `python scripts/calibrate_wiki_floor.py`.
- Índice fica stale sozinho conforme a wiki muda; `--check-stale` compara o hash de
  caminho+mtime+tamanho das páginas.

## Limites conhecidos

1. **Pergunta técnica fora do domínio** pode devolver hit abaixo da barra (classe
   nginx). Mitigação: banda de confiança + a skill manda ler antes de citar.
2. **Latência sob contenção de GPU**: com outro processo pesado na placa, a Camada B
   sobe de ~850ms para ~3s. Quem chamar no caminho quente deve tolerar isso — o
   contrato de falha já degrada para a Camada A quando o embed estoura o timeout.
3. **Sem `ingest`**: `wiki/sources/` continua vazio. A query só alcança o que já está no
   vault; fonte externa nova não entra sozinha.
4. **Subárvore `graphs/`** (949 notas do graphify) está fora do índice de propósito —
   inundaria o corpus com notas de nó. Para grafo, use `graph-context`.

## Rollback

1. Remover o bloco `import wiki_index` do `hooks/harness-session-start.sh` (o resume de
   pipeline volta a ser o único conteúdo do `systemMessage`).
2. Remover o passo 0 de `skills/discuss/SKILL.md`.
3. Opcional: apagar `~/.claude/harness/wiki-index/` (dados inertes) e as skills/tools
   `wiki-query`, `wiki_prior_art`.

Cada item é independente — nenhum passo depende do anterior, e o pipeline funciona com
qualquer subconjunto removido.

## Ship p/ a cópia ativa

O Claude Code carrega a cópia de CACHE (`plugins/cache/harness4claude/...`), não este
clone. Caminho oficial: commit + push p/ `Lharden/harness4claude` +
`/plugin update harness4claude`. Ver `docs/router.md` § Ship.
