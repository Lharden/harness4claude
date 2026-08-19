export const meta = {
  name: 'wf-context-scan',
  description: 'Varredura paralela do codebase para alimentar spec/design em tarefas L2: explora entry points, data model, padroes/convencoes, dependencias e riscos em agents simultaneos e consolida em um mapa estruturado.',
  phases: [{ title: 'Scan', detail: 'um agent por angulo de exploracao' }],
}

// args: { question, target_path }
const input = args || {}
const question = input.question || 'a tarefa atual'
const target = input.target_path ? `Foque em: ${input.target_path}` : 'Foque nas areas relevantes do projeto no diretorio atual.'

const SCAN_SCHEMA = {
  type: 'object',
  required: ['items'],
  additionalProperties: false,
  properties: {
    items: {
      type: 'array',
      items: {
        type: 'object',
        required: ['label', 'detail'],
        additionalProperties: false,
        properties: {
          label: { type: 'string' },
          detail: { type: 'string' },
          path: { type: ['string', 'null'] },
        },
      },
    },
  },
}

const ANGLES = [
  { key: 'entry-points', cat: 'files', prompt: `Mapeie os ENTRY POINTS relevantes para ${question}. ${target}\nListe arquivos/funcoes/CLIs/endpoints que sao pontos de entrada, com caminho. Use Glob/Grep/Read.` },
  { key: 'data-model', cat: 'constraints', prompt: `Mapeie o DATA MODEL relevante para ${question}. ${target}\nEntidades, dataclasses, schemas, storage, relacoes. Inclua caminhos.` },
  { key: 'patterns', cat: 'patterns', prompt: `Identifique PADROES E CONVENCOES do codebase relevantes para ${question}. ${target}\nEstilo, abstracoes, como features similares sao estruturadas, convencoes de teste/erro/logging.` },
  { key: 'dependencies', cat: 'constraints', prompt: `Liste DEPENDENCIAS (externas e internas) relevantes para ${question}. ${target}\nBibliotecas, versoes, modulos internos acoplados. Inclua de onde vem.` },
  { key: 'risks', cat: 'risks', prompt: `Identifique RISCOS para ${question}. ${target}\nCodigo fragil, alto acoplamento, ausencia de testes, side-effects, armadilhas de plataforma. Seja concreto.` },
]

// Censo de nos — `filter(Boolean)` descarta agente morto SEM AVISAR. Um scan de
// contexto que perde o angulo "risks" devolve um mapa que parece completo e nao
// tem riscos. Conte contra o esperado ANTES de filtrar, e nomeie quem morreu.
function censoNos(rotulo, rotulos, obtidos) {
  const mortos = rotulos.filter((_, i) => !obtidos[i])
  if (mortos.length) {
    log(`ATENCAO: ${rotulo} — ${mortos.length} de ${rotulos.length} nos nao retornaram nada: ${mortos.join(', ')}`)
  }
  return { vivos: obtidos.filter(Boolean), mortos, esperado: rotulos.length }
}

phase('Scan')
const results = await parallel(
  ANGLES.map((a) => () =>
    agent(a.prompt, { label: `scan:${a.key}`, phase: 'Scan', schema: SCAN_SCHEMA }).then((r) => ({ cat: a.cat, items: r.items || [] })),
  ),
)

const censo = censoNos('Scan', ANGLES.map((a) => a.key), results)

const out = { files: [], patterns: [], constraints: [], risks: [] }
for (const r of censo.vivos) {
  for (const it of r.items) {
    const entry = { label: it.label, detail: it.detail, path: it.path ?? null }
    if (r.cat === 'files') out.files.push(entry)
    else if (r.cat === 'patterns') out.patterns.push(entry)
    else if (r.cat === 'risks') out.risks.push(entry)
    else out.constraints.push(entry)
  }
}
log(
  `Context scan: ${out.files.length} entradas, ${out.patterns.length} padroes, ` +
    `${out.constraints.length} constraints, ${out.risks.length} riscos ` +
    `(${censo.vivos.length}/${censo.esperado} angulos)`,
)

// Cobertura vai no retorno: quem consome precisa saber que o mapa tem buraco.
out.cobertura = { esperado: censo.esperado, vivos: censo.vivos.length, angulos_mortos: censo.mortos }
return out
