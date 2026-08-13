---
name: verify-against-spec
description: "Verifica cobertura item-por-item entre spec e implementação. Cada REQ, AC, user story, boundary e success criterion é checado com evidência concreta (teste, arquivo, log). Gera report de gaps em Markdown e estende o verify tradicional com rastreabilidade completa spec→teste→código. Usado no fim dos pipelines L1 e L2 do Harness v3 para garantir que nenhum requisito fica órfão."
category: workflow
risk: low
source: custom
date_added: "2026-04-10"
metadata:
  version: 1
  triggers: verify-against-spec, verify spec, spec coverage, spec verification, coverage report
---

# Verify Against Spec — Verificação de Cobertura de Spec

Skill de fechamento que transforma o verify tradicional (testes passando, lint limpo) em uma auditoria de rastreabilidade completa: cada item da spec precisa ter evidência observável na implementação.

## Quando ativar

Ativar esta skill quando:

- O pipeline do Harness v3 atinge o step `verify` ou `verify-against-spec`.
- Existe uma spec formal em `docs/specs/{feature-slug}.md` (gerada por `write-spec` ou `write-spec-light`).
- A implementação está concluída, com testes passando (output de `pytest` limpo) e lint OK (`ruff check`).
- O pipeline é L1 (verify-against-spec light, só REQs e ACs P1) ou L2 (verify-against-spec full, todos os tiers).

**NÃO ativar** quando:

- Não há spec em `docs/specs/` — nesse caso caímos no `verify` tradicional (apenas testes + lint).
- O pipeline é L0 — verificação completa é overkill para mudanças triviais.
- Testes ainda falham — ordem correta é: `tdd` → fix até verde → `verify-against-spec`.

## Objetivo

Ao final desta skill, cada um destes 5 pontos deve estar verificado com evidência:

1. **REQs com implementação observável** — todo REQ-### referenciado na spec aponta para arquivos e funções reais.
2. **ACs com testes correspondentes** — cada acceptance criterion (Given/When/Then) tem pelo menos um teste que o exercita.
3. **User stories implementadas** — P1 é obrigatório (100% coverage); P2/P3 geram warnings se incompletas mas não bloqueiam.
4. **Boundaries respeitadas** — ALWAYS/NEVER/ASK rules passam via hookify ou inspeção manual do código.
5. **Success criteria atingidos** — métricas mensuráveis declaradas na spec (latência, cobertura, precisão) são checadas contra a implementação.

## Workflow

**Passo 1 — Carregar spec e artefatos**

Ler `docs/specs/{feature-slug}.md`, `docs/specs/{feature-slug}-plan.md` e listar testes em `tests/`. Registrar paths dos artefatos para o report.

**Passo 2 — Extrair items da spec via parse**

Usar regex para capturar cada REQ, AC, user story, boundary e success criterion. Exemplo de parse em Python:

```python
import re
from pathlib import Path

def parse_spec(spec_path: Path) -> dict:
    text = spec_path.read_text(encoding="utf-8")
    return {
        "reqs": re.findall(r"^- REQ-(\d+):\s*(.+)$", text, re.M),
        "acs": re.findall(r"^\s*AC-(\d+):\s*Given\s+(.+?)\s+When\s+(.+?)\s+Then\s+(.+)$", text, re.M),
        "stories": re.findall(r"^###\s*(P[123])\s*User Story:\s*(.+)$", text, re.M),
        "boundaries": re.findall(r"^- (ALWAYS|NEVER|ASK):\s*(.+)$", text, re.M),
        "success": re.findall(r"^- Success:\s*(.+)$", text, re.M),
        "clarifications": re.findall(r"\[NEEDS CLARIFICATION\]", text),
    }
```

**Passo 3 — Fail-fast em `[NEEDS CLARIFICATION]`**

Se `clarifications` for não-vazia, ABORTAR com status `FAIL` e mensagem orientando voltar ao `write-spec` para resolver ambiguidades antes de verificar coverage.

**Passo 4 — Verificar cada REQ com evidência**

Para cada REQ-### extraído, procurar nos arquivos de código (via `grep -r "REQ-###"` ou análise semântica de docstrings) referências explícitas. Registrar **um de três** estados — nunca dois:

| estado | significa |
|---|---|
| `COBERTO` | achei a evidência: `arquivo:linha` |
| `ORFAO` | procurei e **não existe** |
| `NAO_OBSERVADO` | **não consegui verificar** — ferramenta faltando, ambiente indisponível, saída ilegível |

**`NAO_OBSERVADO` não é `ORFAO`, e tratá-los como um só faz o report mentir nos dois sentidos**: reprova o que funciona mas não deu para conferir, e esconde que a própria verificação ficou incompleta. Quem lê "1 REQ órfão" vai atrás do código; quem lê "1 REQ não observado" vai atrás do ambiente. São trabalhos diferentes.

Todo `NAO_OBSERVADO` carrega **por que** não deu para observar. Sem isso ele vira um jeito educado de dizer "pulei".

*Absorvido de QoderAI/better-harness (2026-08-13), cujo modelo de evidência declara: comportamento não observado NUNCA é pontuado.*

**Passo 5 — Verificar cada AC com teste correspondente**

Para cada AC-### procurar em `tests/` por um `test_*` cujo nome, docstring ou comentário referencie o AC. Rodar o teste isoladamente para confirmar que passa. Registrar mapeamento AC→test_file:test_function.

**Teste que passa não prova nada sozinho — prove que ele sabe falhar.** Um teste que passaria de qualquer jeito é indistinguível de um teste correto enquanto o código estiver certo, e some no dia em que o código quebrar. Para todo teste novo de AC ou de regressão, o ciclo completo:

```
escrever → rodar (passa) → REVERTER a correção → rodar (TEM que falhar) → restaurar → rodar (passa)
```

Se ele passa com a correção revertida, ele não testa a correção. Registre no report que o red-green foi verificado, não só que o teste passa.

É a mesma ideia que o `health-check.sh` já aplica a hooks — o meta-teste troca o hook por `INERT_HOOK` e exige que o health-check reprove. Aqui ela vale para o teste em vez do detector: em ambos, o que se prova é que a coisa **detecta ausência**, não que ela roda.

**Passo 6 — Verificar boundaries via hookify**

Rodar `hookify list` e conferir que as regras ALWAYS/NEVER declaradas na spec estão configuradas como hooks ativos. Para regras ASK, inspecionar se o código contém branches condicionais que delegam ao usuário. Casos não cobertos por hookify viram warnings inspecionáveis manualmente.

**Passo 7 — Verificar success criteria**

Para cada métrica declarada (ex: "latência < 100ms", "coverage > 80%"), executar o comando de medição correspondente e comparar com o threshold. Registrar valor real e delta vs. spec.

**Passo 7b — Se algum artefato veio de subagente, conferir o diff**

Relatório de subagente é afirmação, não evidência. Um agente que diz "implementei X e os testes passam" pode ter tocado outro arquivo, tocado nenhum, ou passado por um caminho diferente do que descreve — e nada disso aparece no texto que ele devolve.

Antes de contar qualquer entrega de subagente como verificada:

```bash
git status --short          # o que realmente mudou no disco
git diff                    # e o que exatamente foi escrito
```

Confira que os arquivos alterados são os que o relatório diz, e que a mudança faz o que ele afirma. Divergência entre relatório e diff é gap, e entra no report como tal.

O princípio já valia ("não tome relatório de agente pelo valor de face"); o que faltava era a ação que o torna conferível.

**Passo 8 — Gerar report e retornar status**

Escrever `docs/specs/{feature-slug}-verification.md` no formato da seção "Report Format" abaixo. Retornar ao `harness-workflow` um dos 3 status: `VERIFY_STATUS=PASS` (tudo verde), `PARTIAL` (P1 verde, P2/P3 com warnings) ou `FAIL` (qualquer P1 órfão, clarifications pendentes, success criteria não atingido).

**`NAO_OBSERVADO` nunca vira PASS silencioso, e nunca vira FAIL por omissão.** A regra:

- Qualquer P1 em `NAO_OBSERVADO` -> **`PARTIAL`**, nunca `PASS`. Não observar um requisito crítico não é aprová-lo.
- `NAO_OBSERVADO` sozinho **não produz `FAIL`** — `FAIL` afirma que algo está errado, e aqui não se sabe.
- O report abre com a **contagem de não observados e o porquê de cada um**. Verificação que não diz o que deixou de ver entrega confiança que não mediu — que é a forma mais cara de erro deste pipeline.

## Fail-fast em [NEEDS CLARIFICATION]

Se a spec contém marcadores `[NEEDS CLARIFICATION]` não resolvidos, ABORTAR imediatamente a verificação. Motivo: não é possível medir coverage contra requisitos ambíguos. O harness deve voltar ao step `write-spec` (ou `discuss` em L2) para resolver as ambiguidades e só então reentrar em `verify-against-spec`.

Mensagem de abort recomendada:

```
FAIL: Spec contém N markers [NEEDS CLARIFICATION] não resolvidos.
Resolva as ambiguidades antes de verificar coverage:
  - linha 42: [NEEDS CLARIFICATION: timeout em ms ou s?]
  - linha 78: [NEEDS CLARIFICATION: quem recebe o erro?]
Ação: reentrar em write-spec ou discuss para alinhar, depois reexecutar verify-against-spec.
```

## Report Format

O arquivo `docs/specs/{feature-slug}-verification.md` deve seguir este layout com tabelas Markdown:

```markdown
# Verification Report — {feature-slug}

**Date**: 2026-04-10
**Status**: PASS | PARTIAL | FAIL
**Spec**: docs/specs/{feature-slug}.md
**Plan**: docs/specs/{feature-slug}-plan.md

## REQs Coverage

| REQ    | Descrição                    | Evidência                       | Status |
|--------|------------------------------|---------------------------------|--------|
| REQ-01 | Validar input do usuário     | src/validator.py:42             | PASS   |
| REQ-02 | Persistir em SQLite          | src/db.py:18                    | PASS   |
| REQ-03 | Log estruturado              | —                               | FAIL   |

## ACs Coverage

| AC    | Given/When/Then (resumo)       | Teste                              | Status |
|-------|--------------------------------|------------------------------------|--------|
| AC-01 | input vazio → erro 400         | tests/test_api.py::test_empty      | PASS   |
| AC-02 | input válido → 201 + body      | tests/test_api.py::test_valid      | PASS   |

## User Stories Coverage

| Story     | Prioridade | Implementada | Notas                   |
|-----------|-----------|--------------|-------------------------|
| Story A   | P1        | SIM          | —                       |
| Story B   | P2        | PARCIAL      | Falta edge case de null |

## Boundaries Coverage

| Rule                              | Tipo    | Verificação          | Status |
|-----------------------------------|---------|----------------------|--------|
| NEVER: commitar secrets           | NEVER   | hookify warn-secrets | PASS   |
| ALWAYS: logar erros via logging   | ALWAYS  | hookify warn-print   | PASS   |

## Success Criteria

| Critério             | Target    | Medido    | Status |
|----------------------|-----------|-----------|--------|
| Latência por request | < 100 ms  | 73 ms     | PASS   |
| Test coverage        | > 80%     | 87%       | PASS   |

## Gaps Encontrados

- **REQ-03** (log estruturado): nenhum `logger.info` encontrado em `src/`. Ação: implementar ou mover para próxima iteração.
- **Story B** (edge case null): cobrir em test_b_null_input. Ação: adicionar teste ou documentar como known limitation.

## Próximos Passos

Se Status=PARTIAL ou FAIL, invocar `closure-plan` para registrar gaps e decidir entre iterar, fazer follow-up task, ou aceitar dívida explícita.
```

## Princípios

1. **Evidência sobre asserção** — coverage só é reconhecida quando há arquivo:linha ou teste concreto. Claims verbais ("eu implementei isso") não contam.
2. **Traceability completa REQ→AC→Teste→Código** — todo item da spec deve formar uma cadeia rastreável até linhas de código e nomes de testes. Sem isso, o item é órfão.
3. **Non-blocking em falso positivo** — se a skill não consegue confirmar cobertura mas suspeita que existe (ex: teste parametrizado genérico), registra como warning, não FAIL. Usuário decide.
4. **Fail-fast em clarifications pendentes** — `[NEEDS CLARIFICATION]` derruba a verificação imediatamente. Não faz sentido medir coverage contra spec ambígua.
5. **Report é artefato** — o `verification.md` fica versionado no repo e serve como evidência histórica. Próximas iterações consultam reports passados para evitar regressão.

## Saída

Produz **dois outputs**:

1. **Arquivo**: `docs/specs/{feature-slug}-verification.md` com tabelas completas de REQs, ACs, User Stories, Boundaries, Success Criteria e Gaps.
2. **Retorno ao pipeline**: variável `VERIFY_STATUS` com um de três valores:
   - `PASS` — todos os REQs, ACs P1, boundaries e success criteria estão cobertos com evidência.
   - `PARTIAL` — P1 completo mas P2/P3 com warnings. Aceitável para avançar com closure-plan documentando gaps.
   - `FAIL` — qualquer REQ órfão, AC P1 sem teste, boundary violada, success criterion não atingido, ou clarifications pendentes. Bloqueia o encerramento.

## Passo final — propor verbetes ao compêndio

Depois de emitir `VERIFY_STATUS`, e **apenas quando o vault AI-Brain existir**, olhe a
tarefa que acabou de terminar e liste os conceitos, técnicas ou métodos que ela usou.
Confira o que já está catalogado:

```bash
python "$CLAUDE_PLUGIN_ROOT/tools/compendium.py" --root "$VAULT_PATH/AI-Brain" check
```

O que propor, e o que não propor:

- **Proponha** o que tem nome próprio e limite conhecido — técnica que você escolheu,
  métrica que mediu, padrão que aplicou. Especialmente **conceito multi-palavra**
  ("chunking semântico", "degradação graciosa", "banda de confiança"): a varredura de
  token do `compendium.py candidates` não os pega, ela acha sigla. É exatamente por isso
  que existem duas redes, e esta é a que cobre o buraco da outra.
- **Não proponha** nome de arquivo, identificador, produto ou sigla de infraestrutura.
  São vocabulário de ferramenta, e a fila de triagem já vive cheia deles.

Apresente cada proposta com os três campos que ninguém consegue reconstruir depois:
**intuição**, **onde apareceu no código desta tarefa** (`arquivo:símbolo` real, que o
`check` verifica) e **quando não usar**. Se você não consegue escrever o terceiro, o
conceito ainda não está entendido o bastante para virar verbete — diga isso em vez de
preencher.

**Nada entra sozinho.** A proposta vai ao usuário; quem edita `compendio/terms.toml` é
ele. Este passo nunca altera o `VERIFY_STATUS`.

## Integração com pipeline

Fluxo típico em pipelines L1 e L2 do Harness v3:

```
L1: classify → write-spec-light → prd-to-plan → tdd → verify-against-spec → [DONE ou closure-plan + iteração]
L2: classify → discuss → write-spec → validate-plan → tdd → verify-against-spec → [DONE ou closure-plan + iteração]
```

Em ambos os casos, `verify-against-spec` é o penúltimo step. Se `VERIFY_STATUS=PASS`, o pipeline chega a `DONE`. Se `PARTIAL` ou `FAIL`, o `harness-workflow` invoca `closure-plan` para decidir o próximo movimento (iterar mais uma rodada, abrir follow-up, ou aceitar débito técnico).

Esta skill é o garantidor final da rastreabilidade spec-driven: sem ela, o Harness v3 degenera para o v2 tradicional, onde "testes verdes" é confundido com "spec cumprida".
