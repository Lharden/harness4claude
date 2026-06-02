---
name: security-scan-python
description: Varredura de segurança on-demand para código Python usando bandit (SAST) e pip-audit (CVEs de dependências). Use quando o usuário pedir "scan de segurança", "rodar bandit", "verificar vulnerabilidades", "security scan", "auditar dependências" ou antes de commitar/publicar código Python sensível. Grátis, sem token, sem conta, acionado só quando necessário — substitui hooks de SAST que exigem login.
---

# Security Scan (Python) — bandit + pip-audit

Varredura de segurança local, grátis e sem login. `bandit` faz SAST do código;
`pip-audit` checa CVEs nas dependências instaladas/lockfile.

Ambos são instalados automaticamente no bootstrap do Harness (SessionStart →
`pip install --user -r requirements.txt`). Se faltarem nesta máquina, instale:
`pip install --user bandit pip-audit` (ou `uv tool install bandit && uv tool install pip-audit`).

## Quando usar

- Pedido explícito de scan/auditoria de segurança.
- Antes de commit/PR de código que lida com input externo, subprocess, deserialização, crypto, SQL, secrets.
- Após implementar feature com superfície de ataque.

## Como rodar

### 1. Verificar disponibilidade
```bash
command -v bandit || echo "instale: pip install --user bandit"
```

### 2. SAST com bandit (alvo = diretório do projeto)
Reporta apenas severidade média+ e confiança média+ (reduz ruído), pula venvs e testes:
```bash
bandit -r . -ll -ii \
  --exclude './.venv,./venv,./tests,./.git,./node_modules' \
  -f screen
```
- `-ll` = só MEDIUM/HIGH severity · `-ii` = só MEDIUM/HIGH confidence.
- Para relatório completo (inclui LOW), remova `-ll -ii`.
- Saída estruturada para parsing: adicione `-f json -o bandit-report.json`.

Escopo reduzido (só o que mudou no branch):
```bash
git diff --name-only --diff-filter=ACM main...HEAD | grep '\.py$' | xargs -r bandit -ll -ii
```

### 3. CVEs de dependências (pip-audit)
```bash
pip-audit                          # ambiente atual
pip-audit -r requirements.txt      # a partir de um requirements
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
