# Mapa de riscos — `parent_session` por ramo, não por arquivo

**Task:** `t-20260909-195611274295` · L2-bug · 2026-09-09
**Fase:** `systematic-debugging` + `graph-context` → entrada de `grill-me`
**Decisão do usuário:** implementar só depois que o mapa não tiver falha à vista.

---

## 1. O defeito

`branches.json` guarda `parent_session` no **topo do arquivo**, escrito uma vez:

```python
# scripts/branch_state.py:458
if parent_session and not data.get("parent_session"):
    data["parent_session"] = parent_session
```

Quem escreve primeiro fica. Do segundo ramo do projeto em diante, o campo nomeia
a sessão errada — e três leitores diferentes acreditam nele.

Estado real medido em `RSL_Project-aa99cfb0` (2026-09-09):

| ramo | criado por | `parent_session` do arquivo diz |
|---|---|---|
| `persistencia-de-estado-do-harness` | `49fde96d` | `49fde96d` ✓ |
| `resolvedor-de-banco-do-branch-keeper` | `1f008ff6` | `49fde96d` ✗ |

---

## 2. Censo de consumidores

Levantado por varredura do repositório. `graphify query` foi consultado
(fase `graph-context`) e devolveu só nós de `schemas/*.json` — o grafo está
desatualizado para os módulos Python; degradação graceful, o censo abaixo é a
fonte.

| # | Consumidor | Lê o quê | Consequência hoje |
|---|---|---|---|
| C1 | `_transaction_context` (`branch_state.py:141`) | candidato nº 2 da lista | resolve o banco errado. ~~coberto pela varredura de `4b9a670`~~ — **medido: a varredura não cobre C1, ela ENCOBRE C1** (§6d, achado 2) |
| C2 | `parked_block` (`branch_state.py:778`) | quem é "a mãe" | sessão criadora do 2º ramo **nunca vê o próprio parking** |
| C3 | `_conclusoes_pendentes` + `_marcar_entregues` | via C2 | entrega **única** vai para a sessão errada e **morre lá** |
| C4 | `branch_links` (`build_sessions_index.py:157`) | aresta mãe↔ramo do índice | `sessions-index.json` grava aresta falsa; `session-recall` mente |
| C5 | `contract/schemas/branch-record.schema.json:5` | — | o contrato **já** modela `parent_session_id` por ramo; o JSON diverge dele |
| C6 | `branch_seed.py:69` | argumento da skill | não lê o arquivo — **não afetado** |
| C7 | `skills/branch-out/SKILL.md:114` | flag `--parent-session` | flag permanece; só o armazenamento muda |
| C8 | `health-check.sh:272` | existência do arquivo | **não afetado** |

C2, C3 e C4 são falhas **secundárias que ninguém tinha notado**: o fix do
resolvedor não as toca.

---

## 3. Mudança proposta

Nome do campo: **`parent_session_id`**, por ramo — igual ao que o contrato já
usa (C5). Não inventar um terceiro nome.

| Local | Mudança |
|---|---|
| `add()` | grava `parent_session_id` no registro do ramo; continua gravando o campo de arquivo |
| `_origin_of(data, branch)` | novo helper: `branch.get("parent_session_id") or data.get("parent_session")` |
| `set_status` / `attach_files` / `decide` | passam `_origin_of(...)` a `_transaction_context` |
| `_sweep_sessions` | devolve também o session-id descoberto, para o chamador carimbar |
| `parked_block` | filtra **por ramo**, não por arquivo |
| `branch_links` | idem, com fallback por ramo |

---

## 4. Riscos

Severidade: **B** = bloqueia implementação até resolver · **A** = resolver
dentro da implementação · **O** = observar, aceitar consciente.

### R1 [B] — A varredura de `4b9a670` sai de teste sem ninguém perceber

`test_ramo_fecha_quando_nenhum_ponteiro_aponta_para_o_banco_dono` monta o caso
patológico chamando `add(..., parent_session="sessao-antiga")` enquanto o sensor
aponta para `sessao-criadora`. Com `parent_session_id` por ramo, `add` passa a
carimbar **`sessao-antiga`** no registro... e o teste continua verde, mas **por
outro caminho**: o carimbo é o valor errado, o ponteiro segue mentindo, a
varredura continua rodando. Verde por sorte, não por desenho.

Pior cenário vizinho: se `add` carimbar o **criador real** em vez do argumento,
o teste fica verde **sem executar a varredura nenhuma vez** — o código novo de
`4b9a670` deixa de ter cobertura e ninguém vê.

> **Isto é a falha central do plano.** O que o campo deve guardar — o argumento
> `--parent-session` que a skill passa, ou o dono real do `harness.db`? São
> coisas diferentes e hoje se confundem porque coincidiam no primeiro ramo.

**Contingência:** decidir explicitamente (ver §6, D1) e, seja qual for a
decisão, adicionar fixture `_ramo_legado()` que **remove** `parent_session_id`
do registro, para que a varredura tenha um teste que a exercita para sempre.

### R2 [B] — `parked_block` está no caminho quente e não pode escrever

`parked_block` roda em todo `UserPromptSubmit`. As docstrings de `load()` e
`_marcar_entregues` travam a regra: *hook que quebra é pior que hook que não
acha ramo*. Qualquer auto-cura (carimbar o campo em registro legado) **não pode**
morar ali.

**Contingência:** o carimbo só em `set_status`/`attach_files`/`decide`, que já
seguram `_Lock(branches_path)` e já podem levantar. `parked_block` continua
leitura pura.

### R3 [A] — Registro legado não se cura sozinho no parking

Ramo antigo não tem `parent_session_id`; cai no fallback de arquivo e mantém o
comportamento errado de hoje. Não piora — mas também não melhora até alguém
chamar `set_status` naquele ramo. Um ramo `closed` nunca mais será tocado.

**Contingência:** aceitar (não piora), **ou** ação `migrate` no CLI que roda a
varredura uma vez por ramo e carimba. Decisão D2 em §6.

### R4 [A] — `TestCLI.test_add_pela_linha_de_comando_cria_o_registro` fica vermelho

`tests/test_branch_state.py:476` afirma `registro["parent_session"] ==
"sessao-mae-uuid"` no **topo** do arquivo. Parar de escrever o campo de arquivo
quebra este teste e o `test_sem_parent_session_o_ramo_fica_orfao` (linha 487).

**Contingência:** manter o campo de arquivo escrito (é o plano) e **acrescentar**
asserção sobre `branches[0]["parent_session_id"]`, para o teste passar a cobrir
o campo que manda.

### R5 [A] — `branch_links` pode criar chave `None` no índice

O código lê `pai` **uma vez, fora** do laço de ramos (`build_sessions_index.py:157`)
e aborta o bucket inteiro com `if not pai: continue`. Passando a ler por ramo, um
ramo sem parent no meio de um arquivo com outros que têm precisa do mesmo guarda
que já existe para `filho` (linha 163) — senão nasce um nó `None` que a busca
nunca devolve.

**Contingência:** guarda por ramo, espelhando a linha 163; teste com arquivo
misto (um ramo com campo, um sem).

### R6 [A] — `sessions-index.json` fica com aresta velha até ser reconstruído

O índice é um artefato em disco (`build_sessions_index.py:508`). Corrigir
`branch_links` não reescreve índice já gerado.

**Contingência:** reconstruir o índice depois do merge; anotar que
`session-recall` mente até lá. Degradação já é silenciosa por desenho
(`branch_links` docstring), então não há erro visível avisando.

### R7 [A] — `test_sessions_index.py:375` monta o JSON à mão

`_bucket_com_ramos` escreve `{"schema_version": 1, "parent_session": parent,
"branches": ramos}`. Os ACs de `ramo-no-indice-de-sessoes-spec-light.md` (AC-1)
estão escritos sobre esse formato.

**Contingência:** manter os testes atuais (cobrem o fallback legado) e
**acrescentar** casos por ramo. A spec-light ganha nota de que o campo de arquivo
virou fallback — não reescrever o AC-1, que continua válido.

### R8 [O] — Contrato: `additionalProperties: false` — *razão corrigida pela lente 4*

`branch-record.schema.json` proíbe campos extras, e o registro real já viola:
faltam 2 `required` (`branch_id`, `parent_session_id`) e sobram 8 propriedades
(`name`, `session_id`, `launcher_path`, `opened_at`, `closed_at`, `origin_turn`,
`detector`, `conclusion_delivered`). Logo **nada valida `branches.json` contra
esse schema**. O único `jsonschema` do repo está em `migrate_state.py:205,233`
e aplica `schemas/state.schema.json` e `schemas/signals.schema.json` — o
diretório `schemas/` da raiz, **não** `contract/schemas/`.

Acrescentar `parent_session_id` **reduz** a violação de 2 para 1 `required`
ausente.

A razão original ("evita regenerar `contract.lock.json:15`") estava errada:
`contract_adapter.py:171` chama `load_contract()` **sem `root`**, então a árvore
efetivamente validada é `master-harness/contract`, e o lock deste repo é inerte
localmente. Decisão de não mexer permanece; o motivo é outro — uma edição só
aqui passaria despercebida por toda a suíte.

### R9 [O] — `SCHEMA_VERSION` fica em 1 — *é o padrão, não a exceção*

`git log -S "SCHEMA_VERSION" -- scripts/branch_state.py` devolve **um único
commit** (a criação do módulo). O formato já cresceu de forma aditiva sem bump
duas vezes: `conclusion_delivered` (`ca9997f`) e `origin_turn`/`launcher_path`
(`9cc03e0`, `3b21d25`). `migrate_state.py` não menciona `branches` — não existe
migração para este arquivo em lugar nenhum.

Bumpar é que seria a novidade, e derrubaria `test_branch_state.py:70` e
`test_sessions_index.py:375`.

### R10 [O] — Compatibilidade retroativa é o seguro de rollback

Código antigo lendo JSON novo **ignora** `parent_session_id` — `load()` só faz
`setdefault` de duas chaves e não valida o resto. Então `git revert` do commit
volta o comportamento sem precisar limpar dado gravado.

**É a propriedade que torna este plano reversível.** Qualquer variante que
*remova* o campo de arquivo perde essa propriedade.

### R11 [O] — Mudança de quem enxerga o parking é visível ao usuário

Depois do fix, cada sessão vê só os ramos que ela abriu. Hoje uma sessão vê
todos. Para quem opera, isso parece "sumiu meu bloco de parking".

**Contingência:** é o comportamento correto e os testes
`TestParkingSoFalaComAMae` já o descrevem; avisar no commit.

---

## 5. O que NÃO muda

- Flag `--parent-session` do CLI e a instrução da `branch-out`.
- `SCHEMA_VERSION`, `contract/schemas/`, `contract.lock.json`.
- `branch_seed.py`, `health-check.sh`, `emit.py`.
- A varredura de `4b9a670` continua no código: ela é a rede para registro
  legado e para ponteiro corrompido.

## 6. Decisões humanas — RESOLVIDAS 2026-09-09

**D1 — o que o campo por ramo guarda? → (c) os dois, separados.**

| Campo | Guarda | Lido por |
|---|---|---|
| `parent_session_id` | a conversa mãe declarada (argumento `--parent-session`) | `parked_block` (C2), entrega de conclusão (C3), `branch_links` (C4) |
| `origin_bucket` | a sessão cujo `harness.db` recebeu a linha | `_transaction_context` (C1) |

`add()` tem os dois valores em mãos no momento da escrita: o argumento, e o
`home` que `_transaction_context` acabou de resolver. Custo de escrita zero.

O ganho não é só separar leitores. Enquanto era um campo só, **a divergência
entre os dois era invisível** — e foi ela que produziu todo este trabalho.
Gravados em separado, `parent_session_id != origin_bucket` vira sintoma
diagnosticável: quer dizer que a linha do ramo não foi parar no banco da
conversa que o declarou.

Consequência para R1: o teste da varredura deve fixar `origin_bucket` ausente
(registro legado), não mexer em `parent_session_id`.

**D2 — registro legado → auto-cura só na escrita.**

Carimbar em `set_status` / `attach_files` / `decide`, que já seguram
`_Lock(branches_path)` e já podem levantar. **Nunca** em `parked_block` (R2).
Ramo já `closed` não se cura — e também não é mais lido pelo resolvedor. Sem
ação `migrate` no CLI.

## 6b. Achados adversariais (lentes em janela limpa)

### N1 [B] — Os probes de contrato fixam DOIS nomes de teste, e o health-check falha duro

`contract_adapter.py:25,35` e `contract/behavioral-probes.json:36,44` apontam por
**nome** para `test_park_resolve_gate_e_abertura_posterior_cria_nova_aprovacao`
(`tests/test_branch_state.py:349`) e
`test_fluxo_publico_aplica_gate_e_limite_transacionais` (`:303`).
`evidence_is_valid` (`contract_adapter.py:125-163`) faz busca **textual** por
`def <nome>(`. Renomear, mover ou dividir qualquer um dos dois:

- `workflow.human-gates` e `conversation.branch-keeper` viram `degraded`;
- `health-check.sh:474-479` imprime `[FAIL] Harness4Contract degradado`, exit 1;
- `behavioral-probes.json` está **dentro** de `contract.lock.json:4` → corrigir o
  probe é regeneração de sha256.

Agravante: os dois testes chamam `bs.add(...)` **sem** `parent_session`
(`:308-313`, `:351-353`) — são justamente os que exercitam `decide`/`set_status`
com parent nulo.

**Contingência:** acrescentar asserção dentro dessas funções é livre; renomear é
proibido nesta mudança. **O gate de saída (§7.3) passa a incluir
`bash scripts/health-check.sh`.**

### N2 [B] — `parked_block` tem DUAS listas; filtrar só uma deixa o C3 vivo, mais sutil

`branch_state.py:781-782` calcula `vivos` e `entregar =
_conclusoes_pendentes(dados)` separadamente, e `_marcar_entregues` (`:804`)
consome a entrega do arquivo **inteiro**, por slug. Filtrar só `vivos` mantém a
sessão A queimando a entrega única da conclusão de um ramo da sessão B.

O §2 trata C3 como "via C2" — verdade hoje, **falsa no instante em que C2 for
corrigido**.

**Contingência:** filtro independente nas duas listas, e `_marcar_entregues`
recebe a lista já filtrada.

### N3 [B] — Ramo órfão sumiria de TODO bloco de parking

Hoje `branch_state.py:779` é `if session_id and mae and str(session_id) != mae`
— com `mae` falsy o bloco sai para qualquer sessão. Ramo sem parent é caminho
real e suportado (`test_sem_parent_session_o_ramo_fica_orfao`, `:478`). Um filtro
ingênuo `_origin_of(b) == session_id` faz todo órfão desaparecer assim que o
chamador se identifica — perda silenciosa no caminho quente, contra o
invariante 5 do `graph-context`.

R3 cobre "legado não se cura"; **não** cobre "legado desaparece".

**Contingência:** regra explícita — **órfão é visível a todos**. Teste dedicado.

### N4 [A] — `may_offer` fica inconsistente com o parking

`branch_sensor.py:463-495` passa toda oferta por `already_seen` (`:659`, varre
todos os ramos do projeto, sem parent) e `can_open` (`:635`). Depois do fix, a
sessão B recebe `duplicate` de um tema cujo ramo ela não vê mais no bloco — e
`skills/branch-out/SKILL.md:89` manda responder `recall <slug>` com um slug que
ela não tem como descobrir.

Somado: `can_open` já mistura escopos — por `task_id` no caminho transacional
(`database.list_branches`), por arquivo de projeto no fallback
(`open_branches(cwd)`).

**Contingência:** manter orçamento e dedupe **por projeto** (não por mãe), e
fazer `duplicate` devolver o slug para o `recall` continuar acionável.

### N5 [A] — `skills/session-recall/SKILL.md:44` documenta o bug

Texto: *"`--session <parent_session>`, que está em `branches.json`"*. Instrui o
modelo a ler o campo que vira fallback. Num projeto com 2+ ramos, retoma a
sessão errada. Corroborado por duas lentes independentes.

**Contingência:** editar a skill **junto** com o fix.

### N6 [A] — Falta `sessions-catalog.json` no R6

`build_sessions_index.py:460-461,469` grava `branch_of`/`branches` também no
**catálogo**, e é o catálogo que `tools/session_query.py:202-210` (`recent()`)
lê. Reconstruir só `sessions-index.json` deixa a aresta falsa viva no artefato
que `--recent` usa.

### N7 [B] — Existe uma quarta opção de D1 que dissolve o R1

`_sweep_sessions` + `_owns_branch` (`branch_state.py:185-206`) **já derivam** o
dono de disco por conteúdo, em sqlite read-only. `branches.scope_id`
(`transactional_state.py:164`) é o caminho do bucket.

Ou seja: **D1(b) é derivável hoje, sem campo novo.** A quarta opção é gravar no
JSON só a *intenção* (`parent_session_id`, para C2/C3/C4) e **derivar** o dono
de disco para C1.

Consequências:
- R1 não pode acontecer — a varredura vira o caminho **normal**, não o de erro,
  e nenhum carimbo pode tirar cobertura dela.
- Não existe cache para ficar **stale**: `origin_bucket` gravado apontaria para
  um bucket que pode ter sido apagado ou movido, e um ponteiro velho é pior que
  ponteiro nenhum — que é a lição deste subsistema inteiro.
- Custo: `_transaction_context` com `branch_id` passa a varrer sempre. Não é
  caminho quente (`set_status`/`attach_files`/`decide`); `can_open` e
  `already_seen`, que **são** quentes, chamam sem `branch_id` e não varrem.

### N8 [O] — `render_seed` afirma "Irmãs deste pai" e nada calcula isso

`branch_seed.py:86-98`: `siblings` vem do chamador e `main()` nunca preenche. O
campo por ramo é a primeira coisa no sistema que tornaria aquele cabeçalho
verdadeiro. Ou se liga, ou a semente segue afirmando parentesco que ninguém
checa.

### N9 [A] — As docstrings que ESPECIFICAM o defeito precisam mudar junto

`branch_state.py:214-226` documenta como razão de existir da varredura que *"o
`parent_session` de `branches.json` é do ARQUIVO, não do ramo"*. Idem `:109-111`
e a docstring do teste em `tests/test_branch_state.py:219-226`. Também
`docs/specs/ramo-no-indice-de-sessoes-verification.md:23` e
`docs/specs/branch-keeper-design.md:93`.

Neste repo a docstring carrega o registro da medição — deixá-la descrevendo um
defeito que mudou de lugar é regressão da convenção.

### N10 [O] — `decide(discard)` deixa o fallback apontando para mãe sem ramo

`branch_state.py:609-613` remove o ramo e nunca toca `data["parent_session"]`; o
guarda do `add` (`:458`) nunca reescreve. Projeto cujo primeiro ramo foi criado
por P e descartado mantém `parent_session: P` para sempre como fallback de todo
registro legado. Importa porque o R10 vende o campo de arquivo como garantia de
rollback.

### N11 [B] — `git revert` sozinho NÃO faz rollback

`deploy_to_cache.py:51` resolve o plugin instalado em
`~/.claude/plugins/cache/harness4claude/harness4claude/4.0.0`, e **é essa cópia
que os hooks executam**. Reverter o commit deixa o cache gravando o campo novo
com o repo dizendo que a feature não existe —
`tests/test_deploy_drift.py:11-13` documenta um incidente cujo sintoma foi
exatamente `branch_state.py add` recusando `--parent-session`.

**Rollback completo = `git revert <commit>` + `deploy_to_cache.py --apply` +
`pytest tests/test_deploy_drift.py`.**

### N12 [O] — O campo não existe em nenhum dos dois stores

`branch-record.schema.json` casa quase inteiro com a tabela sqlite
(`transactional_state.py:161-178`), **não** com o JSON. O único campo do schema
que nenhum dos dois stores tem é justamente `parent_session_id`. Gravar só no
JSON deixa o store transacional permanentemente incapaz de responder "quem é a
mãe". Não quebra nada hoje — mas é escolha consciente, não omissão.

### N13 [B] — O carimbo dentro do resolvedor é reentrância no `_Lock`, garantida

`_Lock` (`branch_state.py:303-341`) é `mkdir` sem dono e **sem reentrância**.
Todo caminho que passa `branch_id` a `_transaction_context` — logo todo caminho
que pode acionar a varredura — já está **dentro** de `with _Lock(target)`:
`:421→:439`, `:486→:495`, `:558→:563`, `:591→:596`.

Um carimbo que reabra o lock:
1. `FileExistsError` (`:317`); 2. `age ≈ 0`, não reapa (`:322`); 3. gira até
`LOCK_TIMEOUT_S` = **5 s parados** (`:73`, `:328`); 4. fail-open (`:330`);
5. no `__exit__` faz `os.rmdir` (`:337`) e **solta o lock do bloco externo**,
que ainda está na seção crítica.

5 s dentro de hook de 8 s, somado ao `busy_timeout = 5000`
(`transactional_state.py:46`), estoura o orçamento e o host mata o processo —
deixando `branches.json.lockdir` órfão, *stale* por 30 s para **todas** as
sessões do projeto.

E se o carimbo chamar `save()` sem lock para evitar isso, é pior e mudo: o bloco
externo já tem `data` em memória desde `:487`/`:559`/`:592` e escreve por cima
em `:544`/`:580`. **O carimbo é sobrescrito no mesmo turno, sem erro.**

**Contingência (é a forma certa do carimbo):** `_transaction_context` **devolve**
o dono descoberto; quem já segura o lock aplica no `dict` em memória antes do
`save()` que já vai acontecer. Zero I/O extra, zero lock novo.

### N14 [B] — Slug × uuid: o carimbo e a leitura não são do mesmo alfabeto

A varredura itera `sessions/` e tem em mãos o **slug do bucket**
(`49fde96d-1bbb-43e9-ba1d-d147b1344ddf-1c9fbd66` = uuid + `-` + `sha256[:8]`,
`harness_paths.py:80-87`). `parked_block` recebe o `session_id` **cru**
(`branch_sensor.py:716,783`).

Carimbar `home.name` e comparar com `==` **nunca casa** — bloco vazio para
sempre, e `_marcar_entregues` continuando a rodar. `startswith` casaria por
acidente de formato e quebra quando `session_slug` mudar (`readable[:40]` já
trunca id não-uuid).

**Contingência:** normalizar dos dois lados com `harness_paths.session_slug()`
antes de comparar; nunca guardar `home.name` cru como se fosse `session_id`.

### N15 [A] — `_owns_branch` confunde "não tem" com "não consegui olhar"

Medido: escritor ocioso → ok (0,6 ms); `BEGIN IMMEDIATE` → ok (0,5 ms); banco sem
a tabela → `OperationalError`; arquivo corrompido → `DatabaseError`. Os dois
últimos são `sqlite3.Error` → viram `False` (`:203-204`) → "este bucket não é o
dono".

Contenção de WAL não é problema. Mas banco corrompido, `-wal` exigindo recovery
(`SQLITE_READONLY_RECOVERY`, impossível em read-only) ou schema antigo ficam
**invisíveis para sempre** — e um carimbo gravado a partir de um dono errado é
**durável e auto-confirmante**: nas próximas vezes a pista carimbada é lida
primeiro e a varredura nem roda.

**Contingência:** tri-estado (`True`/`False`/`None` = não pude olhar); só
carimbar quando houver um `True` e **nenhum** `None`. E manter a proibição de
instanciar `HarnessDatabase` na varredura fora do bucket vencedor — o construtor
roda `CREATE TABLE`/`ALTER TABLE` (**escrita** no banco de outra sessão) e
`_ensure_schema` (`transactional_state.py:63-64`) **nunca fecha** a conexão.

### N16 [A] — Exceção em `parked_block` apaga a emissão inteira do turno

`branch_sensor.py:783` chama sem `try`; `:805` já consumiu `take_pending`;
`:807-812` emite; `:834-836` engole tudo com `SystemExit(0)`. Hoje é seguro
porque `load` não levanta e `_marcar_entregues` engole. Um `save()` nu ali
introduz `OSError`/`PermissionError` (disco cheio, antivírus segurando o `.tmp`)
e o `main()` aborta **antes** do `flush()` — perdem-se juntos BRANCH SIGNAL,
sinal pendente e parking. Depois de `:805`, o `pending-signal.json` já foi
`unlink`ado e o sinal **morre de vez**.

### N17 [O] — Carimbo não pode emitir `signal()`

`signals.json` fica na raiz (`harness_paths.py:111-113`) e é compartilhado por
**todos os projetos e sessões** — é o lock de maior contenção do sistema, e
também fail-open.

### Orçamento medido (não é estimativa)

- `parked_block` tem **um** chamador em produção: `branch_sensor.py:783`. O CLI
  `parked` não é invocado por hook nenhum.
- `branch_sensor.py` roda em `UserPromptSubmit` e `Stop` (`hooks/hooks.json:29`,
  `:185`, timeout 8000) → **2 execuções por troca**, processo novo em cada.
- Medido: sensor completo **429 ms a frio / 212 ms a quente**, com 125 ms de
  `python -c pass`. Sobram ~90 ms de trabalho real.
- Varredura, pior caso real da máquina (`science-harness-f34c6792`: **239
  buckets, 41 com `harness.db`**): **69 ms a quente, 0 erros**. `~/.claude` não
  está no OneDrive aqui.
- `_transaction_context` e a varredura **não** estão no caminho quente:
  `can_open` chama sem `branch_id` (`:641`) e a varredura só dispara com
  `branch_id` (`:178-181`).

---

## 6c. Bugs VIVOS achados de passagem (fora do escopo desta mudança)

Não corrigir aqui. Registrar para não se perderem.

**B1 — `_Lock` fail-open + `save()` de documento inteiro cria ramo fantasma que
trava o teto para sempre.** Duas sessões dividem um `branches.json`
(`branches_path` usa `state_dir(cwd)` **sem** `session_id`). No fail-open
(`:328-331`) ambas entram na seção crítica; o `os.replace` de B descarta a
árvore de A. Mas `add` já commitou `create_branch` no sqlite (`:441-454`) antes
do `save` (`:461`): o ramo **some do JSON e permanece no banco**. Daí
`get`/`set_status` levantam `KeyError`, e `can_open` conta pelo **sqlite**
(`:641-650`) — três fantasmas e `may_offer` devolve `max_open` para sempre
(`branch_sensor.py:489-490`). O Branch Keeper cala em definitivo, com aparência
de funcionamento normal. Colateral: B, ao sair, faz `os.rmdir` no lockdir de A —
o fail-open **quebra a exclusão mútua para todo mundo**, não só para quem furou.

*Correção mínima:* `__enter__` guarda `self.owned`; `__exit__` só remove se
`owned`; sem aquisição, operação de **escrita** aborta (leitura segue).

**B2 — o `Stop` da própria mãe já consome e joga fora conclusões.**
`branch_sensor.py:783` chama `parked_block` **antes** do desvio de Stop em
`:795`; no Stop o `parked` nunca entra no `Emitter` e o canal é `SILENT`
(`emit.py:99`). `_marcar_entregues` roda mesmo assim.

*Correção mínima:* mover a chamada para depois do desvio, ou `deliver=False`
quando o evento for Stop. **Nunca marcar entrega num caminho que não emite.**

**B3 — não existe um único teste de `_Lock`, de fail-open ou de duas sessões
escrevendo juntas.** `tests/test_branch_state.py` cobre `parked_block` e o gate
de sessão (370-602) e nada de concorrência.

### Confirmações (o mapa estava certo)

- **R5 tem rede:** `tests/test_sessions_index.py:409-418`
  (`test_ac4_ramo_pendente_sem_sessao_nao_entra`) já assert `None not in mapa`.
- **R10 confirmado na linha:** `load()` (`branch_state.py:367-371`) só valida o
  tipo do envelope; `save()` (`:374-380`) **preserva** campo extra ao reescrever.
  Reverter não exige limpeza de dado.
- **Nenhum consumidor externo:** harness4codex não lê `branches.json`; a paridade
  é por id de capacidade.
- **Não afetados (censo fechado):** `hooks/harness-branch-sensor.sh`,
  `hooks/emit.py`, `vault_sync.py:224`, `health-check.sh:483-497`,
  `harvest_branch_labels.py`, `calibrate_*`, `state_cli.py`,
  `expire_stale_pipeline.py`, worktree `harness4claude-self-reform`.

## 6d. O gate de saída não pega quase nada — medido

A lente de testes rodou três experimentos contra
`test_branch_state.py test_branch_sensor.py test_branch_seed.py
test_sessions_index.py` (baseline **174 passed**). Números, não inferência.

> `tests/test_transactional_branches.py` **não conta** como proteção aqui: só
> exercita `HarnessDatabase`, nunca toca `branches.json` nem `parent_session`.

### A1 [B] — O corpo útil da varredura tem UM teste, e ele não prova o mecanismo

Dos 17 testes que entram em `_sweep_sessions`, **um** chega a `_owns_branch`:

```
sweep=1 owns=1  test_ramo_fecha_quando_nenhum_ponteiro_aponta_para_o_banco_dono
sweep=1 owns=0  test_ramo_ausente_de_todo_banco_continua_falhando_alto
sweep=1..4 owns=0   (os outros 15 — sessions/ não existe, iterdir() dá OSError)
```

Trocando **só** o valor carimbado, argumento-da-skill → criador-real (a opção
D1(b) descartada):

```
carimbo = argumento da skill : status=closed banco=closed sweep=1 owns=1
carimbo = criador real       : status=closed banco=closed sweep=0 owns=0
```

Teste verde nos dois. Sob D1(b), **~60 linhas do commit `4b9a670` saem da
cobertura sem a suíte mudar de cor.** Confirma o R1 com número — e confirma
que a decisão D1 revisada (só a intenção) preserva a cobertura.

`_ramo_legado()` **não basta sozinha**: ela conserta a entrada, não a evidência.
O teste assere só o resultado, então não distingue qual mecanismo salvou.

**Contingência:** `_sweep_sessions` emite `signal("sweep")` (o módulo já tem
`signal`, `:683`) e o teste assere o contador em `signals.json`. Transforma
"achou" em "achou **pela varredura**".

### A2 [B] — O candidato `parent_session` já não tem teste HOJE

Removendo o candidato inteiro de `_transaction_context`:

```
174 passed in 2.20s
```

Os dois testes que deveriam prová-lo trocaram de mecanismo em silêncio — antes
resolviam pela lista, agora pela varredura (`sweep=1 owns=1` em `:132` e `:166`).
Ambos asserem só `status == "closed"` e o estado do banco; nada sobre **como** o
banco foi achado.

Logo: `_origin_of` pode ser ligado ao contrário, ler a chave errada, ou o
fallback nunca disparar — e o gate passa.

**Contingência:** em `:132` e `:166`,
`monkeypatch.setattr(bs, "_sweep_sessions", lambda *a, **k: pytest.fail("resolveu pela varredura, nao pelo ponteiro"))`.
Mais testes unitários diretos em `_transaction_context` asserindo o `home`.

### A3 [B] — Dois dos três call sites reescritos não têm teste nenhum

| Call site | Hoje |
|---|---|
| `set_status` `:497` | não-`None` só em 3 testes — **mascarados pelo A2** |
| `attach_files` `:565` | **ZERO testes na suíte** (`rg attach_files tests/` → nada) |
| `decide` `:598` | sempre `None`; `test_descarte_e_explicito` (`:285`) nem tem transação |

`attach_files` é o pior: o único chamador em produção é `branch_seed.py:279` —
**o caminho real de abrir um ramo**. Vai ser editado sem uma linha de teste.

### A4 [B] — `parked_block`: a semântica nova é inexprimível nas fixtures atuais

`TestParkingSoFalaComAMae` tem **um ramo por cenário** (`:577-585`, `:587-595`,
`:597-602`). Com um ramo só, campo-de-arquivo e campo-de-ramo são o mesmo valor —
os três passam com qualquer implementação. E **nenhum teste tem dois ramos com
mães diferentes**, porque `add` (`:458`) torna esse estado impossível de
construir hoje. É exatamente o estado que a mudança existe para permitir.

Medido, com o guarda `and dono` esquecido (ramo sem mãe some para qualquer
chamador identificado — o N3):

```
104 passed in 1.94s
```

Em produção `branch_sensor.py:783` **sempre** passa `session_id`.

*Controle:* esquecer o filtro em `entregar` (e não em `vivos`) **é** pego por
`:587`. O teste cobre o leitor não-mãe; não cobre o leitor mãe-errada.

**Fixtures:** (a) duas mães, um ramo cada, cada uma vê só o seu; (b) chamador
identificado + ramo sem mãe → continua entregando; (c) duas mães, uma conclusão
pendente em cada, mãe A lê e a de B **sobrevive**.

### A5 [B] — `branch_links`: a edição mínima NÃO conserta

`_bucket_com_ramos` (`test_sessions_index.py:371-378`) hardcoda o envelope e os
dicts de ramo nunca carregam `parent_session_id`. Os 6 testes de `branch_links`
passariam a exercitar **só o fallback**.

Medido, arquivo com `parent_session: null` + ramos com `parent_session_id`:

```
codigo de hoje          : {}
edicao minima por-ramo  : {}
```

A linha `:158` (`if not pai: continue`) **aborta o bucket inteiro antes de olhar
ramo nenhum**. O R5 dizia "nasce um nó `None`"; a medição mostra pior: o bucket
some do índice, em silêncio, por desenho. E a forma é alcançável —
`test_sem_parent_session_o_ramo_fica_orfao` (`:478`) já produz campo de arquivo
nulo.

*Controle:* ler **só** o campo por ramo, sem fallback, quebra `test_ac1` — essa
classe está coberta.

### A6 [A] — Os dois testes de CLI viram prova de um campo aposentado

`:476` assere `registro["parent_session"] == "sessao-mae-uuid"` no **topo**.
Depois da mudança quem manda é `branches[0]["parent_session_id"]`, e ninguém o
assere: **a camada (i) inteira pode ser esquecida e o teste segue verde.**

`:487` (`is None`) é onde D1(a) vs D1(b) fica **visível em teste** em vez de
implícito numa decisão de §6 — é o lugar certo para travar a decisão.

### A7 [A] — O docstring de `:214` descreve um cenário que a mudança extingue

"como faz o segundo ramo de qualquer projeto" (`:232-235`) é precisamente o que
deixa de acontecer. Sob D1(a) o teste segue verde e segue rodando a varredura,
mas por um cenário **artificial** ("o chamador mentiu o uuid"), não pela falha de
produção que o docstring de 39 linhas documenta.

**Contingência:** reescrever como `_ramo_legado()` — `parent_session_id`
**ausente** + ponteiro de arquivo herdado. É a forma que vai existir em disco em
`RSL_Project-aa99cfb0` depois do merge.

### Veredito da lente

A suíte fica **verde** com: (a) o candidato `parent_session` removido de
`_transaction_context`; (b) a varredura nunca executada; (c) `parked_block`
escondendo ramo sem mãe de todo chamador identificado; (d) `branch_links` sem
nenhuma linha nova exercitada; (e) `add` sem gravar o campo por ramo.

**Cinco das seis camadas do plano podem ser no-ops ou estar erradas com o gate
verde.**

---

## 7. Plano de execução — reestruturado pelas lentes

**Fase 0 — infraestrutura de teste. PRÉ-REQUISITO, não parte da camada (i).**
Sem ela, o "teste antes do código com o vermelho registrado" não consegue
produzir vermelho nenhum (§6d).

1. `_sweep_sessions` emite `signal("sweep")`; teste assere o contador (A1).
2. `monkeypatch` que falha se a varredura resolver o que o ponteiro devia
   resolver, em `:132` e `:166` (A2).
3. Primeiro teste de `attach_files` da história do módulo; `decide(park)` com mãe
   registrada (A3).
4. `_bucket_com_ramos` ganha parâmetro por ramo (A5).
5. Fixture `_ramo_legado()` — campo ausente + ponteiro herdado (A7, R1).

**Fase 1 — campo + `_origin_of` + `add`.** Grava só `parent_session_id`
(intenção). Dono de disco continua derivado pela varredura (D1 revisado).
Asserção nova em `:476` e `:487` (A6).

**Fase 2 — `parked_block`.** Filtro nas **duas** listas (N2), `_marcar_entregues`
recebe a lista já filtrada, órfão visível a todos (N3), comparação normalizada
por `session_slug` (N14). Fixtures A4 (a)(b)(c).

**Fase 3 — `branch_links`.** Guarda por ramo espelhando `:163-165`, e o
`if not pai: continue` de `:158` **sai** do escopo do bucket (A5).

**Fase 4 — textos que ficariam mentindo.** `skills/session-recall/SKILL.md:44`
(N5), docstrings de `branch_state.py:109-111,214-226` e
`tests/test_branch_state.py:219-226` (N9),
`docs/specs/ramo-no-indice-de-sessoes-verification.md:23`,
`docs/specs/branch-keeper-design.md:93`.

### Gate de saída

- Suíte completa: baseline **1215 passed / 0 failed**, exit 0.
- `python scripts/deploy_to_cache.py --apply` e `pytest tests/test_deploy_drift.py`.
- **`bash scripts/health-check.sh`** — sai 1 se qualquer capacidade do contrato
  degradar (N1).
- **Proibido renomear** `test_park_resolve_gate_e_abertura_posterior_cria_nova_aprovacao`
  e `test_fluxo_publico_aplica_gate_e_limite_transacionais` (N1).

### Rollback

`git revert <commit>` **+** `python scripts/deploy_to_cache.py --apply` **+**
`pytest tests/test_deploy_drift.py`. Só o revert deixa o cache do plugin rodando
o código novo (N11). Dado gravado é inerte para o código antigo (R10).

### Depois do merge

Reconstruir `sessions-index.json` **e** `sessions-catalog.json` (R6, N6).
