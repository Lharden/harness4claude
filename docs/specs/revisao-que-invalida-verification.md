# Verification Report — revisao-que-invalida

**Data:** 2026-09-17
**Status:** **PARTIAL**
**Spec:** `SEMENTE.md` (o briefing do autor é a spec desta sessão; não há spec formal)
**Artefatos:** `docs/specs/revisao-que-invalida-mapa.md`, `docs/specs/revisao-que-invalida-plano.md`

**Não observados: 2.** Ambos nomeados em "Não observado", com o motivo. Nenhum P1.

---

## Achados da semente — cobertura item por item

| # | O que a semente afirma | Evidência | Estado |
|---|---|---|---|
| 1 | `files` poluída: 122/466 = 26,2% não parecem caminho | reproduzido: 121/467 = 25,9% (`medir.py`, régua do autor) | **CONFIRMADO** |
| 1b | 80/268 revisões = 29,9% criadas por essas entradas | denominador colapsa `code_revision` entre 10 tasks; por `(task, rev)` são 366, e 67 = **18,3%** | **CORRIGIDO** |
| 1c | "o extrator pega tokens do shell — variáveis, fatias de array, JSON" | `shell_write_targets` devolve `[]` nos quatro casos citados; `_tokenize` respeita aspas (`harness-transactional.py:184-185`) | **REFUTADO** |
| 1d | (mecanismo real, não afirmado pela semente) | heredoc / PowerShell / código de programa: 5 de 6 casos produzem alvo espúrio; `'0.75:'` regenerado e é a linha `rev=230` do banco | **NOVO, CONFIRMADO** |
| 2 | Portão pede evidência de TESTE para invalidação por ARQUIVO | `transactional_state.py:1122` compara `code_revision`; `touch_files:934-944` sobe sem filtro | **CONFIRMADO** |
| 2b | "`counts_as_modified_file` já sabe, e o conserto não chegou aqui" | `harness-reclassify.sh:135` usa; `harness-transactional.py:580-582` não usa | **CONFIRMADO** |
| 3 | 8 das últimas 9 linhas de `evidence` com número idêntico | 12 linhas agora; as **9 últimas** idênticas (`2230/2191/39`) | **CONFIRMADO E AMPLIADO** |
| 3b | "ao longo de 1h10" | `rev=57` 15:51:27 → `rev=109` 17:56:09 = **2h04min41s**; até `rev=117`, **2h22min19s** | **CORRIGIDO** |
| 4 | "dois contadores com o mesmo nome — descubra" | um contador, escopo por task: `max(first_seen) ≤ code_revision` nas 6 tasks | **REFUTADO — sem defeito** |
| 5 | `complete` não converge porque gravar evidência sobe `code_revision` | isso foi consertado em 2026-09-02 (`is_state_management:152-173`) | **REFUTADO** |
| 5b | (causa real) | a isenção exige `not _has_unquoted_shell_composition`; os 3 blocos `bash` do `SKILL.md` (45-54, 74-80, 378-395) são compostos | **NOVO, CONFIRMADO** |

**Todos os 5 achados endereçados.** Dois confirmados, dois refutados com evidência, um corrigido no número e ampliado.

---

## Falsificação — a métade que prova que o guarda pega o defeito

A semente exige que conserto seja falsificável. Nenhum conserto foi aplicado, então o que se falsificou foi o **diagnóstico**:

| hipótese | pega? | deixa de punir? | veredito |
|---|---|---|---|
| H1 — `>` de comparação entre aspas vira alvo | não: `[]` nos 4 casos | controles devolvem alvo | **REFUTADA** |
| H1b — corpo de heredoc / PowerShell viram alvo | sim: 5 de 6 | 2 de 2 controles corretos | **CONFIRMADA** |
| H2 — assimetria `Write` × `>` | `Write`→False, `>`→True, mesmo caminho | — | **CONFIRMADA** |
| H3 — receita do manual derrota a isenção | receita→False | forma atômica→True, e foi o que rodou nesta sessão | **CONFIRMADA** |
| H4 — interpretador sobe sem escrever | `python`/`node` sobem | `grep`/`git status`/`add`/`commit` **não** sobem | **CONFIRMADA** |

Toda medição usa a função de produção importada de `hooks/harness-transactional.py` e `scripts/post_tool_policy.py`. **Nenhuma reimplementação.** Reprodução: `scratchpad/falsificar.py`, `scratchpad/falsificar2.py`.

O **erro do meu próprio instrumento** está registrado no mapa §2 em vez de apagado: a v1 da régua classificou `'0.75'` e `'F4.0'` como caminhos válidos porque `.75` casava como extensão.

---

## Escopo — o que a semente mandou não fazer

| Regra | Verificação | Estado |
|---|---|---|
| ENTRA: mapa medido + plano escrito | os dois arquivos existem | **PASS** |
| NÃO ENTRA: conserto | `git status --short` → só `SEMENTE.md` e os dois `docs/specs/*.md`, ambos `??` | **PASS** |
| NÃO ENTRA: mudança de régua | `REGRA_TESTE_VALIDO` intocado; o plano recusa explicitamente afrouxá-la | **PASS** |
| NÃO ENTRA: mudança de config do harness | nenhuma variável `HARNESS_*` escrita | **PASS** |
| Conserto de uma linha pode ser **proposto**, não aplicado | R4 proposto com falsificação; `SKILL.md` **não** editado | **PASS** |
| Não mesclar em `main`, não empurrar | branch `claude/revisao-que-invalida`; sem merge, sem push | **PASS** |
| Universal, nada específico de HCE/mestrado/um repo | o defeito é do harness; o banco de HCE foi só o corpus | **PASS** |
| `exit_code == 0` não é aceite | toda saída foi lida; o erro de régua da §2 foi achado assim | **PASS** |
| Número aposentado permanece escrito com `[superado:]` | 4 marcadores: mapa §1, §6.1, §6.2, §6.3; plano no fim | **PASS** |
| Um parágrafo é uma linha | sem quebra rígida nos dois documentos | **PASS** |
| Contestar os números do autor em vez de aceitar | 3 dos 5 achados corrigidos ou refutados | **PASS** |
| Conferir o critério "parece caminho" do autor | §2 do mapa: régua alternativa declarada, 25,9% → 49,9% | **PASS** |

---

## Nível de garantia — o que os documentos afirmam × o que sustenta

| Afirmação | O que sustenta | Estado |
|---|---|---|
| "49,9% de `files` não pode ser um caminho" | régua declarada, reproduzível, e o desacordo com o autor é mostrado nos dois sentidos | SUSTENTA |
| "74,9% das revisões não contêm código sob teste" | vale sobre as **366 com rastro**, e o mapa §4 diz que são 14,1% do total | SUSTENTA — com o limite escrito |
| "9 invalidações, 0 mudanças de código" | banco desta sessão, `code_revision=9`, `git status` limpo de código | SUSTENTA |
| "R1 colapsa os quatro sintomas" | raciocínio sobre o mecanismo, **não medido** — R1 não foi construído | **EXCEDE** → o plano marca como proposta com falsificação desenhada, não como resultado |
| "0,297 é medido contra rótulo corrompido" | 2 de 6 rótulos mudam; o mapa §7 diz explicitamente que **não** afirma que o número está errado | SUSTENTA |

O único `EXCEDE` está contido: R1 aparece como proposta, com o afrouxamento embutido nomeado e duas saídas para o autor escolher.

---

## Não observado

| item | por quê |
|---|---|
| **Suíte de testes do repositório** | `python -m pytest -q -p no:cacheprovider` rodando em background desde o início da sessão; **ainda não terminou** ao fechar este report. Processo vivo. Nenhuma linha de produção foi alterada, então o resultado é o da base `6e7f4e5` por construção — mas **isso é inferência, não medição**, e fica registrado como tal. |
| **Causa das 2 228 invalidações sem rastro** | `INSERT OR IGNORE` (`transactional_state.py:936`) descartou a informação na escrita. Não há fonte no banco. É o item R5 do plano. |

Nenhum dos dois é P1. Nenhum vira `PASS` silencioso.

---

## Gaps

- **Nenhum gap de cobertura.** Os 5 achados, as 12 regras de escopo e as 3 instruções de reporte estão endereçados.
- **Duas decisões abertas**, e são para o autor por construção, não por omissão: o afrouxamento de R1 para mutação de ambiente (opção *a* ou *b*), e se a impressão digital da árvore cobre arquivos não-rastreados. Estão como `[NEEDS CLARIFICATION]` no plano.

O *fail-fast* de `[NEEDS CLARIFICATION]` não dispara: a spec desta sessão (`SEMENTE.md`) não contém nenhum. Os dois marcadores estão no **entregável**, onde nomear decisão pendente é o trabalho pedido.

---

## Próximos passos

`PARTIAL` por dois `NAO_OBSERVADO`, nenhum bloqueante. O autor lê o mapa e o plano e decide a ordem de execução — R4 e R5 não precisam de decisão de desenho; **R1 precisa de gate humano** porque troca a grandeza que o portão mede.
