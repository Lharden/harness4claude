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
