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
| `arsenal.py` | Registry das ferramentas **ativas**: contrato, reconciliação com o disco, orçamento de tokens do roster e colisão de gatilho | `python tools/arsenal.py budget --report` |

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
