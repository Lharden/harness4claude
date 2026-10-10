---
applies_to:
  - scripts/branch_j_spool.py
  - scripts/branch_sensor.py
  - scripts/branch_config.py
  - tests/test_branch_j_spool.py
---

# Design: Camada J do Branch Keeper em modo sombra

**Spec Link**: [`branch-camada-j-ligacao-spec.md`](branch-camada-j-ligacao-spec.md) (aprovada, `b9a21f2`)
**Status**: Draft (aguarda validate-plan e approve-plan)
**Created**: 2026-10-10
**Author**: AI-generated, reviewed by Leonardo
**Repositórios**:
- harness4claude, ramo `feat/camada-j-sombra` (base `563d53f`): o hook. Os caminhos do `applies_to` são deste
  repositório.
- master-harness, ramo `feat/camada-j-sombra` (base `056ab0c`): o lote. Os caminhos governados lá são
  `mh/avaliacao/camada_j_sombra.py`, `mh/avaliacao/comum.py`, `mh/avaliacao/varredura.py`,
  `mh/avaliacao/cli_avaliacao.py`, `mh/saude.py` (só a peça nova) e `tests/avaliacao/test_camada_j_sombra*.py`.
  O `design_scope.py` de cada repo só enxerga o próprio repo, e por isso esta lista fica no texto.

---

## Technical Context

**Language**: Python ≥ 3.11 nos dois lados.
- O hook roda no interpretador que o wrapper `hooks/harness-branch-sensor.sh` escolhe: o `python` do sistema, ou o do
  marcador `~/.master-harness/interpretador`, quando existe.
- Por isso o código do hook usa só a biblioteca padrão e nunca importa `mh`.

**Key Dependencies**:
- harness4claude: `branch_config` (flags), `harness_paths.project_slug` (projeto), `branch_sensor.main` (ponto de
  chamada).
- master-harness:
  - `mh.s1.jev`: `PonteJev`, `sonda`, `vazamento_pendente`, `revisao_pendente`, `revisar`, `estimativa`,
    `_restringir`;
  - `mh.replay.envio.captura`;
  - `mh.s1.estatistica`: `bootstrap_pareado`, `ic_percentil`;
  - `mh.hints.livro` e `mh.hints.caminhos.livro`;
  - `mh.avaliacao.camada_j`: o molde e as funções a extrair para `comum.py`.

  Tudo por import, sem editar `mh/s1` nem `mh/replay` (T2).

**Storage**: arquivos JSON e JSONL em `<casa>/avaliacao/camada-j/sombra/`, onde `<casa>` = `~/.master-harness` (AC-0.2
só aceita a casa padrão).

**Testing**:
- pytest;
- no master-harness, `tests/avaliacao` com o conftest de isolamento que já existe (casa temporária, barreira de rede,
  `vigia`);
- no harness4claude, `tests/test_branch_j_spool.py` com `HARNESS_DIR` e `MASTER_HARNESS_HOME` temporários.

**Constraints**:
- **Latência no hook:** p95 a mais ≤ 20 ms, com a flag ligada (AC-1.8).
- **Sem rede, sem processo e sem ler a chave no hook** (AC-1.8, NEVER).
- **Exceção nunca escapa do hook** (AC-1.6).
- **Tamanho:** `*.jsonl` da pasta < 200 MiB (AC-1.7).

---

## Architecture

O trabalho se divide em dois lados que nunca se chamam:
- **O hook** escreve arquivos numa pasta que já existe.
- **O lote** (`mh`) cria a pasta, abre a janela, lê o que o hook escreveu, chama o Jev e decide.

O contrato entre os dois é o formato dos arquivos (Data Model) e o `sha_filtro`.

```
                      (harness4claude, hook, stdlib)
UserPromptSubmit ──► harness-branch-sensor.sh ──► branch_sensor.main
                                                     │
                       ┌─────────────────────────────┤
                       │ âncora do sensor fixada     │ turno seguinte
                       ▼ (1º prompt da sessão)       ▼
              branch_j_spool.registrar(payload, ancora=True|False)
                       │  gate: flag ∧ janela.json aberta ∧ agora<fim ∧ sha_filtro
                       │  filtro: prefixos, MIN_LEN, session_id
                       ▼
   ~/.master-harness/avaliacao/camada-j/sombra/
       janela.json            (escrito pelo mh; lido pelo hook)
       <sid>.jsonl            (escrito pelo hook; lido pelo mh)
       <sid>.contagens.json   (escrito pelo hook; lido pelo mh)
       seco.jsonl, seco_meta.json, escores.jsonl, fila.json, rotulos.jsonl, veredito.json   (mh)
                       ▲
                       │  (master-harness, venv do mh)
mh avaliar camada-j sombra <verbo> ──► mh/avaliacao/camada_j_sombra.py
     abrir · status · excluir-sessao · seco · ver-amostra · rodar · rotular · medir · purgar
         │            │                │           │
         ▼            ▼                ▼           ▼
   plugin cache   comum.py      PonteJev/captura  estatistica.bootstrap_pareado
   (D2, sha)      varredura.py  livro (gasto)     (IC por sessão)
```

### Key Components

1. **`branch_j_spool`** (`harness4claude/scripts/branch_j_spool.py`, novo) [traces: REQ-001, REQ-002, REQ-003, US-1]
   - **Responsabilidade:** decidir se um `UserPromptSubmit` grava e gravar. Nunca levanta, nunca faz rede, nunca abre
     processo, nunca cria diretório.
   - **Interface pública:**
     - `PREFIXOS: tuple[str, ...]`, `MIN_LEN = 12`;
     - `SHA_FILTRO: str` = sha256 de `json.dumps([list(PREFIXOS), MIN_LEN])`, calculado no import;
     - `casa() -> Path`: `MASTER_HARNESS_HOME`, senão `~/.master-harness`;
     - `pasta() -> Path`: `casa() / "avaliacao" / "camada-j" / "sombra"`;
     - `motivo_de_filtro(texto) -> str | None`: `None` se o turno é humano; senão o rótulo do prefixo ou `curto`;
     - `registrar(payload, *, ancora, agora=None) -> None`: o único ponto de entrada.
   - **Dependências:** `branch_config.get_bool("HARNESS_BRANCH_LAYER_J")` e `harness_paths.project_slug`.

2. **Ligação no sensor** (`harness4claude/scripts/branch_sensor.py::main`) [traces: REQ-001, AC-1.2, AC-1.3]
   - **No ramo que fixa a âncora** (`_anchor is None` e evento ≠ Stop): `branch_j_spool.registrar(payload,
     ancora=True)` antes do `return 0`.
   - **Depois desse bloco**, para `UserPromptSubmit`: `branch_j_spool.registrar(payload, ancora=False)` antes de
     `evaluate`.
   - As duas chamadas ficam num `try/except Exception: pass` próprio: dupla proteção do contrato "nunca levanta".
   - A saída do hook (stdout) não muda; nada do spool escreve em stdout ou stderr.

3. **Flag** (`harness4claude/scripts/branch_config.py`) [traces: REQ-001, D3]
   - `Knob("HARNESS_BRANCH_LAYER_J", BOOL, False, "liga/desliga", <por quê>)`.

4. **`camada_j_sombra`** (`master-harness/mh/avaliacao/camada_j_sombra.py`, novo) [traces: REQ-004…REQ-010, US-0…US-5]
   - **Responsabilidade:** os nove verbos.
   - **Interface:** uma função por verbo, `<verbo>(..., *, agora, escrever) -> int` (0 ok; 2 recusa; 3 pane), e
     `avaliar_sombra(itens, *, B, semente) -> dict` (puro, para AC-4.2).
   - **Dependências:** `comum`, `varredura`, `camada_j` (molde), `jev`, `captura`, `livro`, `estatistica`.

5. **`comum`** (`master-harness/mh/avaliacao/comum.py`, novo) [traces: REQ-004]
   - **Responsabilidade:** funções que hoje estão privadas em `camada_j.py` e servem aos dois avaliadores:
     `molde(nome)`, `sha(bytes)`, `jsonl(arq)`, `acrescentar(arq, linha)`, `infra(motivo)`, `INFRA`,
     `terminal_humano()`.
   - `camada_j.py` passa a importá-las. É refactor sem mudança de comportamento, provado pelos 25 testes de
     `tests/avaliacao` verdes sem edição.

6. **`varredura`** (`master-harness/mh/avaliacao/varredura.py`, novo) [traces: REQ-007, AC-2.3]
   - **Interface:** `PADROES: dict[str, re.Pattern]`, com os 12 da spec, e `residuos(texto) -> list[str]`, que
     devolve os nomes dos padrões que casam, sem trecho.

7. **Peça do `mh saude`** (`master-harness/mh/saude.py`) [traces: AC-5.4]
   - **Interface:** `checar_sombra(casa) -> Peca | None`, chamada em `checar()`.
     - devolve `None` sem `janela.json`: nada a dizer;
     - devolve `Peca("camada-j-sombra", DOENTE, "spool_com_texto_retido", comando="mh avaliar camada-j sombra
       purgar")` com `estado: aberta` e agora > `fim` + 30 dias;
     - devolve `SAUDAVEL` nos outros casos.
   - Lê só o `janela.json`.

---

## Data Model

Todos os instantes são ISO-8601 UTC com `Z` (`AAAA-MM-DDTHH:MM:SSZ`). O compacto `AAAAMMDDTHHMMSSZ` só aparece no
`lote` do livro.

#### `janela.json` (mh escreve; hook lê) [traces: AC-0.1, REQ-008]
```json
{"schema": 1, "estado": "aberta|encerrada", "inicio": "…Z", "fim": "…Z",
 "tau": 0.77, "semente": 20261010, "sha_filtro": "<64 hex>", "spec_commit": "b9a21f2",
 "teto_usd": 0.50, "fila": {"sinalizados": 40, "chamarizes": 20},
 "criterio": {"prec_min": 0.35, "min_sn_sinalizados": 20, "min_sn_chamarizes": 10,
              "B": 10000, "nivel": 0.95, "max_nan": 0.05}}
```
- **Invariantes:**
  - `fim` = `inicio` + 14 dias;
  - de `encerrada` não se volta a `aberta` (NEVER);
  - o hook trata ilegível ou sem campo como ausente.
- **Escrita:** atômica (`tmp` + `os.replace`). Só o `abrir` e o `purgar` escrevem.

#### `<sid>.jsonl` (hook escreve; mh lê) [traces: AC-1.2, AC-1.3]
```json
{"tipo": "ancora|turno", "turno_idx": 0, "instante": "…Z", "sessao": "<sid>",
 "projeto": "<slug>", "texto": "<prompt cru>"}
```
- **Invariantes:**
  - linha 0 é `ancora` com `turno_idx` 0;
  - `turno_idx` da linha *i* = *i*;
  - um escritor por arquivo (a sessão).
- **Escrita:** `os.open(O_WRONLY|O_APPEND|O_CREAT|O_BINARY)`, com **um** `os.write` por linha.
- **Criação:** só com `ancora=True` e o arquivo inexistente.
- **`turno_idx` de um turno:** conta as quebras de linha do arquivo, lendo o arquivo da sessão (pequeno, só a
  sessão). O `<sid>.jsonl` excluído pelo AC-5.1 vira um marcador de uma linha só, `{"excluida": "…Z"}`, e o hook não
  acrescenta a arquivo cuja primeira linha é marcador.

#### `<sid>.contagens.json` (hook escreve; mh lê) [traces: AC-1.4, AC-1.6, AC-1.7]
```json
{"<prefixo|curto|falha_spool|spool_cheio|sessao_sem_ancora_humana>": 3}
```
- **Escrita:** leitura, soma e `tmp` + `os.replace`; um escritor. É criado no primeiro evento contável da sessão,
  mesmo que o `<sid>.jsonl` não exista.

#### `seco.jsonl` e `seco_meta.json` (mh) [traces: AC-2.2…AC-2.4]
- **Linha de `seco.jsonl`:** `{id, sessao, turno_idx, sha_enviado, redacoes, truncado, residuos: [...], corpo}`. O
  `corpo` é o JSON redigido, e `residuos` são os nomes de padrão.
- **`seco_meta.json`:**
  - `{sha_seco, sha_redacao, sha_jev, sha_bloqueio, mtime_max, descartes: {sessao_sem_ancora|corrompida|idx_repetido: n},
    amostra: {ids, codigo}, estimativa_usd}`;
  - `sha_redacao` e `sha_jev` são o sha256 de `mh/s1/redacao.py` e `mh/s1/jev.py` como estão em disco;
  - `sha_bloqueio` é o do kit (`jev.bloqueio()`).
- **id de item:** `<sessao>:<turno_idx>`. A ordem canônica é `(sessao, int(turno_idx))`.

#### `escores.jsonl` (mh) [traces: AC-2.5]
O mesmo formato da avaliação, mais `id`, `sessao` e `turno_idx`, sem texto. Vale a última linha por id.

#### `fila.json`, `rotulos.jsonl` e `veredito.json` (mh) [traces: AC-3.2…AC-4.4]
- **`fila.json`:** `{ordem: [id, ...], grupo: {id: "s"|"c"}}`. O grupo nunca é mostrado (AC-3.3).
- **`rotulos.jsonl`:** `{id, rotulo: "s"|"n"|"pular", instante}`. Vale a última linha por id.
- **`veredito.json`:** `{veredito, prec_J, base, delta, ic_delta, ic_prec_J, ic_base, frac_nan, contagens, gasto_usd,
  abandonada?, motivo?}`.

---

## API Contracts

### Hook

`branch_j_spool.registrar(payload: dict, *, ancora: bool, agora: datetime | None = None) -> None`
[traces: REQ-001…REQ-003, AC-1.1…AC-1.8]

1. Se a flag está desligada, retorna.
2. Se `hook_event_name` ≠ `UserPromptSubmit`, retorna.
3. Lê `pasta()/janela.json`. Se falta, está ilegível, não está `aberta`, se `agora` ≥ `fim` ou se `sha_filtro` ≠
   `SHA_FILTRO`, retorna sem escrever nada.
4. Se `session_id` não casa `[A-Za-z0-9-]{8,64}`, ou `prompt` não é `str`, retorna.
5. Se `motivo_de_filtro(prompt)` não é `None`, conta o motivo (e, com `ancora=True`, conta também
   `sessao_sem_ancora_humana`) e retorna.
6. Se a soma de `st_size` dos `*.jsonl` (`os.scandir`) ≥ 200 MiB, conta `spool_cheio` e retorna.
7. Com `ancora=True` e o arquivo inexistente, cria-o com a linha `ancora`.
8. Com `ancora=False` e o arquivo existente, sem marcador de exclusão, acrescenta a linha `turno`.
9. Nos outros casos não faz nada.

Qualquer exceção é engolida, com tentativa de contar `falha_spool`.

### CLI `mh avaliar camada-j sombra <verbo>` [traces: REQ-004, REQ-010]

Saída: 0 ok, 2 recusa com `recusa: <causa>` numa linha, 3 pane. Todo verbo marcado com **H** chama
`comum.terminal_humano()` (`sys.stdin.isatty()` e `"CLAUDECODE" not in os.environ`) e recusa `terminal_nao_humano`.

| Verbo | H | Recusas, na ordem | Efeito |
|---|---|---|---|
| `abrir` | H | `casa_nao_padrao`, `janela_ja_existe`, `plugin_sem_spool`, `flag_ausente`, `branch_keeper_desligado`, `chave_ausente` | cria a pasta com ACL (`jev._restringir`, herdável) e o `janela.json`; imprime o teste de vida do dia 0 |
| `status` | — | `sem_janela` | contagens sem texto (AC-0.5) |
| `excluir-sessao <sid>` | H | `sem_janela`, `janela_nao_aberta`, `sessao_inexistente` | substitui o `.jsonl` pelo marcador |
| `seco` | — | `sem_janela`, `janela_aberta`, `fila_montada`, `veredito_gravado`, `chave_ausente` | AC-2.2…AC-2.4 |
| `ver-amostra` | H | `seco_ausente` | os 20 corpos redigidos |
| `rodar --confirmo C [--refazer-falhas]` | H | `seco_ausente`, `codigo_divergente`, `kit_mudou`, `spool_mudou`, `fila_montada`, `veredito_gravado`, `teto_da_janela_excedido`, `envelope_excedido`, `livro_ilegivel`, `vazamento_jev_pendente`, `revisao_jev_pendente`, `sonda_sem_resposta`, `modelo_remoto_divergente` | AC-2.5…AC-2.7 |
| `rotular [--corrigir ID]` | H | `escores_incompletos`, `veredito_gravado`, `id_fora_da_fila` | monta `fila.json` na primeira vez; pergunta `s`, `n`, `pular` |
| `medir` | — | `fila_incompleta`, `veredito_gravado` | AC-4.2…AC-4.5 |
| `purgar [--abandonar --motivo T]` | H | `sem_janela`, `sem_veredito` (sem `--abandonar`) | AC-5.2 e AC-5.3 |

A checagem de D2 no `abrir` é feita assim:
1. lê `~/.claude/harness/plugin-root`;
2. exige `<root>/scripts/branch_j_spool.py` e a string `branch_j_spool` em `<root>/scripts/branch_sensor.py`;
3. carrega o módulo pelo caminho (`importlib`, como o `medir_cli` da avaliação) para obter o `SHA_FILTRO`.

A checagem de D3 lê `~/.claude/settings.json` e exige, no bloco `env`, `HARNESS_BRANCH_LAYER_J` verdadeiro e
`HARNESS_BRANCH` ausente ou verdadeiro.

### Funções puras

- `avaliar_sombra(itens, *, B=10_000, semente=20261010) -> dict` [traces: AC-4.2]
  - `itens`: `[{id, sessao, grupo: "s"|"c", rotulo: "s"|"n"|"pular"}]`;
  - devolve `{veredito, prec_J, base, delta, ic_delta, ic_prec_J, ic_base, frac_nan, n: {...}}`;
  - a estatística da réplica devolve `float("nan")` para 0/0 num grupo;
  - o IC sai de `ic_percentil` sobre as réplicas sem NaN;
  - a ordem dos vereditos é a do AC-4.2.
- `varredura.residuos(texto) -> list[str]` [traces: REQ-007].
- `branch_j_spool.motivo_de_filtro(texto) -> str | None` [traces: REQ-003].

---

## Test Strategy

### harness4claude (`tests/test_branch_j_spool.py`)
- **AC-1.1:** para cada caso (flag off; sem janela; janela ilegível; `encerrada`; agora ≥ `fim`; `sha_filtro`
  diferente), nenhum arquivo nasce, e a saída de `branch_sensor.main` com a flag ligada é igual byte a byte à da flag
  desligada (subprocesso com payload fixo).
- **AC-1.2 e AC-1.3:**
  - a primeira chamada `ancora=True` cria a linha 0;
  - as chamadas `ancora=False` acrescentam com `turno_idx` 1, 2;
  - `ancora=False` sem arquivo não cria nada;
  - pelo `main`, uma sessão cuja âncora do sensor já existia antes do `janela.json` não grava.
- **AC-1.4:** cada prefixo, com espaço antes e com caixa diferente (a caixa diferente grava), e `curto` em 11 e 12
  code points.
- **AC-1.5:** `session_id` inválido, prompt não string e evento `Stop`.
- **AC-1.6:** `os.open` falhando (monkeypatch): o hook não levanta e `falha_spool` sobe; com o `replace` das
  contagens também falhando, o hook continua sem levantar.
- **AC-1.7:** com os arquivos já somando 200 MiB (tamanho simulado via monkeypatch de `os.scandir`), `spool_cheio`
  sobe e nada é gravado.
- **AC-1.8:**
  - `socket.socket`, `subprocess.Popen` e a leitura de `segredos/` trocados por funções que levantam: o `registrar`
    não as aciona;
  - banco de 1 000 chamadas de `registrar` com a janela aberta: p95 ≤ 20 ms. É um teste marcado, com a carga moderada
    da memória do projeto.
- **Sabotagem medida:** desligar cada guarda (gate da janela, filtro, `sha_filtro`, `try/except`, teto de tamanho, a
  condição `ancora=False` sem arquivo) tem de derrubar o teste dela.

### master-harness (`tests/avaliacao/test_camada_j_sombra*.py`)
- **US-0 (AC-0.1…AC-0.5):** cada recusa com o seu Given. Para D2, um plugin-root falso em pasta temporária com e sem
  o módulo; para D3, um `settings.json` falso. A ACL é verificada por injeção de `jev._restringir`. `status` sem
  texto (marcador plantado).
- **REQ-010:** cada verbo H recusa com `stdin` não TTY e com `CLAUDECODE` presente.
- **US-2:**
  - seco antes do fim;
  - descartes (linha 0 que não é âncora, linha corrompida, idx repetido);
  - resíduo plantado (e-mail que escapa da redação simulado por monkeypatch da redação) vira `residuo_excluido`;
  - código determinístico (dois secos sobre o mesmo spool dão o mesmo código; mudar um spool muda o código);
  - `kit_mudou` (sha de bloqueio alterado), `spool_mudou` (`mtime` adiantado), `fila_montada`;
  - teto e envelope antes da sonda;
  - sonda divergente;
  - retomada;
  - livro com `lote` da janela.
- **US-3:**
  - a fila sorteada é reprodutível;
  - com menos sinalizados ou chamarizes que o teto, entram todos;
  - a tela não mostra P nem grupo (marcadores plantados);
  - `pular` conta;
  - `--corrigir` funciona;
  - `escores_incompletos`.
- **US-4:**
  - `avaliar_sombra` sobre conjuntos construídos dá os três vereditos;
  - a precedência do `inconclusivo` (s + n = 15 com `prec_J` alto);
  - `bootstrap_degenerado` (sessões de um grupo só);
  - `veredito_gravado` em todos os verbos listados;
  - relatório sem texto.
- **US-5:** `excluir-sessao` (marcador, hook não acrescenta); `purgar` na ordem estado → apagar; `--abandonar`;
  `sem_veredito`; peça do `mh saude` nos três estados.
- **Refactor `comum.py`:** os 25 testes de `tests/avaliacao/test_camada_j.py` sem edição.
- **Guardas do repositório:** `tests/test_orfaos.py`, `tests/test_stdin_explicito.py`, `test_derivados`. Os
  subprocessos novos levam stdin, stdout e stderr explícitos.
- **Sabotagem medida** para cada guarda do REQ-010, AC-2.5, AC-2.6, AC-4.1, AC-4.4 e para S-precedência.

---

## Risks

| Risco | Efeito | Mitigação |
|---|---|---|
| O deploy do harness4claude muda o hook de todas as sessões | regressão no `UserPromptSubmit` | flag desligada por padrão; AC-1.1 testa a saída idêntica; deploy é ASK (D2) |
| Outro deploy do plugin no meio da janela troca o `branch_j_spool.py` | o `SHA_FILTRO` muda e o hook para de gravar em silêncio | é o comportamento desejado (REQ-008); `status` mostra as linhas por sessão; o usuário confere `status` de tempos em tempos |
| Append concorrente na mesma sessão (`--resume` em dois terminais) | linhas intercaladas ou `turno_idx` repetido | o seco descarta a sessão inteira (`idx_repetido`) e conta |
| Latência do hook acima de 20 ms em disco lento | o usuário sente | a leitura do `janela.json` é pequena; `scandir` sem stat extra; o banco do AC-1.8 mede |
| A âncora fica fora da janela de 24 000 code points depois da redação | o par é truncado | a `PonteJev` reparte o orçamento entre os campos, como na avaliação |
| Volume ou taxa de disparo muito diferentes | `teto_da_janela_excedido` ou `inconclusivo` | é o comportamento previsto (ASSUMPTION-010) |
| `CLAUDECODE` não marca todo agente | um agente sem a variável passaria na guarda | soma-se a exigência de TTY, que um subprocesso de agente não tem |

---

## Open Questions

Nenhuma bloqueante. As decisões de produto estão nas ASSUMPTION-001 a 007 da spec, e as de desenho estão acima.

---

## Phases (por user story)

| Fase | Story | Repositório | Entrega | Pré-condição |
|---|---|---|---|---|
| A | US-1 (P1) | harness4claude | `branch_j_spool.py`, ligação no `main`, flag, testes e sabotagem | — |
| B | US-0 (P1) | master-harness | `comum.py` (refactor), `abrir` e `status` com D2 e D3 | A commitada (o teste de D2 usa um plugin-root falso) |
| C | US-2 (P1) | master-harness | `varredura.py`, `seco`, `ver-amostra`, `rodar` | B |
| D | US-3 e US-4 (P1) | master-harness | `rotular`, `medir`, `avaliar_sombra` | C |
| E | US-5 (P2) | master-harness | `excluir-sessao`, `purgar`, peça do `mh saude` | D |
| — | operação | — | deploy D2 (ASK do usuário) → `abrir` (ASK) → 14 dias → seco/rodar (ASK) → rotular → medir | A a E integradas pelo orquestrador |

O merge de cada fase é do orquestrador. Esta sessão commita no ramo e avisa.
