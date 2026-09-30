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
