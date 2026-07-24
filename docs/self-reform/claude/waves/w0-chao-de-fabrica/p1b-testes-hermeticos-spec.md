# Spec: P-1.b — Testes Herméticos (`HARNESS_DIR` override)

**Status**: Draft — aguardando resolução de `[NEEDS CLARIFICATION]`
**Created**: 2026-07-24
**Updated**: 2026-07-24
**Branch**: `self-reform/w0-chao-de-fabrica` (a criar a partir de `self-reform/main`)
**Author**: AI-generated, reviewed by Leonardo
**Onda**: 0 — Chão de fábrica
**Plano**: `PLANO_AUTOREFORMA_HARNESS4CLAUDE.md` §5 (Fase 0) · `STRATEGY.md` §4 P-1.b · risco **R2**

---

## Executive Summary

A suíte de testes do harness executa contra o diretório de estado **real** do usuário (`~/.claude/harness/`), protegida apenas por um backup/restore best-effort em `setUpClass`. Isso significa que rodar `pytest` sobrescreve o `state.json` de produção — inclusive o de uma sessão do Claude Code em andamento.

Esta feature introduz a variável de ambiente `HARNESS_DIR` como ponto único de resolução do diretório de estado, propaga-a a todos os pontos que hoje hardcodam o caminho, e substitui o backup/restore por uma fixture pytest que cria um diretório temporário por classe de teste — com um **assert de segurança** que faz a suíte falhar se qualquer teste resolver para o caminho real.

O valor imediato é eliminar o risco de corrupção durante a reforma. O valor estrutural é maior: sem isolamento não existe crash-injection (Fase 3), stress test (Fase 10), nem baseline reprodutível (Fase 0). P-1.b é pré-condição técnica de três fases do plano.

## Context

### Como funciona hoje

`tests/test_harness.py` define `HARNESS_DIR = os.path.join(HOME, ".claude", "harness")` (L39) e `write_state()` escreve direto em `STATE_FILE` (L74-77). A classe `HarnessTestBase` (L111-142) copia `state.json`, `.session-files-count` e `trace-current.md` para um `tempfile.mkdtemp()` em `setUpClass` e restaura em `tearDownClass`.

Os 56 testes desse arquivo invocam hooks reais por `subprocess` (`run_hook`, L54-71), que por sua vez resolvem o próprio caminho de estado — ou seja, o isolamento precisa atravessar a fronteira de processo via ambiente, não apenas via variável Python.

### O padrão já existe, mas apenas em dois lugares

Dois arquivos **já** implementam exatamente o mecanismo desejado:

- `scripts/state-lock.sh:21` — `: "${HARNESS_DIR:=$HOME/.claude/harness}"`
- `hooks/harness-precompact.sh:7` — `HARNESS_DIR="${HARNESS_DIR:-$HOME/.claude/harness}"`

E três scripts Python já aceitam o diretório como argumento de CLI, com default hardcoded:

- `scripts/record_signal.py:106-107` — `default_dir = Path.home() / ".claude" / "harness"`
- `scripts/migrate_state.py:178` · `scripts/vault_sync.py:98` — recebem `harness_dir` como parâmetro

A feature é, portanto, **propagação de um padrão existente**, não invenção de um novo.

### Files/Modules impactados

Hardcodam o caminho e precisam mudar:

| Arquivo | Linha(s) | Observação |
|---|---|---|
| `hooks/harness-classify.sh` | 14, 56 | L56 é o path do `debug-classify.log`, dentro do Python inline |
| `hooks/harness-session-start.sh` | 10 | |
| `hooks/harness-reclassify.sh` | 6 | |
| `hooks/harness-graphify-autosetup.sh` | 48 | marker dir |
| `hooks/harness-router-warmup.sh` | 8 | índice de skills |
| `hooks/harness-skill-router.sh` | 8, 9 | dir do router e log de erro do shim |
| `hooks/skill_router.py` | 18 | |
| `scripts/init-state.sh` | 8 | |
| `scripts/health-check.sh` | 15, 55, 56, 92, 114, 155 | quatro delas dentro de `python -c` inline |
| `scripts/build_skills_index.py` | 23 | `HOME` usado para compor o dir do índice |
| `scripts/record_signal.py` | 106 | trocar o default do argparse |
| `scripts/migrate_state.py` | ~209 | idem |
| `tests/test_harness.py` | 39 | alvo principal |
| `tests/conftest.py` | — | ganha a fixture |

Já conformes (referência de padrão): `scripts/state-lock.sh`, `hooks/harness-precompact.sh`.

Fora de escopo (documentação, tratada em P-1.d): `README.md`, `skills/**/SKILL.md`, `sync/templates/`.

### Dependências

- **pytest** — já em `requirements.txt`; a fixture usa apenas `tmp_path_factory` e `monkeypatch`, nada novo.
- **`HARNESS_PLUGIN_ROOT`** — mecanismo análogo já existente em `tests/conftest.py`, que serve de modelo direto de implementação.
- **Nenhuma dependência nova.**

---

## User Stories

### US-1: Isolamento por variável de ambiente (Priority: P1) — MVP

**Como** desenvolvedor do harness
**Quero** que todo componente resolva o diretório de estado a partir de `HARNESS_DIR`, com fallback para `~/.claude/harness`
**Para que** testes e ferramentas possam operar sobre um diretório descartável sem tocar o estado de produção

**Why this priority**: sem isto, nada mais nesta spec é possível. É a mudança que atravessa a fronteira de processo.

**Independence**: testável isoladamente — basta exportar `HARNESS_DIR` e verificar que o hook escreve no destino indicado.

**Acceptance Criteria**:

- **AC-1**: Given `HARNESS_DIR` não definida, When qualquer hook ou script é executado, Then o diretório resolvido é `~/.claude/harness` — comportamento idêntico ao atual.
- **AC-2**: Given `HARNESS_DIR=/tmp/h-test` definida no ambiente, When `harness-classify.sh` recebe um prompt, Then `state.json` é criado em `/tmp/h-test/` e `~/.claude/harness/state.json` permanece byte-a-byte inalterado.
- **AC-3**: Given `HARNESS_DIR` apontando para diretório inexistente, When um hook executa, Then o diretório é criado (mesma semântica de bootstrap de hoje) e o hook conclui com exit 0.
- **AC-4**: Given `HARNESS_DIR` com caminho contendo espaço ou acento, When um hook executa no Git Bash do Windows, Then a resolução funciona e nenhum path é truncado.
- **AC-5**: Given `record_signal.py` invocado sem `--harness-dir` e com `HARNESS_DIR` definida, When registra um sinal, Then grava em `$HARNESS_DIR/signals.json`; e `--harness-dir` explícito continua tendo precedência sobre a variável.

**Edge Cases**:
- Path relativo em `HARNESS_DIR` — deve ser resolvido para absoluto antes do uso.
- `HARNESS_DIR` definida como string vazia — tratar como não definida.
- Python inline dentro de heredoc bash: a variável precisa chegar ao subprocesso Python, não apenas ao bash.

---

### US-2: Fixture hermética com assert de segurança (Priority: P1) — MVP

**Como** desenvolvedor rodando a suíte
**Quero** que cada classe de teste receba um `HARNESS_DIR` temporário automaticamente, e que a suíte falhe se algum teste escapar para o caminho real
**Para que** um teste novo não possa, por esquecimento, escrever em produção

**Why this priority**: o override sem enforcement volta a degradar no primeiro teste escrito sem atenção. O assert é o que torna a propriedade durável.

**Independence**: testável por meta-teste — um teste que deliberadamente tenta resolver o path real deve fazer a verificação disparar.

**Acceptance Criteria**:

- **AC-1**: Given a suíte iniciando, When `pytest` roda, Then `HARNESS_DIR` aponta para um diretório temporário e `HOME` do usuário não é modificado.
- **AC-2**: Given um teste hipotético que resolva `HARNESS_DIR` para `~/.claude/harness`, When a suíte roda, Then a execução falha com mensagem explícita identificando o teste infrator.
- **AC-3**: Given a suíte completa executada, When termina, Then o hash SHA-256 de cada arquivo em `~/.claude/harness/` é idêntico ao de antes da execução.
- **AC-4**: Given duas classes de teste distintas, When ambas rodam na mesma sessão, Then cada uma recebe um diretório próprio e não observa artefatos da outra.
- **AC-5**: Given a suíte executada três vezes consecutivas, When comparados os resultados, Then são idênticos — sem flakiness introduzida por estado residual.

**Edge Cases**:
- Teste que invoca hook por `subprocess`: o ambiente do filho precisa herdar o `HARNESS_DIR` da fixture.
- Falha no meio da suíte: o diretório temporário fica para inspeção ou é removido? Ver `[CLARIF-2]`.

---

### US-3: Migração do `test_harness.py` (Priority: P1) — MVP

**Como** mantenedor da suíte
**Quero** que `HarnessTestBase` use a fixture em vez de backup/restore
**Para que** o mecanismo frágil que hoje protege produção deixe de existir

**Why this priority**: é o consumidor que motiva a feature; sem a migração, o risco R2 permanece aberto.

**Acceptance Criteria**:

- **AC-1**: Given `test_harness.py` migrado, When a suíte roda, Then `setUpClass`/`tearDownClass` não contêm mais lógica de backup/restore de `~/.claude/harness`.
- **AC-2**: Given os 56 testes do arquivo, When executados após a migração, Then todos passam, com os mesmos nomes e as mesmas asserções de comportamento.
- **AC-3**: Given `run_hook()` invocando um hook, When o subprocess é criado, Then o `HARNESS_DIR` temporário está presente no ambiente do filho.

**Edge Cases**:
- `test_harness.py` também é executável standalone (`python test_harness.py`, documentado no docstring L7-10) — fora do pytest, a fixture não se aplica. Ver `[CLARIF-1]`.

---

### US-4: Verificação de integridade como teste (Priority: P2)

**Como** responsável pela reforma
**Quero** um teste que compare o hash do diretório real antes e depois da suíte
**Para que** a propriedade do AC-3 da US-2 seja verificada por código, e não por inspeção manual

**Why this priority**: o assert da US-2 previne o caso conhecido (resolução para o path real); este teste detecta vazamentos por caminhos não previstos. Importante, mas o MVP se sustenta sem ele.

**Acceptance Criteria**:

- **AC-1**: Given a suíte completa, When executada com o hook de verificação ativo, Then um relatório confirma que nenhum arquivo em `~/.claude/harness/` mudou de hash.
- **AC-2**: Given um teste que deliberadamente escreva no diretório real, When a verificação roda, Then ela falha nomeando o arquivo alterado.

---

### US-5: `HARNESS_DIR` documentada como contrato público (Priority: P3)

**Como** usuário do harness em outra máquina
**Quero** poder apontar o harness para um diretório alternativo
**Para que** cenários como múltiplos perfis ou sandbox de avaliação sejam possíveis sem editar código

**Acceptance Criteria**:

- **AC-1**: Given o README atualizado, When alguém procura por configuração de ambiente, Then encontra `HARNESS_DIR` com semântica e default documentados.

---

## Current System

### Entry points

- `pytest tests/` — via `tests/conftest.py`, que hoje só define `HARNESS_PLUGIN_ROOT`.
- `python tests/test_harness.py` — execução standalone por `unittest`, sem passar pelo conftest.
- Hooks em runtime — invocados pelo Claude Code com `bash "${CLAUDE_PLUGIN_ROOT}/hooks/..."`, sem `HARNESS_DIR` no ambiente.

### Data flow atual

```
pytest → HarnessTestBase.setUpClass
           └─ copia state.json, .session-files-count, trace-current.md → tempdir
         teste → write_state() → ESCREVE EM ~/.claude/harness/state.json  ← produção
               → run_hook() → subprocess bash → hook resolve $HOME/.claude/harness
         tearDownClass → restaura do tempdir  ← best-effort; falha silenciosa se o teste crashar
```

### Constraints existentes

- Hooks são bash com Python em heredoc; a variável precisa atravessar duas fronteiras.
- Windows/Git Bash é a plataforma primária: `cygpath`, `MSYS_NO_PATHCONV`, separadores mistos.
- `state-lock.sh` já lê `HARNESS_DIR` — o lock passará a operar no diretório temporário automaticamente, o que é o comportamento desejado.
- A suíte leva ~231 s; a mudança não pode piorar isso materialmente.

---

## Requirements

### Functional

- [ ] **REQ-F1**: Todo componente resolve o diretório de estado por `HARNESS_DIR`, com fallback `~/.claude/harness`, usando o padrão já presente em `state-lock.sh:21`. [traces: US-1]
- [ ] **REQ-F2**: A variável atravessa a fronteira bash → Python inline (heredoc) e bash → subprocess. [traces: US-1, US-3]
- [ ] **REQ-F3**: Scripts com `--harness-dir` passam a usar `HARNESS_DIR` como default, mantendo a flag como override de maior precedência. [traces: US-1]
- [ ] **REQ-F4**: `tests/conftest.py` provê fixture autouse com diretório temporário por classe. [traces: US-2]
- [ ] **REQ-F5**: A fixture inclui verificação que falha a suíte se o `HARNESS_DIR` efetivo resolver para o caminho real. [traces: US-2]
- [ ] **REQ-F6**: `HarnessTestBase` deixa de fazer backup/restore. [traces: US-3]
- [ ] **REQ-F7**: Meta-teste demonstra que a verificação de segurança dispara quando violada. [traces: US-2, US-4]

### Non-Functional

- [ ] **REQ-NF1 (Compatibilidade)**: Com `HARNESS_DIR` ausente, o comportamento em runtime é idêntico ao atual — nenhum usuário percebe a mudança. [traces: all]
- [ ] **REQ-NF2 (Segurança de dados)**: Nenhum caminho de execução da suíte escreve fora do diretório temporário. [traces: US-2, US-4]
- [ ] **REQ-NF3 (Performance)**: Tempo total da suíte não aumenta mais que 10% em relação ao baseline medido antes da mudança. [traces: all]
- [ ] **REQ-NF4 (Portabilidade)**: Funciona em Git Bash no Windows, incluindo paths com espaço. [traces: US-1]

---

## Boundaries

### ALWAYS

- Usar o padrão exato de `scripts/state-lock.sh:21` (`: "${HARNESS_DIR:=$HOME/.claude/harness}"`) nos arquivos bash — consistência com o que já existe.
- Resolver `HARNESS_DIR` para caminho absoluto antes de usar.
- Propagar a variável explicitamente ao criar subprocessos em testes.
- Rodar a suíte completa três vezes antes de considerar a task concluída (gate P-1.b).
- Verificar o hash de `~/.claude/harness/` antes e depois de cada execução de validação.

### NEVER

- Nunca alterar o comportamento default (sem a variável) — isso quebraria produção silenciosamente.
- Nunca remover o suporte a `--harness-dir` dos scripts que já o expõem.
- Nunca fazer a fixture escrever, mover ou apagar qualquer coisa em `~/.claude/harness/`.
- Nunca introduzir dependência nova para esta task.
- Nunca tocar em arquivos de documentação nesta task — isso é P-1.d, e misturar as duas dificulta o review.

### ASK

- Se algum hook precisar de estado que legitimamente não pode ser isolado (ex.: índice de skills caro de reconstruir), perguntar antes de decidir entre isolar, compartilhar em modo somente-leitura, ou marcar o teste como `skip`.
- Se a migração de `test_harness.py` exigir mudar a semântica de algum teste (e não apenas o caminho), parar e reportar — mudança de asserção não faz parte desta task.

---

## [NEEDS CLARIFICATION]

- [ ] **CLARIF-1**: `test_harness.py` documenta execução standalone (`python test_harness.py`, docstring L7-10), que não passa pelo `conftest.py`. Três opções: **(a)** manter o modo standalone criando o tempdir também no `setUpClass` quando `HARNESS_DIR` não estiver definida; **(b)** abandonar o modo standalone e exigir pytest, atualizando o docstring; **(c)** fazer o standalone falhar com mensagem explícita instruindo a usar pytest. Qual?
- [ ] **CLARIF-2**: Quando um teste falha, o diretório temporário deve ser **preservado para inspeção** (útil em depuração, mas acumula lixo em `/tmp`) ou **removido sempre**? Sugestão: preservar apenas em falha, via `tmp_path_factory` do pytest, que já mantém as últimas três execuções.
- [ ] **CLARIF-3**: `scripts/build_skills_index.py` grava o índice de skills (276 entradas, embeddings f16) em `~/.claude/harness/skills-index/`. Reconstruí-lo por teste é caro e depende do Ollama. Isolar também (testes passam a usar índice sintético pequeno), ou tratar o índice como recurso externo somente-leitura, fora do escopo do `HARNESS_DIR`? Isso afeta `test_build_skills_index.py` e `test_router_golden.py`.
- [ ] **CLARIF-4**: `scripts/health-check.sh` é ferramenta de diagnóstico do ambiente real do usuário. Ele deve respeitar `HARNESS_DIR` (útil para diagnosticar um ambiente isolado) ou permanecer sempre apontado para o real (evita relatório enganoso)? Sugestão: respeitar a variável e imprimir qual diretório está inspecionando.

---

## Success Criteria

- [ ] Todos os AC de P1 (US-1, US-2, US-3) passando em testes automatizados
- [ ] Suíte completa verde **três vezes consecutivas**
- [ ] SHA-256 de todos os arquivos em `~/.claude/harness/` idêntico antes e depois das três execuções
- [ ] Meta-teste do assert de segurança passando (a verificação dispara quando violada)
- [ ] Tempo da suíte dentro de +10% do baseline pré-mudança
- [ ] Zero findings críticos em `verify-against-spec`
- [ ] Zero findings críticos/altos em `wf-verify-multimodel`
- [ ] Todos os `[NEEDS CLARIFICATION]` resolvidos
- [ ] `MIGRATION_LOG.md` atualizado com o resultado

---

## Spec Metadata

```json
{
  "spec_id": "p1b-testes-hermeticos",
  "version": 1,
  "harness_version": "v3",
  "wave": "w0-chao-de-fabrica",
  "generated_by": "write-spec skill",
  "generated_at": "2026-07-24",
  "priorities": ["P1", "P2", "P3"],
  "requirement_count": 11,
  "user_story_count": 5,
  "needs_clarification_count": 4,
  "risk_refs": ["R2"],
  "plan_refs": ["§5"]
}
```
