export const meta = {
  name: 'wf-grill',
  description: 'Gera o conjunto adversarial do grill-me a partir da spec em contexto LIMPO: cinco lentes independentes leem so a spec e o CONTEXT.md — nunca a conversa que os produziu — e devolvem perguntas que expoem ambiguidade, boundary ausente, suposicao nao declarada, edge case e dependencia nao dita.',
  phases: [{ title: 'Grill', detail: 'uma lente adversarial por agent, cada uma em janela nova' }],
}

// args esperado (passado pelo harness-workflow ou pela skill grill-me):
//   { spec_path, context_path }
//
// O que este script NAO recebe, de proposito: a conversa, o brainstorming, o
// raciocinio de quem escreveu a spec. Um grill que le a justificativa do autor
// vira concordancia — e a fase inteira existe para nao ser isso. A spec tem de
// se defender sozinha, com o que esta escrita nela.
const input = args || {}
const specPath = input.spec_path || null
const contextPath = input.context_path || null

if (!specPath) {
  return {
    perguntas: [],
    lentes_mortas: [],
    cobertura: { esperado: 0, vivas: 0 },
    erro: 'spec_path ausente — sem spec para grelhar.',
  }
}

const leitura =
  `Leia APENAS estes arquivos, na integra, antes de responder:\n` +
  `- ${specPath}\n` +
  (contextPath ? `- ${contextPath}\n` : '') +
  `\nNao procure a conversa que gerou a spec, nao infira intencao do autor e nao ` +
  `complete lacuna com o que "provavelmente" foi combinado. Uma lacuna que voce ` +
  `precisa preencher para entender e exatamente o achado.\n`

const PERGUNTAS_SCHEMA = {
  type: 'object',
  required: ['perguntas'],
  additionalProperties: false,
  properties: {
    perguntas: {
      type: 'array',
      items: {
        type: 'object',
        required: ['pergunta', 'ancora', 'por_que_importa'],
        additionalProperties: false,
        properties: {
          pergunta: { type: 'string' },
          ancora: { type: 'string' }, // REQ-###, AC, secao ou linha da spec
          por_que_importa: { type: 'string' },
          severidade: { type: 'string', enum: ['bloqueante', 'alta', 'media'] },
        },
      },
    },
  },
}

const LENTES = [
  {
    key: 'ambiguidade',
    prompt:
      `${leitura}\nLENTE: AMBIGUIDADE.\nEncontre todo trecho que admite duas leituras defensaveis e que levaria dois ` +
      `implementadores competentes a construir coisas diferentes. Adjetivo sem numero ("rapido", "grande", "seguro"), ` +
      `verbo sem sujeito, criterio sem unidade. Para cada um, a pergunta que forca a decisao.`,
  },
  {
    key: 'boundary-ausente',
    prompt:
      `${leitura}\nLENTE: BOUNDARY AUSENTE.\nA spec declara boundaries ALWAYS/NEVER/ASK. Encontre o que ela NAO proibe e ` +
      `deveria: a acao destrutiva sem ASK, o caminho feliz sem NEVER correspondente, o escopo que ninguem fechou. ` +
      `Pergunte pelo limite que falta, nao pelo que ja esta escrito.`,
  },
  {
    key: 'suposicao',
    prompt:
      `${leitura}\nLENTE: SUPOSICAO NAO DECLARADA.\nTodo requisito so esta correto sob alguma condicao. Encontre os ` +
      `requisitos cuja condicao nao aparece em lugar nenhum da spec — o "isto vale enquanto X for verdade" que ninguem ` +
      `escreveu. Nomeie X e diga qual requisito cai se X for falso.`,
  },
  {
    key: 'edge-case',
    prompt:
      `${leitura}\nLENTE: EDGE CASE.\nPara cada acceptance criterion Given/When/Then, ataque o Given: vazio, zero, nulo, ` +
      `duplicado, concorrente, gigante, malformado, fora de ordem, sem permissao. Traga so os casos em que o Then da spec ` +
      `deixa de fazer sentido — nao a lista generica.`,
  },
  {
    key: 'dependencia',
    prompt:
      `${leitura}\nLENTE: DEPENDENCIA NAO DITA.\nO que precisa existir, estar ligado, ter permissao ou ter sido migrado ` +
      `para que esta spec seja implementavel, e que ela trata como dado? Servico, credencial, schema, ordem de deploy, ` +
      `versao minima, estado previo dos dados. Pergunte por quem garante cada um.`,
  },
]

// Censo de nos — `filter(Boolean)` descarta agente morto SEM AVISAR. Um grill que
// perde a lente "suposicao" devolve um conjunto que parece completo e nao tem
// nenhuma suposicao questionada. Conte contra o esperado ANTES de filtrar.
function censoNos(rotulo, rotulos, obtidos) {
  const mortos = rotulos.filter((_, i) => !obtidos[i])
  if (mortos.length) {
    log(`ATENCAO: ${rotulo} — ${mortos.length} de ${rotulos.length} nos nao retornaram nada: ${mortos.join(', ')}`)
  }
  return { vivos: obtidos.filter(Boolean), mortos, esperado: rotulos.length }
}

phase('Grill')
const respostas = await parallel(
  LENTES.map((l) => () =>
    agent(l.prompt, { label: `grill:${l.key}`, phase: 'Grill', schema: PERGUNTAS_SCHEMA }).then((r) => ({
      lente: l.key,
      perguntas: r.perguntas || [],
    })),
  ),
)

const censo = censoNos('Grill', LENTES.map((l) => l.key), respostas)

// Dedup por ancora + inicio da pergunta: duas lentes chegam na mesma lacuna por
// caminhos diferentes, e isso e sinal de que ela e real — mas so precisa ser
// perguntada uma vez.
const vistas = new Set()
const perguntas = []
for (const r of censo.vivos) {
  for (const p of r.perguntas) {
    const chave = `${p.ancora}::${(p.pergunta || '').toLowerCase().slice(0, 50)}`
    if (vistas.has(chave)) continue
    vistas.add(chave)
    perguntas.push({
      lente: r.lente,
      pergunta: p.pergunta,
      ancora: p.ancora,
      por_que_importa: p.por_que_importa,
      severidade: p.severidade || 'media',
    })
  }
}

const ordem = { bloqueante: 0, alta: 1, media: 2 }
perguntas.sort((a, b) => ordem[a.severidade] - ordem[b.severidade])

const bloqueantes = perguntas.filter((p) => p.severidade === 'bloqueante').length
log(
  `Grill: ${perguntas.length} perguntas unicas (${bloqueantes} bloqueantes) ` +
    `de ${censo.vivos.length}/${censo.esperado} lentes`,
)

// Cobertura vai no retorno: a skill precisa dizer ao humano que uma lente calou.
// "Nenhuma pergunta nesta lente" e "ninguem olhou por esta lente" sao resultados
// diferentes, e so o segundo pede nova rodada.
return {
  perguntas,
  bloqueantes,
  lentes_mortas: censo.mortos,
  cobertura: { esperado: censo.esperado, vivas: censo.vivos.length },
  summary:
    `${perguntas.length} perguntas de ${censo.vivos.length}/${censo.esperado} lentes` +
    (censo.mortos.length ? ` — COBERTURA INCOMPLETA: ${censo.mortos.join(', ')} sem retorno.` : '.'),
}
