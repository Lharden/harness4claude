# Trocar de máquina: o estado do vault_sync

Complementa [`SYNC.md`](SYNC.md), que cobre plugin, configs e segredos. Este guia cobre o que o `SYNC.md` não cobre: o estado que o `vault_sync` guarda fora do vault e as notas que alimentam o `raw/inbox`. Escrito em 2026-09-24, quando havia uma máquina só.

Serve para dois casos: **substituir** a máquina principal, ou passar a rodar o harness **em duas máquinas** sobre o mesmo vault (sincronizado pelo Obsidian Sync).

## O que o vault_sync guarda, e onde

| O quê | Onde | Viaja sozinho? | Sem ele na máquina nova |
|---|---|---|---|
| Manifesto de escritas | `~/.claude/harness/vault-sync-manifest.json` | não (por máquina) | Página igual à fonte é adotada sem reescrita. Nota apagada do inbox volta no próximo PreCompact; nota movida para `_processed/` não volta. |
| Sementes de ramo | `~/.claude/harness/projects/*/branches/*.seed.md` | não | As já espelhadas continuam em `wiki/branches/`; as outras se perdem. |
| Notas diárias | `<repo>/.remember/today-*.md` | não (`.remember` é ignorado pelo git) | As páginas já no vault ficam; deixam de ser atualizadas. |
| Log do sync | `~/.claude/harness/logs/vault-sync.log` | não | Nada: é diagnóstico local. |
| Páginas do vault | `AI-Brain/` | sim, pelo Obsidian Sync | — |

## Antes de sair da máquina antiga

1. Confirme que a migração do inbox já foi aplicada. O ensaio abaixo tem de mostrar `renomear ... : 0` nas duas linhas. Se mostrar renomes, aplique antes de sair (`--aplicar --backup <pasta fora do vault>`).

   ```bash
   python tools/migrar_inbox_rotulado.py --vault "$VAULT_PATH/AI-Brain" --repo <repo> [--repo <repo> ...]
   ```

2. Copie para a máquina nova, se quiser continuidade: o manifesto, `~/.claude/harness/projects/*/branches/` e a pasta `.remember/` de cada repo. O resto de `~/.claude/harness/` é estado de runtime e se recria sozinho.

## Na máquina nova, nesta ordem

1. Siga o [`SYNC.md`](SYNC.md) até o fim.
2. **Instale o plugin a partir do `main` antes da primeira sessão que compactar.** Um plugin anterior a `fix/vault-sync-fontes` grava a nota diária com o nome sem rótulo (`today-X.md`) e recria o legado que a migração tirou. Confira no `bash scripts/health-check.sh`, seção "Proveniencia": nenhum FAIL.
3. **Clone os repos com o mesmo nome de pasta.** O rótulo da nota diária é o nome da pasta que contém o `.remember` (`slb-mestrado-projeto--today-X.md`). Clonar `slb-mestrado-projeto` como `slb` produz `slb--today-X.md`, uma página nova ao lado da antiga.
4. **O manifesto só vale se os caminhos forem os mesmos.** Ele é chaveado pelo caminho absoluto da página e da fonte. Com outro usuário do Windows, outra pasta do vault ou outra pasta de projetos, as chaves não batem, e o efeito é o de manifesto ausente (tabela acima). Copiá-lo nesse caso não faz mal, mas também não ajuda.
5. Rode o ensaio da migração (o comando acima) com os repos desta máquina. Esperado: `renomear ... : 0`. Se aparecer renome, alguma sessão rodou com o plugin antigo depois da migração: aplique com backup.
6. Depois do primeiro PreCompact, leia `~/.claude/harness/logs/vault-sync.log`. Recusa ("recusada: ... difere do que o sync escreveria") quer dizer página no vault diferente da fonte desta máquina. O sync não sobrescreve; a decisão é sua, e a mensagem diz as duas saídas.
7. Rode a suíte do harness com o vault isolado: `AI_BRAIN_PATH=<pasta temporária> python -m pytest -q`. Em 2026-09-24, três testes (`test_27`, `test_30`, `test_hook_liveness`) rodavam o PreCompact sem isolar o vault e, com `VAULT_PATH` definido, miravam o AI-Brain real. O conserto de raiz foi aberto numa sessão separada; confira se ele entrou antes de dispensar a variável.

## Se as duas máquinas rodarem ao mesmo tempo

- **Mova para `_processed/`, não apague.** O manifesto é de cada máquina: nota apagada do inbox numa máquina volta pela outra. `raw/inbox/_processed/` está no vault e vale nas duas.
- **Instale o plugin novo nas duas antes de qualquer migração.** Uma máquina com o plugin antigo desfaz a migração da outra.
- **`C:/.remember` tem o mesmo rótulo nas duas** (`maquina--today-X.md`). Notas globais do mesmo dia colidem, e vence a mais nova, como era antes do rótulo nos repos. Se as duas máquinas escreverem em `C:/.remember`, a decisão de 2026-09-24 precisa ser reaberta, com um rótulo por máquina.
- O mesmo repo nas duas máquinas, no mesmo caminho, dá o mesmo rótulo e a mesma origem no manifesto: as duas escrevem a mesma página, e vence a fonte mais nova. Página que uma máquina escreveu e a outra não tem registro é adotada quando igual; quando diferente e a fonte for mais nova, é recusada com aviso no log.

## Referências

- Comportamento do sync: docstring de [`scripts/vault_sync.py`](../scripts/vault_sync.py).
- Decisões e medições de 2026-09-24: [`specs/vault-sync-fontes-verification.md`](specs/vault-sync-fontes-verification.md) e [`specs/vault-sync-recusa-por-hash-verification.md`](specs/vault-sync-recusa-por-hash-verification.md).
- A ferramenta de migração: [`tools/migrar_inbox_rotulado.py`](../tools/migrar_inbox_rotulado.py).
