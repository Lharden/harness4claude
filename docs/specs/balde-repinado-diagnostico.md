# O agente com o balde de antes do repin — diagnóstico e decisão

Data: 2026-10-02. Ramo: `fix/balde-repinado-avisa-agente`.

## O defeito

`scripts/harness_paths.py` fixa o balde de cada sessão em `pins/<sessão>.json`.
O pin repina quando ficou parado além de `HARNESS_PIN_TTL_H` (24 h) **e** o
projeto corrente é outro (incidente 2026-09-21). O protocolo
(`sync/templates/claude-md.harness.snippet.md`, skill `harness-workflow`) manda o
agente resolver o balde uma vez por sessão e reusar o caminho literal.

Depois de um repin genuíno os hooks criam a task no balde novo e o agente segue
operando o velho. Sessão `b46f67bc`:

    confirm_classification   -> "state.json contem task None"
    agente                   -> recria a task a mao no balde velho (state_cli init)
    record_signal            -> files=0, actual_level L0

O ramo `fix/repin-subpasta-nao-e-outro-projeto` trata o repin FALSO (subpasta
sem git contada como outro projeto). Este trata o repin GENUÍNO.

## Medição (2026-10-02, todos os transcripts da máquina)

| Medida | Valor |
|---|---|
| sessões com comando de estado (`confirm`/`state_cli`/`record_signal`) | 140 |
| tasks distintas operadas pelo agente | 735 |
| resoluções do balde (`harness_paths.py --session-id`) | 415 (0,56 por task) |
| sessões com mais de uma task | 69 |
| maior reuso de uma resolução | 141 tasks com 1 resolução (`44b0dfb5`) |
| sessões com 75 tasks e 0 resoluções | 1 (`fa9aea75`) |
| pins | 195 |
| pins que já repinaram | 1 (o falso) |

E do código: `harness_paths.py` **não** está em `STATE_CLI_PATTERNS`
(`hooks/harness-transactional.py`), então cada resolução sobe o contador de
escrita da task corrente.

## Decisão: (b), o CLI recusa o balde abandonado

**(a) re-resolver por task** foi descartada:

1. custa uma escrita no contador em **toda** task, para um evento que aconteceu
   0 vezes de verdade. Com a régua `0-1 = L0, 2-3 = L1`, uma edição real mais a
   resolução já promove a task — o remédio distorceria `actual_level`, que é a
   métrica que o incidente estragou;
2. depende de obediência, e a obediência medida ao "uma vez por sessão" já é
   0,56 resolução por task, com sessões de 75 tasks sem nenhuma.

**(b) recusa no CLI** custa zero no caminho feliz (uma leitura do pin) e dispara
só no evento, no primeiro comando que o agente roda no balde velho, com o balde
atual e a data do repin na mensagem. Não depende de o agente lembrar de nada.

### O predicado (`harness_paths.balde_repinado`)

Julga só o caminho e o pin — o balde `<raiz>/projects/<projeto>/sessions/<sessão>`
já carrega projeto e sessão. Recusa quando o pin aponta para outro projeto **e**
registra um repin saindo deste. Projeto diferente sem repin é deriva ou balde
anterior ao pin: não julga. Falha de leitura: não julga.

### A isenção (`harness_paths.recusa_balde_repinado`)

A task que já vivia no balde velho antes do repin (`started_at < repinned_at`)
continua operável e registrável: o repin não a move, e recusá-la a deixaria
irregistrável. A task recriada à mão depois do repin nasce depois e é recusada.

### Consumidores

- `scripts/state_cli.py` (`--home`), antes de abrir o banco — `HarnessDatabase`
  cria o `harness.db`, e criar banco no balde abandonado já é o estrago.
- `scripts/record_signal.py` (`--harness-dir`), antes de qualquer leitura.

## O que fica de fora, e por quê

`scripts/confirm_classification.py` também recebe `--harness-dir` e é o primeiro
comando do protocolo, mas está congelado enquanto o P1 do System One estiver
vivo (`status-proprio-da-task-l0-diagnostico.md`, "Dependência do P1"). Hoje ele
já falha (com mensagem que não nomeia o repin); o passo seguinte do agente —
recriar a task à mão ou registrar — é o que este ramo recusa com a causa
nomeada. Ligar o mesmo guarda no `confirm` é uma linha, depois do P1, com OK do
usuário.

## Evidência

`tests/test_balde_repinado.py`: 12 testes, duas metades.

- antes do conserto: 8 vermelhos; o de `record_signal` reproduz literalmente
  `registrado t-depois (level=L0, files=0)`;
- depois: 12 verdes;
- 6 mutantes, todos mortos: sem guarda (3 falham), sem isenção da task antiga,
  julgar sem repin registrado, recusar sempre, `state_cli` sem guarda,
  `record_signal` sem guarda.
