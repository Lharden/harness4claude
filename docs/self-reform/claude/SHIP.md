---
title: Procedimento de Ship e Atualização entre Máquinas
document_type: runbook
status: active
created: 2026-07-25
---

# Ship e atualização entre máquinas

O Claude Code carrega o plugin do **cache** (`~/.claude/plugins/cache/harness4claude/...`), não do clone de trabalho. Editar o clone não muda o que roda. Este é o procedimento que fecha essa distância.

## Por que existe

Durante semanas, o runtime executou a versão `3.2.0 @ 24c1812` enquanto `main` estava doze commits à frente com o skill-router — **mergeado e inativo**. Nenhum diagnóstico apontava isso. O bloco de proveniência do `health-check.sh` existe para que não se repita.

## Ship (máquina de desenvolvimento)

```bash
# 1. Confirme que a suíte está verde no worktree
python -m pytest tests/ -q

# 2. Merge para main e publique
git checkout main
git merge --no-ff self-reform/w0-chao-de-fabrica
git push origin main

# 3. Atualize o plugin nesta máquina
#    (no Claude Code, comando interativo)
/plugin update harness4claude

# 4. Verifique a proveniência
bash scripts/health-check.sh
```

O passo 4 deve mostrar:

```
--- Proveniencia (qual codigo esta rodando) ---
         plugin.json (arvore atual): 3.3.0
         instalado (Claude Code):    3.3.0
[OK]     versao coerente entre arvore local e plugin carregado
```

Se mostrar `DIVERGENCIA`, o `/plugin update` não pegou — reinicie o Claude Code e repita.

## Atualização em outra máquina (desktop / mainframe)

Pré-requisito: o push já foi feito na máquina de origem.

```bash
# 1. Atualize o plugin (no Claude Code do desktop)
/plugin update harness4claude

# 2. Reinicie o Claude Code
#    O hook SessionStart faz a migração de state v2->v3 automaticamente
#    e grava o resolvedor de plugin root.

# 3. Verifique
bash "$(cat ~/.claude/harness/plugin-root)/scripts/health-check.sh"
```

### O que a atualização preserva

O estado existente **não é perdido**: `HARNESS_DIR` mantém o default `~/.claude/harness`, e `migrate_state.py` faz backup antes de qualquer migração de schema. Uma task ativa continua ativa.

### Se o `plugin-root` ainda não existir

Ele é criado no primeiro `SessionStart` após a atualização. Antes disso, use o caminho do cache diretamente:

```bash
bash ~/.claude/plugins/cache/harness4claude/harness4claude/*/scripts/health-check.sh
```

## Máquina nova (instalação limpa)

```bash
git clone git@github.com:Lharden/harness4claude.git ~/.claude/plugins/local/harness4claude
bash ~/.claude/plugins/local/harness4claude/scripts/sync-machine.sh
```

O `sync-machine.sh` mescla de forma aditiva, com backup timestamped: a seção do `CLAUDE.md` global, o `settings.json` e os MCP servers do Obsidian em `~/.claude.json`. Nunca escreve segredo — a `OBSIDIAN_API_KEY` fica como referência de ambiente.

Depois, registre e instale o plugin:

```bash
claude plugin marketplace add ~/.claude/plugins/local
claude plugin install harness4claude@local
```

## Skill-router: o que a máquina precisa

O router tem duas camadas. A **Camada A** (regex sobre nomes e aliases) não depende de nada externo. A **Camada B** (embeddings) precisa de Ollama.

```bash
# Instalar Ollama: https://ollama.com/download
ollama pull nomic-embed-text-v2-moe

# Construir o índice (uma vez; depois o SessionStart mantém atualizado)
python "$(cat ~/.claude/harness/plugin-root)/scripts/build_skills_index.py"
```

**Sem Ollama o harness funciona.** O router degrada para a Camada A, e o `health-check` reporta `WARN`, não `FAIL`. A Camada B só dispara quando a Camada A não encontra nada.

### Modelo configurável

```bash
export HARNESS_EMBED_MODEL="outro-modelo-de-embedding"
```

O default é `nomic-embed-text-v2-moe` (dim 768). Trocar o modelo **invalida o índice** — reconstrua com `build_skills_index.py`. Qualquer GPU moderna dá conta: o gargalo medido é latência de chamada (~1,4 s p95), não computação.

## Variáveis de ambiente

| Variável | Default | Papel |
|---|---|---|
| `HARNESS_DIR` | `~/.claude/harness` | raiz do estado — ver [`docs/HARNESS_DIR.md`](../../HARNESS_DIR.md) |
| `HARNESS_EMBED_MODEL` | `nomic-embed-text-v2-moe` | modelo de embedding do router |
| `HARNESS_OLLAMA_URL` | `http://localhost:11434` | endpoint do Ollama |
| `HARNESS_SKILLS_INDEX` | `$HARNESS_DIR/skills-index` | override do índice |
| `HARNESS_SKIP_DEPCHECK` | — | pula o `pip install` de primeira execução (uso em teste) |
| `VAULT_PATH` / `AI_BRAIN_PATH` | — | vault do Obsidian |
| `OBSIDIAN_API_KEY` | — | REST API do Obsidian; **nunca** commitada |

## Rollback

```bash
# Voltar o plugin para a versão anterior
/plugin update harness4claude@<versao-anterior>

# Ou, em último caso, restaurar o estado
cp docs/self-reform/claude/backups/<data>/* ~/.claude/harness/
```

Nunca use `git reset --hard` ou `git push --force` — são proibições explícitas do plano (§3.2), e o git-guard bloqueia ambos.
