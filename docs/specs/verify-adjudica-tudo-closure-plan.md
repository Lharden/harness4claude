# Closure Plan — t-20260930-181933591685 (verify-adjudica-tudo)

Arquivo próprio da task, e não `docs/closure-plan.md`: aquele é o da sessão-mãe,
e a medição (`verify-adjudica-tudo-medicao.md`) o cita como fonte dos destinos.

## Origem

- Rodada 1 do verify: `wf_d91bfd29-d22`, script do ramo em `feea44e` (sha256
  `3ea2fdfa…`, cópia no scratchpad), `repo_root` = este worktree.
  `pass: false`, `critical_count: 0`, `nos_mortos: []`, **17 não adjudicados**.
  O Workflow de antes do ramo teria respondido `pass: true` para esta rodada.
- Cópia da saída: `<STATE_DIR>/verify/wf_d91bfd29-d22.json`.
- Rodada: 1 de 3.

## Gaps encontrados

- **G1 · linha que cita rodada ausente passa em silêncio (#1, #13).** `checar`
  fazia `continue` para rodada fora de `--saidas`: id digitado errado ou cópia
  esquecida sumiam sem aviso.
- **G2 · entradas que derrubavam o checador com traceback (#4, #9, #10).** Dígito
  Unicode na coluna `#` (`'²'.isdigit()` é True e `int('²')` levanta) e plano
  que não é UTF-8 saíam com 1, sem as linhas prontas.
- **G3 · linha pronta partida em duas (#5, #8).** Título com `\n` gerava uma
  linha que o próprio checador recusava.
- **G4 · forma de texto (#11, #12).** NFD no cabeçalho ou no destino, BOM na
  cópia e o mesmo arquivo passado duas vezes davam erro sem causa real.
- **G5 · Workflow (#14, #16).** `repo_root` sem lista de arquivos dizia "o
  escopo são os arquivos listados" ao lado de "use git diff"; o retorno
  antecipado não tinha `nao_adjudicados`.
- **G6 · lacuna de teste (#2).** Lote misto de `high` confirmado com medium/low.

## Delta tasks

- [x] D1 (G1): linha de rodada sem cópia reprova, com a linha do plano e o id.
- [x] D2 (G2): `isascii() and isdigit()`; leitura do plano com erro vira saída 2.
- [x] D3 (G3): linha pronta monta o início do título com os espaços juntados.
- [x] D4 (G4): texto do plano e títulos em NFC; cópia lida com `utf-8-sig`;
      arquivos deduplicados por caminho resolvido.
- [x] D5 (G5): frase de escopo só com lista; `nao_adjudicados: 0` no retorno antecipado.
- [x] D6 (G6): `test_lote_misto_bloqueia_pelos_dois_motivos`.
- Resultado: 83 testes nos arquivos afetados; mutação **23 de 23** (M18–M26
  novos, um por regra desta iteração).

## Rodada 2 (`wf_e66b2c58-1e7`, script `d5aff37`, sha256 `0ee119e9…`)

`pass: false`, **`critical_count: 2`** (bloqueio), `nos_mortos: []`, 10 não
adjudicados. Rodada: 2 de 3.

### Bloqueio → gap-closure

- **B1 · adjudicador morto apagava o crítico (#1 critical, #2 high, confirmados).**
  `.then((v) => ({ ...f, verdict: v }))` embrulhava o veredito `null` num objeto
  sempre verdadeiro: o censo não via o nó morto, o achado saía de `confirmed` sem
  entrar em lugar nenhum, e o `pass` saía `true`. Anterior ao ramo (a
  metade-controle no blob de `8cd6884` reproduz), mas anulava a garantia central
  desta mudança. Conserto: veredito vazio fica `null` e o censo conta o nó morto.
  `test_adjudicador_morto_bloqueia` + `test_CONTROLE_codigo_de_antes_apagava_o_critico_com_adjudicador_morto`, mutante M27.

### Delta tasks

- [x] E1 (B1): censo do Adjudicate sobre o veredito cru.
- [x] E2 (#7): chave do dedupe com arquivo, linha e título inteiro; na duplicata exata fica o rótulo mais grave. M28, M29.
- [x] E3 (#11): `repo_root` só com espaços é descartado. M30.
- [x] E4 (#6): `checar` com lista vazia sai 2. M31.
- [x] E5 (#10): título ausente ou não texto sai 2. M32.
- [x] E6 (#8): a skill diz o que fazer com execução abortada (não copiar; nó morto).
- Resultado: 91 testes nos arquivos afetados; mutação **29 de 29**.

## Rodada 3 (`wf_69c2fd62-849`, script `933f8bb`, sha256 `307a5834…`) — última

`pass: false`, **`critical_count: 1`** (bloqueio), `nos_mortos: []`, 9 não
adjudicados. Rodada: 3 de 3.

### Bloqueio → gate `escalation`

- **B2 · veredito inconclusivo apagava o achado (#1 high, confirmado; #5 low é o
  mesmo).** `confirmed` exigia `is_real && confidence >= 0.5`; um crítico/alto
  julgado "real com confiança 0,3" (ou refutado com confiança baixa) não entrava
  em `confirmed` nem em lugar nenhum, e o `pass` saía `true`. Anterior ao ramo
  (metade-controle no blob de `8cd6884`).
- **Decisão do usuário (2026-10-01, gate `escalation`):** "Consertar e fechar" —
  conserto com teste e mutante, sem 4ª rodada; a triagem dos 9 restantes fecha
  sem rodada nova. O `state_cli` não abre gate `escalation` (`pending gate not
  found`), então a decisão fica registrada aqui. O checador seguirá apontando o
  `BLOQUEIO` desta rodada, porque a cópia não se edita: o fechamento vale com a
  tabela completa (nenhum `PENDENTE` nem `INVALIDA`), por decisão humana.

### Delta tasks (todas **sem rodada de revisão**)

- [x] F1 (B2): veredito inconclusivo — real ou refutação com confiança < 0,5 —
      vai para a triagem com `adjudicado: false`; refutação confiante sai do
      retorno como antes. `test_veredito_inconclusivo_vai_para_a_triagem` (2
      casos) + `test_CONTROLE_codigo_de_antes_apagava_o_achado_de_veredito_inconclusivo`, M33.
- [x] F2 (#4): linha pronta só para quem não tem linha; quem tem linha inválida
      ou duplicada recebe "edite a linha :N". M35.
- [x] F3 (#6): cerca de código fecha só com o marcador que a abriu. M36.
- [x] F4 (#7): título vazio sai 2. M37.
- [x] F5 (#8, mitigação): com `repo_root`, o prompt manda o revisor devolver um
      achado critical se o diretório ou os arquivos não existirem. M34.
- [x] F6 (#2, #3): testes de texto para o protocolo da skill, o template e o README.
- Resultado: 101 testes nos arquivos afetados; mutação **34 de 34**.

### Consertos sem rodada de revisão

F1 a F6 acima. Cada um tem teste que o pega e mutante que o derruba; nenhum foi
revisto pelo `wf-verify-multimodel`.

## Destino dos achados não adjudicados

| Rodada | # | Achado | Destino | Referência |
|---|---|---|---|---|
| wf_d91bfd29-d22 | 1 | REQ-NF4 não é seguido nem testado para l | consertado | D1, test_linha_que_cita_rodada_ausente_reprova, mutante M18 |
| wf_d91bfd29-d22 | 2 | Edge case de US-1 sem teste: high confir | consertado | D6, test_lote_misto_bloqueia_pelos_dois_motivos |
| wf_d91bfd29-d22 | 3 | REQ-F9 / AC-2.17 (CLAUDE.md global) sem | nao-defeito | previsto na spec: a frase entra no deploy, com merge e OK do usuário (NEVER), e AC-2.17 é conferida por leitura |
| wf_d91bfd29-d22 | 4 | checar_triagem: coluna '#' com digito Un | consertado | D2, test_digito_unicode_no_numero_reprova_sem_traceback, M19 |
| wf_d91bfd29-d22 | 5 | checar_triagem: _linha_pronta nao tira a | consertado | D3, test_linha_pronta_de_titulo_com_quebra_de_linha_fica_numa_linha, M20 |
| wf_d91bfd29-d22 | 6 | checar_triagem trusts only the round cop | limite | linha que cita rodada ausente agora reprova (D1); rodada sem cópia E sem linha segue invisível porque registrar os ids exige state_cli ou hook (NEVER, L-05); declarado no Nível de garantia da spec e na docstring |
| wf_d91bfd29-d22 | 7 | The reviewer's title and file go into th | limite | padrão anterior ao ramo (o diff só prefixa linhaRaiz); mudar a aresta do adjudicador é outra mudança de contrato; risco baixo, exige revisor copiar texto injetado para um título curto — fica para ramo próprio |
| wf_d91bfd29-d22 | 8 | A linha pronta para colar quebra quando | consertado | D3, mesmo conserto do #5, M20 |
| wf_d91bfd29-d22 | 9 | Plano que não é UTF-8, ou que não abre, | consertado | D2, test_plano_que_nao_e_utf8_sai_2, M21 |
| wf_d91bfd29-d22 | 10 | Dígito Unicode na coluna # passa por isd | consertado | D2, mesmo conserto do #4, M19 |
| wf_d91bfd29-d22 | 11 | Destino e cabeçalho da seção em Unicode | consertado | D4, test_cabecalho_e_destino_em_nfd_casam, M22 |
| wf_d91bfd29-d22 | 12 | Cópia de rodada com BOM, ou a mesma roda | consertado | D4, test_copia_com_bom_e_lida e test_mesmo_arquivo_passado_duas_vezes_nao_e_duplicata, M23/M24 |
| wf_d91bfd29-d22 | 13 | Linha da tabela com id de rodada digitad | consertado | D1, mesmo conserto do #1, M18 |
| wf_d91bfd29-d22 | 14 | Com repo_root e sem changed_files, o pro | consertado | D5, test_repo_root_sem_lista_de_arquivos_nao_contradiz_o_git_diff, M25 |
| wf_d91bfd29-d22 | 15 | Sem workflowProgress, a rodada final sai | limite | só o formato de retorno não tem startedAt, e ele não é o que a skill copia (a saída do host traz startedAt, conferido nos dados reais); AC-2.11 da spec fixa o mtime como desempate |
| wf_d91bfd29-d22 | 16 | Early-return path of wf-verify-multimode | consertado | D5, test_rodada_sem_achado_traz_nao_adjudicados_zero, M26 |
| wf_d91bfd29-d22 | 17 | Intentional behavior change: a batch wit | nao-defeito | é o propósito da mudança (L-01); o próprio revisor o registra como contrato, não defeito |
| wf_e66b2c58-1e7 | 3 | REQ-F9 / AC-2.17 (frase de L-03 no item | nao-defeito | previsto na spec (deploy com OK do usuário); ver wf_d91bfd29-d22 #3 |
| wf_e66b2c58-1e7 | 4 | REQ-NF3 (.js sem CR) sem teste nos arqui | nao-defeito | o teste existe: test_workflow_returns.py::test_workflow_pode_ser_invocado_pela_ferramenta varre todo scripts/workflows/*.js; o blob de controle é conferido por hash |
| wf_e66b2c58-1e7 | 5 | A 'ultima rodada' pode sair errada quand | limite | mesma ordem por mtime de wf_d91bfd29-d22 #15: a skill copia a saída do host, que traz startedAt; AC-2.11 fixa o mtime como desempate |
| wf_e66b2c58-1e7 | 6 | checar() com lista de saidas vazia levan | consertado | E4, test_lista_de_saidas_vazia_sai_2, M31 |
| wf_e66b2c58-1e7 | 7 | Dedup por (file, 40 primeiros caracteres | consertado | E2, test_dedupe_nao_junta_titulos_diferentes_com_o_mesmo_prefixo e test_dedupe_de_duplicata_exata_fica_com_a_maior_severidade, M28/M29 |
| wf_e66b2c58-1e7 | 8 | Copia de rodada que falhou (sem result/f | consertado | E6, passo 2 da seção verify da skill; test_skill_manda_copiar_a_saida_e_rodar_o_checador |
| wf_e66b2c58-1e7 | 9 | Rodada final escolhida por mtime quando | limite | mesmo mecanismo do #5 desta rodada; ver wf_d91bfd29-d22 #15 |
| wf_e66b2c58-1e7 | 10 | title null no achado vira a string 'None | consertado | E5, test_titulo_ausente_ou_nao_texto_sai_2, M32 |
| wf_e66b2c58-1e7 | 11 | repo_root so com espacos e aceito e prod | consertado | E3, test_repo_root_so_com_espacos_e_ignorado, M30 |
| wf_e66b2c58-1e7 | 12 | Referencias arquivo:linha para wf-verify | nao-defeito | fora do diff e anterior a ele: exemplo didático de skills/source-selection já defasado, registrado no graph-context |
| wf_69c2fd62-849 | 2 | REQ-F8/AC-2.16: passos 1, 5-7 e 10 do pr | consertado | F6, test_skill_diz_limite_de_rodadas_ordem_do_fim_e_tabela_no_done (sem rodada de revisão) |
| wf_69c2fd62-849 | 3 | US-5 (AC-5.1, AC-5.3): template e README | consertado | F6, test_template_e_readme_dizem_o_contrato_novo (sem rodada de revisão) |
| wf_69c2fd62-849 | 4 | checar_triagem offers a paste-ready line | consertado | F2, test_achado_com_linha_invalida_aponta_a_linha_e_nao_manda_colar_outra, M35 (sem rodada de revisão) |
| wf_69c2fd62-849 | 5 | A critical/high that the adjudicator con | consertado | F1, mesmo conserto do bloqueio #1, test_veredito_inconclusivo_vai_para_a_triagem, M33 (sem rodada de revisão) |
| wf_69c2fd62-849 | 6 | Cerca de codigo alterna em qualquer ``` | consertado | F3, test_cercas_de_tipos_diferentes_nao_se_fecham, M36 (sem rodada de revisão) |
| wf_69c2fd62-849 | 7 | Titulo vazio ou so de espacos aceita cel | consertado | F4, test_titulo_vazio_sai_2, M37 (sem rodada de revisão) |
| wf_69c2fd62-849 | 8 | repo_root inexistente ou com quebra de l | limite | o Workflow não lê disco; mitigado por F5 (prompt pede achado critical se a raiz não existir, M34); a raiz vem da skill, que passa a do worktree da task |
| wf_69c2fd62-849 | 9 | Dedupe nao une o mesmo achado com line n | limite | erra duplicando, nunca perdendo: variação de caminho ou de linha custa uma linha a mais na triagem ou um adjudicador a mais; normalizar exigiria heurística sobre o texto do revisor |
| wf_69c2fd62-849 | 10 | Dedupe com a linha na chave deixa de jun | limite | mesmo mecanismo do #9 desta rodada: o custo é duplicata, nunca perda (o próprio revisor o diz) |
