# Spec: [FEATURE NAME]

**Status**: Draft
**Created**: [YYYY-MM-DD]
**Updated**: [YYYY-MM-DD]
**Branch**: [feature-branch-name]
**Author**: AI-generated, reviewed by Leonardo

---

## Executive Summary

[3-5 frases claras: O que é a feature, por que é importante, qual o impacto esperado.
Esta seção deve ser auto-contida — um stakeholder não-técnico deve entender o escopo.]

## Context

[Como o sistema funciona hoje na área impactada. Entry points, key files, data flows, constraints.
Esta seção é crucial para alinhamento — evita que o AI assuma contexto errado.]

### Files/Modules Impactados

- `path/to/file1.py`: [propósito atual e como será impactado]
- `path/to/file2.py`: [propósito atual e como será impactado]

### Dependencias

- [Dep 1]: [por que é relevante]
- [Dep 2]: [por que é relevante]

---

## User Stories

### US-1: [Título da User Story] (Priority: P1) — MVP

**Como** [tipo de usuário/persona]
**Quero** [capability desejada]
**Para que** [benefício/valor]

**Why this priority**: [justificativa — por que é P1 e não P2]

**Independence**: Esta story é independentemente testável e pode ser MVP sozinha.

**Acceptance Criteria**:

- **AC-1**: Given [estado/contexto inicial], When [ação do usuário/sistema], Then [resultado esperado]
- **AC-2**: Given [estado], When [ação], Then [resultado]
- **AC-3**: Given [estado de erro], When [ação], Then [comportamento esperado de erro]

**Edge Cases**:
- [Edge case 1: descrição]
- [Edge case 2: descrição]

---

### US-2: [Título] (Priority: P2)

**Como** [persona]
**Quero** [capability]
**Para que** [benefício]

**Why this priority**: [justificativa]

**Acceptance Criteria**:
- **AC-1**: Given [estado], When [ação], Then [resultado]
- **AC-2**: Given [estado], When [ação], Then [resultado]

---

### US-3: [Título] (Priority: P3 — Nice to have)

**Como** [persona]
**Quero** [capability]
**Para que** [benefício]

**Acceptance Criteria**:
- **AC-1**: Given [estado], When [ação], Then [resultado]

---

## Current System

[Como o sistema se comporta HOJE, antes desta feature. Importante para entender o delta.]

### Entry Points
- [Entry point 1]: [descrição]

### Data Flow
[Descrição textual ou diagrama ASCII do data flow atual]

### Constraints Existentes
- [Constraint 1]
- [Constraint 2]

---

## Requirements

### Functional
- [ ] **REQ-F1**: [descrição do requisito funcional] [traces: US-1, US-2]
- [ ] **REQ-F2**: [descrição] [traces: US-1]
- [ ] **REQ-F3**: [descrição] [traces: US-3]

### Non-Functional
- [ ] **REQ-NF1 (Performance)**: [descrição] [traces: all]
- [ ] **REQ-NF2 (Security)**: [descrição] [traces: US-2]
- [ ] **REQ-NF3 (Observability)**: [descrição]

---

## Boundaries

Regras explícitas para o agente (e humanos) durante a implementação:

### ALWAYS
- [Regra inviolável 1 — ex: "usar logging ao invés de print"]
- [Regra inviolável 2 — ex: "respeitar typing com pyright strict"]
- [Regra inviolável 3]

### NEVER
- [Anti-pattern proibido 1 — ex: "nunca commitar secrets"]
- [Anti-pattern proibido 2 — ex: "nunca usar except bare"]
- [Anti-pattern proibido 3]

### ASK
- [Decisão que requer humano 1 — ex: "se surgir trade-off entre performance e legibilidade, perguntar"]
- [Decisão que requer humano 2]

---

## Nível de garantia

O que esta feature vai poder ser **afirmada** como fazendo, quando estiver pronta.
Boundaries restringem o comportamento do agente; esta seção restringe a frase que
o README, o release note e o próximo agente terão direito de escrever.

**Esta feature entrega:** [uma frase, no nível mais baixo que ainda é verdade —
ex: "um registro de auditoria e rastreabilidade", não "reprodução determinística"]

**E não cobre:** [o que alguém razoavelmente assumiria estar incluído e não está]
- [Limite 1 — ex: "o inventário de ambiente é observação, não lockfile: não recria o ambiente"]
- [Limite 2 — ex: "evidência ausente é reportada como indisponível, nunca inferida"]

Cada limite aqui precisa ser um limite do que **foi construído**, não uma lista de
features futuras — isso é roadmap, e vive em outro arquivo.

---

## [NEEDS CLARIFICATION]

Ambiguidades que o AI não conseguiu resolver do contexto. USUÁRIO DEVE RESPONDER antes de continuar:

- [ ] **CLARIF-1**: [Pergunta específica — ex: "Qual é o formato esperado para o payload do webhook?"]
- [ ] **CLARIF-2**: [Pergunta]
- [ ] **CLARIF-3**: [Pergunta]

---

## Suposições

Toda `CLARIF-n` respondida vira uma linha aqui, **e a `CLARIF-n` original fica**
riscada em vez de apagada. O requisito guarda o que fazer; esta seção guarda
**por que ele está correto** — e é o que permite descobrir o que cai quando a
condição deixa de valer.

Formato — uma linha, quatro campos:

`ASSUMPTION-001 · <a condição que se assume verdadeira> · decidido AAAA-MM-DD por <usuário|inferência> · justifica: REQ-004, AC-2`

- **ASSUMPTION-001**: o payload do webhook nunca passa de 1 MB · decidido 2026-08-26 por usuário · justifica: REQ-004, REQ-011, AC-2
- **ASSUMPTION-002**: o serviço de auth já está migrado para v2 em produção · decidido 2026-08-26 por inferência · justifica: REQ-007

Regras:

- **`justifica:` não pode ser vazio.** Suposição que não sustenta nenhum requisito
  não é suposição — é observação, e observação vai para `## Context`.
- **`por inferência` é dívida, não decisão.** Marca a suposição que ninguém
  confirmou; `grill-me` ataca essas primeiro.
- Suposição derrubada depois **não se apaga**: vira
  `~~ASSUMPTION-002~~ FALSA em AAAA-MM-DD` com uma linha do que a substituiu.
  Apagar é como o raio de impacto some.

---

## Success Criteria

Como saberemos que a feature está completa e funcionando:

- [ ] Todos os AC (P1) passando em testes automatizados
- [ ] Cobertura de testes >= 80% para novos código
- [ ] Zero findings críticos em `verify-against-spec`
- [ ] Zero findings críticos em `wf-verify-multimodel` (L2; 5 dimensões + adjudicação)
- [ ] Performance dentro de REQ-NF1
- [ ] Documentação atualizada (README, CLAUDE.md se aplicável)
- [ ] Todos os `[NEEDS CLARIFICATION]` resolvidos
- [ ] Toda `CLARIF-n` resolvida virou uma `ASSUMPTION-nnn` com `justifica:` não-vazio

---

## Spec Metadata (machine-readable)

```json
{
  "spec_id": "[feature-slug]",
  "version": 1,
  "harness_version": "v3",
  "generated_by": "write-spec skill",
  "generated_at": "[ISO timestamp]",
  "priorities": ["P1", "P2", "P3"],
  "requirement_count": 0,
  "user_story_count": 0,
  "needs_clarification_count": 0,
  "assumption_count": 0,
  "assumptions_by_inference": 0
}
```
