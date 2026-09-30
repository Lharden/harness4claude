---
applies_to:
  - scripts/workflows/wf-verify-multimodel.js
  - scripts/checar_triagem.py
  - skills/harness-workflow/SKILL.md
  - tests/wf_run_harness.cjs
  - tests/test_verify_pass_honesto.py
  - tests/test_verify_por_tabela.py
  - tests/test_checar_triagem.py
  - tests/data/verify-rodadas-2026-09-30/**
---

# Design: o `pass` do verify não sai verde com achado aberto

**Spec Link**: [verify-adjudica-tudo-spec.md](verify-adjudica-tudo-spec.md) (v2, `a221fad`, aprovada em 2026-09-30)
**Status**: Draft
**Created**: 2026-09-30
**Author**: AI-generated, reviewed by Leonardo

---

## Technical Context

**Language**: Python 3.10+ (`pyproject.toml`: `target-version = "py310"`; máquina com 3.14.6) e JavaScript do host de Workflow (Node 24.16 no harness de teste)
**Framework**: nenhum; stdlib (`argparse`, `json`, `pathlib`, `re`)
**Key Dependencies**:
- Host do Claude Code — tool `Workflow` (`agent`, `parallel`, `phase`, `log`, `args`) e o formato do `.output`
- `tests/wf_run_harness.cjs` — executa o corpo do Workflow com os primitivos mockados

**Storage**: arquivos — cópia JSON da saída de cada rodada em `<STATE_DIR>/verify/`; tabela em Markdown no `closure-plan.md`
**Testing**: pytest (testes afetados, um processo)
**Type Check**: pyright
**Lint**: ruff (`line-length = 120`, regras E/F/I/UP/B/C4/SIM/ISC/RUF)

**Constraints**:
- `.js` sem CR (`.gitattributes`, `*.js eol=lf`)
- Nenhum `agent()` a mais por rodada (REQ-NF1)
- Checador erra reprovando (REQ-NF4)
- Nada em `state_cli`, `transactional_state` ou hooks (NEVER)

---

## Architecture

Três unidades, cada uma com um dono claro da decisão:

1. **O Workflow decide o `pass` de uma rodada.** Continua sendo a única fonte de
   `pass`, agora com a fórmula de REQ-F1. Ganha três coisas no retorno (`n`,
   `summary` autoexplicativo) e três nos prompts (rubrica, `repo_root`,
   instrução ao adjudicador). Não sabe nada de triagem nem de rodadas anteriores.
2. **O checador decide se a triagem está completa.** Script puro, sem estado:
   recebe as cópias das rodadas e o `closure-plan.md`, devolve 0/1/2. Não chama
   o host, não escreve nada.
3. **A skill decide o laço.** A seção de verify do `SKILL.md` é o protocolo de
   rodadas (REQ-F8): quando rodar de novo, quando copiar, quando chamar o
   checador, quando escalar, a ordem até o `complete`.

A medição de B não é componente de produção: é um procedimento com dados
congelados no repo e um juiz cego, e só consome o Workflow.

### High-Level Diagram (ASCII)

```
                       ┌────────────── skill harness-workflow (REQ-F8) ──────────────┐
                       │                                                              │
 rodada k ──▶ Workflow (script) ──▶ .output do host ──cópia──▶ <STATE_DIR>/verify/<run>.json
                │  pass, n, summary                                     │
                │                                                       ▼
                │                          closure-plan.md ──▶ checar_triagem.py ──▶ 0 / 1 / 2
                ▼                          (tabela de destino)          │
        bloqueio? ── sim ──▶ gap-closure ──┐                            │
        conserto? ── sim ──▶ rodada k+1 ◀──┘ (k < 3)                    │
                                                                        ▼
                                      0 ──▶ evidência de teste final ──▶ state_cli complete
```

### Key Components

1. **Fórmula do `pass` e retorno** (`scripts/workflows/wf-verify-multimodel.js`)
   - Responsibility: `pass = criticals.length === 0 && nosMortos.length === 0 && naoAdjudicaveis.length === 0`; `n` = posição 1-based em `findings`; `summary` com "N aguardam triagem — rode checar_triagem.py (secao verify do harness-workflow)".
   - Interface: retorno `{ pass, critical_count, nos_mortos, nao_adjudicados, findings[{n, title, severity, file, line, rationale, confidence, adjudicado}], summary }`.
   - Dependencies: nenhuma nova.
   - [traces: REQ-F1, REQ-F2, REQ-F3, REQ-F4, US-1]

2. **Prompts** (`scripts/workflows/wf-verify-multimodel.js`)
   - Responsibility:
     - `RUBRICA_SEVERIDADE`: constante de texto (sem acento, como os outros prompts), primeira linha fixa `SEVERIDADE (julgue pela consequencia):`, anexada a cada prompt de revisor no momento da chamada (`agent(\`${d.prompt}\n\n${RUBRICA_SEVERIDADE}\`, …)`), não embutida em `DIMENSIONS`.
     - `linhaRaiz`: com `args.repo_root`, `Repositorio: <raiz>. Todo caminho abaixo e relativo a ele; leia e rode git la. O escopo sao os arquivos listados: nao compare com outro ramo.`, prefixada ao `scope` e ao prompt do adjudicador. Sem `repo_root`, string vazia.
     - Adjudicador: acrescenta `Severidade alegada errada nao e motivo para refutar: refute so se o defeito nao existe no codigo.`; continua sem `rationale` e sem a rubrica.
     - `spec-coverage`: mantém "P1 ausente=critical"; a rubrica diz que a regra vale para requisito sem implementação.
   - Interface: argumento opcional `repo_root` (string, caminho absoluto).
   - [traces: REQ-F5, REQ-F6, REQ-F10, REQ-NF2, US-1, US-3, US-4]

3. **Checador** (`scripts/checar_triagem.py`, novo)
   - Responsibility: carregar rodadas, extrair a tabela, cruzar, reportar.
   - Interface: `checar(plano: Path, saidas: list[Path]) -> tuple[int, list[str]]` e `main(argv: list[str] | None = None) -> int`; CLI `--plano PATH --saidas PATH [PATH …]` (arquivo `.json` ou diretório com `*.json`).
   - Dependencies: stdlib.
   - [traces: REQ-F7, REQ-NF4, US-2]

4. **Protocolo de rodadas** (`skills/harness-workflow/SKILL.md`)
   - Responsibility: as duas seções de verify (`:251-257` e `:324-347`) viram uma só fonte (a de `:324`), e a outra passa a apontar para ela; contrato em `:180` e exemplo de invocação em `:174` (com `repo_root`) atualizados; modelo de `closure-plan.md` ganha a seção `## Destino dos achados não adjudicados`.
   - Interface: comandos literais — cópia (bash e PowerShell) e `python "<PLUGIN_ROOT>/scripts/checar_triagem.py" --plano "docs/closure-plan.md" --saidas "<STATE_DIR>/verify"`. O caminho literal com `scripts/checar_triagem.py` numa `SKILL.md` é o que `tools/orfaos.py` (`_raizes`, `:264`) reconhece como raiz.
   - [traces: REQ-F8, AC-2.16, US-2]

5. **Harness de teste** (`tests/wf_run_harness.cjs`)
   - Responsibility: gravar `prompt` em `calls.review[]` e `calls.adjudicate[]`.
   - [traces: REQ-F11, US-1, US-3]

6. **Medição de B** (`tests/data/verify-rodadas-2026-09-30/medicao-b/`)
   - Responsibility: dados congelados e resultado da rodada de US-4.
   - Arquivos: `conhecidos.csv` (antes da rodada), `script.sha256`, `wf_<run>.output`, `casamento.csv`, `resultado.md` ou seção 7 de `verify-adjudica-tudo-medicao.md`.
   - [traces: REQ-F12, US-4]

---

## Data Model

### Entities

#### Rodada (lida pelo checador)
```python
@dataclass(frozen=True)
class Rodada:
    id: str                  # nome do arquivo sem extensão (wf_…)
    inicio: float            # max(workflowProgress[].startedAt) em ms; na falta, mtime * 1000
    achados: list[Achado]
    critical_count: int      # ausente -> saída 2
    nos_mortos: list[str]    # ausente -> saída 2
```

#### Achado
```python
@dataclass(frozen=True)
class Achado:
    n: int                   # posição 1-based; campo `n` presente e diferente -> saída 2
    titulo: str
    adjudicado: bool         # campo ausente -> False (conta como pendente)
```

#### Linha (da tabela de destino)
```python
@dataclass(frozen=True)
class Linha:
    rodada: str
    n: int                   # célula não inteira -> linha inválida (saída 1)
    achado: str              # início do título
    destino: str             # normalizado: casefold, strip, "não" -> "nao"
    referencia: str
    lineno: int              # linha no closure-plan.md, para a mensagem
```

**Invariantes**:
- Pendentes de uma rodada = achados com `adjudicado` falso ou ausente.
- A rodada final é a de maior `inicio`; só nela o bloqueio (`critical_count > 0` ou `nos_mortos` não vazio) reprova.
- Uma linha casa com um achado quando `rodada` e `n` batem e `normaliza(achado)` é prefixo de `normaliza(titulo)` com tamanho ≥ `min(20, len(titulo))`. `normaliza` = casefold, espaços colapsados, `|` trocado por `/` (título com barra vertical não quebra a tabela; a linha impressa já sai trocada).
- Referência válida: ≥ 7 caracteres sem espaço; diferente de `<teste, commit ou motivo>`; fora de {`n/a`, `-`, `?`, `ok`, `tbd`, `todo`}.
- Destino válido: `consertado`, `limite`, `nao-defeito`. `<consertado|limite|nao-defeito>` é inválido.

**Extração da tabela**: do cabeçalho `## Destino dos achados não adjudicados`
(regex sem diferença de caixa, `n[aã]o`) até o próximo `^## `; linhas dentro de
cerca de código (```` ``` ```` ou `~~~`) ignoradas; linha de cabeçalho
(`Rodada`) e separador (`---`) ignorados; linha de tabela com número de células
diferente de 5 é inválida.

---

## API Contracts

### CLI: `checar_triagem.py`

**Request**:
```
python scripts/checar_triagem.py --plano docs/closure-plan.md --saidas <STATE_DIR>/verify
```

**Response (0)** — stdout:
```
OK: 23 achado(s) nao adjudicado(s) em 2 rodada(s), todos com destino; ultima rodada (wf_…) sem bloqueio.
```

**Response (1)** — stdout, um bloco por problema, e no fim as linhas prontas:
```
PENDENTE wf_6bd9e71a-c4f #7: "Lancamento sem tool_use_id ou transcript_path fica pendente para sempre"
INVALIDA docs/closure-plan.md:41: destino "corrigido" (use consertado, limite ou nao-defeito)
BLOQUEIO wf_47eeb4d4-140: critical_count=1, nos_mortos=[] — resolva pelo gap-closure (secao verify do harness-workflow)

Linhas para colar sob "## Destino dos achados não adjudicados" (edite destino e referencia):
| wf_6bd9e71a-c4f | 7 | Lancamento sem tool_use_id ou tr | <consertado|limite|nao-defeito> | <teste, commit ou motivo> |
```

**Errors (2)** — stdout com a causa: arquivo ausente, JSON inválido, sem
`findings`, sem `critical_count`/`nos_mortos`, `n` fora da posição, diretório
sem `*.json`.

**Traces**: US-2, REQ-F7, REQ-NF4

### Workflow: `wf-verify-multimodel`

**Request (`args`)**:
```json
{ "task_id": "t-…", "changed_files": ["…"], "spec_path": "docs/specs/<slug>-spec.md",
  "base_ref": "HEAD", "repo_root": "C:/…/worktree" }
```

**Response**: como em Key Components 1. `pass` é `false` com qualquer achado não adjudicado.

**Traces**: US-1, US-3, REQ-F1–F6, REQ-F10

### Invocação neste ramo (antes do merge)

O tool recusa `scriptPath` fora do diretório da sessão (sondado). A medição e o
verify desta task passam o conteúdo de
`scripts/workflows/wf-verify-multimodel.js` do ramo no parâmetro `script`, e o
sha256 do arquivo fica registrado junto da saída.

**Traces**: REQ-F12, ALWAYS "script do ramo, inline"

---

## Implementation Phases

### Phase 1: Foundation
- [ ] `tests/wf_run_harness.cjs` grava `prompt` [REQ-F11]
- [ ] Blob de `wf-verify-multimodel.js` em `8cd6884` fixado em `tests/data/wf-verify-multimodel-antes-de-verify-adjudica-tudo.js`, com o hash no teste

### Phase 2: Core — US-1 (P1)
- [ ] Fórmula do `pass`, `n`, `summary`, instrução ao adjudicador, `repo_root`
- [ ] Inverter `test_pass_invariante_lote_so_medium_low`; testes de AC-1.x e REQ-F5

### Phase 3: Core — US-2 (P1)
- [ ] `scripts/checar_triagem.py` com testes de AC-2.1 a AC-2.15
- [ ] Seção de verify do `SKILL.md` (REQ-F8, AC-2.16)

### Phase 4: US-3 (P1, condicionada)
- [ ] `RUBRICA_SEVERIDADE` e testes de AC-3.1 a AC-3.4

### Phase 5: US-4 — medição (P1)
- [ ] Pré-registro de `conhecidos.csv` e commit
- [ ] Rodada inline sobre `578e0bc`, cópia, juiz cego, contagem, decisão L-06
- [ ] Se a rubrica sair: remover constante e testes de AC-3.1/3.2/3.4 (REQ-F12)

### Phase 6: US-5 (P2)
- [ ] `spec-template.md:193`, `verify-por-tabela-spec-light.md` AC-3, `README.md:38-40`

### Depois do merge (fora do tdd)
- [ ] Deploy no cache do plugin e frase no `CLAUDE.md` global, com OK do usuário (REQ-F9, AC-2.17)

---

## Test Strategy

### Unit Tests
- **`tests/test_checar_triagem.py`** [traces: US-2, AC-2.1–AC-2.15]: um teste por AC; entradas sintéticas em `tmp_path` (JSON no formato do host e no formato do retorno, `closure-plan.md` montado no teste). Os casos de AC-2.2/2.3 conferem que a linha impressa, colada, reprova.
- **Dado real** [traces: AC-2.15]: a saída da rodada 1 da mãe (`tests/data/verify-rodadas-2026-09-30/wf_6bd9e71a-c4f/workflow.output`), copiada para `tmp_path/wf_6bd9e71a-c4f.json`, com tabela gerada de `destinos.csv` → sai 0; tirando uma linha → sai 1 com aquele `#`.
- **CLI** [traces: REQ-F7]: um teste por subprocess conferindo o código de saída de `main` (os demais chamam `checar` direto).

### Behavior Tests (Workflow via `wf_run_harness.cjs`)
- **`tests/test_verify_pass_honesto.py`** [traces: US-1, US-3, REQ-F5, REQ-F6]:
  - AC-1.1, AC-1.3, AC-1.4, AC-1.6, AC-1.7, AC-1.8, AC-1.9
  - REQ-F5: com `repo_root`, a frase está em todo prompt de revisão e de adjudicação; sem, não está
  - AC-3.1 a AC-3.4 (a rubrica inteira nos cinco; ausente no adjudicador; regra P1 no `spec-coverage`)
- **Metades-controle** [traces: AC-1.2, AC-3.2]: os mesmos cenários contra o blob de `8cd6884`, conferido por hash de blob do git (padrão de `test_verify_por_tabela.py:190-203`), afirmando o comportamento antigo (`pass: true`; primeira linha da rubrica ausente).
- **`tests/test_verify_por_tabela.py`**: `test_pass_invariante_lote_so_medium_low` passa a afirmar `pass: false`; os outros ficam.

### Contract / guard tests existentes que precisam seguir verdes
- `tests/test_workflow_returns.py` (censo, rationale, `nosMortos.length === 0`, sem CR, `.gitattributes`)
- guarda de órfão (`tools/orfaos.py` na suíte) com `checar_triagem.py` alcançável pela `SKILL.md`

### Measurement (US-4)
- Não é teste automatizado: é a rodada congelada da seção "Medição de B" da spec, com CSV e resultado no repo.

### Coverage Target
- `scripts/checar_triagem.py`: cada ramo de saída (0/1/2) e cada regra de linha com teste.

---

## Risks & Mitigations

| Risk | Impact | Probability | Mitigation |
|------|--------|-------------|-----------|
| Formato do `.output` do host muda | High | Low | Checador sai 2 (erra reprovando); ASK na spec |
| Rubrica infla `high` e multiplica adjudicadores | Med | Med | US-4 mede o número de promovidos; teto adiado para depois da medição |
| Até 3 rodadas por task (custo de verify) | Med | High | Decisão do usuário no grill (ASSUMPTION-009); REQ-NF1 declara |
| Sessão com o texto antigo da skill e o script novo, na janela do deploy | Med | Low | `summary` nomeia o checador e a seção (AC-1.7) |
| Agente edita a cópia da saída | High | Low | NEVER na spec; limite declarado no Nível de garantia; título amarra linha à execução |
| Título com `|` quebra a tabela | Low | Med | `normaliza` troca `|` por `/` dos dois lados; a linha impressa já sai trocada |
| Revisor da medição roda testes antigos no worktree destacado, sob os hooks da sessão | Med | Low | Já ocorre em todo verify; limite declarado na spec; worktree removido no fim |
| Rodada única da medição é ruído | Med | Med | ASK na fronteira; lista e casamento congelados antes; limite declarado |
| Escrita da triagem depois da evidência invalida o `complete` | Med | High | Ordem fixa em REQ-F8 passo 7: tabela → checador → evidência → `complete` |

---

## Open Questions

Nenhuma. As decisões de design em aberto foram resolvidas no grill (spec,
seção "Grill") ou são Discretion registradas acima (texto da rubrica, formato
da mensagem do checador, nome dos testes).
