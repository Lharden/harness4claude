# Portão de task fantasma — diagnóstico

**Task:** `t-20260912-024540272814` · classificação corrigida `L2-feature` → **`L2-bug`**
**Data:** 2026-09-11 (UTC 2026-09-12)
**Ramo de:** sessão `86459dbf-2585-482f-814e-7b3dca136264` (science-harness)

---

## Conclusão em uma linha

**A task nunca foi fantasma.** Ela existe, íntegra, em **outro bucket**: uma
sessão que muda de diretório de trabalho passa a cunhar um `project_slug`
diferente, e o estado dela se parte em dois. O gate de Stop estava **certo** —
escalava sobre uma task real que de fato tinha `verified=0` e zero evidência.
Quem escreveu no lugar errado foi o agente.

---

## O incidente, reconstruído por timestamp

Uma sessão (`86459dbf…`), dois buckets:

| bucket | `project_slug` | caminho que o cunha |
|---|---|---|
| A | `slb-mestrado-projeto-7c89ab0c` | `Documents\projects\master_project\slb-mestrado-projeto` |
| B | `science-harness-f34c6792` | `Documents\projects\science-harness` |

O sufixo de sessão é **idêntico** nos dois (`…-06f60408`): mesma sessão, mesmo
`session_slug`. Só o slug de projeto diverge.

```
02:00:07  hook classify cria t-20260912-020007618177   -> bucket B (science)
02:07:09  agente grava evidence (1454 testes, exit 0)  -> bucket A (slb)
02:27:33  agente grava evidence (1462 testes, exit 0)  -> bucket A
02:40:58  agente grava evidence (1477 testes, exit 0)  -> bucket A
          bucket A: t-20260911-201150540862 vira done, verified=1, phase 4/4
02:41:18  hook Stop lê o bucket B: t-20260912-020007618177
          evidence = 0 linhas, verified = 0, stop_continuations = 2
          -> INSERT gate escalation 'pending'
```

Estado final medido nos dois bancos:

- **Bucket B** (`harness.db`): `t-20260912-020007618177`, `status=awaiting_gate`,
  `pending_gate=escalation`, `stop_continuations=2`, `verified=0`,
  **`select count(*) from evidence where task_id=… → 0`**.
- **Bucket A** (`harness.db`): 3 linhas de `evidence` com `exit_code=0` e
  1454 / 1462 / 1477 testes — todas sob `t-20260911-201150540862`.

As três medições que o relato original registrou como prova de fantasma são, na
verdade, prova do split: `select task_id from tasks` foi rodado no **bucket A**,
onde a task do bucket B naturalmente não aparece.

---

## Mecanismo

`scripts/harness_paths.py::state_dir` (linha 106):

```python
project = base / PROJECTS_SUBDIR / project_slug(cwd or os.getcwd())
session = session_slug(session_id)
return project / SESSIONS_SUBDIR / session if session else project
```

O bucket é `projects/<slug do cwd>/sessions/<slug da sessão>`. O **projeto vem
antes da sessão**, e o projeto é derivado de um `cwd` **mutável**:

- `hooks/harness-classify.sh:142` → `HARNESS_SESSION_CWD="$SESSION_CWD"`, extraído
  de `payload["cwd"]` (linha 85).
- `hooks/harness-transactional.py:482-484` → `ensure_state_dir(..., payload.get("cwd"))`.
- **O agente**, seguindo o protocolo da skill `harness-workflow`, passo 2:
  `harness_paths.py --cwd "$PWD"` — o `$PWD` do **shell**, que persiste entre
  comandos e não é a mesma variável.

São três amostragens de um valor que muda, feitas em momentos diferentes. Os dois
hooks concordam entre si (mesma fonte, `payload["cwd"]`); o agente lê outra
fonte. Quando as duas se desencontram, evidência e gate vão para bancos
diferentes e **nenhum dos lados erra sozinho** — cada um está internamente
consistente.

Não há exceção engolida: `transactional-state-error.log` não existe em nenhum
dos buckets, e `start_task` gravou normalmente nos dois casos. O `try/except`
largo de `harness-classify.sh:694` não participou deste incidente (mas continua
podendo anunciar uma task que o banco não recebeu — ver "Achado secundário").

---

## Reprodução determinística

`scratchpad/repro_split.sh` — mesma `session_id`, dois `cwd`, `HARNESS_DIR`
isolado:

```
--- prompt 1, cwd=repo-a ---
--- prompt 2, cwd=repo-b (mesma sessao) ---

=== buckets criados ===
projects/repo-a-8c1f5022/sessions/repro-…-45a275d4/state.json
   task_id: t-20260912-025219238446 | L2-architecture | active
projects/repo-b-fb8cded0/sessions/repro-…-45a275d4/state.json
   task_id: t-20260912-025221708264 | L2-architecture | active
```

Duas tasks `active` para uma sessão. A de `repo-a` nunca mais recebe evidência e
fica `active` indefinidamente — é exatamente a condição que o Stop transforma em
escalação.

---

## O que o gate faz quando o id não resolve

`scripts/transactional_state.py:978-1011`, `register_stop_continuation`:

- Exige `status == "active"`, pipeline não-vazio e `verified == 0`; caso
  contrário levanta `StateTransitionError("task does not require a stop
  continuation")`.
- Abaixo do limite: `stop_continuations += 1`.
- No limite (2): insere gate `escalation` `pending` e move a task para
  `awaiting_gate`.

O contador conta sobre **a task do bucket que o `payload["cwd"]` daquele turno
resolve**. Um `task_id` que "não resolve" não chega aqui: `_locked_task` não o
encontra e o hook morre antes. O sintoma relatado — escalar sobre task que o
banco diz `done` — não é id irresolvível, é **id resolvido no banco errado**.

Não há bug em `register_stop_continuation`. Ele é a vítima visível.

---

## Achado secundário (não é a causa deste incidente)

`hooks/harness-classify.sh:670-700`: `state.json` é escrito com o `task_id` novo
**antes** do `start_task`, e o `start_task` está dentro de um `try/except
Exception` que só escreve num log. O anúncio `HARNESS v3 CLASSIFIED: … Task ID:
…` sai na linha 820, **fora** do `try` e sem checar se o banco recebeu.

Ou seja: se `start_task` falhar, o hook anuncia um `task_id` que existe só no
`state.json` — sem `revision`, sem `scope_id`, sem linha em `tasks`. Aí sim seria
uma task fantasma de verdade. Neste incidente não ocorreu (sem log de erro), mas
o caminho está aberto.

---

## Conserto proposto

**Fixar o bucket na sessão, não no diretório.** A sessão é a unidade de trabalho;
o projeto é um rótulo dela. Hoje a hierarquia está invertida.

Um índice em `$ROOT/sessions-index.json` mapeando `session_slug → project_slug`,
escrito na primeira resolução e lido em todas as seguintes. Se o `cwd` depois
cunhar outro slug, `state_dir` devolve o slug **fixado** e registra a deriva —
visível, não silenciosa.

Isso fecha os três caminhos de uma vez: hook de classify, hook de Stop e agente
passam a resolver o mesmo bucket mesmo lendo `cwd` diferentes.

**Fora do escopo deste conserto** (decisão separada, com risco próprio): fundir
os dois buckets já partidos da sessão `86459dbf`. Migrar `tasks`, `evidence` e
`gates` entre bancos é operação destrutiva e precisa de decisão explícita.

### Alternativas consideradas e por que perdem

- *Exportar `HARNESS_SESSION_CWD` para o agente e trocar `$PWD` no protocolo da
  skill:* conserta o lado do agente, mas não impede o hook de criar uma segunda
  task quando o `cwd` muda no meio da sessão. Metade do problema.
- *Detectar e avisar:* torna o split visível sem removê-lo. Útil como
  complemento do índice, insuficiente sozinho.

---

## grill-me — o conserto passado pelas lentes

Oito objeções ao desenho proposto, e o que cada uma mudou nele.

**1. Arquivo único = corrida entre sessões.** `sessions-index.json` compartilhado
seria escrito por toda sessão da máquina a cada prompt. Escrita atômica do arquivo
inteiro faz *last writer wins* e some com a entrada de outra sessão, em silêncio.
→ **Mudou o desenho:** um arquivo **por sessão**, `pins/<session_slug>.json`.
Sem arquivo compartilhado não há corrida — é o mesmo argumento de exclusividade
que `harness-classify.sh:756` usa para o spool (`open(path,'a')` seguro sem lock
porque o nome do arquivo é exclusivo do escritor).

**2. O pin não pode entrar em `project_slug`.** `_escopo.divergencia()` compara
`de_caminho(cwd).id` com `project_slug(cwd)` e reprova se discordarem — é o portão
do degrau `dupla` do master-harness. Pin dentro de `project_slug` quebraria essa
comparação e transformaria a deriva num teste vermelho permanente.
→ **Restrição:** o pin vive em `state_dir`, e só. `_escopo.py` é DERIVADO
(cabeçalho: "nao edite este arquivo") e não é tocado.

**3. Sessão sem id.** 9,4% das emissões desta máquina não têm `session_id`
(medido, todas de `session_start`). `session_slug(None)` devolve `None` e o bucket
vira o do projeto, sem camada de sessão. Não há chave para fixar.
→ **Degrada:** sem `session_id`, sem pin. Comportamento atual, intacto.

**4. Sessões que já existem, com bucket já criado.** Índice vazio na primeira
resolução depois da mudança faz o pin nascer apontando para o cwd de AGORA — que
pode ser o bucket novo, órfãozinho, enquanto o histórico está no antigo.
→ **Adoção:** sem pin registrado, procurar buckets já existentes em disco para
esse `session_slug` e fixar no de **modificação mais recente** (é onde o trabalho
está vivo). Sem nenhum, fixa no cwd atual. Para a sessão `86459dbf` isso escolhe
`science-harness` (02:49) sobre `slb` (02:40) — que é a task viva. Correto por
medição, não por sorte.

**5. Troca legítima de projeto.** Alguém abre a sessão em A e passa a trabalhar de
verdade em B. O pin mantém A, e o NOME do bucket fica mentindo.
→ **Aceito com registro.** A sessão é a unidade de trabalho; um `cd` não devia
partir o estado dela em dois. Mas a deriva é gravada no próprio pin (lista
`drifts`), então a discordância fica legível em vez de silenciosa. Quem quiser
outro bucket abre outra sessão — que é o que o `branch-out` já faz.

**6. `state_dir` deixa de ser função pura.** Ela ganha leitura de arquivo, e é
chamada em todo `UserPromptSubmit` por 8 hooks.
→ **Custo medido antes de aceitar** (ver TDD). Um JSON de ~200 bytes, com cache
em processo. A comparação relevante é `_canonico`, que já custa 94,5 µs e entrou
mesmo assim por ser uma chamada por resolução.

**7. Índice corrompido, disco cheio, permissão negada.** `ensure_state_dir`
promete: "Nunca levanta — hook nao pode falhar."
→ **Invariante:** toda falha de leitura ou escrita do pin degrada para a
resolução por cwd. O pin é otimização de correção, não dependência dura.

**8. `HARNESS_SCOPE=global`.** Retorna a raiz antes de qualquer coisa.
→ Sem interação: o pin fica abaixo do early-return e nunca roda.

### Lente que ficou morta — e o número que ela não tinha

**Concorrência dentro da MESMA sessão.** Dois processos da mesma sessão (hook +
tool call do agente) podem resolver o pin ao mesmo tempo na primeira vez, antes
de qualquer um gravar.

O grill registrou isso como "limite conhecido" sem medir. Limite sem número é
palpite, então foi medido — 40 rodadas, dois processos concorrentes com `cwd`
diferentes, sessão idêntica (`scratchpad/medir_corrida_pin.py`):

```
resolucoes divergentes na 1a vez:  6 de 40  (15%)
pins corrompidos/ilegiveis:        0
baldes distintos APOS a corrida:   1        (converge sempre)
```

O dano real é menor do que o palpite supunha: a divergência existe só na
**primeira** resolução e **se cura** — a partir da segunda, todo `cwd` cai no
mesmo balde. Pior caso: uma escrita órfã, uma vez, no primeiro turno de uma
sessão cujo `cwd` mudou dentro do mesmo instante.

Comparação que importa: antes do pin, a divergência era de **100%** sempre que o
`cwd` mudava, e era **permanente**.

---

## Conserto entregue

**`scripts/harness_paths.py`** — `state_dir` fixa o slug na primeira resolução de
cada sessão, em `pins/<slug da sessao>.json`. Funções novas: `_pin_file`,
`_read_pin`, `_write_pin` (atômico, silencioso em falha), `_adopt` (sessão
anterior ao pin herda o balde de mtime mais recente) e `_pinned_slug` (fixa e
registra deriva, deduplicada, com teto `MAX_DRIFTS = 20`).

**`hooks/harness-classify.sh`** — `transactional_ok` passa a guardar o resultado
do dual-write, e o bloco `CLASSIFIED` só é emitido se o banco recebeu a task.
Quando não recebe, sai um `WARNING` que diz explicitamente para não invocar
`harness-workflow`, tratar como L0 e onde achar a causa.

### Custo, medido

```
resolucao com pin (le):           284,5 us
resolucao sem sessao (baseline):  214,2 us
delta do pin:                      70,3 us
primeira resolucao (grava pin):  1999,7 us   (uma vez por sessao)
```

Referência da casa: `_canonico` custa 94,5 µs e entrou mesmo assim, por ser uma
chamada por resolução. O pin custa menos que ela. `json` já era importado por
todos os chamadores (`harness-classify.sh:78,147`, `harness-transactional.py:6`,
`harness-lifecycle.py:5`, `record_signal.py:25`); só `datetime` é novo, 1,1 ms no
caminho CLI.

### Prova ponta a ponta

A mesma reprodução de antes, contra o plugin corrigido:

```
antes:  projects/repo-a-8c1f5022/... t-20260912-025219238446  active
        projects/repo-b-fb8cded0/... t-20260912-025221708264  active

depois: projects/repo-a-b7a015a5/... t-20260912-080914561035  active   (unico)
```

O segundo prompt, com outro `cwd`, caiu no caminho `CONTINUING` da mesma task em
vez de criar uma segunda.

### Migração das sessões já partidas

`_adopt` aplicado às sessões reais desta máquina, sem escrever:

```
sessao 86459dbf -> adota science-harness-f34c6792  (mtime 1789199776)
                   candidato  slb-mestrado-projeto-7c89ab0c (mtime 1789194957)
sessao 21c0a950 -> adota harness4claude-7aba6948   (balde unico)
```

A sessão do incidente adota o balde onde a task viva está (`awaiting_gate`,
`stop_continuations=2`) — não o que tem a evidência. **Isso é deliberado:** o
balde `slb` contém evidência gravada sob o `task_id` errado, e importá-la seria
carimbar como verificada uma task que ninguém verificou. O gate continua pedindo
evidência fresca, que é o comportamento correto.

Fundir os dois bancos segue **fora de escopo**, pelo mesmo motivo.

---

## Nota operacional: fechar a task tem uma corrida própria

Duas tentativas de `complete` morreram antes de acertar, e as duas por motivos que
valem registro porque não estão documentados em lugar nenhum.

**1. `code_revision` invalida evidência mais rápido do que parece.**
`_has_fresh_test_evidence` compara o `code_revision` da evidência com o da task, e
ele sobe com **qualquer** escrita de arquivo — inclusive `.md` e arquivos de
scratchpad, não só código. Gravar a evidência num tool call e chamar `complete` no
seguinte perde a corrida por construção: a evidência entrou em `code_revision` 70
e a task já estava em 73.

*Como fechar:* rodar suíte, gravar evidência e chamar `complete` no **mesmo
processo**, sem nenhum tool call entre eles (`scratchpad/fechar.py`).

**2. Editar qualquer arquivo versionado depois do deploy reprova a suíte.**
`test_deploy_drift` compara o repo com o plugin instalado em
`~/.claude/plugins/cache/...`. Editar este próprio documento depois de
`deploy_to_cache.py --apply` derruba a suíte inteira por um `.md` — o que custou
uma rodada de 12 minutos.

*Ordem que funciona:* terminar toda edição → `deploy_to_cache.py --apply` →
rodar o fechamento.
