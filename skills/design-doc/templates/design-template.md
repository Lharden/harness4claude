---
applies_to:
  - [glob relativo à raiz, ex: src/auth/**]
  - [outro glob, ex: src/middleware/session.py]
---

# Design: [FEATURE NAME]

**Spec Link**: [path relativo para spec.md]
**Status**: Draft
**Created**: [YYYY-MM-DD]
**Author**: AI-generated, reviewed by Leonardo

---

## Technical Context

**Language**: Python [versão]
**Framework**: [nome + versão]
**Key Dependencies**:
- [dep 1]: [versão] — [por que]
- [dep 2]: [versão] — [por que]

**Storage**: [db/cache/files]
**Testing**: pytest [versão]
**Type Check**: pyright [modo]
**Lint**: ruff

**Constraints**:
- Performance: [target]
- Memory: [limite]
- Latency: [target]

---

## Architecture

[Descrição da abordagem técnica — 2-4 parágrafos]

### High-Level Diagram (ASCII)

```
[Usuário] --> [API] --> [Service] --> [Repository] --> [DB]
                  |
                  v
              [Logger]
```

### Key Components

1. **[Component 1]** (`path/to/component1.py`)
   - Responsibility: [o que faz]
   - Interface: [métodos públicos]
   - Dependencies: [outros components]
   - [traces: REQ-F1, US-1]

2. **[Component 2]** (`path/to/component2.py`)
   - Responsibility:
   - Interface:
   - Dependencies:
   - [traces: REQ-F2, US-1]

---

## Data Model

### Entities

#### [Entity1]
```python
@dataclass
class Entity1:
    id: int
    name: str
    created_at: datetime
    # ...
```

**Relations**:
- [Entity1] 1:N [Entity2]
- [Entity1] N:1 [User]

**Indexes**:
- `idx_entity1_created_at` (performance)

---

## API Contracts

### Endpoint 1: `POST /api/v1/resource`

**Request**:
```json
{
  "field1": "string",
  "field2": 123
}
```

**Response (201)**:
```json
{
  "id": 1,
  "field1": "string",
  "created_at": "2026-04-10T00:00:00Z"
}
```

**Errors**:
- `400`: Bad request (validation)
- `401`: Unauthorized
- `404`: Not found
- `500`: Server error

**Traces**: US-1, REQ-F1, REQ-F2

---

## Implementation Phases

### Phase 1: Foundation (blocking prerequisites)
- [ ] Setup diretórios
- [ ] Criar types/interfaces
- [ ] Configurar dependências

### Phase 2: Core (MVP — US-1)
- [ ] [Componente 1]
- [ ] [Componente 2]
- [ ] Tests para US-1

### Phase 3: Extension (US-2)
- [ ] [Componente 3]
- [ ] Tests para US-2

### Phase 4: Nice-to-have (US-3)
- [ ] [Componente 4]
- [ ] Tests para US-3

---

## Test Strategy

### Unit Tests
- [Component 1]: test methods X, Y, Z
- [Component 2]: test edge cases A, B

### Integration Tests
- [Flow 1]: end-to-end teste do US-1
- [Flow 2]: end-to-end teste do US-2

### Contract Tests
- API Endpoint 1: schema validation

### Performance Tests
- [Scenario]: deve completar em <X ms

### Coverage Target
- Mínimo: 80% em src/[modulo]
- Crítico: 95% em src/[modulo/core]

---

## Risks & Mitigations

| Risk | Impact | Probability | Mitigation |
|------|--------|-------------|-----------|
| [Risco 1] | High/Med/Low | High/Med/Low | [estratégia] |
| [Risco 2] | | | |

---

## Open Questions

Se houver decisões de design ainda em aberto (não necessariamente da spec):

- [ ] [pergunta técnica 1]
- [ ] [pergunta técnica 2]
