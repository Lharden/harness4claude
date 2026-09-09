# Ramo nasce no projeto dono, não no projeto da mãe

**Status:** registrado, não iniciado · aberto em 2026-09-09
**Origem:** observação do usuário durante a Fase 1 de
[`parent-session-por-ramo-riscos.md`](parent-session-por-ramo-riscos.md)

## O problema

O Branch Keeper resolve **misturar assuntos na mesma sessão**. Não resolve
**em que projeto o assunto vai morar**. Um ramo nasce sempre no projeto da mãe,
mesmo quando o trabalho pertence, inteiro, a outro repositório.

Consequência: estado, memória, telemetria e notas de vault de um projeto passam
a conter trabalho que não é dele. Fica bagunçado e desconexo — e, pior que a
bagunça, contamina as fontes que outras sessões leem como verdade sobre aquele
projeto.

## Evidência (esta própria sessão)

O ramo `resolvedor-de-banco-do-branch-keeper` foi aberto a partir de uma sessão
do `RSL_Project` (submissão de artigo acadêmico). O trabalho é **inteiramente**
em `harness4claude`. Resultado medido:

| Onde | O que ficou lá | Onde deveria estar |
|---|---|---|
| `cwd` da sessão do ramo | `Documents/projects/RSL_Project` | `Documents/projects/harness4claude` |
| CLAUDE.md carregado | protocolo de fases F1.1–F7, masterdata de 38 artigos, regras Elsevier Harvard | o do `harness4claude` |
| Estado do harness | task `t-20260909-195611274295` (L2-bug sobre `branch_state.py`) em `projects/RSL_Project-aa99cfb0/sessions/2f61e400-.../` | `projects/harness4claude-<hash>/` |
| Semente e launcher | `projects/RSL_Project-aa99cfb0/branches/` | idem |
| `.remember/now.md` do RSL | duas entradas inteiras, verbatim abaixo | nada disso |
| Commits | `harness4claude` (`982f2f8`, `4b9a670`, `7bd46e3`) | ✓ certo |

A memória do projeto acadêmico, hoje, contém:

> Debugged harness4claude parent_session/branch handling; identified critical
> field-ambiguity issue (R1) + 3 secondary consumer failures (C2/C3/C4) […]

> Analysis via 4 lentes identified 3 live bugs (B1-B3: Lock/concurrency) in
> harness4claude; restructured plan + Phase 0 (test infra) implemented:
> signal(sweep) + test guards in branch_state.py/test_branch_state.py […]

O arquivo que a próxima sessão do RSL lê como "onde eu parei" fala de `_Lock`,
concorrência em sqlite e guardas de teste. Nada disso é da RSL, e nada disso
ajuda quem for escrever a Seção 4.2.

**O código foi para o lugar certo e o rastro foi para o lugar errado.** Não é só
desorganização: é uma fonte que outras sessões leem como verdade sobre o projeto.

## Mecanismo

O cwd da mãe é propagado sem que ninguém pergunte de quem é o assunto:

| Local | O que faz |
|---|---|
| `scripts/branch_seed.py:190` (`render_launcher`) | `Set-Location -LiteralPath {cwd}` — o cwd é o da MÃE |
| `scripts/branch_seed.py:207` (`write_branch_files`) | semente e launcher em `branch_state.branches_dir(cwd)` |
| `scripts/branch_seed.py:241` (`launch_command`) | passa o mesmo cwd ao processo filho |
| `scripts/branch_state.py:83` (`branches_path`) | registro em `state_dir(cwd=cwd)` |

Nenhum deles está errado isoladamente — todos assumem, corretamente para o caso
comum, que o ramo continua no mesmo projeto. O que falta é a pergunta.

## O que a resposta precisa ter

- **Uma decisão explícita de projeto no momento de ramificar.** Hoje não existe
  campo nem pergunta; o projeto é herdado em silêncio.
- **Herança como default, não como regra.** A maioria dos ramos continua no mesmo
  projeto, e perguntar sempre seria atrito.
- **Registro no bucket do projeto DONO**, não no da mãe — o que reabre a pergunta
  de como a mãe continua vendo o parking de um ramo que mora noutro projeto.
  `parent_session_id` (Fase 1) é o fio que torna isso possível; sem ele o vínculo
  se perderia.
- **Ramo já aberto no lugar errado precisa de caminho de volta**, ou o registro
  vira dívida permanente.

## Fora de escopo aqui

O que o Branch Keeper já resolve e continua valendo: separar assuntos em sessões
próprias, com semente, parking na mãe e conclusão de volta. Este documento é só
sobre **onde** a sessão nova nasce.

## Por que não agora

Levantado no meio da Fase 1 de `parent-session-por-ramo-riscos.md`, que tem
plano, gate e rollback já aprovados. Corrigir dois subsistemas de ramificação ao
mesmo tempo é como o "arruma um, quebra três" começa. Fica registrado para entrar
na rota depois que as fases 2 a 4 fecharem.
