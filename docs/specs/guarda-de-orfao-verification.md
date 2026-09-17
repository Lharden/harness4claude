# Verificação — Guarda de órfão

**Task:** `t-20260916-215220419949` · **Spec:** [`guarda-de-orfao-spec.md`](guarda-de-orfao-spec.md)
**Verificado em:** 2026-09-16 · **Base:** `5ca9d4e`

---

## US-1 — O guarda reprova órfão não declarado (P1)

| AC | evidência | veredito |
|---|---|---|
| AC-1 função chamada por hook `.sh` é viva | `test_funcao_chamada_por_hook_sh_e_viva` | PASS |
| AC-2 órfão não declarado reprova com `arquivo:linha` | `test_orfao_nao_declarado_reprova_com_arquivo_e_linha` | PASS |
| AC-3 órfão declarado passa | `test_orfao_declarado_passa` | PASS |
| AC-4 referência só em teste não torna viva | `test_referencia_so_em_teste_nao_torna_viva` | PASS |
| AC-5 recursão não torna viva | `test_recursao_nao_torna_viva` | PASS |
| AC-6 alcance é transitivo a partir da raiz | `test_alcance_e_transitivo_a_partir_da_raiz` | PASS |
| edge: zero raízes reprova | `test_zero_raizes_reprova` | PASS |
| edge: arquivo não parseável reprova com nome | `test_arquivo_que_nao_parseia_reprova_com_nome` | PASS |

---

## US-2 — O guarda tem de ser chamado, e a prova não depende dele (P1)

**AC-1 — `pytest` executa o guarda.** `tests/test_orfaos.py` é coletado pelo
`pytest` sem marcador nem opt-in, e o CI roda `python -m pytest -q` no job `tests`
(matriz de 6: ubuntu/windows × 3.10/3.11/3.14). **PASS.**

**AC-2 — `health-check.sh` invoca o scanner.** `scripts/health-check.sh`, seção
`--- Orphaned artifacts ---`, chama `python "$PLUGIN_DIR/tools/orfaos.py" --raiz
"$PLUGIN_DIR"` e propaga `EXIT_CODE=1`. Travado por
`test_health_check_invoca_o_scanner`. **PASS.**

**AC-3 — evidência externa, com a saída colada.** Órfão plantado em
`scripts/record_signal.py`:

```python
def funcao_plantada_para_provar_o_guarda():
    """Plantada de proposito em 2026-09-16. Ninguem a chama."""
    return "se a suite passar com isto aqui, o guarda nao roda"
```

Execução:

```
===== SUITE COM ORFAO PLANTADO =====
>       assert p["ready"] is True, orf.mensagem_de_falha(p)
E       AssertionError: ORFAO NAO DECLARADO  scripts/record_signal.py:234
E                       funcao_plantada_para_provar_o_guarda()  [inalcancavel]
E
E             python tools/orfaos.py --sync
E
E         Orfao novo ele NAO resolve sozinho — entra com motivo vazio e a suite segue
E         vermelha. Falta escrever, em tools/orfaos.json, por que a funcao existe sem
E         chamador. Essa frase e o produto: e a unica diferenca entre uma ferramenta
E         de mao e uma peca esquecida.
FAILED tests/test_orfaos.py::TestRepositorioReal::test_repositorio_real_passa_o_guarda
1 failed, 29 passed in 1.01s
```

Órfão removido; `git diff --stat scripts/record_signal.py` vazio; re-execução:
`30 passed in 0.95s`.

**O veredito é do `pytest`, não do guarda** — é por isso que isto não virou teste:
um teste que espera falha estaria medindo a si mesmo. **PASS.**

**AC-4 — o scanner aparece como vivo no próprio scanner.**
`test_o_proprio_scanner_aparece_como_vivo`. **Corroboração, não prova** — um
registro que se confirma sozinho mede consistência, não vida. **PASS.**

---

## US-3 — A allowlist guarda julgamento (P1)

| AC | evidência | veredito |
|---|---|---|
| AC-1 motivo vazio reprova | `test_motivo_vazio_reprova` | PASS |
| AC-2 motivo que só repete o nome reprova | `test_motivo_que_so_repete_o_nome_reprova` | PASS |
| AC-3 categoria fora do vocabulário reprova | `test_categoria_fora_do_vocabulario_reprova` | PASS |
| AC-4 dupla inclusão (não contagem) | `test_dupla_inclusao_allowlist_e_codigo` | PASS |
| AC-5 decisão e dívida distinguíveis pela categoria | `tools/orfaos.py --report`: `decisao declarada 51 / divida (ORFAO) 38` | PASS |

`tools/orfaos.json`: **89 declarações, 89 com `motivo` não-vazio, 0 inválidas.**

---

## US-7 — Linha obsoleta reprova, e a mensagem traz o conserto (P1)

| AC | evidência | veredito |
|---|---|---|
| AC-1 ganhou chamador reprova | `test_linha_obsoleta_por_ganhar_chamador_reprova` | PASS |
| AC-2 alvo sumiu reprova | `test_linha_obsoleta_por_alvo_sumido_reprova` | PASS |
| AC-3 a mensagem distingue os dois casos | `test_mensagem_distingue_os_dois_casos` + saída de AC US-2/AC-3 acima | PASS |
| AC-4 **o comando impresso de fato roda** | `test_comando_que_a_mensagem_imprime_de_fato_roda` — extrai a linha da mensagem, executa por `subprocess`, exige exit 0 | PASS |
| AC-5 `--sync` acrescenta mas não aprova | `test_sync_acrescenta_orfao_novo_mas_nao_aprova` | PASS |
| REQ-F14 `--sync` nunca apaga motivo | `test_sync_nunca_apaga_motivo_escrito` | PASS |

**AC-1 foi verificado fora do teste também, e por acidente:** ao acrescentar
`wiki_lint.py` e `wiki_moc.py` à tabela do `tools/README.md`, 17 linhas da
allowlist viraram obsoletas e o guarda reprovou, imprimindo o comando. Foi essa
reprovação que expôs o defeito de desenho registrado abaixo.

---

## US-4 — Os erros do instrumento viram regressão (P2)

| AC | o erro real que trava | teste | veredito |
|---|---|---|---|
| AC-1 | `from harness_paths import find_repo_root as _raiz` | `test_import_com_as_no_heredoc_conta` | PASS |
| AC-2 | `mod.mark_stale()` via `spec_from_file_location` | `test_modulo_carregado_por_spec_from_file_location_conta` | PASS |
| AC-3 | `tests/test_X.py` virando raiz de `X` | `test_teste_nao_vira_raiz_de_producao` | PASS |
| AC-4 | `build_wiki_index.py` virando raiz de `wiki_index` | `test_prefixo_nao_vira_raiz` | PASS |
| AC-5 | sufixo (`outroM.py` → `M`) | `test_sufixo_nao_vira_raiz` | PASS |

---

## US-5 — Capacidade declarada exige consumidor nomeado (P2)

| AC | evidência | veredito |
|---|---|---|
| AC-1/AC-2 `write-spec` exige `consumidor:`, teste não conta | `skills/write-spec/SKILL.md`, seção Princípios | PASS |
| AC-3 `write-spec-light` exige em uma linha | `skills/write-spec-light/SKILL.md`, seção Princípios | PASS |
| AC-4 um teste confere que a regra está nas duas | `test_skills_de_spec_exigem_consumidor_nomeado` | PASS |

---

## US-6 — Relatório legível (P3)

`python tools/orfaos.py --report`. **PASS.**

---

## Execução

```
python -m pytest tests/test_orfaos.py -q
30 passed in 0.95s

python tools/orfaos.py --report
orfaos: 426 funcoes publicas de topo | 337 vivas | 42 raizes | 89 declaradas
  decisao declarada    51
  divida (ORFAO)       38   <- ninguem sabe por que existe
[OK] nenhum orfao nao declarado

ruff check tools/orfaos.py tests/test_orfaos.py
All checks passed!
```

**Suíte completa:** ver seção de execução final.

**Ruff no repositório:** 30 achados, **todos pré-existentes** e nenhum em arquivo
tocado por este ramo (`git diff --stat` vazio para cada um). Não corrigidos:
estão fora do escopo declarado, e corrigi-los aqui misturaria dois trabalhos.

---

## Defeito de desenho achado durante a verificação, e corrigido

Ao acrescentar `wiki_lint.py` e `wiki_moc.py` à tabela do `tools/README.md` —
passo que a própria spec pedia nos Success Criteria — **o guarda reprovou 17
linhas da allowlist como obsoletas**. A leitura estava certa: sob a primeira
versão, `README.md` era raiz de execução, então escrever a linha no README tornava
a função "viva".

Isso produzia uma incoerência com a medição: `wiki_lint`, documentado num README,
viraria **viva**; `deploy_to_cache`, documentado em `docs/`, ficaria na allowlist.
Mesma natureza, tratamento diferente, decidido pelo acaso de em qual arquivo a
frase caiu. E abria uma saída: escrever `python tools/x.py` num README silenciaria
o guarda sem passar pela allowlist, pelo motivo, nem pela categoria.

**Correção:** Markdown só é raiz quando é `skills/*/SKILL.md`. A distinção não é
estética — uma `SKILL.md` é carregada pelo harness e **instrui o agente executor**;
um README é prosa que um humano pode ler. Implementado em `_md_e_raiz`.

**Segundo defeito, e este era meu:** acrescentar as duas linhas ao README
**violava a decisão Locked L3** do `CONTEXT.md` — *"as 38 esquecidas entram como
`ORFAO` e nada mais acontece com elas; nenhuma é ligada"*. A spec pedia o
contrário nos Success Criteria, e o `grill` não pegou a contradição. Consertar uma
peça esquecida no mesmo gesto em que se mede **apaga o que foi medido**, e quem
decide que uma peça esquecida vira ferramenta declarada é quem mantém o
repositório, não quem passou medindo. As duas linhas foram revertidas; o
`tools/README.md` agora **nomeia a ausência** sem resolvê-la, e a spec foi
corrigida.

**Consequência numérica, registrada:** com README fora das raízes, 22 ferramentas
de mão que apareciam como vivas passaram a aparecer como declaradas. O total de
não-vivas foi de 67 para 89, e **o número de esquecidos não mudou: 38.**

---

## Suíte completa — 1 falha declarada, não silenciada

```
python -m pytest -q
2 failed, 1301 passed, 6 subtests passed in 760.43s (0:12:40)
  FAILED tests/test_console_utf8.py::test_todo_entrypoint_de_tools_chama_usar_utf8[orfaos.py]
  FAILED tests/test_deploy_drift.py::TestOQueRodaEOQueEstaNoRepo::test_sem_drift_entre_repo_e_plugin_instalado
```

**A primeira era defeito meu, e foi corrigida.** `test_console_utf8` exige que todo
entrypoint de `tools/` chame `usar_utf8()` antes do `main`; sem isso, um console
cp1252 levanta `UnicodeEncodeError` e o traceback substitui a resposta inteira. O
`--report` deste scanner ecoa motivo escrito à mão, com acento e travessão — era
uma quebra garantida. É um **teste-de-conjunto**, da mesma família que este ramo
produziu, e pegou o arquivo novo no dia em que ele nasceu para pegar o
esquecimento dos outros. Corrigido; `83 passed` em `test_console_utf8.py` +
`test_orfaos.py`.

**A segunda permanece, declarada.** `test_deploy_drift` compara a árvore de
trabalho com o plugin instalado em `~/.claude/plugins/cache/`. O que ele acusa é
**este trabalho ainda não implantado** — seis arquivos, três alterados e três
novos. Não há divergência pré-existente: medido, `hooks/`, `scripts/` e `skills/`
são idênticos fora dos que este ramo tocou.

**Não foi silenciada, e não foi contornada com `deploy_to_cache.py --apply`.** O
`--apply` escreveria código não revisado e não commitado no plugin que toda sessão
carrega — mudando o comportamento de `write-spec` para qualquer sessão em curso, e
instalando um `test_orfaos.py` que reprova por desenho enquanto a allowlist não
estiver sincronizada. **Deploy é consequência de merge, não pré-requisito de
evidência**; publicar para obter verde e pedir aprovação depois inverte quem
decide. Contornar o portão para ficar verde seria o oposto exato do que este ramo
existe para consertar.

### Sexto achado, e ele virou conserto: `test_deploy_drift` media a pergunta errada

> [superado: "fica como achado, não como conserto"] — o autor decidiu não mesclar
> com vermelho conhecido, e a regra de ouro que entrou no `CLAUDE.md` em
> 2026-09-16 diz por quê: *"vermelho conhecido não se mescla; teste que reprova
> por construção morre por uso: quem vê vermelho sempre para de ver vermelho"*.

**A causa raiz.** `tests/test_deploy_drift.py:43` fazia
`ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])`, e `tests/conftest.py` aponta
essa variável para o checkout onde ele vive. Num worktree de ramo, `ROOT` é o
worktree — então `dtc.drift(ROOT, cache, shipped_files(ROOT))` perguntava **"o meu
worktree está implantado?"**, e num ramo a resposta é não por definição.

A pergunta do incidente de 2026-09-02 é outra: **"o que roda é código
publicado?"**. Trabalho em ramo não é publicado, não deveria estar no cache, e
exigir que esteja inverte a ordem deploy/merge — o erro que esta sessão quase
cometeu hoje, com o raciocínio certo e a informação incompleta.

**O conserto.** `drift_publicado(root, cache, ref)` compara o cache com a árvore
de `main`, e — esta é a correção inteira — **deriva a lista de arquivos da árvore
de `ref`, não do worktree**. Um arquivo que só existe no ramo deixa de ser cobrado
do cache. Um `git archive` por chamada, não 250 `git show`.

**A falsificação, nas duas metades, contra o caminho real.** Não bastava mostrar
verde: verde podia significar silenciamento. Executado sobre o cache instalado de
verdade (`~/.claude/plugins/cache/harness4claude/4.0.0`), `main` = `5ca9d4e`:

```
[1] cache real vs main            ->  0 divergentes   VERDE
[2] cópia do cache real SABOTADA  ->  1 divergentes   VERMELHO   hooks/harness-classify.sh
[3] cópia com arquivo AUSENTE     ->  1 divergentes   VERMELHO   scripts/record_signal.py
[4] critério ANTIGO (worktree)    -> 16 divergentes   VERMELHO   — e os 16 são este ramo
```

A linha [4] é o diagnóstico: os 16 "divergentes" do critério antigo eram, um a um,
os arquivos deste ramo. Ele não media drift; media a existência de trabalho.

Travado por seis testes, cinco deles hermeticos sobre cache fabricado:
`test_cache_igual_ao_publicado_passa`, `test_cache_com_arquivo_alterado_REPROVA`,
`test_cache_com_arquivo_faltando_REPROVA`, `test_deploy_velho_REPROVA` (extrai
`main~1` — o incidente de 2026-09-02 em forma de teste),
`test_arquivo_que_so_existe_no_RAMO_nao_e_exigido` (o falso positivo estrutural), e
`test_o_comando_que_a_mensagem_imprime_e_aceito_pelo_parser_real`.

Esse último tem uma limitação honesta: `--apply` escreve no plugin instalado,
então **não dá para provar que o comando roda executando-o**. A prova possível é
parsear com o parser real (`_parser()`, extraído do `main` para isso), o que pega
a ordem errada de flag — o defeito de `5ca9d4e`. É mais fraco que executar: pega
comando malformado, não pega comando bem formado que faz a coisa errada.

**Considerado e recusado: `--destino <tmpdir>`.** Com ele o teste executaria o
comando real contra um diretório temporário, e a prova ficaria tão forte quanto a
de `5ca9d4e`. `apply(origem, destino, arquivos)` já recebe o destino como
parâmetro; só o `main` não expõe. Proposto pela sessão
`apresentacao-alta-gestao-refinamento-ac9-f9`, **com o contra-argumento junto**:
acrescentar flag para satisfazer teste é a porta de entrada de código-para-teste,
e só vale se houver justificativa independente — instalação em caminho não padrão.

Medido: **a justificativa independente não se sustenta.** `installed_root()` já
resolve caminho não padrão por duas vias, o marcador `plugin-root` e o
`installPath` do `installed_plugins.json`. A capacidade existe, por descoberta em
vez de por flag. `--destino` serviria só ao teste.

Então a prova fraca fica, declarada. **É melhor uma prova fraca escrita do que uma
prova forte comprada com uma flag que existe para o teste** — a segunda parece mais
rigorosa e esconde onde o rigor terminou.

**Descartado, com o motivo:** mover o teste para `health-check.sh`. Separaria a
responsabilidade corretamente, mas reduz a frequência de detecção — drift acontece
na máquina de trabalho, onde a suíte roda toda hora, e o `health-check` não. O
`skip` sem cache já resolve o CI. Análise da sessão
`apresentacao-alta-gestao-refinamento-ac9-f9`, que também fez o diagnóstico da
causa raiz.

**Terceira saída para dar raiz a `deploy_to_cache`, registrada e não medida:** um
hook de `SessionStart` roda com o cwd do **projeto**, não do plugin — e detectar
drift no início de uma sessão é exatamente quando o dado importa, antes de
qualquer trabalho ser feito sobre código errado. Não se sabe se o contrato de
`SessionStart` permite, nem o custo em latência. Levantada pela sessão
`apresentacao-alta-gestao-refinamento-ac9-f9`; **não medida por ninguém.** Fica ao
lado das outras duas (comparar contra `main` — feito; mover para `health-check` —
recusado por reduzir frequência de detecção).

E o diagnóstico do módulo é mais forte do que esta verificação dizia antes:
`deploy_to_cache` aparece em `main` inteiro apenas em dois documentos
(`parent-session-por-ramo-riscos.md`, `portao-de-task-fantasma-diagnostico.md`) e
num teste. Nenhum hook, nenhum CI, nenhuma `SKILL.md`, nenhuma linha de
`health-check.sh`. **O módulo inteiro é ferramenta de mão, não só as quatro
funções novas** — e as onze linhas dele na allowlist dizem isso desde a primeira
medição.

**E o guarda pegou o autor do guarda.** As quatro funções novas de
`deploy_to_cache.py` apareceram como órfãs cinco minutos depois de escritas —
`deploy_to_cache` é módulo sem raiz externa, e o único consumidor delas era o
portão. A saída não foi criar categoria nova para acomodá-las: foi dar a elas a
entrada de CLI que faltava (`--publicado`), o que torna `FERRAMENTA_DE_MAO`
verdade em vez de rótulo, e declarar as quatro com o motivo nomeando os dois
consumidores. **Uma capacidade cujo único consumidor é o teste dela é exatamente o
que este ramo existe para acusar** — inclusive quando a capacidade é minha.

---

## O que esta entrega NÃO cobre

- **Não diz se uma ferramenta de mão foi executada alguma vez.** Para as 51
  declaradas o caminho termina no humano. É a parte A, e é outro ramo.
- **38 métodos públicos em 18 classes de topo ficam fora** (~9% da superfície).
  Mover uma função órfã para dentro de uma classe a tira do radar sem mudar sua
  natureza — rota de evasão possível sem intenção. `ASSUMPTION-004`.
- **Não retrofita o contrato.** As 22 capacidades `required` provadas só por teste
  continuam assim. A regra do `consumidor:` vale para spec nova.
- **Não detecta dispatch que o AST não vê.** Vai para a allowlist com motivo — que
  é o desfecho correto: declaração explícita em vez de detecção mágica.
- **Um motivo mentiroso passa.** Nenhuma máquina lê prosa. O guarda garante que a
  frase exista, nunca que ela seja verdadeira.
- **A cobertura do `grill` é menor que a projetada.** `wf-grill` foi recusado pela
  camada de permissão (CRLF nos `.js`), e a rodada foi inline — no mesmo contexto
  que escreveu a spec. As cinco lentes ficaram mortas; os achados valem, a
  cobertura não tem a garantia de janelas descorrelacionadas.

---

## Encerramento — a fase 11 não foi executada, e por quê

**Declarado, não silenciado**, conforme a regra de ouro: *"item que não fecha sai
declarado com causa nomeada e ação conhecida"*.

O pipeline `L2-architecture` tem onze fases. Dez foram executadas. A décima
primeira, `verify-multimodel`, roda `scripts/workflows/wf-verify-multimodel.js` —
**o arquivo que este mesmo trabalho descobriu que não podia ser invocado.**

| | |
|---|---|
| **causa** | `.gitattributes` não declarava `*.js`/`*.cjs` com `eol=lf`; CRLF na árvore de trabalho faz a camada de permissão recusar o script como *"control characters"* |
| **ação** | feita em `8da0a84`, com dois testes e um controle |
| **o que falta** | provar que a ferramenta aceita o script agora |
| **por que não foi provado aqui** | o diretório de trabalho desta sessão foi removido no merge, e o Workflow só aceita caminhos dentro dele. Precisa de uma sessão cujo cwd contenha os workflows |

**A mesma causa atingiu as duas pontas do pipeline.** `grill-me` (fase 5) também
mapeia para um Workflow, e às 22h ele foi recusado pela mesma mensagem. A fase
rodou inline, com as cinco lentes mortas e a cobertura reduzida — registrado na
abertura de `guarda-de-orfao-grill.md`. Três horas separaram as duas recusas e só
na segunda a causa foi investigada.

**O que substituiu a fase 11, e não é equivalente:** sete achados adversariais
aplicados no `grill-me` inline, três rodadas de revisão independente pela sessão
`apresentacao-alta-gestao-refinamento-ac9-f9` — que verificou as quatro metades da
falsificação do drift construindo a divergência por conta própria — e sete suítes
completas. É revisão por outro contexto, não fan-out multi-modelo. A diferença
está aqui para quem precisar dela depois.

### Os seis rostos do mesmo defeito

O ramo nasceu para achar função sem chamador. O que ele achou foi um padrão:

| onde | perguntava | devia perguntar |
|---|---|---|
| `contract/capabilities.json` | "a prova passa?" | "a capacidade é usada?" |
| `test_deploy_drift` | "o worktree está implantado?" | "o que roda é publicado?" |
| `counts_as_modified_file` | "algum arquivo foi escrito?" | "o código sob teste mudou?" |
| `is_read_only` | "este comando escreve?" | "isto muda a árvore de trabalho?" |
| `scripts/workflows/*.js` | *(nada perguntava)* | "esta capacidade pode ser invocada?" |
| `is_state_management` | — | ver abaixo |

**O sexto ficou aberto e é o que travou este encerramento.** `is_state_management`
isenta `state_cli.py` de expirar evidência, mas só quando o comando não tem
composição de shell. Gravar evidência exige resolver o balde antes
(`harness_paths.py && state_cli.py evidence`), o comando composto perde a isenção,
e `touch_files` sobe `code_revision` no mesmo `PostToolUse`. **O comando que existe
para satisfazer o portão é classificado pelo portão como alteração de código.**
Medido: evidência gravada em 259, revisão em 260 no instante seguinte.

O docstring de `is_state_management` já descreve esse deadlock, medido em
2026-09-02, com a frase *"nenhuma task podia ser concluída pelo caminho previsto"*.
O conserto de então cobriu o comando simples e não o composto. **Ação conhecida:**
isentar o segmento do `state_cli.py` mesmo em comando composto, desde que nenhum
outro segmento escreva — a mesma forma que `is_read_only` já usa para recusar
quando um elo escreve. Não feito aqui.

### O número, uma última vez

**38 esquecidos contra 55 decisões declaradas, de 93 não-vivas sobre 432.**

Os 38 atravessaram **cinco** correções do instrumento que os mediu sem se mover.
É o único número da sessão que sobreviveu a mudanças no próprio instrumento, e é
essa a razão de confiar nele — não a quantidade de vezes que foi repetido.
