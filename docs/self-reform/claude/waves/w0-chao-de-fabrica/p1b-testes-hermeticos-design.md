# Design: P-1.b — Testes Herméticos (`HARNESS_DIR`)

**Status**: Draft
**Created**: 2026-07-24
**Spec**: [`p1b-testes-hermeticos-spec.md`](p1b-testes-hermeticos-spec.md) (grilhada, round 1, 18 requisitos, 0 clarificações abertas)
**Branch**: `self-reform/w0-chao-de-fabrica`

---

## Technical Context

**Stack**: Python 3.12+ stdlib · bash (Git Bash no Windows) · pytest. Nenhuma dependência nova.

**Convenções herdadas do repo** (a seguir, não a reinventar):

| Convenção | Onde já existe |
|---|---|
| `: "${VAR:=default}"` para env com fallback em bash | `scripts/state-lock.sh:21` |
| `VAR="${VAR:-default}"` idem | `hooks/harness-precompact.sh:7` |
| `os.environ.get("X", default)` para override em Python | `hooks/skill_router.py:19` (`HARNESS_SKILLS_INDEX`), `:24` (`HARNESS_OLLAMA_URL`) |
| `--flag` argparse com default computado | `scripts/record_signal.py:106-107` |
| fixture `harness_dir(tmp_path)` + `_env()` para subprocess | `tests/test_state_lock.py:38-55` |
| `cygpath -w` para passar path a Python no Windows | `hooks/harness-reclassify.sh:7-11` |

O desenho abaixo é, em essência, **a propagação uniforme de padrões que o repositório já usa em seis lugares**. A quantidade de invenção é deliberadamente próxima de zero.

**Propriedade central que simplifica tudo**: como o fallback preserva exatamente o comportamento atual (REQ-NF1), **cada arquivo é migrável de forma independente e a suíte permanece verde a cada passo**. Não há big-bang, não há ordem obrigatória entre os arquivos de produção, e cada commit é individualmente revertível.

---

## Architecture

### Três camadas de resolução

O caminho precisa atravessar duas fronteiras de processo. Isso define três camadas, cada uma com seu mecanismo:

```
                    ambiente do processo
                            │
        ┌───────────────────┼───────────────────┐
        │                   │                   │
   ┌────▼─────┐      ┌──────▼──────┐     ┌──────▼──────┐
   │ Camada 1 │      │  Camada 2   │     │  Camada 3   │
   │   bash   │      │python inline│     │python módulo│
   │          │      │ (heredoc)   │     │             │
   ├──────────┤      ├─────────────┤     ├─────────────┤
   │: "${HAR- │ ───► │os.environ   │     │paths.resol- │
   │NESS_DIR:=│export│.get("HAR-   │     │ve() ou      │
   │$HOME/... │      │NESS_DIR",…) │     │argparse     │
   │}"        │      │             │     │default      │
   └──────────┘      └─────────────┘     └─────────────┘
        │                   │                   │
        └───────────────────┴───────────────────┘
                            │
                  ┌─────────▼─────────┐
                  │  diretório único  │
                  │   já resolvido    │
                  └───────────────────┘
```

**Camada 1 — bash** `[traces: REQ-F1]`
Substituir a atribuição direta pelo padrão de `state-lock.sh:21`:

```bash
# antes:  HARNESS_DIR="$HOME/.claude/harness"
: "${HARNESS_DIR:=$HOME/.claude/harness}"
export HARNESS_DIR          # <-- necessário para a Camada 2
mkdir -p "$HARNESS_DIR"
```

O `export` é o que faz a variável atravessar para o Python inline. Sem ele, a Camada 2 não enxerga.

**Camada 2 — Python inline em heredoc** `[traces: REQ-F2]`
Este é o ponto que o levantamento superficial não pega. Em `hooks/harness-classify.sh:56`, o path do log de debug é composto **dentro** do Python, ignorando a variável bash:

```python
# hoje — imune a qualquer mudança no bash acima:
debug = os.path.join(os.path.expanduser('~'), '.claude', 'harness', 'debug-classify.log')

# design:
_hd = os.environ.get('HARNESS_DIR') or os.path.join(os.path.expanduser('~'), '.claude', 'harness')
debug = os.path.join(_hd, 'debug-classify.log')
```

Todo `expanduser('~')` que compõe caminho de estado dentro de heredoc precisa da mesma correção. Auditar por `grep -n "expanduser" hooks/*.sh scripts/*.sh` é parte do trabalho, não opcional.

**Camada 3 — Python como módulo** `[traces: REQ-F3, REQ-F13]`
Trocar o default do argparse, preservando a flag como override de maior precedência:

```python
# antes:
default_dir = Path.home() / ".claude" / "harness"

# design:
def default_harness_dir() -> Path:
    env = os.environ.get("HARNESS_DIR")
    return Path(env).expanduser().resolve() if env else Path.home() / ".claude" / "harness"
```

E, quando ambos existem e divergem (REQ-F13), aviso no stderr — não silêncio:

```python
if env and args.harness_dir != Path(env).expanduser().resolve():
    print(f"aviso: --harness-dir={args.harness_dir} sobrepõe HARNESS_DIR={env}", file=sys.stderr)
```

### Ordem crítica no Windows `[traces: REQ-NF4]`

`hooks/harness-reclassify.sh:7-11` aplica `cygpath -w` sobre o diretório. A resolução da variável **precisa vir antes** da conversão, senão converte-se o default e descarta-se o override:

```bash
: "${HARNESS_DIR:=$HOME/.claude/harness}"     # 1. resolve
export HARNESS_DIR
if command -v cygpath &>/dev/null; then
    HARNESS_DIR_WIN=$(cygpath -w "$HARNESS_DIR")   # 2. só então converte
else
    HARNESS_DIR_WIN="$HARNESS_DIR"
fi
```

### Componente: `tests/conftest.py` — fixture promovida `[traces: REQ-F4, US-2]`

Promoção do padrão de `test_state_lock.py:38-55`, com escopo de classe. `monkeypatch` do pytest é function-scoped, então o escopo de classe exige `pytest.MonkeyPatch()` explícito:

```python
@pytest.fixture(scope="class", autouse=True)
def harness_dir(tmp_path_factory, request):
    """HARNESS_DIR isolado por classe. Promovido de test_state_lock.py."""
    if request.node.get_closest_marker("touches_real"):
        yield None                      # opt-out declarado — REQ-F5
        return
    d = tmp_path_factory.mktemp("harness")
    mp = pytest.MonkeyPatch()
    mp.setenv("HARNESS_DIR", str(d))
    yield d
    mp.undo()
```

`tmp_path_factory` retém as três execuções mais recentes por padrão — atende CLARIF-2 sem código de limpeza `[traces: REQ-F9]`.

`test_state_lock.py` passa a consumir esta fixture e remove a local; o helper `_env()` permanece, pois a injeção explícita no subprocess continua sendo o mecanismo correto — e mais claro que confiar em herança implícita `[traces: REQ-F4]`.

### Componente: assert de segurança `[traces: REQ-F5, REQ-F7, US-2 AC-2/AC-6]`

Hook `pytest_runtest_setup` no `conftest.py`:

```python
REAL = Path.home() / ".claude" / "harness"

def pytest_runtest_setup(item):
    if item.get_closest_marker("touches_real"):
        return                                   # AC-6
    env = os.environ.get("HARNESS_DIR")
    if env and Path(env).resolve() == REAL.resolve():
        pytest.fail(
            f"{item.nodeid} resolveu HARNESS_DIR para o diretório real ({REAL}).\n"
            f"Use a fixture harness_dir, ou declare @pytest.mark.touches_real "
            f"se o teste precisa mesmo do ambiente real.",
            pytrace=False,
        )
```

Marcas registradas em `pytest.ini`/`pyproject` (ou `pytest_configure`): `touches_real`, `integration`.

Falha por padrão, escape possível mas **nunca acidental** — a marca precisa ser escrita no teste e aparece no sumário.

### Componente: verificação de integridade `[traces: REQ-F7, US-4]`

Duas formas, porque servem a propósitos diferentes:

1. **`tests/test_hermeticity.py`** — fixture session-scoped que faz hash do conjunto protegido no início e no fim. Cobre o uso cotidiano.
2. **`scripts/check_hermeticity.py`** — script independente (`--snapshot` / `--verify`), usado pelo gate das três execuções. Necessário porque uma suíte interrompida nunca executa o teardown do teste session-scoped, e o gate precisa de verificação externa e confiável.

**Conjunto protegido** (AC-3 da US-2) — decisão de escopo, não detalhe de implementação:

```python
PROTECTED = ["state.json", "signals.json", ".session-files-count",
             "trace-current.md", "traces/**"]
# fora: router/, skills-index/, graphify-autosetup/
```

O que está fora é cache derivado e log, escrito pela sessão do Claude Code que executa a própria suíte. Incluí-los tornaria o gate um falso positivo garantido — foi o achado nº 1 do grill-me.

### Componente: aviso de divergência `[traces: REQ-F12, R10]`

Mitiga o risco que a própria feature cria. Em `harness-classify.sh`, após a resolução:

```bash
if [ "$HARNESS_DIR" != "$HOME/.claude/harness" ]; then
    printf 'HARNESS_DIR override ativo: %s\n' "$HARNESS_DIR" >> "$HARNESS_DIR/debug-classify.log"
fi
```

E em `health-check.sh`, no cabeçalho `[traces: REQ-F11]`:

```bash
: "${HARNESS_DIR:=$HOME/.claude/harness}"
echo "Inspecionando: $HARNESS_DIR"
[ "$HARNESS_DIR" != "$HOME/.claude/harness" ] && echo "WARN: HARNESS_DIR override ativo (default: $HOME/.claude/harness)"
```

**Exceção do bloco de proveniência** `[traces: REQ-NF5]`: o bloco que P-1.a adiciona inspeciona sempre o cache real do plugin, ignorando `HARNESS_DIR`. Ele responde *"qual código roda"*, não *"qual estado"* — compartilhar a variável seria bug sutil. Vale registrar em comentário no próprio script, porque é o tipo de coisa que alguém "corrige" seis meses depois.

---

## Data Model

Não há entidades novas. O que o design introduz é uma **hierarquia de precedência** com quatro níveis:

| Nível | Fonte | Precedência | Observação |
|---|---|---|---|
| 1 | `--harness-dir` (CLI) | maior | avisa no stderr se divergir do nível 2 (REQ-F13) |
| 2 | `HARNESS_DIR` (env) | — | vazia é tratada como ausente |
| 3 | `~/.claude/harness` | menor | preserva comportamento atual (REQ-NF1) |
| — | `HARNESS_SKILLS_INDEX` | ortogonal | override independente, **já existente** em `skill_router.py:19` |

**Invariantes:**

- **INV-1**: Sem `HARNESS_DIR` no ambiente, o caminho resolvido é sempre `~/.claude/harness` — em todas as três camadas. É o que garante REQ-NF1.
- **INV-2**: Todo caminho resolvido é absoluto (`.resolve()` em Python; `mkdir -p` aceita relativo mas o log registra o absoluto).
- **INV-3**: A resolução ocorre **antes** de qualquer conversão de path (`cygpath`).
- **INV-4**: Nenhum componente compõe caminho de estado a partir de `$HOME` ou `expanduser('~')` depois desta mudança — a auditoria por grep é critério de conclusão, não sugestão.

---

## API Contracts

### Contrato público: `HARNESS_DIR` `[traces: US-1, US-5]`

```
Nome:      HARNESS_DIR
Tipo:      caminho de diretório (absoluto ou relativo)
Default:   ~/.claude/harness
Semântica: raiz de todo estado runtime do harness
Vazia:     tratada como não definida
Criação:   o diretório é criado se não existir (mesma semântica de bootstrap de hoje)
Escopo:    NÃO cobre skills-index (ver HARNESS_SKILLS_INDEX) nem o cache do plugin
```

### Marcas pytest

```python
@pytest.mark.touches_real   # opt-out do assert; teste pode usar o diretório real
@pytest.mark.integration    # requer ambiente externo (Ollama, índice real); fora do gate hermético
```

### `scripts/check_hermeticity.py`

```
--snapshot <arquivo>   grava hashes do conjunto protegido
--verify <arquivo>     compara com o snapshot; exit 1 e lista de diferenças se divergir
--harness-dir DIR      default: HARNESS_DIR ou ~/.claude/harness
```

---

## Test Strategy

| Camada | Cobre | Como |
|---|---|---|
| **Unit** | resolução de path em Python | `default_harness_dir()` com env definida, ausente, vazia, relativa; precedência flag>env com captura do aviso `[REQ-F13]` |
| **Integration (subprocess)** | Camadas 1 e 2 | executar cada hook com `HARNESS_DIR` apontando para tmp e verificar onde o arquivo apareceu `[AC-2 US-1]`; caso especial: `debug-classify.log` com JSON malformado, para exercitar o caminho de erro da Camada 2 |
| **Meta-teste** | o próprio assert | teste que resolve para o real **sem** a marca deve falhar; **com** a marca deve passar `[REQ-F7, AC-6]` |
| **Regressão** | os 56 testes migrados | mesmos nomes, mesmas asserções — mudança de semântica é motivo de parada (boundary ASK) `[AC-2 US-3]` |
| **Integridade** | conjunto protegido | snapshot antes / verify depois, nas 3 execuções do gate `[AC-3 US-2]` |
| **Portabilidade** | Windows | path com espaço e com acento; ordem resolve→cygpath `[REQ-NF4]` |

**Edge cases que precisam de teste explícito** (derivados dos Edge Cases da spec):

- `HARNESS_DIR=""` → tratada como ausente
- `HARNESS_DIR` relativa → resolvida para absoluta
- `HARNESS_DIR` inexistente → criada, hook conclui exit 0
- duas classes na mesma sessão → diretórios distintos `[AC-4 US-2]`
- modo standalone sem `HARNESS_DIR` → `setUpClass` cria tmpdir próprio `[REQ-F8]`

**Baseline antes de tudo** `[traces: REQ-NF3]`: a primeira ação da fase `tdd`, antes de qualquer edição, é `pytest tests/` 3× com tempos e variância gravados em `baseline-suite.json`. Sem isso, o requisito de +10% é circular.

---

## Risks

| Risco | Probabilidade | Impacto | Mitigação |
|---|---|---|---|
| **R10 — `HARNESS_DIR` vazada redireciona produção em silêncio** (criado por esta feature) | baixa | médio | REQ-F12: log do path resolvido + WARN no health-check. Propagar ao `RISK_REGISTER.md` ao concluir. |
| `expanduser('~')` remanescente em heredoc não auditado | **média** | alto | INV-4 como critério de conclusão: `grep -n "expanduser" hooks/*.sh scripts/*.sh` deve ter zero ocorrências compondo caminho de estado |
| Ordem resolve→cygpath invertida em algum hook | baixa | médio | teste de portabilidade com path contendo espaço |
| Fixture class-scoped conflitar com `unittest.TestCase` | **média** | médio | `test_harness.py` usa `unittest`; fixtures autouse funcionam, mas o valor não é injetável por argumento. Daí `setUpClass` ler de `os.environ` — o que também é o que faz REQ-F8 funcionar no modo standalone. Dois problemas, uma solução. |
| Suíte fica mais lenta por criar tmpdir por classe | baixa | baixo | medido contra `baseline-suite.json`; orçamento de +10% |
| Migração alterar semântica de teste sem perceber | baixa | alto | boundary ASK da spec: parar e reportar, nunca ajustar asserção |

---

## Open Questions

Nenhuma. As quatro clarificações foram resolvidas antes do grill-me, e os sete achados do grill-me round 1 estão incorporados à spec e refletidos aqui.

---

## Phases

Organizadas por prioridade de user story, conforme o princípio do design-doc.

### Dependência de ordem entre as fases `[ajuste do validate-plan, GAP-3]`

As fases **não** são livremente ordenáveis, ao contrário do que a redação anterior sugeria. A ordem `1 → 2 → 3` é obrigatória:

```
Fase 1 (hooks respeitam HARNESS_DIR)
   │  sem ela, os hooks invocados por subprocess continuam escrevendo no
   │  diretório real — e o assert da Fase 2 NÃO detecta, porque ele inspeciona
   │  a variável do processo pytest, não o destino real da escrita do filho
   ▼
Fase 2 (fixture + assert)
   │  a Fase 3 depende da fixture promovida para o conftest
   ▼
Fase 3 (migração do test_harness.py)
```

O ponto sutil: entre as fases 2 e 1, o `AC-2` da US-3 ("os 56 testes passam") seria satisfeito com os hooks ainda escrevendo em produção. Verde enganoso. Só a verificação de integridade da Fase 4 pegaria — tarde demais.

**Dentro** da Fase 1 a ordem entre os 13 arquivos permanece livre: o fallback preserva o comportamento, então cada arquivo é migrável e revertível isoladamente.

### Fase 1 — US-1 (P1): resolução por `HARNESS_DIR`

Camadas 1, 2 e 3 nos 13 arquivos de produção. Ordem livre entre eles — o fallback garante compatibilidade a cada passo. Auditoria de `expanduser` como critério de conclusão (INV-4).
`[traces: REQ-F1, F2, F3, F12, F13, NF1, NF4]`

### Fase 2 — US-2 (P1): fixture promovida + assert

`conftest.py` recebe a fixture de `test_state_lock.py`, o hook `pytest_runtest_setup`, as duas marcas, e o meta-teste. `test_state_lock.py` passa a consumir a fixture promovida. `test_router_golden.py` recebe as marcas. Cria-se `TEST_MATRIX.md` em versão mínima.
`[traces: REQ-F4, F5, F7, F9, F10]`

### Fase 3 — US-3 (P1): migração do `test_harness.py`

`HarnessTestBase` troca backup/restore por leitura de `os.environ`, com criação de tmpdir próprio quando a variável estiver ausente (modo standalone). Os 56 testes preservam nomes e asserções.
`[traces: REQ-F6, F8]`

**Gate do MVP**: fases 1–3 completas, suíte verde 3×, conjunto protegido íntegro.

### Fase 4 — US-4 (P2): verificação de integridade

`test_hermeticity.py` e `scripts/check_hermeticity.py`.
`[traces: REQ-F7]`

### Fase 5 — US-5 (P3): documentação do contrato

`HARNESS_DIR` documentada. **Nota de fronteira**: a boundary NEVER da spec proíbe tocar documentação nesta task — esta fase entrega apenas a seção do contrato em `docs/`, e as correções de README ficam com P-1.d.
`[traces: US-5]`

---

## Validation Report

- **Status**: PASS (after revision 1)
- **Validado em**: 2026-07-24
- **Artefatos conferidos**: spec (18 REQs, 5 US), design (5 componentes, 4 invariantes, 5 fases), `docs/CONTEXT.md`

### Cobertura de requisitos

Todos os 18 requisitos mapeiam para pelo menos uma fase e um componente. Matriz:

| Fase | Requisitos cobertos |
|---|---|
| 1 | REQ-F1, F2, F3, F11, F12, F13, NF1, NF4 |
| 2 | REQ-F4, F5, F7, F9, F10 |
| 3 | REQ-F6, F8 |
| 4 | REQ-F7 (integridade), NF2 |
| 5 | US-5 |
| Test Strategy | REQ-NF3 (com pré-condição `baseline-suite.json`) |
| **transferido** | REQ-NF5 → P-1.a |

`docs/CONTEXT.md` verificado: escopo é a integração Graphify (L1–L4 Locked, D1–D5 Discretion, DF1–DF3 Deferred). **Nenhuma decisão Locked impacta P-1.b, e nenhum item Deferred aparece no escopo** — sem scope leak.

### Gaps encontrados e corrigidos

| # | Gap | Correção aplicada |
|---|---|---|
| **GAP-1** | REQ-F10 referenciava `TEST_MATRIX.md` como se existisse; é entregável da Onda 0 sem dono declarado | Criar versão mínima nesta task (Fase 2), expandida ao longo da onda |
| **GAP-2** | REQ-NF5 depende do bloco de proveniência, entregue por P-1.a — seria FAIL permanente no gate de P-1.b | Transferido para P-1.a como pré-requisito documentado; P-1.b entrega só o comentário-âncora no `health-check.sh` |
| **GAP-3** | Design declarava as fases "independentemente verificáveis", mas a ordem 1→2→3 é obrigatória: sem a Fase 1, os 56 testes passariam com os hooks ainda escrevendo em produção — **verde enganoso** que só a Fase 4 detectaria | Dependência de ordem declarada explicitamente, com o mecanismo do falso-verde documentado |
| **GAP-4** | REQ-NF2 prometia "nenhuma escrita fora do tmp", mais amplo do que qualquer mecanismo da task verifica | Redigido no verificável (conjunto protegido); lacuna registrada como limitação conhecida |

### Integridade de dependências

- Sem dependências circulares. Cadeia única: Fase 1 → Fase 2 → Fase 3 → (4, 5).
- Dentro da Fase 1, os 13 arquivos são independentes entre si — propriedade do fallback compatível.
- Dependência externa declarada: `baseline-suite.json` precisa existir **antes** da primeira alteração de arquivo (REQ-NF3).

### Completude técnica

- Sem mudança de schema, endpoint ou API pública — nada a migrar ou versionar.
- Contrato público novo (`HARNESS_DIR`) tem documentação prevista na Fase 5.
- Novas marcas pytest têm registro previsto em `pytest_configure`.

### Viabilidade

- Zero dependências novas; tudo em stdlib, pytest e bash já presentes.
- Nenhuma fase é "faça tudo": a maior (Fase 1) são 13 edições mecânicas do mesmo padrão, verificáveis uma a uma.

### Revisões aplicadas

Quatro correções pontuais em spec e design (GAP-1 a GAP-4). Nenhuma reescrita estrutural — a arquitetura passou sem alteração.

---

## Design Metadata

```json
{
  "design_id": "p1b-testes-hermeticos",
  "spec_ref": "p1b-testes-hermeticos-spec.md",
  "version": 1,
  "harness_version": "v3",
  "wave": "w0-chao-de-fabrica",
  "generated_at": "2026-07-24",
  "components": 5,
  "invariants": 4,
  "phases": 5,
  "open_questions": 0,
  "new_dependencies": 0,
  "risks": 6
}
```
