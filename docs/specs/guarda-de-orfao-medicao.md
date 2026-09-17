# Guarda de órfão — a medição, e o que ela derrubou

**Task:** `t-20260916-215220419949` · **Ramo:** `claude/guarda-de-orfao` · **Base:** `5ca9d4e`
**Medido em:** 2026-09-16 · **Repositório:** `harness4claude`

---

## O número que o autor pediu

**38 esquecidos contra 51 decisões declaradas**, de 89 funções públicas de topo
que não são alcançáveis a partir de nenhuma raiz externa — sobre um total de 426
(419 antes do scanner existir).

| | funções | % do total |
|---|---:|---:|
| **vivas** (há caminho de uma raiz que o host executa até elas) | 337 | 79,1% |
| **decisão declarada** (ferramenta de mão, com o como e o quando escritos) | 51 | 12,0% |
| **esquecimento** (nada as nomeia, nada as roda) | **38** | 8,9% |

> [superado: "38 esquecidos contra 29 decisões declaradas, de 67 não-vivas sobre
> 419"] — o primeiro corte tratava `README.md` e `tools/README.md` como raiz de
> execução. Isso classificava 22 ferramentas de mão como **vivas** em vez de
> **declaradas**, e criava uma incoerência que o próprio guarda expôs ao rodar:
> `wiki_lint` (documentado num README) virava viva, enquanto `deploy_to_cache`
> (documentado em `docs/`) ia para a allowlist — mesma natureza, tratamento
> diferente, decidido pelo acaso de em qual arquivo a frase caiu. Markdown só é
> raiz quando é uma `SKILL.md`, que o harness carrega e que **instrui o agente a
> executar**; README é prosa para humano ler. **O número de esquecidos não mudou:
> continua 38.** O que mudou foi onde as 22 ferramentas de mão aparecem.

A razão **decisão : esquecimento é 57 : 43**. Não é um repositório onde tudo que
não roda foi escolhido, e também não é um onde nada foi. É perto de meio a meio, e
por isso a lista escrita vale — ela é o que separa os dois grupos, que fora dela
têm a mesma aparência.

---

## O número do seed está superado, e o motivo importa mais que o número

O seed trouxe **56 só-em-teste (34,1%) e 38 com zero chamadores (23,2%)**, de 164
funções.

> [superado: o instrumento é que estava respondendo. `orfaos2.py` olhava só
> `scripts/`, exigia **duas** chamadas para considerar uso — a definição não é um
> `ast.Call`, então o `>1` do código não é o "definição + 1 chamada" do
> comentário —, casava por nome nu, e era cego a hook `.sh`, workflow `.js` e
> dispatch dinâmico.]

Rodando o instrumento corrigido sobre o mesmo repositório: **56 só-em-teste caem
para 7**. O número do seed media a cegueira da varredura, não o abandono do código.

### Os oito vieses, e os cinco que o seed não listou

O seed nomeou três. Havia oito.

| # | viés | efeito | no seed? |
|---|---|---|---|
| 1 | olhava `src/` **ou** `scripts/`, nunca `hooks/`, `tools/`, `skills/` | 255 funções de produção invisíveis | não |
| 2 | `cp.get(n,0) > 1` exigia **duas** chamadas; a definição não é `ast.Call` | função com exatamente 1 chamador virava órfã | não |
| 3 | casava por nome nu — `.close()` contava como uso de `close()` | inflava uso | sim |
| 4 | só `ast.Call`; callback, registro em dict e decorator não contavam | subestimava uso | não |
| 5 | cego a dispatch dinâmico (`getattr`, `module_from_spec`) | subestimava uso | sim |
| 6 | cego a chamador não-Python (hook `.sh`, workflow `.js`) | subestimava uso | não |
| 7 | excluía `main` | 49 entrypoints fora da conta | sim |
| 8 | contava uso no próprio arquivo sem distinguir recursão de chamada | `render_seed` parecia viva por se chamar | parcial |

### E três que eu mesmo introduzi, e que só apareceram porque fui olhar

Medir o instrumento não é retórica: **três das minhas próprias leituras estavam
erradas, e todas as três eram plausíveis.**

1. **`find_repo_root` apareceu como órfã.** É importada em
   `hooks/harness-classify.sh:742` — mas como `from harness_paths import
   find_repo_root as _raiz`, e minha regex de heredoc não tirava o ` as `.
2. **`mark_stale` apareceu como órfã.** Chega por
   `hooks/harness-lifecycle.py:103`, num módulo carregado por
   `importlib.util.module_from_spec` — a variável não tem nome de módulo.
3. **`tests/test_harness_paths.py` estava servindo de raiz de produção para o
   módulo `harness_paths`**, por substring. Esse é o pior dos três, e é o erro
   que este ramo existe para caçar: **o teste certificando a produção**, dentro do
   instrumento que deveria flagrar exatamente isso. Ele inflava `viva` em 9
   funções.

Achado 3 é também a razão de `hooks/harness_lite_adapter.py` ter mudado de
categoria: ele parecia ter raiz externa porque `contract/behavioral-probes.json`
aponta para `tests/test_harness_lite_adapter.py`. Tirada a substring, o módulo
inteiro é morto.

---

## O achado que reformula o plano: o contrato certifica capacidade com teste

`contract/capabilities.json` declara **22 capacidades com `"level": "required"`**.
`contract/behavioral-probes.json` prova cada uma delas — e **as 44 provas (22 × 2
adaptadores) são, sem exceção, um nó de teste.**

```json
"integration.harness-lite": ["tests/test_harness_lite_adapter.py::test_a_passed_bundle_with_artifacts_is_acceptable"]
```

A capacidade é `required`. A prova é um teste. O teste passa. E
`hooks/harness_lite_adapter.py` — 7 funções públicas, 24 referências em teste —
**não tem um único chamador de produção.**

A causa nº 1 do seed ("o teste é indistinguível de um chamador real") não é um
descuido que acontece aqui: **é o contrato do repositório**. Enquanto a definição
de "capacidade presente" for "a prova passa", nenhuma peça órfã pode ser
distinguida de uma peça viva por nenhum indicador que o projeto tenha hoje.

Isso muda a parte A do plano. A superfície de "capacidade declarada" já existe e é
`contract/capabilities.json`; o que falta não é declarar capacidades, é **exigir
que cada uma nomeie um consumidor de produção, verificado por sinal independente
do teste.**

---

## Os 38 esquecidos

Nada em `CLAUDE.md`, em `tools/README.md`, em `scripts/health-check.sh`, em
`.github/workflows/ci.yml` ou em qualquer `skills/*/SKILL.md` diz como ou quando
rodá-los.

| peça | fn | testes | evidência do esquecimento |
|---|---:|---:|---|
| `tools/wiki_lint.py` | 11 | 18 em `analyze_wiki` | **ausente da tabela do `tools/README.md`**, que lista 7 irmãos. Citado em 12 docstrings como *o contrato que os outros herdam* — e nenhuma delas o chama |
| `hooks/harness_lite_adapter.py` | 7 | 24 | capacidade `required` no contrato, prova é teste, zero produção |
| `scripts/calibrate_classify_guard.py` | 6 | 0 | **zero menções em todo o repositório** fora do próprio arquivo |
| `tools/wiki_moc.py` | 6 | 13 em `build_moc` | gera "a porta de entrada humana do vault"; nada a invoca |
| `scripts/harvest_branch_labels.py` | 2 | 0 | aparece só dentro de uma enumeração num doc de risco, sem como nem quando |
| `scripts/_escopo.py` (`de_sessao`, `divergencia`) | 2 | 0 | módulo de desenho com `__all__` completo; o degrau que usaria `divergencia()` não foi ligado |
| `branch_seed.render_seed` | 1 | 6 | ver abaixo |
| `branch_sensor.reset_session` | 1 | 0 | única menção é um doc que o descreve como defeituoso |
| `branch_state.pending` | 1 | 0 | irmão de `open_branches`, que é vivo |
| `wiki_query.chaves_de_citacao` | 1 | 1 | o único chamador é `calibrate_wiki_floor.py`, que também não roda |
| **total** | **38** | | |

### `render_seed` — a documentação afirma uma validação que não roda

`skills/branch-out/SKILL.md:142` diz, sobre a semente de um ramo:

> "Seis seções obrigatórias (**o renderizador recusa semente incompleta**)."

`scripts/branch_seed.py::render_seed` é esse renderizador: 110 linhas, seis seções,
e uma correção de `RecursionError` com comentário explicando o caso que derrubava a
skill inteira. **A linha 155 da mesma SKILL.md manda rodar
`branch_seed.py write --seed-file <arquivo.md>`, e o `main` lê o arquivo pronto e
grava.** A única chamada a `render_seed` no repositório é a recursão dela mesma,
na linha 150.

Quem escreve a semente é o agente, à mão, como a linha 139 da skill diz
explicitamente. Ninguém recusa semente incompleta. **A capacidade existe, tem
teste, e a frase que a anuncia é falsa** — três propriedades que só coexistem
porque nada mede a diferença entre estar escrito e estar rodando.

## As 51 decisões declaradas

Um documento diz **o quê, como e quando**. São ferramentas de mão, e estarem sem
chamador é o desenho.

| peça | fn | onde está declarado |
|---|---:|---|
| `scripts/deploy_to_cache.py` | 7 | `docs/specs/parent-session-por-ramo-riscos.md:706` com o comando; `tests/test_deploy_drift.py:103` imprime a linha a rodar |
| `scripts/calibrate_branch_floor.py` | 5 | `CLAUDE.md`: *"refazer com `calibrate_branch_layer_a.py` e `calibrate_branch_floor.py`"* |
| `scripts/calibrate_branch_layer_a.py` | 5 | idem |
| `scripts/check_hermeticity.py` | 5 | `p1b-testes-hermeticos-design.md:172`, com o porquê de ser script e não teste |
| `scripts/calibrate_wiki_floor.py` | 3 | `docs/wiki-query.md:190`, com o comando e o gatilho |
| `scripts/bench_stats.py` | 3 | `MIGRATION_LOG.md:249` (D5 do ADR-002) |
| `scripts/bench_wiki.py` | 1 | `docs/wiki-query.md:103`, com a medição que produziu |
| `tools/vault_maintenance.py` | 12 | tabela do `tools/README.md:21`, com o comando |
| `tools/export_plugins.py` | 4 | tabela do `tools/README.md:20` e `README.md:493` |
| `scripts/diagnose_ollama.py` | 6 | `README.md:274-282`, seção própria com o comando e a flag |
| **total** | **51** | |

As três últimas linhas entraram na segunda leitura. Elas já estavam declaradas —
com comando, em tabela — e o erro do primeiro corte foi tratar essa declaração
como **execução**. São ferramentas de mão como as outras sete, e é aqui que
pertencem.

**A fronteira entre os dois grupos é uma frase escrita, e nada mais.**
`calibrate_wiki_floor` e `calibrate_classify_guard` são o mesmo tipo de
ferramenta, escritas no mesmo formato. Uma tem duas linhas num doc e é decisão; a
outra não tem nenhuma e é esquecimento. É isso que a allowlist da parte B
converte de acidente em registro.

---

## O que a medição sustenta e o que ela derruba

**Sustenta a parte B (guarda com allowlist).** 38 peças esquecidas num repositório
de 426 funções não é ruído, e a fronteira ser uma frase escrita é exatamente o que
uma allowlist com motivo captura.

**Sustenta a parte C (consumidor nomeado), com força maior que a prevista.** O
problema não é só specs que descrevem a peça e param: o **contrato** faz isso, em
22 capacidades `required`.

**Derruba a premissa de escala do seed.** Não há 56 só-em-teste; há 38 esquecidos
no total. A allowlist inicial tem 89 linhas — mais que as 56 do seed, e por uma
razão melhor: ela inventaria também as 51 ferramentas de mão, que o seed nem
contava. É um inventário que cabe numa sessão.

**Não decide a parte A.** Alcançabilidade estática responde *"existe caminho?"*.
Para as 51 ferramentas de mão o caminho termina no humano, e nenhuma varredura de
AST distingue *"o autor roda toda semana"* de *"ninguém rodou desde que foi
escrita"*. Essa pergunta é de liveness em execução, não estática — e é o que
justifica a parte A continuar existindo depois desta medição, em vez de ser
absorvida pelo guarda.

---

## Como reproduzir

```bash
python <scratchpad>/alcance.py . alcance.json
```

O instrumento e a saída bruta estão no scratchpad desta sessão. Ele vira código de
produção na parte B; até lá, os números acima são dele, e os três erros próprios
listados acima são o motivo de a saída bruta ficar guardada.
