# Diagnóstico — o git-guard falha aberto sob carga

Task: `t-20261007-180451660248` (L2-bug). Ramo: `fix/git-guard-prazo-sob-carga`.
Data das medidas: 2026-10-07. Ferramentas no scratchpad da sessão `ebc6cbfc`:
`medir.py` (custo por etapa), `carga.py` (laço preso com homem-morto),
`prod_guard`/agregação dos transcripts, `sonda_parser.py`.

## Sintoma

`hooks/harness-git-guard.sh` é PreToolUse:Bash com `timeout: 10`. Quando o host
mata o hook por prazo, a ferramenta segue: **a checagem não acontece**.

## Produção (transcripts do host, deduplicado por `toolUseID`)

O host só grava anexo do guarda quando ele emite algo (aviso) ou é cancelado; o
`command` do anexo é o `statusMessage` ("Git guardrails..."). Por isso a contagem
abaixo é de eventos, não de chamadas.

| Período | `hook_cancelled` (≥ 10 s) | aviso "não conseguiu analisar" | aviso de push |
|---|---|---|---|
| antes de 20/09 | 2 | 322 | 95 |
| 20–30/09 | 335 | 782 | 31 |
| 01–07/10 | 196 | 8 | 1 |

- Cancelamentos: mínimo 10,07 s, p50 12,7 s (o host demora a matar).
- Sucessos com anexo desde 24/09 (n=123): p50 2,2 s, p90 4,6 s, p99 7,1 s, máx 7,7 s.
  A cauda já encosta no prazo sem estourar.

## Custo por etapa (medido, `medir.py`, 8 rodadas intercaladas)

Medição presa às CPUs 30–31. "Carga" = + 4 laços ocupados nos mesmos 2 núcleos.
Máquina: 31–47 % de CPU alheia durante as rodadas (outra sessão com pytest -n 4).

| Etapa | ocioso p50 | carga p50 | carga máx |
|---|---|---|---|
| `bash -c "exit 0"` | 0,07 | 0,13 | 0,16 |
| marcador do interpretador (`cat \| tr` em `$( )`) | 0,29 | 0,52 | 0,59 |
| heartbeat (`mkdir -p` + `printf >`) | 0,19 | 0,31 | 0,35 |
| Python nomeado vazio (`-c pass`) | 0,22 | 0,30 | 0,34 |
| Python extrator do payload | 0,23 | 0,33 | 0,36 |
| Python `command_policy.py` | 0,28 | 0,39 | 0,45 |
| Python leitor de campo JSON (roda 2×) | 0,23 | 0,32 | 0,38 |
| **guarda inteiro, `git status`** | **2,14** | **3,76** | 4,01 |
| **guarda inteiro, `git reset --hard`** | 2,14 | 3,59 | 3,95 |
| forma do host (`bash -c 'bash guard'`) | 2,32 | 3,93 | 5,35 |

Leitura: o guarda custa ~10× um Python vazio. A soma das etapas listadas dá
~1,5 s ocioso; o resto são os forks implícitos (`$(cat)` da entrada, subshells
dos `printf | python`, `$(dirname ...)`). **Cada chamada cria ~15 processos:
4 interpretadores Python e ~11 processos do bash do Windows (Cygwin)**, cada um
passando pelo Cortex XDR. O custo cresce com o número de processos e a disputa
de CPU multiplica todos eles (×1,75 com 4 laços em 2 núcleos). Em produção, com
várias sessões e os outros hooks do mesmo evento disputando, a cauda passa de 10 s.

## Causa raiz

1. **Prazo:** o desenho do guarda é uma cadeia de ~15 processos por chamada,
   e nenhum deles é necessário para decidir a maioria dos comandos. A decisão
   em si (`evaluate_command`) é microssegundos de Python; o resto é custo de
   criar processo.
2. **Parser:** `command_policy.parse_invocations` levanta `ValueError` quando o
   `shlex` não fecha uma aspa — basta um `'` num comentário ou num heredoc em
   QUALQUER linha do comando — e o guarda trata `unknown` como "passa com aviso".
   Sonda (`sonda_parser.py`), código atual:

   | comando | exit |
   |---|---|
   | `# don't do this` ⏎ `git reset --hard HEAD~1` | **0** (aviso) |
   | heredoc com `it's` ⏎ `git push --force origin main` | **0** (aviso) |
   | `echo it's && git clean -fd` | **0** (aviso) |
   | `git commit -m "it's ok" && git reset --hard` | 2 |
   | `git reset --hard` | 2 |

   1 112 eventos desse aviso nos transcripts: em cada um, o comando inteiro
   passou sem checagem.
3. **Cobertura (fora do prazo, registrado):** o matcher é `Bash`; o hook não
   roda para a ferramenta PowerShell, que é o shell primário nas sessões deste
   Windows. A queda dos avisos em 01–07/10 não vem de conserto nenhum no
   parser; a hipótese (não medida) é menos chamadas pela ferramenta Bash.
4. **Código morto:** `harness-git-guard.sh:106-150` (regex antigos) nunca roda —
   todo ramo do `case` anterior sai.

## Contexto estrutural (fase graph-context)

`graphify-out/graph.json` é de 2026-07-28, anterior a HEAD: não serve como
estrutura conferida. A lista abaixo saiu de `grep` no ramo, em 2026-10-07.

| Consumidor | O que trava |
|---|---|
| `hooks/hooks.json` | registro `PreToolUse` matcher `Bash`, `timeout: 10`, `statusMessage` |
| `scripts/command_policy.py` | a política (deny/warn/require_approval/allow), compartilhada com Harness4Contract |
| `tests/test_command_policy.py` + `contract/behavioral-probes.json` + `scripts/contract_adapter.py` | sonda de contrato `safety.command-policy` (texto citado não executa; cadeia destrutiva nega; mutação de plugin pede aprovação) |
| `tests/test_harness.py::TestGitGuard` | 6 cenários: push --force, reset --hard, clean -fd, branch -D/-d, push com aviso, comandos seguros, vazio |
| `tests/test_host_contract_resilience.py::TestGitGuardFailsLoud` | destrutivo bloqueia; benigno e vazio mudos; forma desconhecida avisa sem bloquear; aviso com rate-limit de 1 h |
| `tests/test_hook_liveness.py::TestHooksGravamHeartbeat` | heartbeat `heartbeats/PreToolUse` com epoch > 0 |
| `scripts/health-check.sh` + `tests/test_health_check_smoke.py` | smoke: bloqueia destrutivo, passa benigno, avisa payload estranho; sabotagem do guard reprova |
| `tests/test_hooks_timeout.py` | timeout em segundos e acima do teto interno |
| `hooks/harness-arsenal-gate.sh` | só cita o guard em comentário (mesmo padrão de extrator) |

Faltam nos testes atuais: `checkout .` e `restore .` no guard inteiro (só a
política os cobre indiretamente), e qualquer medida de latência.

## O que não é causa

- O interpretador nomeado não é lento: parte em 0,22 s ocioso, 0,30 s sob carga.
- A decisão de política não é lenta: o custo do `command_policy.py` é o custo de
  partir o interpretador (0,28 s contra 0,22 s vazio).
