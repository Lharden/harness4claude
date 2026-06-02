#!/usr/bin/env node
/*
 * validate_workflows.cjs — valida os Workflow scripts (.js) do Harness.
 *
 * Workflow scripts NAO sao modulos ESM standalone: o runtime do Workflow tool
 * injeta agent/parallel/pipeline/phase/log/workflow/args/budget como globais e
 * executa o corpo dentro de uma async function (por isso top-level await/return
 * sao validos). Para validar a SINTAXE corretamente, envolvemos o corpo numa
 * async function antes de compilar com `new Function`.
 *
 * Tambem confere que cada script comeca com `export const meta` contendo
 * name/description/phases.
 *
 * Uso: node validate_workflows.cjs [arquivo.js ...]
 *      (sem args: valida todos os *.js neste diretorio)
 * Saida: linha por arquivo + exit code 0 (tudo OK) ou 1 (algum erro).
 */
const fs = require('fs')
const path = require('path')
const vm = require('vm')

const HOOKS = 'agent,parallel,pipeline,phase,log,workflow,args,budget'

function listTargets() {
  if (process.argv.length > 2) return process.argv.slice(2)
  const dir = __dirname
  return fs
    .readdirSync(dir)
    .filter((f) => f.endsWith('.js'))
    .map((f) => path.join(dir, f))
}

function validate(file) {
  const src = fs.readFileSync(file, 'utf8')
  const errors = []

  if (!/^export\s+const\s+meta\s*=/m.test(src)) {
    errors.push("falta 'export const meta = {...}' no inicio")
  }
  for (const field of ['name', 'description', 'phases']) {
    if (!new RegExp(`\\b${field}\\s*:`).test(src)) {
      errors.push(`meta sem campo '${field}'`)
    }
  }

  // Sintaxe: envolve o corpo (sem 'export') numa async function e COMPILA com
  // vm.Script (valida sintaxe sem NUNCA executar o codigo).
  try {
    const body = src.replace(/^export\s+const\s+meta/m, 'const meta')
    const wrapped = `async function __wf(${HOOKS}){\n${body}\n}`
    new vm.Script(wrapped, { filename: path.basename(file) })
  } catch (e) {
    errors.push(`sintaxe: ${e.message}`)
  }
  return errors
}

let allOk = true
for (const file of listTargets()) {
  const errors = validate(file)
  if (errors.length) {
    allOk = false
    console.log(`${path.basename(file)} -> ERRO: ${errors.join('; ')}`)
  } else {
    console.log(`${path.basename(file)} -> OK`)
  }
}
process.exit(allOk ? 0 : 1)
