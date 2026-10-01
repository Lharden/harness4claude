export const meta = {
  name: 'wf-verify-multimodel',
  description: 'Fase de verificacao do Harness: review multi-perspectiva paralelo do diff (cobertura de spec, correcao, seguranca, edge cases, regressoes), seguido de verificacao adversarial de cada finding para filtrar falsos-positivos. Retorna pass + findings confirmados.',
  phases: [
    { title: 'Review', detail: 'um agent por dimensao revisa os arquivos alterados' },
    { title: 'Adjudicate', detail: 'verificacao adversarial de cada finding' },
  ],
}

// args esperado (passado pelo harness-workflow):
//   { task_id, changed_files: [paths], spec_path, base_ref, repo_root? }
const input = args || {}
const changedFiles = Array.isArray(input.changed_files) ? input.changed_files : []
const specPath = input.spec_path || null
const baseRef = input.base_ref || 'HEAD'

// `repo_root` existe porque o diretorio da sessao pode nao ser o codigo revisado:
// sem ele, revisor e adjudicador resolvem caminho relativo no cwd da sessao. Na
// medicao de verify-adjudica-tudo esse cwd era o worktree da mae com os consertos
// ja aplicados. Vai para o prompt dos revisores E do adjudicador.
// So espacos nao e raiz: viraria "Repositorio:  ." e os agentes voltariam ao cwd.
const repoRoot = typeof input.repo_root === 'string' && input.repo_root.trim() ? input.repo_root.trim() : null
// "O escopo sao os arquivos listados" so quando ha lista: sem ela, o scope manda
// usar `git diff <base_ref>`, e as duas frases juntas se contradiziam.
const linhaRaiz = repoRoot
  ? `Repositorio: ${repoRoot}. Todo caminho abaixo e relativo a ele; leia e rode git la.` +
    (changedFiles.length ? ' O escopo sao os arquivos listados: nao compare com outro ramo.' : '') +
    '\n'
  : ''

// O Workflow nao le disco: raiz errada levaria os revisores a um diretorio vazio e a
// um `pass` verde por "nenhum finding". Quem le o disco e o revisor, entao ele diz.
const avisoRaiz = !repoRoot
  ? ''
  : changedFiles.length
    ? 'Se esse diretorio ou os arquivos listados nao existirem, devolva um achado critical dizendo isso.\n'
    : 'Se esse diretorio nao existir, devolva um achado critical dizendo isso.\n'

const scope =
  linhaRaiz +
  avisoRaiz +
  (changedFiles.length
    ? `Arquivos alterados:\n${changedFiles.map((f) => `- ${f}`).join('\n')}`
    : `Use \`git diff ${baseRef}\` para descobrir os arquivos alterados.`)
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

// agentType por dimensao — regra do CLAUDE.md global ("Fan-out (Workflow):
// cada agent() com o tipo da tabela"). Sem isto, todo agent() herda modelo e
// esforco da sessao que chama (Opus no esforco maximo), mesmo para revisao
// mecanica. `security` vai para juiz-alto-risco porque a tabela poe seguranca
// no risco alto; as demais dimensoes sao analise complexa de risco baixo.
const DIMENSIONS = [
  {
    key: 'spec-coverage',
    agentType: 'analise-complexa',
    prompt: `Voce e um revisor de COBERTURA DE SPEC. ${specLine}\n${scope}\n\nPara cada requisito (REQ), acceptance criterion (AC Given/When/Then) e user story da spec, verifique se ha implementacao e teste correspondentes nos arquivos alterados. Reporte como finding cada item da spec SEM cobertura (severity conforme a prioridade: P1 ausente=critical). Se nao houver spec, retorne findings vazio.`,
  },
  {
    key: 'correctness',
    agentType: 'analise-complexa',
    prompt: `Voce e um revisor de CORRECAO. ${scope}\n\nLeia os arquivos alterados e procure bugs reais: off-by-one, condicoes invertidas, null/None nao tratado, excecoes engolidas, contratos violados, retornos incorretos. Reporte apenas problemas concretos com evidencia (arquivo:linha). Nao reporte estilo.`,
  },
  {
    key: 'security',
    agentType: 'juiz-alto-risco',
    prompt: `Voce e um revisor de SEGURANCA. ${scope}\n\nProcure: injection (SQL/command/path), secrets hardcoded, validacao de entrada ausente, deserializacao insegura, authz/authn faltando, exposicao de dados sensiveis em logs. Reporte com severity. Sem achados = findings vazio.`,
  },
  {
    key: 'edge-cases',
    agentType: 'analise-complexa',
    prompt: `Voce e um revisor de EDGE CASES. ${scope}\n\nIdentifique entradas-limite nao tratadas: vazio, None, listas vazias, unicode, valores negativos/zero, concorrencia, timeouts, paths inexistentes. Reporte casos plausiveis e nao cobertos por teste.`,
  },
  {
    key: 'regressions',
    agentType: 'analise-complexa',
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
    agent(d.prompt, {
      label: `review:${d.key}`,
      phase: 'Review',
      schema: FINDINGS_SCHEMA,
      agentType: d.agentType,
    }),
  ),
)

const censoReview = censoNos('Review', DIMENSIONS.map((d) => d.key), reviews)

// Consolida + deduplica por (file, line, titulo inteiro). A chave antiga usava os 40
// primeiros caracteres do titulo: dois achados diferentes do mesmo arquivo com o
// mesmo comeco viravam um, e o segundo sumia sem `n` e sem destino. Na duplicata
// exata fica o rotulo mais grave, que e o que decide se ela vai ao adjudicador.
const PESO = { critical: 3, high: 2, medium: 1, low: 0 }
const porChave = new Map()
for (const r of censoReview.vivos) {
  for (const f of r.findings || []) {
    const key = `${f.file}::${f.line ?? ''}::${(f.title || '').trim().toLowerCase()}`
    const atual = porChave.get(key)
    if (!atual || (PESO[f.severity] ?? -1) > (PESO[atual.severity] ?? -1)) porChave.set(key, f)
  }
}
const findings = [...porChave.values()]
log(`Review: ${findings.length} findings unicos de ${censoReview.vivos.length}/${DIMENSIONS.length} dimensoes`)

if (findings.length === 0) {
  const completo = censoReview.mortos.length === 0
  return {
    pass: completo,
    critical_count: 0,
    nao_adjudicados: 0,
    findings: [],
    nos_mortos: censoReview.mortos,
    summary: completo
      ? `Nenhum finding nas ${DIMENSIONS.length} dimensoes.`
      : `Nenhum finding, MAS ${censoReview.mortos.length} de ${DIMENSIONS.length} dimensoes nao retornaram (${censoReview.mortos.join(', ')}) — cobertura incompleta, nao aprovado.`,
  }
}

// Adjudicacao so para critico/alto (regra do CLAUDE.md global: "revisao ampla
// adjudica so achados criticos e altos"). Medium/low nunca abrem agent() de
// adjudicacao — sairiam do fan-out do mesmo jeito que entraram, sem custo de
// refutacao adversarial, e vao no retorno com `adjudicado: false`.
const adjudicaveis = findings.filter((f) => f.severity === 'critical' || f.severity === 'high')
const naoAdjudicaveis = findings.filter((f) => f.severity !== 'critical' && f.severity !== 'high')

// Aresta descontaminada — o adjudicador recebe a ALEGACAO, nunca o raciocinio de quem
// a levantou. Este no so vale porque a janela dele e nova: verificacao adversarial
// funciona por contexto descorrelacionado. Passar `f.rationale` recorrela as duas pontas
// e devolve o vies de confirmacao que o fan-out foi aberto para eliminar — o refutador
// ancora na narrativa antes de abrir o arquivo. `rationale` segue no retorno, para o
// humano ler; so nao entra neste prompt.
phase('Adjudicate')
const adjudicated = await parallel(
  adjudicaveis.map((f) => () =>
    agent(
      linhaRaiz +
        `Tente REFUTAR esta alegacao de review. Se nao conseguir refutar com evidencia, ela e real.\n\n` +
        `Alegacao: ${f.title}\nArquivo: ${f.file}${f.line ? `:${f.line}` : ''}\nSeveridade alegada: ${f.severity}\n\n` +
        `Voce NAO recebe o raciocinio de quem levantou a alegacao — isso e proposital. ` +
        `Leia o codigo real e julgue por ele. Default para is_real=true apenas se a evidencia sustentar. ` +
        `Severidade alegada errada nao e motivo para refutar: refute so se o defeito nao existe no codigo.`,
      { label: `adjudicate:${f.file}`, phase: 'Adjudicate', schema: VERDICT_SCHEMA, agentType: 'juiz-alto-risco' },
    // Veredito vazio fica vazio: embrulhado em `{ ...f, verdict: null }` ele era um
    // objeto verdadeiro, o censo nao via o no morto, e o critico sumia com `pass: true`.
    ).then((v) => (v ? { ...f, verdict: v } : null)),
  ),
)

const censoAdj = censoNos('Adjudicate', adjudicaveis.map((f) => f.title), adjudicated)
// Confirmado: real com confianca. Refutado: nao real com confianca — sai do retorno.
// O resto e inconclusivo e vai para a triagem: ate 2026-10-01 ele saia de
// `confirmed` sem entrar em lugar nenhum, e um critico "real com confianca 0,3"
// sumia com `pass: true` (rodada 3 do verify de verify-adjudica-tudo).
const confiante = (f) => f.verdict.confidence >= 0.5
const confirmed = censoAdj.vivos.filter((f) => f.verdict.is_real && confiante(f))
const inconclusivos = censoAdj.vivos.filter((f) => !confiante(f))
const pendentes = [...naoAdjudicaveis, ...inconclusivos]

const criticals = confirmed.filter((f) => f.severity === 'critical' || f.severity === 'high')
log(
  `Adjudicate: ${confirmed.length} confirmados, ${criticals.length} criticos/altos` +
    (naoAdjudicaveis.length ? `, ${naoAdjudicaveis.length} medium/low nao adjudicados` : '') +
    (inconclusivos.length ? `, ${inconclusivos.length} com veredito inconclusivo` : ''),
)

// Finding nao julgado NAO e finding liberado: no morto bloqueia a aprovacao.
const nosMortos = [...censoReview.mortos, ...censoAdj.mortos]

// `pass` so sai verde sem achado aberto. Ate 2026-09-30 ele contava so
// critico/alto confirmado, e tres rodadas seguidas responderam `pass: true` com
// 49 medium/low abertos, 25 deles defeito real (docs/specs/verify-adjudica-tudo-medicao.md).
// O corte da adjudicacao fica: o achado nao adjudicado vai para a triagem da
// skill, com destino escrito conferido por scripts/checar_triagem.py.
const pass = criticals.length === 0 && nosMortos.length === 0 && pendentes.length === 0

// `n` e a posicao na lista: e o numero que a tabela de destino do closure-plan cita.
const saida = [
  ...confirmed.map((f) => ({
    title: f.title,
    severity: f.severity,
    file: f.file,
    line: f.line ?? null,
    rationale: f.rationale,
    confidence: f.verdict.confidence,
    adjudicado: true,
  })),
  ...pendentes.map((f) => ({
    title: f.title,
    severity: f.severity,
    file: f.file,
    line: f.line ?? null,
    rationale: f.rationale,
    confidence: f.verdict ? f.verdict.confidence : null,
    adjudicado: false,
  })),
].map((f, i) => ({ n: i + 1, ...f }))

return {
  pass,
  critical_count: criticals.length,
  nos_mortos: nosMortos,
  nao_adjudicados: pendentes.length,
  findings: saida,
  summary:
    `${confirmed.length} findings confirmados (${criticals.length} bloqueantes) de ${adjudicaveis.length} adjudicaveis` +
    (pendentes.length
      ? `, ${pendentes.length} aguardam triagem (nao adjudicados ou com veredito inconclusivo): cada um precisa de destino escrito ` +
        `no closure-plan, conferido por scripts/checar_triagem.py — ver a secao verify da skill harness-workflow`
      : '') +
    `.` +
    (nosMortos.length ? ` COBERTURA INCOMPLETA: ${nosMortos.length} no(s) sem retorno — ${nosMortos.join(', ')}.` : ''),
}
