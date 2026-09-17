---
applies_to:
  - "tools/orfaos.py"
  - "tools/orfaos.json"
  - "tests/test_orfaos.py"
---

# Design — Guarda de órfão

**Task:** `t-20260916-215220419949` · **Spec:** [`guarda-de-orfao-spec.md`](guarda-de-orfao-spec.md)
**Grill:** [`guarda-de-orfao-grill.md`](guarda-de-orfao-grill.md) · **Criado:** 2026-09-16

---

## 1. Arquitetura

Três peças, e a fronteira entre elas é a doutrina do `arsenal.py`: **o registro
guarda apenas julgamento; todo fato vem do disco, medido na hora.**

```
                       ┌──────────────────────────┐
  hooks/*.sh           │  tools/orfaos.py         │
  scripts/*.sh   ─────▶│                          │
  .github/*.yml  raiz  │  1. varre defs (AST)     │
  skills/*.md          │  2. acha raízes externas │
  README.md            │  3. propaga alcance      │──▶ JSON {ready, orfaos[], obsoletas[]}
                       │  4. confronta allowlist  │        exit 0 | 1
                       └────────────┬─────────────┘
                                    │ lê (nunca escreve, exceto --sync)
                       ┌────────────▼─────────────┐
                       │  tools/orfaos.json       │  ← JULGAMENTO
                       │  {modulo, nome,          │     (categoria + motivo)
                       │   categoria, motivo}     │
                       └──────────────────────────┘

  dois consumidores INDEPENDENTES, e é isso que satisfaz US-2:
     scripts/health-check.sh   ──▶ python tools/orfaos.py
     tests/test_orfaos.py      ──▶ import orfaos            (veredito do pytest)
```

### Por que o scanner e o guarda são peças separadas

O scanner é um linter da família existente (`graph_lint.py`, `arsenal.py check`) e
herda o contrato dela. O guarda é um teste. Separá-los é o que dá **dois
chamadores independentes** — e US-2 exige que a prova de vida não venha do próprio
guarda. Se fossem a mesma peça, só haveria um caminho, e derrubá-lo derrubaria a
prova junto.

### Por que não usa `graphify-out/`

O grafo é não-direcionado; a vizinhança de `graph_lint.py` nele tem zero arestas.
Alcançabilidade é dirigida por definição. Usar vizinhança seria inventar
causalidade a partir de adjacência — o limite que `tools/README.md` já declara.

---

## 2. Data model

### `tools/orfaos.json`

```json
{
  "versao": 1,
  "medido_em": "2026-09-16",
  "base": "5ca9d4e",
  "declaracoes": [
    {
      "modulo": "calibrate_branch_floor",
      "arquivo": "scripts/calibrate_branch_floor.py",
      "nome": "medir",
      "categoria": "FERRAMENTA_DE_MAO",
      "motivo": "CLAUDE.md manda refazer a calibracao com este script apos mudanca no corpus."
    }
  ]
}
```

**Chave:** `(modulo, nome)`. `arquivo` é redundante de propósito — ele é o que
permite recolar o motivo quando um `git mv` quebra a chave (REQ-F14).

**`categoria`** — vocabulário fechado, validado:

| valor | significado | conta como |
|---|---|---|
| `FERRAMENTA_DE_MAO` | humano roda sob demanda; `motivo` cita onde está o como e o quando | decisão |
| `RESERVA_DECLARADA` | construído para degrau ainda não ligado; `motivo` cita a spec | decisão |
| `API_EXTERNA` | consumida por outro projeto ou host | decisão |
| `ORFAO` | ninguém sabe por que existe | **dívida** |

A divisão decisão/dívida é o produto: é ela que separa os 29 dos 38.

**`motivo`** — prosa. Validado contra: vazio, só espaço, e conter apenas o nome da
função (com ou sem pontuação). Um motivo que só repete o nome não informa.

### Estrutura interna do scanner

```python
Definicao  = {modulo, nome, arquivo, linha, entrypoint}
Aresta     = (modulo, nome) -> set[(modulo, nome)]     # dirigida
Raiz       = modulo -> [arquivo_que_a_declara, ...]
Veredito   = "viva" | "inalcancavel" | "modulo_morto"
```

Privadas (`_x`) entram no grafo mas **não** no veredito: uma privada viva pode
alcançar uma pública, e ignorá-la produziria falso órfão.

---

## 3. Contrato de CLI

```
python tools/orfaos.py                 # JSON no stdout, exit 1 se ready=false
python tools/orfaos.py --report        # legível, agrupado por arquivo e categoria
python tools/orfaos.py --sync          # sincroniza a allowlist (ver abaixo)
python tools/orfaos.py --raiz <dir>    # outro repositório
```

Saída JSON:

```json
{
  "ready": false,
  "total": 419,
  "vivas": 352,
  "orfaos_nao_declarados": [{"modulo":"x","nome":"f","arquivo":"...","linha":12}],
  "declaracoes_obsoletas": [{"modulo":"y","nome":"g","por_que":"ganhou_chamador|alvo_sumiu"}],
  "declaracoes_invalidas": [{"modulo":"z","nome":"h","por_que":"motivo_vazio"}],
  "arquivos_nao_analisados": ["scripts/quebrado.py"],
  "raizes": 43,
  "comando": "python tools/orfaos.py --sync"
}
```

### `--sync`, e o que ele deliberadamente NÃO faz

| caso | `--sync` faz | suíte depois |
|---|---|---|
| linha obsoleta — a função ganhou chamador | **remove a linha** | **verde** |
| linha obsoleta — o alvo sumiu do código | marca `obsoleta: true` e **imprime o motivo para recolagem**; não apaga | vermelha até ação explícita |
| órfão novo | acrescenta com `categoria: "ORFAO"` e `motivo: ""` | **vermelha** — falta o julgamento |

**Sincronizar não é aprovar.** O único caso que fica verde sozinho é o que
representa progresso — que era exatamente a preocupação do autor em CLARIF-1.
A mensagem de falha diz qual caso é, e só promete verde no primeiro (G1).

O motivo escrito **nunca** é destruído por `--sync` (G6): a allowlist é o produto
desta feature, e um `git mv` não pode apagá-la.

---

## 4. Algoritmo

```
1. DEFINIÇÕES  — AST de scripts/, hooks/, tools/, skills/; FunctionDef de topo.
                 Públicas viram veredito; privadas entram só no grafo.

2. RAÍZES      — varre .sh .js .cjs .json .yml .md em hooks/, scripts/,
                 skills/, contract/, .github/ e raiz. Dois padrões:
                   a) caminho:  <fronteira>(dir/)*<modulo>.py
                   b) heredoc:  from <modulo> import a as b, c
                                import <modulo>
                 FRONTEIRA À ESQUERDA é obrigatória (G2).
                 Caminho sob tests/ NUNCA é raiz.

3. PROPAGAÇÃO  — BFS a partir de:
                   - código de topo e corpo de classe de cada módulo com raiz
                   - nome importado direto por heredoc de host
                   - módulos importados por módulo vivo (fecho de import)
                 arestas: Name(Load), Attribute sobre módulo, getattr com
                 literal, star de spec_from_file_location.
                 Auto-referência NÃO conta (recursão não é vida).

4. VEREDITO    — viva | inalcancavel (módulo vivo, função não) | modulo_morto

5. CONFRONTO   — não-vivas \ allowlist  = orfaos_nao_declarados   → reprova
                 allowlist ∩ vivas      = obsoletas (ganhou chamador) → reprova
                 allowlist \ definições = obsoletas (alvo sumiu)     → reprova
                 allowlist inválida     = motivo/categoria ruim      → reprova
                 zero raízes            = reprova (REQ-F10)
                 arquivo não parseia    = reprova (REQ-F9, G7)
```

**Dupla inclusão, não contagem** (G4): o invariante é `não-vivas = allowlist`, nos
dois sentidos. O 67 é linha de base medida, nunca um `assert`.

---

## 5. Test strategy

`tests/test_orfaos.py`, três blocos.

### Bloco A — comportamento, contra árvore sintética em `tmp_path`

Uma mini-árvore com `scripts/`, `hooks/`, `tests/` própria. Nenhum teste deste
bloco lê o repositório real: fixture sintética é o que torna AC-2 (falha) e AC-3
(allowlist) determinísticos.

| teste | AC |
|---|---|
| `test_funcao_chamada_por_hook_sh_e_viva` | US-1/AC-1 |
| `test_orfao_nao_declarado_reprova_com_arquivo_e_linha` | US-1/AC-2 |
| `test_orfao_declarado_passa` | US-1/AC-3 |
| `test_referencia_so_em_teste_nao_torna_viva` | US-1/AC-4 |
| `test_recursao_nao_torna_viva` | US-1/AC-5 |
| `test_alcance_e_transitivo_a_partir_da_raiz` | US-1/AC-6 |
| `test_zero_raizes_reprova` | REQ-F10 |
| `test_arquivo_que_nao_parseia_reprova_com_nome` | REQ-F9 |

### Bloco B — as três armadilhas de substring (G2 / US-4)

| teste | o erro real que ele trava |
|---|---|
| `test_import_com_as_no_heredoc_conta` | `from harness_paths import find_repo_root as _raiz` |
| `test_modulo_carregado_por_spec_from_file_location_conta` | `mod.mark_stale()` em `harness-lifecycle.py` |
| `test_teste_nao_vira_raiz_de_producao` | `tests/test_harness_paths.py` → `harness_paths` |
| `test_prefixo_nao_vira_raiz` | `build_wiki_index.py` → `wiki_index` |
| `test_sufixo_nao_vira_raiz` | `outroM.py` → `M` |

**Os três primeiros são erros que aconteceram**, medidos em 2026-09-16, não casos
imaginados. O terceiro é o próprio defeito que este guarda caça, ocorrido dentro
dele.

### Bloco C — contrato da allowlist e o comando (US-3, US-7)

| teste | AC |
|---|---|
| `test_motivo_vazio_reprova` | US-3/AC-1 |
| `test_motivo_que_so_repete_o_nome_reprova` | US-3/AC-2 |
| `test_categoria_fora_do_vocabulario_reprova` | US-3/AC-3 |
| `test_dupla_inclusao_allowlist_e_codigo` | US-3/AC-4 |
| `test_linha_obsoleta_por_ganhar_chamador_reprova` | US-7/AC-1 |
| `test_linha_obsoleta_por_alvo_sumido_reprova` | US-7/AC-2 |
| `test_sync_remove_linha_obsoleta_e_deixa_verde` | US-7/AC-5 |
| `test_sync_nunca_apaga_motivo_escrito` | REQ-F14 / G6 |
| **`test_comando_que_a_mensagem_imprime_de_fato_roda`** | US-7/AC-4 |

O último segue `5ca9d4e` literalmente: extrai a linha de comando **da própria
mensagem de falha** e a executa via `subprocess`, exigindo exit 0. Quem verificar
esta mensagem no futuro executa em vez de ler.

### Bloco D — o guarda sobre o repositório real

| teste | AC |
|---|---|
| `test_repositorio_real_passa_o_guarda` | US-1, US-3/AC-4 |
| `test_health_check_invoca_o_scanner` | US-2/AC-2 — lê `health-check.sh` e procura a linha |
| `test_skills_de_spec_exigem_consumidor_nomeado` | US-5/AC-4 |

**`test_repositorio_real_passa_o_guarda` é o guarda.** Os outros testam o scanner;
este é o que falha quando nasce órfão.

### O que NÃO é teste

A evidência de US-2/AC-3 — a suíte falhando por órfão plantado — **não** vira
teste: um teste que espera falha estaria medindo a si mesmo. É uma execução
manual, com a saída colada na verificação. Veredito do `pytest`, não do guarda.

---

## 6. Riscos

| # | risco | mitigação | risco residual |
|---|---|---|---|
| R1 | Regex de raiz casa endereço em prosa negativa (*"não rode `orfaos.py`"*) | nenhuma — lê endereço, não negação | marca viva o que não é: **o estado de hoje**, não regressão. Declarado |
| R2 | Dispatch que o AST não vê (nome computado, import por string montada) | vai para a allowlist com `motivo` | declaração explícita em vez de detecção mágica — é o desfecho correto |
| R3 | **38 métodos públicos em 18 classes ficam fora, e mover função para dentro de classe é evasão** | nenhuma nesta volta | **9% da superfície cega.** Registrado em ASSUMPTION-004 como dívida medida |
| R4 | Allowlist vira `# noqa` em massa | validação de motivo + categoria `ORFAO` explicitamente rotulada como dívida | um motivo mentiroso passa. Nenhuma máquina lê prosa |
| R5 | `--sync` destrói julgamento num rename | REQ-F14: nunca apaga motivo; imprime para recolagem | recolagem é manual |
| R6 | Scanner lento demais para rodar em toda suíte | 129 arquivos, AST puro; REQ-NF1 = 5 s | medir, não supor |
| R7 | O guarda vira órfão | dois chamadores independentes (US-2), um deles o `pytest` do CI | se ambos caírem juntos, nada avisa — é o limite de qualquer guarda |

---

## 7. Ordem de implementação

1. `tools/orfaos.py` — scanner, sem allowlist, `--report`. Bloco A + B dos testes.
2. `tools/orfaos.json` — as 67 linhas, com motivo escrito uma a uma.
3. Confronto + `--sync` + mensagem com comando. Bloco C.
4. `tests/test_orfaos.py::test_repositorio_real_passa_o_guarda`. Bloco D.
5. `scripts/health-check.sh` + `tools/README.md` (inclui as linhas ausentes de
   `wiki_lint.py` e `wiki_moc.py`, que a medição achou).
6. Parte C: `consumidor:` bloqueante nas duas SKILL.md + teste que confere.
7. Plantar órfão, rodar a suíte, colar a saída na verificação, remover o órfão.
