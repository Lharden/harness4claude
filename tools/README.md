# tools/ — Camada técnica do vault Obsidian

Tooling de sincronização, backup e manutenção do **vault Obsidian**, mantido aqui
(e **não** dentro do vault) para que o vault permaneça **universal — só notas**.
Como o `scripts/sync-machine.sh` já clona este repo em toda máquina, este tooling
chega de graça em qualquer lugar.

> Fronteira de arquitetura (3 camadas):
> - **harness4claude repo** = configs técnicas/código (inclui este `tools/`).
> - **Vault Obsidian** = universal, só notas (notas de projeto + configs do app).
>   Sobre o harness, o vault guarda apenas **notas de funcionamento e decisões**.
> - **Obsidian Sync** = integração viva entre máquinas (notas + configs + plugins).

Todos operam sobre a **raiz do vault** via `--root`. Aponte para o seu vault
(ou exporte `VAULT_PATH` e use `--root "$VAULT_PATH"`):

| Script | O que faz | Uso |
|--------|-----------|-----|
| `vault_sync_doctor.py` | Valida prontidão de sync (plugins, .gitignore, git aninhado, REST) | `python -m tools.vault_sync_doctor --root "$VAULT_PATH" --check-rest` |
| `export_plugins.py` | Gera `vault-plugins.lock.json` (lista+manifests leve, sem binários) | `python -m tools.export_plugins --root "$VAULT_PATH" --out vault-plugins.lock.json` |
| `vault_maintenance.py` | Auditoria/manutenção conservadora das notas Markdown | `python -m tools.vault_maintenance --root "$VAULT_PATH"` |
| `graph_lint.py` | Health check do knowledge graph: integridade referencial (erro) e caracteristica de uso (aviso) | `python tools/graph_lint.py --report` |
| `impact.py` | Raio de impacto de mudança **não commitada**, sobre o grafo do graphify | `python tools/impact.py --report` |
| `design_scope.py` | Qual design doc governa um caminho, via `applies_to` declarado no front matter | `python tools/design_scope.py --changed --explain` |
| `arsenal.py` | Registry das ferramentas **ativas**: contrato, reconciliação com o disco, orçamento de tokens do roster e colisão de gatilho | `python tools/arsenal.py budget --report` |
| `orfaos.py` | Guarda de órfão: função pública sem caminho até uma raiz que o host execute. Reprova o que não estiver declarado em `orfaos.json` | `python tools/orfaos.py --report` |
| `migrar_inbox_rotulado.py` | Migração única: renomeia as notas diárias legadas `raw/inbox/today-*.md` para `<repo>--today-*.md`, o nome que o `vault_sync` grava desde 2026-09-24. Ensaio por padrão; `--aplicar` exige `--backup` fora do vault; `--restaurar` desfaz. Rodar depois de instalar o plugin novo, em cada máquina | `python tools/migrar_inbox_rotulado.py --vault "$VAULT_PATH/AI-Brain" --repo <dir> [--repo <dir> ...]` |

**Esta tabela tem sete linhas, e `tools/` tem dezoito arquivos.** `wiki_lint.py` e
`wiki_moc.py` — 17 funções, 31 testes — não estão aqui, não estão no
`health-check.sh`, não estão em nenhuma `SKILL.md`, e nada as chama. `wiki_lint` é
citado em 12 docstrings deste diretório como *o contrato que os outros herdam*:
`graph_lint`, `arsenal`, `compendium`, `impact` e `design_scope` dizem seguir o
formato dele, e nenhum o invoca.

Elas **não** foram acrescentadas à tabela ao serem descobertas, e isso é
deliberado. Escrever a linha aqui as tornaria ferramentas de mão declaradas — e
quem decide que uma peça esquecida vira ferramenta declarada é quem mantém o
repositório, não quem passou medindo. Enquanto ninguém decide, elas estão em
`orfaos.json` com `categoria: ORFAO` e o motivo escrito, que é onde dívida com
nome pertence. Consertar no mesmo gesto em que se mede apaga o que foi medido.

**Compêndio e arsenal são irmãos, e a diferença importa:** um verbete do compêndio
é inerte e custa zero token por sessão; uma skill instalada é ativa, cobra ~93
tokens em *toda* sessão e muda como o agente decide. Por isso o compêndio não tem
teto e o arsenal tem. O arsenal guarda **apenas julgamento** — por que entrou,
com que limite, como sair; todo fato (versão, custo, uso, se está habilitado) é
lido do disco na hora, e `check` reprova se um campo mensurável aparecer no TOML.

Rode como módulo (`python -m tools.X`) a partir da raiz do repo, ou passe `--root`
explícito. Testes em `../tests/test_vault_*.py` e `../tests/test_export_plugins.py`.

**Por que o lock em vez de versionar binários:** os `main.js` dos plugins somam
~52 MB e não fazem diff. O lock (~6 KB) captura o CONJUNTO (id, versão, autor,
enabled) para reinstalar os mesmos plugins via community store numa máquina nova.

**Sobre o `impact.py` e o que ele se recusa a dizer:** o grafo do graphify é
não-direcionado, então a saída é **vizinhança**, não dependência — chamar de
"quem depende" seria inventar causalidade a partir de adjacência. Ele também não
atravessa hub (medido: com a barreira, 1 arquivo afetado; sem ela, 511) e não
devolve "sem impacto" para arquivo que o grafo não conhece — devolve "não sei",
que é diferente e é o ponto.

**`design_scope.py` e `impact.py` respondem perguntas diferentes, e a diferença é
a natureza do fato.** O `impact.py` pergunta *o que minha mudança afeta*, e a
vizinhança é **medida** no grafo. O `design_scope.py` pergunta *que norma governa
este caminho*, e o escopo é **declarado** por quem escreveu a spec. Um glob largo
demais casa com o repositório inteiro e devolve resposta verdadeira e inútil — daí
o `--explain`, que mostra qual padrão casou com qual alvo. Ele também não adivinha
`applies_to`: documento sem front matter é reportado como governando nada, porque
inferir escopo do conteúdo produziria roteamento plausível e errado.
