---
name: compress-memory
description: "Comprime arquivos de memória secundários (recent.md, archive.md, today-*.md) removendo filler/hedging e mantendo conteúdo técnico intacto. Inspirado no caveman-compress, mas local e com blacklist crítica para nunca tocar em specs, CLAUDE.md, MEMORY.md ou core-memories.md. Reduz tokens de input em sessões longas. Cria backup .original.md antes de sobrescrever."
category: utility
risk: low
source: custom
date_added: "2026-04-27"
metadata:
  version: 1
  triggers: compress memory, compress-memory, comprimir memoria, reduzir tokens memoria, compactar memory file
---

# Compress Memory — Compressão Segura de Arquivos de Memória

Skill utilitária que reduz tamanho de arquivos de memória secundários sem perder informação técnica. Baseada na observação do projeto [caveman](https://github.com/JuliusBrussee/caveman) de que filler/hedging consome ~46% do conteúdo de memory files típicos.

## Quando ativar

- Arquivos de memória secundários cresceram além de ~1500 linhas
- Sessões longas estão atingindo limites de contexto por causa de memory bloat
- Usuário pediu explicitamente para comprimir um arquivo de memória
- Antes de iniciar pipeline L2 que vai consumir muito contexto

**NÃO ativar para:**

- `docs/specs/**` (specs formais — perda de Given/When/Then quebra `verify-against-spec`)
- `CLAUDE.md` em qualquer escopo (instruções operacionais críticas)
- `MEMORY.md` (índice — formato fixo de uma linha por entrada)
- `core-memories.md` (decisões de longo prazo)
- `README.md`, `plugin.json`, `.claude-plugin/**`
- Arquivos com frontmatter YAML do plugin system
- Qualquer arquivo que contenha `Given/When/Then`, `[NEEDS CLARIFICATION]`, ou `REQ-###`

## Como rodar

```bash
python "$(cat "${HARNESS_DIR:-$HOME/.claude/harness}/plugin-root")/skills/compress-memory/compress.py" <arquivo>
# ou com opções:
python compress.py recent.md --dry-run        # preview, não escreve
python compress.py recent.md --no-backup      # pula backup (não recomendado)
python compress.py recent.md --stats          # só imprime estatísticas
```

## O que é preservado (NUNCA comprimido)

- Code blocks (```...```) e inline code (`...`)
- URLs (http://, https://)
- File paths (qualquer string com `/` ou `\` consecutivos)
- Headings Markdown (`#`, `##`, etc.)
- Frontmatter YAML (entre `---`)
- Números, datas, versões (`v1.2.3`, `2026-04-10`)
- Comandos shell (linhas iniciando com `$`)
- Flags CLI (`--flag`, `-f`)
- Tabelas Markdown (linhas com `|`)
- Listas (`-`, `*`, `1.`)

## O que é removido (filler/hedging)

**Português BR:**
- `basicamente`, `simplesmente`, `na verdade`, `de fato`, `realmente`, `apenas`, `talvez`
- `eu acho que`, `eu acredito que`, `acho que`, `me parece que`
- `por favor`, `se você puder`, `se não for incômodo`
- Leading: `Então,`, `Bom,`, `Bem,`, `Agora,`, `Olha,`

**Inglês:**
- `basically`, `simply`, `actually`, `really`, `just`, `perhaps`, `kind of`, `sort of`
- `I think`, `I believe`, `it might`, `could potentially`, `it seems`
- `please`, `if you don't mind`, `would you mind`
- Leading: `So,`, `Well,`, `Now,`, `Look,`

## Garantias

1. **Sempre cria backup** `.original.md` antes de sobrescrever (a menos que `--no-backup`)
2. **Blacklist absoluta** — falha imediatamente se arquivo está na lista crítica
3. **Detecção de spec** — falha se conteúdo tem Given/When/Then ou `[NEEDS CLARIFICATION]`
4. **Idempotente** — rodar 2x não comprime além do primeiro pass
5. **Reversível** — `mv arquivo.original.md arquivo.md`

## Output

```
[compress-memory] Input: recent.md (1245 lines, 8932 chars)
[compress-memory] Backup: recent.original.md
[compress-memory] Output: recent.md (1198 lines, 5843 chars)
[compress-memory] Saved: 34.6% (3089 chars removed)
```
