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

phase('Review')
const reviews = await parallel(
  DIMENSIONS.map((d) => () =>
    agent(d.prompt, { label: `review:${d.key}`, phase: 'Review', schema: FINDINGS_SCHEMA }),
  ),
)

// Consolida + deduplica por (file, title) aproximado
const seen = new Set()
const findings = []
for (const r of reviews.filter(Boolean)) {
  for (const f of r.findings || []) {
    const key = `${f.file}::${(f.title || '').toLowerCase().slice(0, 40)}`
    if (seen.has(key)) continue
    seen.add(key)
    findings.push(f)
  }
}
log(`Review: ${findings.length} findings unicos de ${DIMENSIONS.length} dimensoes`)

if (findings.length === 0) {
  return { pass: true, critical_count: 0, findings: [], summary: 'Nenhum finding nas 5 dimensoes.' }
}

phase('Adjudicate')
const adjudicated = await parallel(
  findings.map((f) => () =>
    agent(
      `Tente REFUTAR este finding de review. Se nao conseguir refutar com evidencia, ele e real.\n\n` +
        `Finding: ${f.title}\nArquivo: ${f.file}${f.line ? `:${f.line}` : ''}\nSeveridade alegada: ${f.severity}\nJustificativa: ${f.rationale}\n\n` +
        `Leia o codigo real e decida. Default para is_real=true apenas se a evidencia sustentar.`,
      { label: `adjudicate:${f.file}`, phase: 'Adjudicate', schema: VERDICT_SCHEMA },
    ).then((v) => ({ ...f, verdict: v })),
  ),
)

const confirmed = adjudicated
  .filter(Boolean)
  .filter((f) => f.verdict && f.verdict.is_real && f.verdict.confidence >= 0.5)

const criticals = confirmed.filter((f) => f.severity === 'critical' || f.severity === 'high')
log(`Adjudicate: ${confirmed.length} confirmados, ${criticals.length} criticos/altos`)

return {
  pass: criticals.length === 0,
  critical_count: criticals.length,
  findings: confirmed.map((f) => ({
    title: f.title,
    severity: f.severity,
    file: f.file,
    line: f.line ?? null,
    rationale: f.rationale,
    confidence: f.verdict.confidence,
  })),
  summary: `${confirmed.length} findings confirmados (${criticals.length} bloqueantes) de ${findings.length} brutos.`,
}
