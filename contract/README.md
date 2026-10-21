# contract — a árvore autoritativa

Esta é a única árvore de contrato declarada como autoridade, e `mh/contrato.py`
a nomeia em código (`CANONICA`). Até 2026-09-05 **nenhuma linha de código elegia
dona**: cada programa se amarrava à cópia adjacente a si mesmo por `__file__` —
`Contract.load_builtin()` lia `parent/'data'`, `ContractSnapshot.load()` lia
`parent/'_contract'`, `load_contract()` lia `parents[1]/'contract'` — e não havia
script de sincronização entre elas.

## Como este conteúdo foi escolhido

Não foi escolhido: foi **medido**. Das 12 árvores que existiam na máquina, 10
concordavam semanticamente. Esta é a serialização canônica desse acordo, e
`canonizar` recusa rodar quando as árvores correntes divergem — canonizar sobre
divergência seria escolher um lado sem dizer que escolheu.

As 2 restantes estão em branches `equipotence-v1` que nunca receberam o merge de
`1.1.0`. Elas aparecem no relatório como defasadas e **não** bloqueiam: branch
atrás de main é o estado normal de uma branch.

## Serialização

LF, indentação de 2, quebra de linha final, UTF-8 literal. LF porque as oito
cópias que de fato rodam são LF — a fonte declarada (`harness4contract`) era a
exceção, com CRLF na árvore inteira, e era só isso que a fazia parecer diferente
das outras.

## O que é comparado

Significado, não bytes. O hash cobre o JSON parseado com chaves ordenadas, então
indentação, ordem de chave e fim de linha somem. **Divergência semântica reprova;
divergência de formatação é reportada e não bloqueia** — um detector que grita
por causa de fim de linha é um detector que ninguém lê.

`contract.lock.json` fica fora do hash de propósito: ele é sobre os bytes de uma
cópia específica, e a autoridade aqui é sobre o que o contrato **diz**.

## Uso

    mh contrato check      # toda arvore corrente bate com esta? (exit 1 se nao)
    mh contrato arvores    # lista as arvores da maquina, marcando quem bate

## Mudança de sonda

O `mh paridade` confere por AST que cada sonda canônica executa a entrada de
produção declarada em `entradas` (L-65). É piso: pega a sonda que nem chama a
entrada, não a que chama e não confere o efeito. A prova dinâmica fecha isso:
num clone isolado de um commit do host, ela sabota a produção
(`mh/sabotagens.py`) e exige que cada sonda reprove ao constatar o resultado
(L-66..L-69, `docs/specs/prova-dinamica-sondas-spec.md`).

**Quando rodar** — antes do merge de qualquer mudança que toque:

- `behavioral-probes.json`;
- um arquivo de teste de um host (pasta `tests`/`test`, `conftest.py`,
  `test_*.py`, `*_test.py`), e em especial o arquivo de um nó canônico;
- um arquivo de produção que `mh/sabotagens.py` edita.

**Como rodar**

    mh paridade --dinamica --host claude --ref <ramo>    # mudança só no harness4claude
    mh paridade --dinamica --host codex  --ref <ramo>    # mudança só no harness4codex
    python -m mh.cli paridade --dinamica --host <h> --ref <ramo>   # de dentro do checkout
                                                                   # do ramo do master-harness

Quando a mudança toca o contrato canônico, os hosts recopiam primeiro nos ramos
deles, e o portão roda para **cada** host com `python -m mh.cli` de dentro do
checkout do ramo do master-harness: é o `mh` importado que decide qual contrato
canônico e qual `mh/sabotagens.py` valem, e o comando informa o caminho e o SHA
dele.

**O que libera o merge**

- Código combinado 0 (o estático e a prova dinâmica), ou cada `nao_verificado`
  declarado com causa, ação e dono, e aceito pelo usuário.
- O SHA medido é o SHA que entra por fast-forward. Commit novo, rebase ou
  resolução de conflito pedem rodada nova.
- `verde_sabotada` é achado: corrige-se a sonda, no host, depois de um juiz
  descontaminado decidir que a sabotagem tira efeito prometido (L-69 e a exceção
  dela). Trocar a sabotagem só pelo caminho do juiz, com OK do usuário, em commit
  próprio.
