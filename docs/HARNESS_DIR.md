# `HARNESS_DIR` — contrato do diretório de estado

Variável de ambiente que define onde o Harness4Claude guarda seu estado de runtime. Introduzida pela task P-1.b da autorreforma.

## Contrato

| | |
|---|---|
| **Nome** | `HARNESS_DIR` |
| **Tipo** | caminho de diretório, absoluto ou relativo |
| **Default** | `~/.claude/harness` |
| **String vazia** | tratada como não definida |
| **Diretório inexistente** | criado automaticamente |
| **Precedência** | `--harness-dir` (CLI) > `HARNESS_DIR` (env) > default |

Quando a flag e a variável divergem, a flag vence **e um aviso vai para o stderr** — divergência silenciosa esconde bug.

## O que a variável cobre

Todo o estado de runtime: `state.json`, `signals.json`, `.session-files-count`, `trace-current.md`, `traces/`, `router/`, `skills-index/`, `graphify-autosetup/`, `debug-classify.log`.

## O que ela **não** cobre

- **O código do plugin.** Resolvido por `CLAUDE_PLUGIN_ROOT`. São perguntas diferentes: `HARNESS_DIR` responde *"onde fica o estado"*, `CLAUDE_PLUGIN_ROOT` responde *"qual código roda"*. O bloco de proveniência do `health-check.sh` inspeciona sempre o cache real do plugin, ignorando `HARNESS_DIR` — de propósito.
- **O índice de skills**, que tem override próprio e ortogonal: `HARNESS_SKILLS_INDEX`.
- **O vault do Obsidian**: `VAULT_PATH` / `AI_BRAIN_PATH`.

## Usos legítimos

**Testes herméticos.** A suíte define um `HARNESS_DIR` temporário por classe, e falha se algum teste resolver para o diretório real sem declarar `@pytest.mark.touches_real`. Antes disso, rodar `pytest` sobrescrevia o `state.json` de uma sessão do Claude Code em andamento.

**Diagnóstico de ambiente isolado.** `HARNESS_DIR=/tmp/h bash scripts/health-check.sh` inspeciona o diretório indicado. O script imprime qual está examinando e emite `WARN` quando difere do default.

**Perfis paralelos.** Instâncias com estado separado na mesma máquina.

## Cuidado — o risco que a variável cria

Antes desta feature o caminho era fixo, o que tornava impossível apontar o harness para o lugar errado por acidente. Agora, uma `HARNESS_DIR` esquecida no `.bashrc`, herdada de um terminal, ou vazada de uma execução de teste **redireciona o estado de produção**. O sintoma aparente é "o harness esqueceu a task", e a causa fica invisível.

Por isso o override nunca é silencioso:

- `harness-classify.sh` registra o caminho resolvido em `debug-classify.log` sempre que difere do default;
- `health-check.sh` imprime `Inspecionando: <dir>` e emite `WARN` no cabeçalho.

Se o harness parecer ter perdido o estado, `echo $HARNESS_DIR` é a primeira verificação.

## Variável relacionada

`HARNESS_SKIP_DEPCHECK=1` pula o dep-check de primeira execução do `harness-session-start.sh`. Existe porque, com um `HARNESS_DIR` temporário, o flag `.bootstrap-done` nunca existe — e cada invocação em teste dispararia um `pip install --user`. É um recurso de teste, não de produção.
