# Spec: P-1.b — Testes Herméticos (`HARNESS_DIR` override)

**Status**: Grilled (round 1) — pronta para `design-doc`
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

**Fronteira de escopo do lado dos testes** — verificada no grill-me (round 1), declarada aqui para evitar retrabalho na revisão:

| Arquivo de teste | N | Situação |
|---|---|---|
| `test_harness.py` | 56 | **único que precisa migrar** — usa backup/restore sobre o diretório real |
| `test_state_lock.py` | 9 | já isolado; **é o modelo a promover** para o conftest |
| `test_record_signal.py` | 9 | já usa `tmp_path` puro |
| `test_build_skills_index.py` | 9 | já isolado, inclusive com embeddings falsos |
| `test_router_golden.py` | 2 | precisa do ambiente real por definição — vira `integration` + `touches_real` |
| `test_compress_memory.py` | 34 | verificado: não toca o diretório de estado — **fora de escopo** |
| `test_context7_trigger.py` | 21 | verificado: não toca o diretório de estado — **fora de escopo** |
| demais | 8 | doctor, workflows, export, vault sync — não tocam o diretório de estado |

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

### US-2: Promover a fixture existente para `conftest.py`, com assert de segurança (Priority: P1) — MVP

**Como** desenvolvedor rodando a suíte
**Quero** que cada classe de teste receba um `HARNESS_DIR` temporário automaticamente, e que a suíte falhe se algum teste escapar para o caminho real
**Para que** um teste novo não possa, por esquecimento, escrever em produção

**Why this priority**: o override sem enforcement volta a degradar no primeiro teste escrito sem atenção. O assert é o que torna a propriedade durável.

> **Achado do grill-me (round 1):** a fixture **já existe** em `tests/test_state_lock.py:38-55` — `harness_dir(tmp_path)` mais o helper `_env()` que injeta `HARNESS_DIR` no ambiente do subprocess. Está validada por 9 testes de concorrência. Esta user story é **promover e generalizar** esse padrão, não criar um novo. Divergir dele seria regressão.

**Independence**: testável por meta-teste — um teste que deliberadamente tenta resolver o path real deve fazer a verificação disparar.

**Acceptance Criteria**:

- **AC-1**: Given a suíte iniciando, When `pytest` roda, Then `HARNESS_DIR` aponta para um diretório temporário e `HOME` do usuário não é modificado.
- **AC-2**: Given um teste que resolva `HARNESS_DIR` para o caminho real **sem** a marca `@pytest.mark.touches_real`, When a suíte roda, Then a execução falha com mensagem explícita identificando o teste infrator.
- **AC-3**: Given a suíte completa executada, When termina, Then o hash SHA-256 de cada arquivo do **conjunto protegido** é idêntico ao de antes da execução. Conjunto protegido: `state.json`, `signals.json`, `.session-files-count`, `trace-current.md`, `traces/**`. Explicitamente **fora** do conjunto: `router/`, `skills-index/`, `graphify-autosetup/` — são cache derivado e log, escritos pela sessão do Claude Code que executa a própria suíte, e verificá-los produziria falha garantida por motivo alheio aos testes.
- **AC-4**: Given duas classes de teste distintas, When ambas rodam na mesma sessão, Then cada uma recebe um diretório próprio e não observa artefatos da outra.
- **AC-5**: Given a suíte executada três vezes consecutivas, When comparados os resultados, Then são idênticos — sem flakiness introduzida por estado residual.
- **AC-6**: Given um teste marcado `@pytest.mark.touches_real`, When a suíte roda, Then ele é permitido a resolver para o caminho real e aparece nomeado no sumário da execução.

**Edge Cases**:
- Teste que invoca hook por `subprocess`: o ambiente do filho precisa herdar o `HARNESS_DIR` da fixture — o helper `_env()` de `test_state_lock.py` já resolve isso.
- Falha no meio da suíte: `tmp_path_factory` retém as três execuções mais recentes (CLARIF-2).

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

- **AC-1**: Given a suíte completa, When executada com o hook de verificação ativo, Then um relatório confirma que nenhum arquivo do conjunto protegido (AC-3 da US-2) mudou de hash.
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
- [ ] **REQ-F4**: `tests/conftest.py` provê fixture autouse com diretório temporário por classe, **generalizando o padrão já existente em `tests/test_state_lock.py:38-55`** (`harness_dir(tmp_path)` + helper `_env()`); `test_state_lock.py` passa a consumir a fixture promovida em vez da local. [traces: US-2]
- [ ] **REQ-F5**: A fixture inclui verificação que falha a suíte se o `HARNESS_DIR` efetivo resolver para o caminho real, com opt-out explícito por `@pytest.mark.touches_real` — declarado no teste e visível no sumário. [traces: US-2]
- [ ] **REQ-F6**: `HarnessTestBase` deixa de fazer backup/restore. [traces: US-3]
- [ ] **REQ-F7**: Meta-teste demonstra que a verificação de segurança dispara quando violada. [traces: US-2, US-4]
- [ ] **REQ-F8**: `HarnessTestBase.setUpClass` cria o diretório temporário quando `HARNESS_DIR` não estiver definida, de modo que o isolamento valha também na execução standalone; o assert de segurança opera nos dois modos. [traces: US-3; resolve CLARIF-1]
- [ ] **REQ-F9**: A fixture pytest usa `tmp_path_factory`; o modo standalone usa `tempfile.mkdtemp()` com remoção apenas em sucesso e caminho impresso no stderr em caso de falha. [traces: US-2; resolve CLARIF-2]
- [ ] **REQ-F10**: `test_router_golden.py` recebe `@pytest.mark.integration` e `@pytest.mark.touches_real`, mantém o `skip` condicional e sai do gate hermético. **Esta task cria `docs/self-reform/claude/TEST_MATRIX.md`** em versão mínima — as duas marcas, os testes que as usam e suas pré-condições — a ser expandida ao longo da Onda 0. [traces: US-2; resolve CLARIF-3]
      *Ajuste do validate-plan (GAP-1):* o requisito referenciava o `TEST_MATRIX.md` como se existisse; ele é entregável da Onda 0 sem dono declarado. Criar a versão mínima aqui custa pouco e remove a dependência pendente.
      *Nota do grill-me (round 1):* a outra metade da CLARIF-3 — tornar `test_build_skills_index.py` hermético — **já está satisfeita no código atual**: ele usa `tmp_path` e `out = str(tmp_path / "idx")` (L115-117, L153-159), inclusive com embeddings falsos. Nenhuma ação necessária ali.
- [ ] **REQ-F11**: `health-check.sh` resolve o diretório por `HARNESS_DIR` e imprime no cabeçalho qual está inspecionando. [traces: US-1; resolve CLARIF-4]
- [ ] **REQ-F12**: Quando `HARNESS_DIR` estiver definida e divergir do default, os hooks registram o caminho resolvido em `debug-classify.log` e o `health-check.sh` emite WARN visível no cabeçalho. [traces: US-1; mitiga o risco introduzido pela própria feature — ver Riscos]
- [ ] **REQ-F13**: Quando `--harness-dir` e `HARNESS_DIR` estiverem ambos definidos e divergirem, a flag prevalece **e** um aviso é emitido no stderr identificando os dois valores. [traces: US-1]

### Non-Functional

- [ ] **REQ-NF1 (Compatibilidade)**: Com `HARNESS_DIR` ausente, o comportamento em runtime é idêntico ao atual — nenhum usuário percebe a mudança. [traces: all]
- [ ] **REQ-NF2 (Segurança de dados)**: Nenhuma execução da suíte altera qualquer arquivo do **conjunto protegido** em `~/.claude/harness/` (definido no AC-3 da US-2). [traces: US-2, US-4]
      *Ajuste do validate-plan (GAP-4):* a redação anterior — "não escreve fora do diretório temporário" — prometia mais do que qualquer mecanismo desta task verifica. O assert cobre a resolução de `HARNESS_DIR`; a checagem de integridade cobre o conjunto protegido. Escritas em outros pontos do `$HOME` (por exemplo `~/.claude/settings.json`) não são detectadas por nenhum dos dois. Requisito redigido no que é de fato verificável; a lacuna fica registrada como limitação conhecida.
- [ ] **REQ-NF3 (Performance)**: Tempo total da suíte não aumenta mais que 10% em relação ao baseline. **Pré-condição**: a primeira ação da fase `tdd` — antes de tocar em qualquer arquivo — é executar a suíte 3× e gravar tempos e variância em `waves/w0-chao-de-fabrica/baseline-suite.json`. Sem isso o requisito é circular, já que o `BASELINE.md` é entregável posterior da mesma onda. [traces: all]
- [ ] **REQ-NF4 (Portabilidade)**: Funciona em Git Bash no Windows, incluindo paths com espaço. [traces: US-1]
- [ ] **REQ-NF5 (Corretude de diagnóstico)** — **transferido para P-1.a**: o bloco de proveniência do `health-check.sh` inspeciona sempre o cache real do plugin, **ignorando** `HARNESS_DIR`. "Qual código roda" e "qual estado" não compartilham variável.
      *Ajuste do validate-plan (GAP-2):* o bloco de proveniência é entregue por P-1.a, que ainda não começou — logo este requisito não é verificável dentro de P-1.b e seria um FAIL permanente no gate. Sai do escopo verificável desta task e entra como **pré-requisito documentado de P-1.a**. P-1.b entrega apenas o comentário-âncora no `health-check.sh` avisando que o futuro bloco não deve usar `HARNESS_DIR` — porque é exatamente o tipo de coisa que alguém "corrige" meses depois sem entender o motivo. [traces: US-1; resolve CLARIF-4; handoff → P-1.a]

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

## [NEEDS CLARIFICATION] — todas resolvidas

Resolvidas por Leonardo em 2026-07-24. Registradas com o racional, porque cada decisão tem consequência de desenho.

- [x] **CLARIF-1 — modo standalone do `test_harness.py`**
  **Decisão: manter, com o tempdir criado no próprio `setUpClass` quando `HARNESS_DIR` não estiver definida.**
  Racional: o invariante nº 3 da estratégia diz que nenhum invariante pode depender de disciplina. Se o isolamento existisse apenas no `conftest.py`, seria propriedade do *caminho de invocação* — rodar o arquivo por qualquer outra via (standalone, runner futuro, crash test que executa direto) perderia a proteção em silêncio. Com a criação no `setUpClass`, o isolamento vira propriedade do arquivo de teste, e o conftest apenas reforça. Consequência: o assert de segurança precisa valer nos dois modos.
  → gera **REQ-F8**

- [x] **CLARIF-2 — retenção do diretório temporário**
  **Decisão: `tmp_path_factory` do pytest, sem lógica própria de limpeza.**
  Racional: já retém as três execuções mais recentes e descarta as anteriores — em falha há o que inspecionar, em uso normal não acumula. No modo standalone, `tempfile.mkdtemp()` com remoção apenas em sucesso e o caminho impresso no stderr em caso de falha.
  → gera **REQ-F9**

- [x] **CLARIF-3 — `skills-index`**
  **Decisão: separar por natureza do teste. O índice é cache derivado, não estado do harness.**
  - `test_build_skills_index.py` testa a *construção*: torna-se hermético, com `HARNESS_DIR` isolado e fixture sintética de 3–5 skills, usando a flag **`--no-embed` já existente**. Rápido, determinístico, sem Ollama.
  - `test_router_golden.py` é o *gate de acurácia* (93,3% contra as 276 skills reais): precisa do artefato real e do Ollama por definição. Mantém o `skip` atual, ganha marca `@pytest.mark.integration`, sai do gate hermético e entra no `TEST_MATRIX.md` como teste de integração com pré-condição declarada.
  Consequência: o gate "suíte verde 3× sem tocar o real" passa a ser honesto, e o golden set continua medido — em outra categoria.
  → gera **REQ-F10**

- [x] **CLARIF-4 — `health-check.sh`**
  **Decisão: respeitar `HARNESS_DIR` e imprimir no cabeçalho qual diretório está sendo inspecionado — com uma exceção.**
  Racional: a partir da Onda 2 haverá store em shadow e canários; diagnosticar ambiente isolado sem editar o script deixa de ser conveniência. O risco de relatório enganoso é eliminado pela linha de cabeçalho.
  **Exceção explícita:** o bloco de **proveniência** introduzido em P-1.a inspeciona sempre o cache real do plugin, ignorando `HARNESS_DIR`. A pergunta dele é *"qual código está rodando"*, não *"qual estado"* — as duas não devem compartilhar a mesma variável, sob pena de bug sutil na P-1.a.
  → gera **REQ-F11** e **REQ-NF5**

---

## Riscos introduzidos por esta feature

Levantado no grill-me (round 1). O plano §4.3 exige registro de riscos de cada mudança; este é específico desta task e será propagado ao `RISK_REGISTER.md` como **R10** ao fim da implementação.

### R10 — `HARNESS_DIR` vazada redireciona o estado de produção em silêncio

**Causa.** Hoje o caminho é hardcoded, o que torna impossível apontar o harness para o lugar errado por acidente. Ao tornar a variável efetiva em runtime, uma definição esquecida em `.bashrc`, herdada de um terminal, ou vazada de uma execução de teste passa a redirecionar as escritas de produção.

**Efeito.** O usuário perde continuidade de estado sem qualquer sinal — o sintoma aparente é "o harness esqueceu a task", e a causa real fica invisível.

**Probabilidade.** Baixa, mas com janela permanente após esta feature.
**Impacto.** Médio — recuperável, porém confuso e demorado de diagnosticar.

**Mitigação.** REQ-F12: os hooks registram o caminho resolvido em `debug-classify.log` sempre que ele divergir do default, e o `health-check.sh` emite WARN visível no cabeçalho. Custo baixo, elimina a classe inteira de confusão.

**Rollback.** Nenhum necessário — o default preserva o comportamento atual.

---

## Success Criteria

- [ ] Todos os AC de P1 (US-1, US-2, US-3) passando em testes automatizados
- [ ] Suíte completa verde **três vezes consecutivas**
- [ ] SHA-256 do **conjunto protegido** (AC-3 da US-2) idêntico antes e depois das três execuções
- [ ] Meta-teste do assert de segurança passando (a verificação dispara quando violada, e a marca `touches_real` a suprime)
- [ ] `baseline-suite.json` gravado **antes** da primeira alteração de arquivo
- [ ] Tempo da suíte dentro de +10% do baseline registrado em `baseline-suite.json`
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
  "requirement_count": 18,
  "user_story_count": 5,
  "needs_clarification_count": 0,
  "needs_clarification_resolved": 4,
  "grilled": true,
  "grill_rounds": 1,
  "grill_findings": 7,
  "scope_reductions": 2,
  "new_risks": ["R10"],
  "risk_refs": ["R2"],
  "plan_refs": ["§5"]
}
```
