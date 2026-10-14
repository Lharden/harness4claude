---
name: security-scan-python
description: Varredura de segurança on-demand para código Python usando bandit (SAST) e pip-audit (CVEs de dependências). Use quando o usuário pedir "scan de segurança", "rodar bandit", "verificar vulnerabilidades", "security scan", "auditar dependências" ou antes de commitar/publicar código Python sensível. Grátis, sem token, sem conta, acionado só quando necessário — substitui hooks de SAST que exigem login.
---

# Security Scan (Python) — bandit + pip-audit

Varredura de segurança local, grátis e sem login. `bandit` faz SAST do código;
`pip-audit` checa CVEs nas dependências instaladas/lockfile.

Os dois rodam por `uvx`, cada um num ambiente efêmero do uv, com a versão fixada
abaixo; nada fica instalado no site do usuário nem no do sistema. Trocar de versão é
mudar o número nos comandos desta skill, num commit.

- `bandit` 1.9.4: `uvx --from bandit==1.9.4 bandit`
- `pip-audit` 2.10.1: `uvx --from pip-audit==2.10.1 pip-audit`

## Quando usar

- Pedido explícito de scan/auditoria de segurança.
- Antes de commit/PR de código que lida com input externo, subprocess, deserialização, crypto, SQL, secrets.
- Após implementar feature com superfície de ataque.

## Como rodar

### 1. Verificar disponibilidade
```bash
command -v uvx || echo "falta o uv (https://docs.astral.sh/uv/): os scanners rodam por uvx"
```

### 2. SAST com bandit (alvo = diretório do projeto)
Reporta apenas severidade média+ e confiança média+ (reduz ruído), pula venvs e testes:
```bash
uvx --from bandit==1.9.4 bandit -r . -ll -ii \
  --exclude './.venv,./venv,./tests,./.git,./node_modules' \
  -f screen
```
- `-ll` = só MEDIUM/HIGH severity · `-ii` = só MEDIUM/HIGH confidence.
- Para relatório completo (inclui LOW), remova `-ll -ii`.
- Saída estruturada para parsing: adicione `-f json -o bandit-report.json`.

Escopo reduzido (só o que mudou no branch):
```bash
git diff --name-only --diff-filter=ACM main...HEAD | grep '\.py$' | xargs -r uvx --from bandit==1.9.4 bandit -ll -ii
```

### 3. CVEs de dependências (pip-audit)
O `pip-audit` roda no ambiente efêmero do `uvx`; "o ambiente atual" seria o dele, não o
do projeto. Aponte sempre o alvo:
```bash
# projeto com uv.lock: as versoes travadas, exportadas para um arquivo temporario
uv export --frozen --format requirements.txt --no-hashes -o "${TMPDIR:-/tmp}/req-audit.txt"
uvx --from pip-audit==2.10.1 pip-audit -r "${TMPDIR:-/tmp}/req-audit.txt"

# projeto com requirements.txt
uvx --from pip-audit==2.10.1 pip-audit -r requirements.txt

# um venv ja criado (os pacotes instalados nele)
uvx --from pip-audit==2.10.1 pip-audit --path .venv/Lib/site-packages   # Windows; fora dele: .venv/lib/python3.X/site-packages
```

## Como reportar

Resuma por severidade. Para cada achado relevante (MEDIUM+):
- **Regra** (ex.: B602 subprocess shell=True), **arquivo:linha**, **severidade/confiança**.
- Risco em 1 frase + correção concreta.
- Falsos-positivos legítimos: sugira `# nosec BXXX` com justificativa, nunca silencie em massa.

Priorize: injeção (shell/SQL), deserialização insegura (pickle/yaml.load), secrets hardcoded,
crypto fraca (md5/sha1 para segurança), `subprocess` com `shell=True`, `requests` sem verificação TLS.

## Limites

- bandit é heurístico (AST patterns) — complementa, não substitui, revisão humana.
- Para auditoria mais profunda (STRIDE/OWASP/red-team), use a skill `autoresearch:security` ou `/security-review`.
- Não cobre lógica de negócio nem authz — só padrões de código inseguro conhecidos.
