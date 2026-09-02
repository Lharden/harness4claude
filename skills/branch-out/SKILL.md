---
name: branch-out
description: "Ramifica uma conversa longa antes que ela se perca. Quando surge uma ideia digna de trabalho próprio — ou quando o fio escorrega do objetivo — nomeia o ramo, pergunta se abre, e abrindo: gera prompt-semente, abre uma sessão nova em janela própria com contexto limpo e parkeia o tema na conversa original, reversível por recall. Use quando o hook emitir BRANCH SIGNAL, quando o usuário disser /branch, ou quando você mesmo perceber que nasceu um assunto paralelo."
category: workflow
risk: low
source: custom
date_added: "2026-08-27"
metadata:
  version: 1
  triggers: branch-out, /branch, ramificar, ramo, parkear, recall, deriva, BRANCH SIGNAL
---

# Branch Out — ramificação com parking reversível

Conversa longa perde de três jeitos: o fio escorrega do objetivo, ideias-ramo
nascem e só uma é desenvolvida, e o assunto abandonado continua ocupando
janela. Esta skill trata os três com um mesmo movimento: **um ramo vira uma
sessão de verdade, com endereço próprio, e o tema sai do pai.**

## Quando ativar

- O hook emitiu `HARNESS v3 BRANCH SIGNAL` (ramo ou deriva) — **ativar antes de
  responder ao conteúdo do turno**.
- O usuário digitou `/branch` com qualquer verbo.
- **Você mesmo percebeu** que abriu um assunto paralelo. Esta é a camada mais
  importante e a que falha em silêncio: o hook existe porque ela falha, não no
  lugar dela. Se você acabou de escrever "isso daria um subsistema próprio" ou
  "vale explorar depois", já ramificou — pare e ofereça.

**Não ativar** quando: o assunto novo é um passo do trabalho atual (dependência,
não ramo); a conversa tem menos de ~5 turnos (ainda não há de onde ramificar);
ou o usuário já disse "descarta" para este mesmo tema.

## Autocheck a cada turno

Uma pergunta, antes de responder: *o que acabei de levantar cabe no objetivo
desta conversa, ou é assunto com vida própria?* Se tem vida própria, ofereça.

O teste prático: **o ramo precisaria do contexto acumulado aqui para começar?**
Se não precisa, ele não deveria pagar por ele.

## Verbos

| Comando | O que faz |
|---|---|
| `offer` | Nomeia, justifica em até 3 linhas, pergunta: abrir / parkear / descartar |
| `open <slug>` | Escreve semente + launcher, abre janela, parkeia o tema no pai |
| `list` | Ramos pendentes, abertos e fechados (com conclusão) |
| `recall <slug>` | Devolve o tema ao pai; sai do parking |
| `close <slug>` | Fecha e grava a conclusão para o pai ler |
| `drift` | Deriva: diz de onde a conversa saiu e pergunta o rumo. **Nunca abre janela** |

## offer — o caminho principal

1. **Nomeie o ramo.** Duas a quatro palavras, específicas. "Sensor de Deriva",
   não "Melhorias". O nome vira slug, título de janela e nome de arquivo.
2. **Justifique em até 3 linhas:** o que é, por que tem vida própria, e o que
   o pai perde se continuar carregando isso.
3. **Pergunte** com `AskUserQuestion`, três opções nesta ordem: *Abrir agora* /
   *Parkear* / *Descartar*.
4. **Sem resposta, ou resposta ambígua: parkeie.** Decisão explícita do usuário
   — "agora não" nunca apaga a ideia. Só `descartar` apaga.

Registre sempre, mesmo antes de perguntar:

```bash
python "$CLAUDE_PLUGIN_ROOT/scripts/branch_state.py" add \
  --name "<Nome do Ramo>" --topic "<tema em uma frase>" --detector claude
```

O `session_id` nasce aí, antes da janela: um ramo `pending` já é endereçável
por `claude --resume <uuid>` semanas depois.

Depois da resposta, resolva o gate do ramo. Para parkear (inclusive no default
ambíguo), rode `branch_state.py decision --slug <slug> --decision park`; para
descartar, use `--decision discard`. A opção de abrir é resolvida atomicamente
no passo `status --set open` abaixo.

## open — abrir a janela

**A semente é escrita por você, não pelo hook.** Só você tem o contexto. Ela é
o único fio entre um contexto limpo e a decisão que o originou.

Seis seções obrigatórias (o renderizador recusa semente incompleta):

1. **Origem** — sessão pai (uuid + nome), projeto, uuid do ramo
2. **O ramo** — 3 a 5 linhas
3. **Por que saiu da conversa pai** — e o que não desenvolver lá
4. **Contexto mínimo** — **paths e decisões, jamais conteúdo colado.** Colar o
   arquivo inteiro moveria o desperdício de lugar em vez de acabar com ele
5. **Primeira ação** — concreta. Ramo sem primeira ação é ramo que não começa
6. **Como reportar de volta** — `/branch close <slug>`

Depois:

```bash
python "$CLAUDE_PLUGIN_ROOT/scripts/branch_seed.py" write --slug <slug> --seed-file <arquivo.md>
python "$CLAUDE_PLUGIN_ROOT/scripts/branch_state.py" status --slug <slug> --set open
python "$CLAUDE_PLUGIN_ROOT/scripts/branch_seed.py" launch --slug <slug>
```

`status --set open` é a fronteira de autorização: resolve o gate específico do
ramo e aplica o teto transacional antes de qualquer janela ser lançada. Se o
teto estiver cheio, o comando falha e o launcher não deve ser executado.

A janela abre em Windows Terminal + PowerShell 7, no diretório do projeto, já
rodando `claude --session-id <uuid> -n "<nome>"` com a semente. O launcher fica
em disco e é reexecutável: rodá-lo de novo **retoma a mesma sessão**, não cria
outra.

Teto de 3 ramos abertos ao mesmo tempo (`HARNESS_BRANCH_MAX_OPEN`). No teto, o
ramo novo fica `pending` — diga isso ao usuário em vez de abrir a quarta janela.

## Parking — o que muda na conversa pai

Depois de `open`, o hook injeta a cada turno:

```
<harness-parked>
- <tema> -> ramo "<nome>" (open). NAO desenvolver aqui; ofereca /branch recall <slug>.
</harness-parked>
```

**Como se comportar com um tema parkeado:** se ele voltar — por você ou pelo
usuário — não desenvolva. Uma frase: *"isso está no ramo X; `/branch recall
<slug>` traz de volta pra cá."* Depois siga o assunto do pai.

O parking é soft de propósito. Ele redireciona; não recusa. Uma palavra do
usuário desfaz.

## drift — deriva não é ramo

Deriva é a conversa escorregando sem ideia nova. Ação: **uma frase, nenhuma
janela.**

> "Saímos de `<âncora>` há N turnos. Voltamos, reancoramos aqui, ou isto virou
> um ramo?"

Se ele escolher reancorar, atualize a âncora:

```bash
python -c "import sys; sys.path.insert(0,'$CLAUDE_PLUGIN_ROOT/scripts'); \
import branch_sensor as s; s.set_anchor(cwd='.', text='<novo objetivo>', \
source='reanchor', session_id=None, embedding=s.embed('<novo objetivo>'))"
```

## close e recall

`close` no ramo grava a conclusão; ela aparece para o pai no próximo `list`. É
assim que o trabalho volta — sem merge automático, sem reinjeção surpresa.

`recall` no pai devolve o tema: leia a semente, resuma em duas linhas, siga
dali. Se o ramo tinha janela aberta, avise que ela continua viva.

## Limites

- **NUNCA** abrir janela sem resposta afirmativa do usuário. Detecção é
  passiva; abertura nunca é.
- **NUNCA** apagar ramo sem `descartar` explícito.
- **NUNCA** oferecer o mesmo tema duas vezes — o sensor deduplica, você também.
- **SEMPRE** parkear o tema no pai depois de abrir. Ramo aberto com tema ainda
  vivo no pai é o pior dos dois mundos: dois lugares desenvolvendo a mesma
  coisa.
- Máximo 2 ofertas por sessão (`HARNESS_BRANCH_MAX_OFFERS`). Perguntar demais
  quebra o foco tanto quanto a tangente.
