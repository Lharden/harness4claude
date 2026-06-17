# Sincronizar o Harness numa nova maquina

Guia para reproduzir **as mesmas configs** (Harness v3 SDD + Obsidian + Graphify)
numa maquina nova, de forma portavel (Windows/Git Bash, macOS, Linux).

> TL;DR: clone o repo, exporte `OBSIDIAN_API_KEY`, rode `scripts/sync-machine.sh`,
> reabra o Claude Code. O script faz auto-merge aditivo com backup timestamped.

---

## O que viaja sozinho vs. o que este kit replica

| Camada | Viaja com o plugin? | Tratado por |
|--------|---------------------|-------------|
| Skills, hooks, scripts, schemas, graph | ✅ `/plugin install` (marketplace) | nada a fazer |
| `state.json` / `signals.json` | ❌ per-maquina (by design) | `init-state.sh` (auto no SessionStart) |
| Seção "Harness v3 SDD" no `~/.claude/CLAUDE.md` | ❌ host-local | `sync-machine.sh` (bloco com marcadores) |
| `env.VAULT_PATH`, `NODE_EXTRA_CA_CERTS`, marketplaces, `enabledPlugins` | ❌ host-local | `sync-machine.sh` (merge em `settings.json`) |
| MCP `obsidian-fs` + `obsidian` | ❌ host-local | `sync-machine.sh` (merge em `~/.claude.json`) |
| API key do Obsidian REST | ❌ segredo | **manual** via `OBSIDIAN_API_KEY` (nunca versionado) |
| Cert do Local REST API, plugins do app Obsidian | ❌ GUI/segredo | **manual** |
| Pacote `graphify` (typo-safe: PyPI `graphifyy`) | ❌ install de pacote | `setup-graphify.sh` (decisao humana) |

---

## Passo a passo

### 1. Pre-requisitos por OS

| Dependencia | Windows | macOS | Linux |
|-------------|---------|-------|-------|
| Python 3.10+ | `winget install Python.Python.3.12` | `brew install python@3.12` | `apt install python3` |
| jq 1.7+ | `winget install jqlang.jq` | `brew install jq` | `apt install jq` |
| Node/npm (mcpvault) | `winget install OpenJS.NodeJS.LTS` | `brew install node` | `apt install nodejs npm` |
| git, bash | nativo / Git Bash | nativo | nativo |

### 2. Clonar o repo no diretorio do marketplace local

```bash
git clone git@github.com:Lharden/harness4claude.git \
  ~/.claude/plugins/local/harness4claude
```

(O proprio `sync-machine.sh` faz isso se voce rodar com `--clone`, que e o default.)

### 3. Definir o vault e o segredo

```bash
export VAULT_PATH="$HOME/Documents/Obsidian Vault"   # raiz do vault (ajuste o path)
export OBSIDIAN_API_KEY="<chave do plugin Local REST API>"
```

- `VAULT_PATH` aponta para a **raiz** do vault. O espelhamento do Harness usa o
  sub-vault `<VAULT_PATH>/AI-Brain` (ou `AI_BRAIN_PATH` se setado).
- `OBSIDIAN_API_KEY` e lido pelo MCP `obsidian` em runtime; **nunca** e escrito em
  disco por este kit. Adicione ambos ao perfil do shell para persistir.

### 4. Rodar o sync (auto-merge + backup)

```bash
cd ~/.claude/plugins/local/harness4claude
bash scripts/sync-machine.sh --dry-run     # previa, nao escreve nada
bash scripts/sync-machine.sh               # aplica
```

O script:
1. Faz backup de `settings.json`, `~/.claude.json` e `CLAUDE.md` (`*.bak-sync-<ts>`).
2. Merge **aditivo** dos snippets em `sync/templates/` (nunca clobbera chave existente;
   placeholders nao resolvidos sao ignorados).
3. Insere/atualiza o bloco `<!-- HARNESS4CLAUDE:BEGIN..END -->` no CLAUDE.md global.
4. Roda `init-state.sh` + `health-check.sh`.

Flags: `--dry-run`, `--no-clone`, `--vault-root <path>`, `--repo <url>`.

### 5. Passos MANDATORIOS (segredo / GUI / install de pacote)

Estes nao podem ser automatizados com seguranca e sao **obrigatorios** — o
`sync-machine.sh` roda um **gate de verificacao no fim** e sai com codigo `!= 0`
se algum faltar (use `--skip-verify` para ignorar conscientemente):

| Item mandatorio | Como satisfazer |
|-----------------|-----------------|
| `OBSIDIAN_API_KEY` exportada | `export OBSIDIAN_API_KEY="<chave>"` no perfil do shell |
| Cert do Local REST API | copiar `obsidian-local-rest-api.crt` para `~/.claude/obsidian-config/` |
| Plugin `superpowers` habilitado | `/plugin install superpowers` (o Harness delega brainstorming/TDD/debug) |

```bash
# Obsidian: instale o plugin "Local REST API" no app, copie o cert acima.
bash scripts/setup-graphify.sh    # instala graphify (PyPI graphifyy) + skill + hook
```

Reinicie o Claude Code para recarregar as configs. Itens nao-bloqueantes (mcpvault
via npx, `/plugin install harness4claude`) sao apenas avisos.

### 6. Verificar

```bash
bash scripts/health-check.sh
python -m pytest tests/ -v
```

Esperado: dependencias OK, `state.json`/`signals.json` presentes, hooks de
classify registrados, skills carregadas.

---

## Idempotencia e seguranca

- Reexecutar o script e seguro: chaves ja presentes nao sao tocadas; o bloco do
  CLAUDE.md e substituido in-place (nao duplica).
- Todo arquivo mexido ganha backup `*.bak-sync-<timestamp>` antes da escrita.
- Segredos nunca entram no repo: a API key fica so em `OBSIDIAN_API_KEY`; o MCP
  referencia `${OBSIDIAN_API_KEY}`.

## Diferencas por maquina que sao esperadas

- `state.json` / `signals.json` divergem entre maquinas (estado de runtime local).
- O grafo `graphify-out/` e regenerado localmente (`graphify .`), nao sincronizado.
- Specs/design/verification vivem em `docs/` de cada projeto e viajam via git do
  projeto — nao deste repo.
