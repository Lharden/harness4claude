# science_intent cercado pela presença do MCP — verificação

Origem: decisão 5 de `master-harness/docs/decisoes-capacidades-orfas.md` (seções 5 e 10),
tomada pelo usuário em 2026-10-07: registrar o `science_harness` no Claude e cercar o hook
pela presença dele. Sessão de 2026-10-07, ramo `claude/nervous-shaw-348dc1`.

## Antes

- O Codex registra `science_harness` (`~/.codex/config.toml:194-196`, `shs.exe claims-mcp`,
  `tools.server_info approval_mode = "approve"`). O Claude não registrava.
- `hooks/science_intent.py` disparava só pela regex e mandava usar `science_harness`:
  995 emissões em `~/.claude/harness/emissions.jsonl` de 2026-09-02 a 2026-10-07, todas
  para uma ferramenta que o Claude não tinha.
- A sonda `tests/test_contract_adapter.py::test_science_intent_routes_evidence_prompts`
  provava o roteamento e passava com o MCP ausente.

## O que mudou

| Item | Onde | Evidência |
|---|---|---|
| Registro em escopo de usuário | `~/.claude.json` via `claude mcp add --scope user science_harness -- C:\Users\LHarden2\.local\bin\shs.exe claims-mcp`, com aprovação do usuário | `claude mcp get science_harness` → `Status: ✔ Connected` |
| Equivalente do `approval_mode = "approve"` | `~/.claude/settings.json` → `permissions.allow: ["mcp__science_harness__server_info"]`, com aprovação do usuário | as outras 8 ferramentas continuam pedindo aprovação |
| Servidor responde | handshake MCP stdio contra o executável real | `server_info` → corpora `hce`, `probe`, `rsl`, `timeseries`; `retrieval: bm25`; `isError: false` |
| Cerco do hook | `hooks/science_intent.py::registro_science` | regex **e** registro alcançável |
| Sonda exercita o caminho até o MCP | `tests/test_contract_adapter.py::test_science_intent_routes_evidence_prompts` | sobe o servidor do registro que o hook aceitou e chama `server_info` |

### Como o hook detecta a presença

Sem rede e sem subir processo, porque roda em todo prompt. Lê o `.claude.json`
(`$CLAUDE_CONFIG_DIR/.claude.json`, ou `~/.claude.json`), o mesmo arquivo que `claude mcp add`
grava:

1. escopo local do projeto (`projects[<cwd ou ancestral>].mcpServers`), depois o de usuário
   (`mcpServers`);
2. `projects[...].disabledMcpServers` com `science_harness` (desligado pelo `/mcp`) conta como
   ausente;
3. "alcançável" = `command` existe no disco ou no PATH (ou o registro tem `url`);
4. qualquer falha de leitura conta como ausente, como a skill `science-evidence` trata o MCP
   ausente (UNOBSERVED).

Custo medido com o `.claude.json` real (157 KB): 3,0 ms por checagem (média de 20).

## Testes e falsificação

`tests/test_science_intent.py` (12 testes) roda o hook como processo, com `CLAUDE_CONFIG_DIR`
temporário:

- MCP ausente + prompt científico → não emite;
- MCP ausente + prompt de infraestrutura com "evidência" → não emite;
- MCP presente + prompt científico → emite;
- também: presente + prompt sem ciência, config ausente, config corrompida, executável
  inexistente, escopo local, subdiretório do projeto, escopo de outro projeto, desligado
  pelo `/mcp`.

Metades medidas:

- Os 12 testes contra o hook antigo: 7 reprovaram (todos os "não emite"), 5 passaram.
- `test_CONTROLE_codigo_antigo_emitia_sem_mcp` roda o hook de `22d0442` (SHA fixo) e exige
  que ele emita sem o MCP, nos dois prompts.
- Sonda do contrato com o duble do servidor removido (`tests/fixtures/fake_claims_mcp.py`):
  reprovou; restaurado: passou.

## Contrato

O nome da sonda não mudou, então `contract/behavioral-probes.json` e o espelho em
`scripts/contract_adapter.py` continuam válidos. Nada no contrato canônico do master-harness
foi tocado.

## O que falta (fora deste ramo)

1. **Decisão 8 (master-harness):** a regra "a sonda exercita o chamador de produção" ainda
   não está no `docs/CONTEXT.md` nem no contrato. Esta sonda já a cumpre do lado do Claude,
   com um limite que o registro da regra deve decidir: o chamador final do MCP é o modelo,
   que segue a skill, e esse passo não é determinístico. A sonda prova hook → registro →
   servidor responde, com um duble no formato do `shs.exe claims-mcp`, não o executável real
   (que a suíte não pode exigir em toda máquina).
2. **Lado do Codex:** `harness4codex` usa
   `tests/test_science_adapter.py::test_scientific_evidence_prompts_activate_the_read_only_mcp_route`.
   Se ele cumpre a decisão 8 é com o harness4codex.
3. **Regex larga:** com o MCP registrado nesta máquina, prompt de infraestrutura com
   "evidência" volta a emitir. A decisão 5 aceitou isso; estreitar a regex ou cercar por
   projeto seria decisão nova.
