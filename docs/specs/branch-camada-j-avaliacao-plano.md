# Camada J: plano da avaliação offline

Spec: [`branch-camada-j-spec-light.md`](branch-camada-j-spec-light.md). Este plano diz **como** medir. O critério e os
cortes estão na spec e não mudam aqui. Nada abaixo roda antes da aprovação da spec. A rodada real (~US$ 0,08) espera um
segundo OK do usuário.

## Onde o código mora

`master-harness/mh/avaliacao/camada_j.py` e `tests/avaliacao/test_camada_j.py`, num ramo próprio do master-harness.
Roda no venv do `mh`, porque precisa da `PonteJev` e da redação do kit. Imports, todos sem edição:

| De | O quê | Por quê |
|---|---|---|
| `mh.s1.jev` | `PonteJev`, `sonda`, `vazamento_pendente`, `revisao_pendente`, `revisar`, `estimativa`, `MODELO` | o mesmo caminho de envio da produção, com redação, bloqueio, truncagem, guarda da chave e log auditado |
| `mh.replay.envio` | `captura` | o seco monta o corpo pela mesma ponte, sem abrir conexão |
| `mh.s1.estatistica` | `auc_pr`, `auc`, `bootstrap_pareado`, `ic_percentil`, `brier`, `ece` | a estatística do kit, sem reimplementar |
| `mh.hints.livro` | `gasto_do_mes`, `ENVELOPE_USD` | o envelope é um só |
| `harness4claude/scripts/branch_sensor.py` (por caminho, como faz o `calibrate_branch_layer_a.py`) | `layer_a` | a linha de base é a função de produção, não uma cópia dos padrões; o sha do commit vai ao relatório |

Moldes ficam fora do kit, em `mh/avaliacao/moldes/camada_j_par.json` (estado `ancora`→`ancora`, `turno`→`turno`) e
`camada_j_turno.json` (só `turno`). Os dois usam a mesma `questions`, com o texto do REQ-002. Os dois são passados à
`PonteJev(porta="C4", nome=..., molde_=..., timeout_s=30, fase=...)`.

## Verbos (`mh avaliar camada-j <verbo>`)

| Verbo | Rede | O que faz | Saída |
|---|---|---|---|
| `seco` | não | Confere o sha256 do arquivo de rótulos contra o da spec (`rotulos_divergentes` se mudou). Monta, dentro de `captura()`, os 2 × 703 corpos pela `PonteJev`. Grava esses corpos em `<casa>/avaliacao/camada-j/seco.jsonl` com ACL só do usuário e fora do git. Imprime as contagens de redação por regra, o número de pares truncados, a estimativa em US$, o gasto do mês e o código de uma amostra de 20 corpos | 0, ou 2 com a causa |
| `ver-amostra` | não | Mostra os 20 corpos da amostra do seco para a revisão do usuário | 0 |
| `rodar --confirmo <código>` | sim | Recusa se o código não for o da amostra vista, se faltar o seco, se a estimativa passar de US$ 0,15, se o envelope estourar, se houver vazamento pendente ou revisão `C4` pendente, ou se a sonda vier com outro `model`. Depois pontua os dois moldes, uma conexão reusada por molde e um trabalhador. Grava por item só id, molde, status, escore, motivo, tokens, redações e `sha_enviado`, sem texto, e confere cada `sha_enviado` contra o do seco (`envio_divergente`). As falhas de infraestrutura são retomáveis com `--refazer-falhas`. No fim, registra o gasto | 0, 2 recusa, 3 pane |
| `medir` | não | Lê os escores e os rótulos e calcula S0–S3, a ablação e as métricas relatadas. Escreve `docs/avaliacao/camada-j-resultado.md` com o veredito (`avança`, `não avança` ou `inconclusivo`). Nenhum texto de par vai ao relatório | 0 |
| `revisar [--confirmo]` | não | `jev.revisar("C4", ...)`: a revisão por amostra do log que de fato saiu (G2-D04), exigida antes de qualquer rodada seguinte | 0, ou 2 |

O `medir` é determinístico e roda quantas vezes for preciso. O critério não depende de nenhuma escolha feita nele.

## Estatística, exatamente

- **k** é o número de pares em que `layer_a(turno)` casa, recalculado no `medir` (hoje 25). A precisão@k do J sai dos k
  pares de maior P. Empate na fronteira se resolve pelo id do par, em ordem, para ser determinístico.
- **Cluster** é a `sessao` do par. O bootstrap reamostra sessões inteiras e, em cada réplica, recalcula o k da camada A
  e os k primeiros do J. A estatística é Δ = precisão@k(J) − precisão@k(A) para S2 e
  Δ = AUC-PR(J) − AUC-PR(comprimento) para S3.
- O par sem escore (`nao_verificado`) conta contra S0 e sai das métricas. Ele não é imputado.
- O relatório mostra a tabela de confusão em k, o recall@k, a AUC, o Brier, o ECE e o tempo de cada chamada. A
  latência mede conexão reusada e **não** é a latência de produção (ver Gateway, na spec).

## Testes (TDD, antes do código)

As duas metades de cada guarda, medidas:

1. o seco não abre conexão: uma barreira em `_abrir_conexao` falha o teste se algo tentar;
2. o seco pega um segredo plantado num par sintético: o corpo capturado não contém o valor, e a contagem da regra
   sobe. Num texto sem segredo, o corpo sai igual byte a byte;
3. `rodar` recusa com código errado, sem seco, com estimativa acima do teto, com envelope estourado e com sonda
   divergente. Cada recusa tem o seu teste, com transporte falso;
4. um `sha_enviado` diferente do sha do seco gera `envio_divergente`, e o item não conta;
5. o `medir`, sobre escores sintéticos com resposta conhecida, devolve os três vereditos nos três casos construídos.
   O k e o bootstrap são reprodutíveis pela semente;
6. nenhum texto de par aparece no relatório nem nos escores: o teste planta um marcador nos pares e varre as saídas;
7. o avaliador não toca `mh/s1/` nem os arquivos de P1, conferido por `git diff --stat` vazio em `mh/s1` e pela
   ausência de escrita na casa do S1, salvo o log `enviados-C4` e a revisão.

## Sequência

1. Aprovação da spec, pelo usuário, com a resposta à NEEDS CLARIFICATION-1.
2. TDD do avaliador num ramo do master-harness, com a suíte com e sem `tests/s1` separados (memória do projeto) e
   commit no ramo, sem merge.
3. `seco` e `ver-amostra`, revisados pelo usuário.
4. OK do usuário para a rodada real; depois `rodar` e `medir`.
5. O veredito vai ao usuário. Com `avança`, é escrita a spec de ligação, que carrega a decisão do gateway (L-46).
