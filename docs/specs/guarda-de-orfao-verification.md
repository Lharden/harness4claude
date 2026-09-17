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

### Sexto achado: `test_deploy_drift` reprova em todo ramo aberto, por construção

Ele compara o **worktree** com o plugin instalado. Enquanto houver trabalho não
implantado — que é o estado normal de qualquer ramo antes do merge — ele reprova.
Isso o torna um **teste de ambiente dentro da suíte de código**, e é uma das razões
pelas quais dar evidência de conclusão num ramo é impossível sem publicar.

Duas saídas possíveis, nenhuma feita aqui: comparar contra `main` em vez do
worktree, ou separá-lo da suíte de código e movê-lo para o `health-check.sh`, onde
os outros checks de ambiente vivem. Levantado pela sessão
`apresentacao-alta-gestao-refinamento-ac9-f9` em 2026-09-16; **não medido por
mim** além de confirmar o mecanismo de comparação. Fica como achado, não como
conserto.

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
