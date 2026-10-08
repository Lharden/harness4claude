# Spec-light — git-guard decide dentro do prazo, e não passa o que não leu

Task: `t-20261007-180451660248` (L2-bug). Diagnóstico medido:
`docs/specs/git-guard-prazo-sob-carga-diagnostico.md`.
Estado: **grilled** (1 rodada, `wf-grill` 5/5 lentes, 49 perguntas, 8 bloqueantes;
decisões do usuário em 2026-10-07).

## Objetivo

O guarda de operações git destrutivas tem de decidir dentro do `timeout` de
10 s mesmo com a máquina carregada, e não pode deixar passar um comando
destrutivo porque não o leu — nem por prazo, nem por parser, nem por opção
global do git, nem por ter sido rodado pela ferramenta PowerShell.

## Medidas que mudaram o desenho (grill, 2026-10-07)

- Dos 1 112 avisos "não conseguiu analisar" de produção, **973 a política lê
  em processo**: a falha era a passagem do comando como argumento
  (`--command "$COMMAND"`) do bash para o Python nativo, com aspas e quebras
  de linha. Nenhum dos 1 112 tinha texto destrutivo.
- No corpus de 64 317 comandos (57 938 Bash, 6 379 PowerShell), o parser não lê
  296. O fallback abaixo, rodado sobre eles: 0 bloqueios, 290 decididos (289 allow, 1 warn), 6
  continuam ilegíveis. Nas sondas destrutivas, bloqueia as 3.
- `git -C <dir> ...` aparece 4 081 vezes; a política lia `-C` como subcomando.
  Pular as opções globais muda 12 decisões de `allow` para `deny` (todas
  `branch -D` reais) e 41 de `allow` para `warn` (push).
- Prefixo antes do git (`VAR=1 git`, `xargs git`): 33 ocorrências, nenhuma
  destrutiva. **Resíduo declarado**, fora deste ramo.

## Desenho

1. `hooks/harness-git-guard.sh` vira um invólucro sem fork e sem `set -e`:
   - escolhe o interpretador nomeado com `read -r` do marcador (aceita falta de
     newline final; tira `\r` e BOM UTF-8); sem marcador válido, `python`;
   - acha o próprio diretório com `${BASH_SOURCE[0]%[/\\]*}` (barra ou
     contrabarra); sem separador, `.`;
   - grava o heartbeat `heartbeats/PreToolUse` com `printf >` e
     `EPOCHSECONDS` (o `mkdir -p` só roda se o diretório faltar). Fica no
     invólucro, e não no Python, para medir a chamada mesmo quando o Python
     não parte (mudança de implementação na fase tdd);
   - roda UM processo: `"$PY" "$HOOK_DIR/harness_git_guard.py"`, com a stdin
     do host passada direto (nada de comando em argv).
   - **Contrato de saída**: Python 0 → sai 0; Python 3 → sai 2 (bloqueio, o
     JSON já foi escrito em stderr pelo Python); qualquer outro código (1, 2 do
     "can't open file", 126, 127, sinal) → aviso em stdout "git-guard falhou
     (rc=N); bloqueio INATIVO nesta chamada", com o mesmo rate-limit de 1 h do
     `.git-guard-blind` (só builtins: `EPOCHSECONDS`, `read`, `printf >`), e
     sai 0. O 2 do Python nunca vira bloqueio.
2. `hooks/harness_git_guard.py`, num processo só, nesta ordem:
   1. (heartbeat: no invólucro, item 1);
   2. lê a stdin em bytes, decodifica UTF-8 com `replace`, `json.loads`;
      qualquer falha aqui → `SHAPE_UNKNOWN`. `tool_input` não-dict →
      `SHAPE_UNKNOWN`; sem chave `command` → `NO_COMMAND_KEY`; `command`
      não-string → `SHAPE_UNKNOWN`. Forma desconhecida → aviso com rate-limit
      de 1 h (texto de hoje), sai 0. **Exceção antes de extrair o comando nunca
      bloqueia.**
   3. comando vazio → sai 0 mudo;
   4. `command_policy.evaluate_command` no mesmo processo (import estático,
      `scripts/` no `sys.path`);
   5. se `unknown`: fallback (abaixo). Se o fallback também não decide → aviso
      de hoje ("command-policy nao conseguiu analisar..."), sai 0;
   6. exceção depois de extrair o comando → fallback; se ele também levantar
      → aviso, sai 0.
   - `deny` → stderr `{"decision":"block","reason":"BLOCKED: <motivo>"}`, sai 3;
     `require_approval` → idem com `APPROVAL REQUIRED:`, sai 3; `warn` →
     stdout `## Harness Warning: <motivo>. Confirme com o usuario antes de
     prosseguir.`, sai 0; `allow` → sai 0 mudo.
   - Mensagens só em ASCII (o host não roda com `PYTHONUTF8`).
   - `HARNESS_DIR`: variável de ambiente, senão `~/.claude/harness` (o mesmo
     diretório que o `$HOME` do bash do Git resolve).
3. **Fallback para comando que o parser não lê** (no guarda, reusa a política,
   sem segunda lista de padrões):
   1. tira corpo de heredoc (`<<EOF`, `<<'EOF'`, `<<"EOF"`, `<<-EOF`) e linhas
      de comentário → `evaluate_command` no texto inteiro;
   2. ainda `unknown` → linha a linha (continuação com `\` juntada);
   3. linha ainda `unknown` → a mesma linha com as aspas trocadas por espaço.
   Qualquer `deny` vence (motivo + "(lido pelo fallback)"); depois
   `require_approval`; depois `warn`. Linhas que nenhum passo lê → o resultado
   é `unknown` se nada mais decidiu.
4. `scripts/command_policy.py`: o subcomando do git passa a ser o primeiro
   argumento depois das opções globais. Com valor no token seguinte: `-C`,
   `-c`, `--git-dir`, `--work-tree`, `--namespace`, `--config-env`,
   `--super-prefix`, `--exec-path`; as formas `--opt=valor` e as sem valor
   (`--no-pager`, `-P`, `--bare`, `--no-optional-locks`, ...) ocupam um token.
   Sem subcomando depois delas (`git --version`) → `allow`. Nenhuma outra
   regra da política muda.
5. `hooks/hooks.json`: matcher do git-guard `Bash` → `Bash|PowerShell`;
   `timeout: 10` e `statusMessage` iguais.
6. Sai o código morto de `harness-git-guard.sh:106-150`.

## Requisitos

- REQ-1: sob carga focada (processo do teste + 4 laços nos mesmos 2 núcleos
  lógicos), mediana(guarda) / mediana(script bash que chama um Python vazio do
  MESMO interpretador que o guarda escolhe) ≤ 2,0, medidas intercaladas, guarda
  invocado como `bash harness-git-guard.sh` com payload `git status`.
  Código-base: 7,6×. Revisado em 2026-10-07 (ramo `fix/preludio-sem-fork`): a
  primeira versão comparava com um Python vazio sozinho e tinha teto absoluto de
  5 s; na suíte completa com `-n 4` a disputa dos outros workers pesa por
  processo, o Python vazio foi a 1,45 s e a razão a 2,8× sem mudança no guarda.
  Com referência de mesma estrutura (2 processos) a disputa pesa igual nos dois
  lados. O prazo absoluto fica com a verificação em produção, abaixo.
- REQ-2: nenhum bloqueio de hoje se perde: push `--force`/`-f`/`--force-with-lease`,
  `reset --hard`, `clean -f*`, `branch -D`, `checkout .`, `restore .`; cadeia,
  aninhado em `bash -c`/`pwsh -c`; texto citado não executa; mutação de plugin
  pede aprovação; push comum avisa.
- REQ-3 (validado pelo usuário): destrutivo achado pelo fallback é bloqueado.
- REQ-4: preservado — aviso de payload desconhecido com rate-limit de 1 h,
  heartbeat, interpretador nomeado com degradação para `python`, benigno e
  vazio mudos.
- REQ-5 (validado pelo usuário): a ferramenta PowerShell passa pelo guarda.
- REQ-6 (validado pelo usuário): sem pré-filtro no bash; um lugar de decisão.
- REQ-7 (validado pelo usuário): `git` com opções globais antes do subcomando
  é julgado pelo subcomando.
- REQ-8: falha de infraestrutura (Python ausente, script ausente, exceção) nunca
  bloqueia e nunca passa calada.

## Critérios de aceite (1 AC = 1 teste)

- AC-1 (falsificação do prazo, REQ-1): Given carga focada, When 6 rodadas
  intercaladas guarda/(bash → Python vazio), Then razão ≤ 2,0. Reprova no
  código-base (medido: 7,6×, guarda a 23,6 s com a máquina carregada), passa no
  novo (1,0× a 1,5×, também sob `-n 4` com os testes de hook). Na suíte padrão. Pula (com motivo)
  só se a máquina tiver < 2 núcleos lógicos ou sem API de afinidade.
- AC-2: `# don't` ⏎ `git reset --hard HEAD~1` → 2.
- AC-3: heredoc com `it's` ⏎ `git push --force origin main` → 2.
- AC-4: `echo it's && git clean -fd` → 2.
- AC-5: `git checkout .` e `git restore .` → 2.
- AC-6: `echo it's fine` → 0 sem bloqueio; `git commit -F - <<'EOF'` com corpo
  `don't use git reset --hard` → 0; `echo it's "git push --force"` → 0.
- AC-7: marcador apontando para caminho inexistente, com CRLF, sem newline final
  ou com BOM → o guarda ainda bloqueia `git reset --hard`.
- AC-8: `git -C repo reset --hard`, `git -C "$M" branch -D x`,
  `git --no-pager push --force`, `git -c a=b clean -fd` → 2;
  `git -C repo push origin main` → 0 com aviso; `git -C repo status`,
  `git --version` → 0 mudo.
- AC-9: payload da ferramenta PowerShell (`tool_name: PowerShell`,
  `tool_input.command`) com `git reset --hard` → 2; e o hooks.json registra o
  guarda com matcher que casa `PowerShell`.
- AC-10: script Python do guarda ausente → 0 com aviso "falhou" (nunca 2);
  segunda chamada dentro de 1 h → muda.
- AC-11: comando com aspas duplas e quebras de linha que hoje vira "não
  conseguiu analisar" (forma real do corpus) → decidido pela política, sem aviso.
- AC-12: sabotar `hooks/harness_git_guard.py` (trocar por um `exit 0` inerte) faz
  o smoke do health-check reprovar "git-guard bloqueia destrutivo".
- AC-13: os testes atuais do guard, da política, do heartbeat e do smoke passam
  sem edição.

## Boundaries

- ALWAYS: medir com o interpretador de produção; carga só focada (2 núcleos);
  laços com homem-morto (sentinela + 180 s) e encerrados pelo handle; testes
  com `HARNESS_DIR` isolado.
- NEVER: bloquear payload de forma desconhecida ou falha de infraestrutura;
  mudar `command_policy.py` além do REQ-7; segunda lista de padrões destrutivos;
  deploy, merge ou push sem OK do usuário.
- ASK: prefixos antes do git (`VAR=1`, `env`, `command`, `xargs`) — resíduo
  declarado acima, 0 destrutivos no corpus.

## Verificação em produção (depois do deploy, que depende de OK)

Agregar os transcripts (`hook_cancelled` com `command` "Git guardrails...",
deduplicado por `toolUseID`) nos 3 dias seguintes ao deploy. Critério: zero
cancelamentos. Hoje: 196 em 01–07/10.
