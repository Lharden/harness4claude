export const meta = {
  name: 'wf-verify-multimodel',
  description: 'Fase de verificacao do Harness: review multi-perspectiva paralelo do diff (cobertura de spec, correcao, seguranca, edge cases, regressoes), seguido de verificacao adversarial de cada finding para filtrar falsos-positivos. Retorna pass + findings confirmados.',
  phases: [
    { title: 'Review', detail: 'um agent por dimensao revisa os arquivos alterados' },
    { title: 'Adjudicate', detail: 'verificacao adversarial de cada finding' },
  ],
}

// args esperado (passado pelo harness-workflow):
//   { task_id, changed_files: [paths], spec_path, base_ref }
const input = args || {}
const changedFiles = Array.isArray(input.changed_files) ? input.changed_files : []
const specPath = input.spec_path || null
const baseRef = input.base_ref || 'HEAD'

const scope = changedFiles.length
  ? `Arquivos alterados:\n${changedFiles.map((f) => `- ${f}`).join('\n')}`
  : `Use \`git diff ${baseRef}\` para descobrir os arquivos alterados.`
const specLine = specPath ? `Spec de referencia: ${specPath} (leia-a primeiro).` : 'Sem spec formal (tarefa bug/refactor).'

// Schema de retorno de cada reviewer
const FINDINGS_SCHEMA = {
  type: 'object',
  required: ['findings'],
  additionalProperties: false,
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        required: ['title', 'severity', 'file', 'rationale'],
        additionalProperties: false,
        properties: {
          title: { type: 'string' },
          severity: { type: 'string', enum: ['critical', 'high', 'medium', 'low'] },
          file: { type: 'string' },
          line: { type: ['integer', 'null'] },
          rationale: { type: 'string' },
        },
      },
    },
  },
}

const VERDICT_SCHEMA = {
  type: 'object',
  required: ['is_real', 'confidence', 'reason'],
  additionalProperties: false,
  properties: {
    is_real: { type: 'boolean', description: 'true se o finding se confirma apos tentativa de refutacao' },
    confidence: { type: 'number', minimum: 0, maximum: 1 },
    reason: { type: 'string' },
  },
}

const DIMENSIONS = [
  {
    key: 'spec-coverage',
    prompt: `Voce e um revisor de COBERTURA DE SPEC. ${specLine}\n${scope}\n\nPara cada requisito (REQ), acceptance criterion (AC Given/When/Then) e user story da spec, verifique se ha implementacao e teste correspondentes nos arquivos alterados. Reporte como finding cada item da spec SEM cobertura (severity conforme a prioridade: P1 ausente=critical). Se nao houver spec, retorne findings vazio.`,
  },
  {
    key: 'correctness',
    prompt: `Voce e um revisor de CORRECAO. ${scope}\n\nLeia os arquivos alterados e procure bugs reais: off-by-one, condicoes invertidas, null/None nao tratado, excecoes engolidas, contratos violados, retornos incorretos. Reporte apenas problemas concretos com evidencia (arquivo:linha). Nao reporte estilo.`,
  },
  {
    key: 'security',
    prompt: `Voce e um revisor de SEGURANCA. ${scope}\n\nProcure: injection (SQL/command/path), secrets hardcoded, validacao de entrada ausente, deserializacao insegura, authz/authn faltando, exposicao de dados sensiveis em logs. Reporte com severity. Sem achados = findings vazio.`,
  },
  {
    key: 'edge-cases',
    prompt: `Voce e um revisor de EDGE CASES. ${scope}\n\nIdentifique entradas-limite nao tratadas: vazio, None, listas vazias, unicode, valores negativos/zero, concorrencia, timeouts, paths inexistentes. Reporte casos plausiveis e nao cobertos por teste.`,
  },
  {
    key: 'regressions',
    prompt: `Voce e um revisor de REGRESSOES. ${scope}\n\nVerifique se as mudancas podem quebrar comportamento existente: assinaturas alteradas, contratos publicos, side-effects, imports removidos, testes existentes que deixariam de passar. Rode os testes se possivel e reporte falhas.`,
  },
]

// Censo de nos — `filter(Boolean)` descarta agente morto SEM AVISAR, e num
// fan-out o relatorio continua com cara de completo. Numa fase de verificacao
// isso e a pior falha possivel: silenciosa e com a forma de uma boa noticia.
// Conte contra o esperado ANTES de filtrar, e nomeie quem morreu.
function censoNos(rotulo, rotulos, obtidos) {
  const mortos = rotulos.filter((_, i) => !obtidos[i])
  if (mortos.length) {
    log(`ATENCAO: ${rotulo} — ${mortos.length} de ${rotulos.length} nos nao retornaram nada: ${mortos.join(', ')}`)
  }
  return { vivos: obtidos.filter(Boolean), mortos, esperado: rotulos.length }
}

phase('Review')
const reviews = await parallel(
  DIMENSIONS.map((d) => () =>
    agent(d.prompt, { label: `review:${d.key}`, phase: 'Review', schema: FINDINGS_SCHEMA }),
  ),
)

const censoReview = censoNos('Review', DIMENSIONS.map((d) => d.key), reviews)

// Consolida + deduplica por (file, title) aproximado
const seen = new Set()
const findings = []
for (const r of censoReview.vivos) {
  for (const f of r.findings || []) {
    const key = `${f.file}::${(f.title || '').toLowerCase().slice(0, 40)}`
    if (seen.has(key)) continue
    seen.add(key)
    findings.push(f)
  }
}
log(`Review: ${findings.length} findings unicos de ${censoReview.vivos.length}/${DIMENSIONS.length} dimensoes`)

if (findings.length === 0) {
  const completo = censoReview.mortos.length === 0
  return {
    pass: completo,
    critical_count: 0,
    findings: [],
    nos_mortos: censoReview.mortos,
    summary: completo
      ? `Nenhum finding nas ${DIMENSIONS.length} dimensoes.`
      : `Nenhum finding, MAS ${censoReview.mortos.length} de ${DIMENSIONS.length} dimensoes nao retornaram (${censoReview.mortos.join(', ')}) — cobertura incompleta, nao aprovado.`,
  }
}

// Aresta descontaminada — o adjudicador recebe a ALEGACAO, nunca o raciocinio de quem
// a levantou. Este no so vale porque a janela dele e nova: verificacao adversarial
// funciona por contexto descorrelacionado. Passar `f.rationale` recorrela as duas pontas
// e devolve o vies de confirmacao que o fan-out foi aberto para eliminar — o refutador
// ancora na narrativa antes de abrir o arquivo. `rationale` segue no retorno, para o
// humano ler; so nao entra neste prompt.
phase('Adjudicate')
const adjudicated = await parallel(
  findings.map((f) => () =>
    agent(
      `Tente REFUTAR esta alegacao de review. Se nao conseguir refutar com evidencia, ela e real.\n\n` +
        `Alegacao: ${f.title}\nArquivo: ${f.file}${f.line ? `:${f.line}` : ''}\nSeveridade alegada: ${f.severity}\n\n` +
        `Voce NAO recebe o raciocinio de quem levantou a alegacao — isso e proposital. ` +
        `Leia o codigo real e julgue por ele. Default para is_real=true apenas se a evidencia sustentar.`,
      { label: `adjudicate:${f.file}`, phase: 'Adjudicate', schema: VERDICT_SCHEMA },
    ).then((v) => ({ ...f, verdict: v })),
  ),
)

const censoAdj = censoNos('Adjudicate', findings.map((f) => f.title), adjudicated)
const confirmed = censoAdj.vivos.filter(
  (f) => f.verdict && f.verdict.is_real && f.verdict.confidence >= 0.5,
)

const criticals = confirmed.filter((f) => f.severity === 'critical' || f.severity === 'high')
log(`Adjudicate: ${confirmed.length} confirmados, ${criticals.length} criticos/altos`)

// Finding nao julgado NAO e finding liberado: no morto bloqueia a aprovacao.
const nosMortos = [...censoReview.mortos, ...censoAdj.mortos]

return {
  pass: criticals.length === 0 && nosMortos.length === 0,
  critical_count: criticals.length,
  nos_mortos: nosMortos,
  findings: confirmed.map((f) => ({
    title: f.title,
    severity: f.severity,
    file: f.file,
    line: f.line ?? null,
    rationale: f.rationale,
    confidence: f.verdict.confidence,
  })),
  summary:
    `${confirmed.length} findings confirmados (${criticals.length} bloqueantes) de ${findings.length} brutos.` +
    (nosMortos.length ? ` COBERTURA INCOMPLETA: ${nosMortos.length} no(s) sem retorno — ${nosMortos.join(', ')}.` : ''),
}
