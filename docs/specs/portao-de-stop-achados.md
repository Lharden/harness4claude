# Portão de Stop — verificação dos três achados do seed

Data da medição: 2026-09-16. Sessão deste ramo: `f07a2bcc-6a28-4fc2-a494-0c80df31bd9e`.
Sessão pai: `86459dbf-2585-482f-814e-7b3dca136264`.

Antes de qualquer medição, confirmei que o **código em execução é o código que estou lendo**: o cache do plugin (`~/.claude/plugins/cache/harness4claude/harness4claude/4.0.0`) e este worktree divergem em 6 arquivos, e os 6 são idênticos após `tr -d '\r'`. A diferença é só CRLF. Nenhuma conclusão abaixo depende de código não implantado.

---

## Achado #1 — REFUTADO. A task não é fantasma.

O seed afirma: `t-20260916-113513958619` não existe em nenhum dos 92 bancos, e zero tasks foram gravadas em 2026-09-16.

Medição desta sessão, 93 bancos varridos (`projects/*/sessions/*/harness.db`), todos com a tabela `tasks`, zero erros de abertura:

```
bancos contendo t-20260916-113513958619 : 1
   ...\projects\science-harness-f34c6792\sessions\86459dbf-...-06f60408\harness.db
tasks criadas em 2026-09-16: 3
```

A linha:

```
task_id     t-20260916-113513958619
legacy_level L2-feature
status      awaiting_gate
phase_index 0
verified    0
stop_continuations 2
started_at  2026-09-16T11:35:13.958619+00:00
```

E o portão que o seed descreve está registrado como gate de verdade:

```
(7, 't-20260916-113513958619', 'escalation', None, 'pending', None, '2026-09-16T12:07:20.839405+00:00', None)
```

A task existe, o gate existe, os dois se referem ao mesmo id. **O instrumento da sessão pai é que respondeu errado** — exatamente a classe de erro que o próprio seed manda vigiar.

### O que o gate estava lendo, e por que ele estava certo

No banco dessa sessão, `SELECT COUNT(*) FROM evidence` devolve **0** — não só para a task em questão, mas para o banco inteiro (163 KB, 6 tasks). `transitions` para essa task: **0 linhas**. `artifacts`: 0. `phase_index`: 0, num pipeline de 11 fases.

Para comparação, a mesma varredura sobre os 93 bancos: 274 tasks, 277 transitions, **186 linhas de evidence distribuídas em 22 bancos**. O registrador de evidência funciona; nessa sessão ele nunca foi acionado.

`_handle_stop` (`hooks/harness-transactional.py:531-548`) bloqueia quando `status == "active"`, há pipeline e `verified == 0`. Não havia evidência, logo `verified` nunca subiu para 1, logo o bloqueio. **O gate contou certo sobre uma task que existe e que de fato não tinha prova nenhuma registrada.**

### Isto já tinha sido diagnosticado, e o diagnóstico está no repositório

O docstring de `scripts/harness_paths.py` (commit `aee62c6`, 2026-09-12) descreve o incidente anterior com as mesmas palavras:

> A task era dada por fantasma porque `select task_id from tasks` era rodado no balde errado. O gate estava certo o tempo todo: a task que ele leu de fato nunca recebeu evidência.

O pin de sessão corrigiu a partição do balde e está funcionando hoje: `~/.claude/harness/pins/86459dbf-...json` fixa a sessão em `science-harness-f34c6792` e registra duas derivas (`slb-mestrado-projeto`, `mainframe`). A task viva está no balde fixado. **A partição não é mais a causa; a conclusão "fantasma" é que se repetiu.**

### O defeito que sobra do #1

Não é a escrita nem a leitura da task. É a **mensagem do gate**: ela não cita o `task_id`, não cita o balde, e não diz o comando que registraria evidência. Quem a recebe não tem como confirmar nada, e a saída mais barata é inventar um diagnóstico — foi o que aconteceu duas vezes.

---

## Achado #2 — CONFIRMADO. Duas portas, cooldowns independentes.

`branch_sensor.may_offer` (`scripts/branch_sensor.py:488-495`):

```python
if not explicito and not budget_allows(cwd=cwd, turn=turn):
    return BUDGET
```

Com `--explicito`, o cooldown do sensor é pulado por desenho — o docstring diz "`/branch` explícito — o usuário pediu. Ignora orçamento e cooldown".

`branch_state.add` → `HarnessDatabase.create_branch` (`scripts/transactional_state.py:561-566`) aplica um **segundo** cooldown, sobre a coluna `branches.offered_turn` no SQLite:

```python
if (offer_stats["last_turn"] is not None
        and offered_turn - int(offer_stats["last_turn"]) < cooldown_turns):
    raise StateTransitionError("branch offer cooldown is active")
```

A assinatura de `create_branch` (`transactional_state.py:539-547`) **não tem parâmetro `explicito`**, e `branch_state.py:510-520` não passa nada equivalente. O caminho que o usuário consulta concede; o caminho que escreve recusa. As duas leituras são do mesmo fato — "posso oferecer este ramo agora?" — e só a frouxa responde ao usuário.

---

## Achado #3 — CONFIRMADO no efeito, ERRADO na causa.

O seed afirma que `HARNESS_BRANCH_COOLDOWN_TURNS` está documentada mas o caminho do `add` não a consulta. **Isso é falso.** `scripts/branch_state.py:519`:

```python
cooldown_turns=_integer_setting("HARNESS_BRANCH_COOLDOWN_TURNS", 8),
```

e `_integer_setting` (`branch_state.py:304-308`) lê `os.environ`. A variável é lida.

O que realmente derrota `=0` é **o contador de turno andar para trás**. Medido:

| Fonte | Valor |
|---|---|
| `branch-sensor.json` do projeto `science-harness-f34c6792` | `turn: 33`, `offers: 2`, `last_offer_turn: 25` |
| `branches.offered_turn` da task `t-20260916-113513958619` | **47** |

`offered_turn = origin_turn or _sensor_turn(cwd)`. Quando o `origin_turn` não vem, `_sensor_turn` devolve 33; o `last_turn` guardado é 47. A conta vira `33 - 47 = -14`, e `-14 < 0` é **verdadeiro**. Com `cooldown_turns=0` o portão continua fechado, porque zero não desliga uma comparação cujo lado esquerdo é negativo.

### O terceiro relógio (achado novo, não estava no seed)

`scripts/branch_state.py:311-318`:

```python
def _sensor_turn(cwd):
    payload = json.loads(
        (harness_paths.state_dir(cwd=cwd) / "branch-sensor.json").read_text(...)
    )
```

`state_dir` é chamado **sem `session_id`** — devolve o balde do PROJETO, não o da SESSÃO. Confirmado em execução:

```
sem session_id -> ...\projects\science-harness-f34c6792
com session_id -> ...\projects\science-harness-f34c6792\sessions\86459dbf-...-06f60408
```

E de fato todos os 20 `branch-sensor.json` da máquina estão na raiz do projeto, nenhum em `sessions/`. É o mesmo resto da correção `aee62c6`: um chamador que ficou sem o pin. Duas sessões no mesmo projeto compartilham um contador de turno e o sobrescrevem uma à outra.

---

## A forma comum

Os três casos — o que sobrou do #1, o #2 e o #3 — são a mesma assinatura que o seed nomeia: **duas leituras do mesmo fato, e só uma confere.**

| Caso | Leitura frouxa | Leitura estrita | Quem decide |
|---|---|---|---|
| #1 (resto) | a task anunciada no `additionalContext` | a evidência gravada em `evidence` | a estrita, sem dizer o que leu |
| #2 | `may_offer(explicito=True)`, budget JSON | `create_branch`, cooldown SQLite | a estrita, depois do `ok` |
| #3 | `branch-sensor.json` do projeto (`turn: 33`) | `branches.offered_turn` da task (47) | a estrita, com delta negativo |

## Consequência para este ramo

Este ramo **não está registrado em `branches.json`** — o `add` foi recusado três vezes com `branch offer cooldown is active`, e a sessão foi aberta direto. A recusa é reproduzível e a causa está medida acima (#3). Registrar o ramo continua sendo o teste de fumaça correto do conserto.
