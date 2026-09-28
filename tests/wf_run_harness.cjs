#!/usr/bin/env node
/*
 * wf_run_harness.cjs — executa de verdade um Workflow script (.js) do Harness
 * com agent/parallel/phase/log/args mockados, para testar COMPORTAMENTO
 * (nao so sintaxe, como validate_workflows.cjs faz).
 *
 * Uso: node wf_run_harness.cjs <arquivo.js> <cenario.json>
 * cenario.json:
 *   {
 *     "args": {...},                          // passado como `args` do workflow
 *     "reviews": { "<dimension-key>": { "findings": [...] } },
 *     "verdicts": { "<file>": { "is_real": bool, "confidence": num, "reason": str } }
 *   }
 * Saida (stdout): JSON { "result": <retorno do workflow>, "calls": {
 *   "review": [{ "label": ..., "agentType": ... }, ...],
 *   "adjudicate": [{ "label": ..., "agentType": ..., "file": ... }, ...],
 *   "phases": [...]
 * } }
 */
const fs = require('fs')

const [, , wfFile, scenarioFile] = process.argv
const src = fs.readFileSync(wfFile, 'utf8')
const scenario = JSON.parse(fs.readFileSync(scenarioFile, 'utf8'))

const calls = { review: [], adjudicate: [], phases: [], logs: [] }

function extractLabelKey(label, prefix) {
  return label.startsWith(prefix) ? label.slice(prefix.length) : null
}

async function agent(prompt, opts) {
  const label = (opts && opts.label) || ''
  if (label.startsWith('review:')) {
    const key = extractLabelKey(label, 'review:')
    calls.review.push({ label, agentType: opts.agentType })
    const reviews = scenario.reviews || {}
    // chave explicitamente presente com valor null = no morto (agent() nao
    // retornou nada); chave ausente = review sem findings (default).
    if (Object.prototype.hasOwnProperty.call(reviews, key)) return reviews[key]
    return { findings: [] }
  }
  if (label.startsWith('adjudicate:')) {
    const file = extractLabelKey(label, 'adjudicate:')
    calls.adjudicate.push({ label, agentType: opts.agentType, file })
    const verdicts = scenario.verdicts || {}
    if (Object.prototype.hasOwnProperty.call(verdicts, file)) return verdicts[file]
    return { is_real: true, confidence: 0.9, reason: 'default-mock' }
  }
  throw new Error(`agent() mock nao sabe responder para label='${label}'`)
}

async function parallel(fns) {
  return Promise.all(fns.map((fn) => fn()))
}

function phase(name) {
  calls.phases.push(name)
}

function log(msg) {
  calls.logs.push(String(msg))
}

async function pipeline() {
  throw new Error('pipeline() nao mockado neste harness')
}

function workflow() {
  throw new Error('workflow() nao mockado neste harness')
}

const budget = {}
const args = scenario.args || {}

async function main() {
  const body = src.replace(/^export\s+const\s+meta/m, 'const meta')
  const wrapped = `(async function __wf(agent, parallel, pipeline, phase, log, workflow, args, budget) {\n${body}\n})`
  // eslint-disable-next-line no-eval
  const fn = eval(wrapped)
  const result = await fn(agent, parallel, pipeline, phase, log, workflow, args, budget)
  process.stdout.write(JSON.stringify({ result, calls }))
}

main().catch((e) => {
  process.stderr.write(String((e && e.stack) || e))
  process.exit(1)
})
