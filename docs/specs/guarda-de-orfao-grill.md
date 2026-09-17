# Grill — guarda de órfão

**Task:** `t-20260916-215220419949` · **Spec atacada:** [`guarda-de-orfao-spec.md`](guarda-de-orfao-spec.md)
**Rodada:** 2026-09-16

**Degradação declarada:** `wf-grill.js` foi recusado pela camada de permissão
(*"script contains control characters"* — o arquivo tem CRLF). A rodada foi feita
inline, em contexto que **não** é descorrelacionado do que escreveu a spec. Isso
enfraquece a regra 4 de fan-out ("descontaminar a aresta"): o mesmo contexto que
produziu a spec a atacou. Os achados abaixo valem; a **cobertura** não tem a
garantia que cinco janelas novas dariam. Lente morta: todas as cinco.

---

## G1 — BLOQUEANTE · O comando prometido não conserta os dois casos

**Ataque.** ASSUMPTION-006 diz que falhar não pune quem conserta, *porque a
mensagem traz o comando copiável*. Mas US-7/AC-5 diz que o comando acrescenta
órfão novo com `motivo` vazio, e US-3/AC-1 diz que `motivo` vazio **reprova**.
Então, para o caso "nasceu órfão novo", rodar o comando prometido **não deixa a
suíte verde** — troca uma reprovação por outra.

**Por que importa.** A promessa feita ao autor foi específica: o custo de falhar é
desarmado porque o conserto é copiável. Se o comando só conserta metade dos casos e
a spec não diz qual, a próxima pessoa lê "o comando corrige" e descobre que não.

**Veredito.** A distinção é real e favorável, mas precisa estar escrita:
- **linha obsoleta** (a função ganhou chamador, ou sumiu) → o comando **conserta
  inteiramente**, e a suíte fica verde. É exatamente o caso que o autor nomeou
  como "o único que representa progresso".
- **órfão novo** → o comando **não pode** consertar sozinho: falta o julgamento
  humano, que é o produto. Ele reduz o trabalho a escrever uma frase.

**Correção aplicada na spec:** US-7/AC-3 passa a exigir que a mensagem diga **qual
dos dois casos** é, e que só prometa desfecho verde no primeiro.

---

## G2 — BLOQUEANTE · O detector de raiz é o mesmo tipo de regex que foi calibrado a 0.10

**Ataque.** REQ-F2 descobre raízes por regex sobre arquivos de prosa e shell. Este
repositório **já mediu** o que acontece com detecção por regex sobre prosa: a
camada A do Branch Keeper tem precisão ~0.10, 12 de 16 padrões nunca dispararam, e
o desfecho foi desligar a camada e trocar o veredito por pedido de julgamento. Por
que esta regex seria diferente?

**Resposta, e ela sustenta.** As duas coisas parecem iguais e não são:

| | camada A do Branch Keeper | detector de raiz |
|---|---|---|
| o que casa | **intenção** humana ("e se…") | **um literal** que já é o endereço (`tools/orfaos.py`) |
| verdade de referência | julgamento, discutível | o arquivo existe ou não |
| falso positivo | oferta indevida, custo cognitivo | função marcada viva sem ser — **e a medição de hoje pegou três** |

O risco não é a regex casar o errado por ambiguidade semântica: é ela casar por
**substring**, e isso aconteceu **três vezes hoje**:
`tests/test_harness_paths.py` → módulo `harness_paths`; `build_wiki_index.py` →
módulo `wiki_index`; e o `grep -l` que me fez crer que `skills/wiki-query/SKILL.md`
declarava `wiki_lint`, quando o que ele contém é `build_wiki_index.py`.

**Correção aplicada na spec:** REQ-F2 ganha a exigência de fronteira à esquerda e
o teste de regressão vira **três** casos (US-4/AC-3 passa a cobrir os três), não um.

**Ponto cego que permanece, declarado:** um documento que diga *"não rode
`tools/orfaos.py`"* conta como raiz. O detector lê endereço, não negação. Custo
aceito: falso positivo aqui marca uma função como viva, e o desfecho é o estado de
hoje, não uma regressão.

---

## G3 — ALTO · 38 métodos públicos ficam fora, e viram rota de evasão

**Ataque.** O recorte é "função pública de topo de módulo". Há **18 classes de
topo em produção com 38 métodos públicos**. Uma função órfã movida para dentro de
uma classe sai do radar do guarda sem mudar de natureza. O guarda pode ser
contornado por refatoração trivial, inclusive sem intenção.

**Veredito.** O recorte se justifica por comparabilidade com a medição — mas
"Deferred" sem número é fraco. **38 métodos é 9% da superfície**, e o guarda nunca
os viu. Não há base para afirmar que a proporção 29:38 se manteria.

**Correção aplicada na spec:** ASSUMPTION-004 ganha o número medido e a marca de
dívida explícita; o limite em "Nível de garantia" passa a citar 38 e a nomear a
evasão.

---

## G4 — MÉDIO · "exatamente 67 entradas" não pode virar assert

**Ataque.** US-3/AC-4 diz que a allowlist tem exatamente 67 entradas em `5ca9d4e`.
Se isso virar `assert len(allowlist) == 67`, qualquer função nova legítima quebra a
suíte de quem não mexeu na lista — e é o defeito que G1 acabou de nomear.

**Veredito.** Procede. O 67 é uma **medição de linha de base**, não um invariante.
O invariante é `não-vivas ⊆ allowlist` e `allowlist ⊆ não-vivas`.

**Correção aplicada na spec:** AC-4 passa a ser afirmação da medição, não critério
de teste, e o critério fica sendo a dupla inclusão.

---

## G5 — MÉDIO · A prova de que o guarda roda ainda passa por dentro dele

**Ataque.** US-2/AC-4 pede que `tools/orfaos.py` apareça como `viva` **no próprio
scanner**. Isso é o registro se confirmando: o scanner atesta a si mesmo. É
literalmente a propriedade nº 2 do seed sendo violada pelo mecanismo que existe
para aplicá-la — e uma sessão irmã reportou hoje o mesmo padrão em outra superfície
(`ListAgents` afirmando sessão viva sem confrontar a tabela de processos).

**Veredito.** Procede em parte. AC-4 não é a prova; AC-3 é — a suíte falhando por
órfão plantado é veredito do **pytest**, não do guarda. Mas AC-3 está escrito como
critério e não como evidência a colher.

**Correção aplicada na spec:** AC-3 passa a exigir **execução registrada com a
saída colada** na verificação, e AC-4 é rebaixado a corroboração.

---

## G6 — MÉDIO · Chave da allowlist quebra em renomeação

**Ataque.** A allowlist é indexada por módulo + nome. Renomear `tools/wiki_lint.py`
invalida 11 linhas de uma vez, e o comando de sincronização as apagaria e
reinseriria com `motivo` vazio — **o julgamento escrito seria destruído por um
`git mv`**.

**Veredito.** Procede, e é grave: a allowlist é o produto desta feature.

**Correção aplicada na spec:** REQ-F13 passa a exigir que a sincronização **nunca
apague motivo**; linha cujo alvo sumiu é *marcada* como obsoleta e só sai por ação
explícita, e o comando imprime os motivos órfãos para recolagem.

---

## G7 — BAIXO · Um arquivo com SyntaxError esconde tudo o que há nele

**Ataque.** REQ-F9 diz que arquivo não-analisável é nomeado. Mas nomear não
impede que as funções dele sumam da conta — o total cai e ninguém percebe, porque
o guarda não tem o que comparar.

**Veredito.** Procede, custo baixo. O repositório tem 129 `.py` e zero com
`SyntaxError` hoje.

**Correção aplicada na spec:** REQ-F9 passa a exigir **reprovação**, não só
menção — arquivo de produção que não parseia é defeito, e o guarda é o lugar onde
ele aparece.

---

## Não procede

**"O guarda deveria consultar `graphify-out/`, já que o grafo existe."** Não. O
grafo é não-direcionado e a vizinhança de `graph_lint.py` nele tem zero arestas.
Alcançabilidade é dirigida; usar vizinhança seria inventar causalidade a partir de
adjacência, que é o limite que o próprio `tools/README.md` declara.

**"A parte A deveria entrar, já que o problema é o mesmo."** Não. A medição
mostrou que são perguntas diferentes: B é decidível estaticamente hoje; A precisa
de sinal de execução por capacidade, e a pergunta de desenho não tem resposta
óbvia. Decidido no `CONTEXT.md`, L4.
