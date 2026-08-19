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

## Success Criteria

Como saberemos que a feature está completa e funcionando:

- [ ] Todos os AC (P1) passando em testes automatizados
- [ ] Cobertura de testes >= 80% para novos código
- [ ] Zero findings críticos em `verify-against-spec`
- [ ] Zero findings críticos em review multi-modelo (Claude + Codex + Gemini)
- [ ] Performance dentro de REQ-NF1
- [ ] Documentação atualizada (README, CLAUDE.md se aplicável)
- [ ] Todos os `[NEEDS CLARIFICATION]` resolvidos

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
  "needs_clarification_count": 0
}
```
