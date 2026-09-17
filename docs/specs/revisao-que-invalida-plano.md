# Plano — o que fazer, ranqueado

**Companheiro de** `revisao-que-invalida-mapa.md`. Toda afirmação de efeito aqui tem número lá.
**Nada neste plano foi aplicado.** É proposta para o autor ler e decidir.

---

## A correção que muda o plano do autor

A semente ranqueia *"consertar o extrator de caminhos"* como causa raiz de 30% das invalidações. Medido: o extrator é a causa de **49,9% das linhas** de `files`, mas **não é a causa da maioria das invalidações**.

Nesta sessão, verdade-base, nove invalidações:

| causa | invalidações |
|---|---:|
| placeholder `shell-command` — toda invocação de interpretador | **8** |
| alvo de redirecionamento fora do repositório | 1 |
| lixo do extrator | 0 |
| mudança real de código | **0** |

O placeholder (`harness-transactional.py:582`, `alvos or ["shell-command"]`) existe porque `python - <<PY … write_text(…) PY` **realmente escreve**, e não há como saber lendo a linha de comando. Ele está certo pelo que sabe. E é ele, não o lixo, que produz o volume.

**Consequência para a ordem:** consertar o extrator e o filtro de raiz teria removido **1 das 9** invalidações desta sessão. Não são desperdício — eles consertam o **outro** consumidor (§7 do mapa, a telemetria `actual_level`), que o conserto de causa raiz não toca. Mas quem espera que eles esvaziem o portão vai medir depois e não achar a melhora.

Os dois grupos servem consumidores diferentes e nenhum substitui o outro:

- **R1** serve ao **portão** — decide quando a evidência expira.
- **R2 + R3 + R6** servem à **telemetria** — decidem o rótulo `actual_level`, contra o qual `proxy_regex_vs_observado = 0,297` é calculado.

---

## Ranque por força

### R1 — o portão pergunta à árvore, não ao comando

**Causa raiz que ataca:** `code_revision` é um contador monotônico de *escritas observadas*. A pergunta que o portão quer fazer é *a árvore que a suíte mediu ainda é esta?* — e essa pergunta tem uma resposta direta que ninguém está lendo.

**Forma:** `record_evidence` grava, junto do resultado, a impressão digital da árvore no momento da medição (`git rev-parse HEAD` + hash de `git status --porcelain` incluindo não-rastreados). `_has_fresh_test_evidence` (`transactional_state.py:1118-1131`) compara **impressão digital**, não `code_revision`. `code_revision` continua existindo e continua subindo — vira histórico, deixa de ser o critério.

**Efeito medido:** colapsa os quatro sintomas de uma vez, porque nenhum deles muda a árvore — o lixo do extrator (§2), a escrita fora da raiz (§3 classe B), o placeholder (8 de 9 desta sessão), e o laço do `complete` (§6.4). As nove linhas de `evidence` idênticas da §6.3 teriam sido **uma**.

**Risco, nomeado em vez de escondido:** muda **o que o portão mede**. Hoje `pip install`, `npm install` e mudança de variável de ambiente sobem `code_revision` pelo placeholder e invalidam — corretamente, porque mudam o resultado da suíte sem tocar na árvore. Com R1 eles passariam despercebidos. **Isto é um afrouxamento real e não deve ser aceito em silêncio.** Duas saídas, e a escolha é do autor:
   (a) manter o placeholder só para comandos que casem um padrão de mutação de ambiente (`pip|uv|npm|poetry|conda` + `install|sync|add|remove`), aceitando que a lista é incompleta e declarando isso;
   (b) incluir na impressão digital um hash do ambiente (`pip freeze`), pagando o custo a cada gravação de evidência.

**Custo operacional:** um `git status --porcelain` por gravação de evidência — não por chamada de ferramenta. Barato.

**Falsificação — as duas metades, medidas:**

| metade | o que provar | como |
|---|---|---|
| **pega o defeito** | evidência gravada, **depois** uma linha real mudada em `src/`, `complete` **recusa** | task de teste; `record_evidence`; `Path('scripts/x.py').write_text(…)`; `complete` levanta `StateTransitionError` |
| **deixa de punir** | evidência gravada, depois 20 comandos que não tocam a árvore, `complete` **aceita** | os mesmos 20 comandos do §5 do mapa: `python -c`, redirect para o scratchpad, `git add`, `git commit`, heredoc com `>` no corpo |
| **não afrouxa** | `pip install` entre a evidência e o `complete` **recusa** (sob a opção escolhida) | falha por construção se (a) e (b) forem ambos rejeitados — e aí o afrouxamento fica **escrito**, não silencioso |

Sem a terceira linha, R1 é indistinguível de silenciar o portão.

---

### R2 — a mesma pergunta nos dois caminhos

**Causa raiz:** `b771b6b` ensinou `counts_as_modified_file` a distinguir *escreveu algo* de *mudou o código sob teste*, e ligou isso no caminho `Edit`/`Write` (`harness-reclassify.sh:135`). O caminho do shell (`harness-transactional.py:580-582`) chama `touch_files` sem passar por ele. Mesmo arquivo, mesmo lugar, dois veredictos — medido ao vivo no §5 do mapa.

**Forma:** resolver a raiz do projeto a partir do `cwd` do payload (`find_repo_root`, como `harness-reclassify.sh:131` já faz) e filtrar `alvos` por `inside_root` antes de `touch_files`. **Se sobrar lista vazia mas o comando não for read-only, o placeholder continua** — a segunda metade de `:576-579` não pode ser perdida.

**Efeito medido:** remove a classe B, **133 de 467 linhas = 28,5%**. Sobre invalidações desta sessão: 1 de 9.

**Consumidor nomeado:** `record_signal.py:68` → `actual_level`. É ele que melhora, não o portão.

**Risco:** sem `cwd` no payload não há raiz, e aí conta tudo — o fail-closed que `harness-reclassify.sh:123-126` já escolheu e já custou 7 testes quando alguém tentou cair em `os.getcwd()`. Repetir a decisão, não reabri-la.

**Falsificação:**

| metade | prova |
|---|---|
| pega | `echo x > <repo>/src/novo.py` sobe `code_revision` e cria linha em `files` |
| deixa de punir | `echo x > <scratchpad>/nota.txt` **não** cria linha (hoje cria — é a linha `rev=7` desta sessão) |
| não cega | comando sem `cwd` no payload continua contando tudo |
| simetria | `Write` e `>` para o **mesmo caminho** devolvem o **mesmo** veredicto, nos dois lados da fronteira — é o teste que não existe hoje |

---

### R3 — alvo que não pode ser um arquivo não entra, e a recusa é declarada

**Causa raiz:** `_tokenize` é um tokenizador de shell POSIX aplicado a strings que muitas vezes não são comandos POSIX — corpo de heredoc, código de programa, PowerShell (§6.2 do mapa). `shell_write_targets.considerar` (`:361-367`) rejeita exatamente quatro coisas: vazio, operador, `-…`, destino nulo. **Não há nenhum teste de que o candidato possa ser um caminho.**

**Evidência nova, colhida enquanto este plano era escrito:** o commit do mapa entrou como `git commit -F - <<'MSGEOF'`, e `'MSGEOF'` virou linha em `files` (revisão 21 desta task). Causa: a última linha da mensagem é a de atribuição obrigatória, `Co-Authored-By: … <noreply@anthropic.com>`, e o `>` que fecha o e-mail é operador para `_tokenize` — o token seguinte é o delimitador de fechamento do heredoc. **Toda mensagem de commit escrita por heredoc dispara isto**, porque a linha de atribuição sempre termina em `>`.

**Forma, e a ordem importa:**

1. **Reconhecer heredoc antes de tokenizar.** É a correção de causa raiz: detectar `<<'DELIM'` / `<<DELIM` e **excluir o corpo** da tokenização. Sozinha, mata os quatro casos reproduzidos no §6.2 do mapa.
2. **Rejeitar candidato que não pode ser um caminho** — a régua declarada na §2 do mapa. É defesa em profundidade para PowerShell e para a aspa ímpar que a etapa 1 não pega.
3. **Registrar toda recusa.** Uma linha em `events` ou num `jsonl` com `(comando_hash, candidato, motivo)`. **Sem isto, trocar ruído por cegueira é exatamente o mesmo erro numa direção nova** — e o mapa não teria conseguido medir nada se as recusas de hoje fossem silenciosas.

**Efeito medido:** classe A, **233 de 467 linhas = 49,9%**. Sobre invalidações desta sessão: 0 — o lixo cria linha, não sobe contador que já ia subir.

**Risco, e é o sério:** um candidato **rejeitado por engano** é uma escrita real que some do contador. O erro é na direção perigosa — a oposta de `inside_root`, que é fail-closed de propósito. Por isso a régua da etapa 2 tem de ser *"não pode ser um caminho"*, nunca *"não parece um caminho"*, e por isso a etapa 3 não é opcional.

**Falsificação:**

| metade | prova |
|---|---|
| pega | os 6 casos não-controle de `scratchpad/falsificar2.py` devolvem `[]` ou só o alvo legítimo |
| **deixa de punir** | os 2 controles continuam devolvendo o alvo; e `tests/test_transactional_hook.py:459-488` (5 testes já existentes) continuam verdes sem edição |
| não cega | `cat > "docs/nota final.md" <<'EOF'` devolve o `.md` **e** exclui o corpo — alvo legítimo e corpo ignorado no mesmo comando |
| regressão de banco | reprocessar os 467 caminhos: os 92 da classe D sobrevivem, **todos** |

A última linha é a que vale mais: é um corpus real de 467 entradas com o rótulo já feito, disponível agora, de graça.

---

### R4 — a receita do manual derrota a isenção que existe para ela

**Causa raiz:** `is_state_management` isenta `state_cli.py` só quando o comando não tem composição (`harness-transactional.py:171`). Os três blocos `bash` em `skills/harness-workflow/SKILL.md` que invocam CLI de estado — linhas **45-54**, **74-80** e **378-395** — são todos compostos (`_has_unquoted_shell_composition = True` nos três). Medido: receita literal → `is_state_management = False`; forma atômica → `True`.

**Forma — e este é o conserto de uma linha que a semente autorizou propor:** substituir a composição por chamadas atômicas separadas, cada uma numa invocação. Resolver `STATE_DIR` num comando; usar o valor literal no seguinte.

**Já demonstrado nesta sessão, ponta a ponta.** Fechar esta própria task exigiu **sete** chamadas atômicas seguidas — `evidence`, 3× `transition`, 3× `artifact`, `complete`. `code_revision` ficou parado em **24 nas sete**, `verified` ficou em `1`, e o `complete` devolveu `status: "done"`. Ver mapa §5.1.

Não é proposta não testada: é o caminho que fechou uma task hoje, enquanto o caminho do manual travava três outras sessões. **A isenção já funciona; ela só nunca é alcançada por quem copia o bloco.**

**Efeito:** fecha o laço do Achado 5 sozinho, **sem tocar em código de produção e sem tocar na régua**. Três sessões chegaram ao laço por caminhos independentes porque as três seguiram o manual.

**Falsificação:**

```python
# test_receita_do_manual_e_isenta  — trava a doc contra o código
import re, pathlib
skill = pathlib.Path("skills/harness-workflow/SKILL.md").read_text(encoding="utf-8")
for bloco in re.findall(r"```bash\n(.*?)```", skill, re.S):
    for linha in bloco.splitlines():
        if "state_cli.py" in linha or "confirm_classification.py" in linha:
            assert hook.is_state_management(linha), linha
```

| metade | prova |
|---|---|
| pega | o teste **reprova** no `SKILL.md` de hoje (as três receitas) |
| deixa de punir | passa depois da reescrita, e `state_cli.py … && sed -i …` continua **não** isento |

Este teste é o consumidor que faltava: hoje nada liga a doc à condição que ela precisa satisfazer, e foi por isso que a distância entre as duas sobreviveu desde 2026-09-02.

---

### R5 — preservar o rastro da invalidação

**Causa raiz:** `INSERT OR IGNORE` (`transactional_state.py:936`) descarta a informação de *quem* causou a subida quando o caminho já foi visto. **2 228 de 2 594 invalidações (85,9%) são invisíveis.** Este mapa foi escrito sobre os 14,1% que sobraram, e disse isso em voz alta porque não tinha alternativa.

**Forma:** uma tabela `touches(task_id, code_revision, path, origem)` com uma linha por toque, ou um contador `touch_count` por caminho. `files` continua igual; nada que lê hoje muda.

**Efeito:** zero sobre o portão. **Torna mensurável a próxima pergunta.** Sem isto, medir se R1/R2/R3 funcionaram é impossível pelo mesmo motivo que tornou este mapa parcial.

**Falsificação:** tocar o mesmo caminho 3× produz 1 linha em `files` e 3 em `touches`; a soma de `touches` por task bate com `tasks.code_revision`. É uma identidade aritmética — reprova se desviar de 1.

**Ordem:** se for feito, **antes** de R1/R2/R3, senão a medição do antes se perde.

---

### R6 — o custo residual honesto, para ficar escrito

`_SOMENTE_LEITURA` (`:240-244`) deliberadamente exclui interpretador, e a docstring diz por quê: *"uma afirmação errada apaga alteração de código do contador"*. Correto. Consequência aceita: **todo `python -c` de diagnóstico invalida a evidência** — 8 das 9 invalidações desta sessão.

**Não recomendo mexer.** Sem R1 não há como distinguir um `python` que lê de um que escreve, e adivinhar erra na direção perigosa. Fica registrado como **limite conhecido**, não como pendência: se R1 for aprovado, some sozinho; se não for, é o preço do portão e o portão vale o preço.

---

## Ordem de execução recomendada

Diferente do ranque por força, porque leva em conta dependência e risco.

| # | item | por quê nesta posição |
|---|---|---|
| 1 | **R4** | uma linha ×3, arquivo de doc, zero risco de produção, fecha o Achado 5 inteiro. Independente de tudo. |
| 2 | **R5** | instrumentação antes da intervenção. Sem ela não há "antes" para comparar. |
| 3 | **R3** etapa 1 (heredoc) + etapa 3 (registrar recusa) | causa raiz do lixo, corpus de validação de 467 entradas já disponível |
| 4 | **R2** | cirúrgico, reusa função já testada, fecha a assimetria |
| 5 | **R3** etapa 2 (régua de caminho impossível) | defesa em profundidade; só depois que a etapa 3 estiver registrando |
| 6 | **R1** | maior força, maior risco, e é o único que precisa de decisão humana sobre o afrouxamento (a) ou (b) |

**R1 exige gate humano.** Os cinco anteriores são consertos dentro do desenho atual. R1 troca a grandeza que o portão mede, e essa troca tem um afrouxamento embutido que nenhuma engenharia remove — só declara. É decisão do autor, não do executor.

---

## O que este plano recusa

- **Afrouxar a régua.** `REGRA_TESTE_VALIDO` está certa e `tests_passed > 0` já impediu uma classe de fraude (`transactional_state.py:45-47`).
- **TTL de evidência.** Faria um número velho passar por fresco pelo relógio, que é a falha que o portão existe para impedir.
- **Escape hatch.** Portão com saída não é portão.
- **Remover o placeholder.** Sem R1 não há substituto, e removê-lo deixaria escrita real passar.

O portão está certo na intenção. Está medindo a grandeza errada, e cada item acima corrige uma parte dessa medição sem tocar no rigor.

---

## Aberto

- `[NEEDS CLARIFICATION]` R1 afrouxa o portão para mutação de ambiente. Opção (a) lista de padrões incompleta e declarada, ou (b) hash de `pip freeze` a cada evidência? **Decisão do autor.**
- `[NEEDS CLARIFICATION]` a impressão digital da árvore cobre não-rastreados e `.gitignore`d? Testes que leem arquivo ignorado existiriam fora dela. Não medido nesta sessão.
- `[superado: "o extrator é a causa raiz de 30% das invalidações"]` — é a causa de 49,9% das **linhas** e de 0 das 9 invalidações medidas com verdade-base. Ver o topo deste documento.
