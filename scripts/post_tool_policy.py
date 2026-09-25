"""O que conta como mudanca de codigo para efeito de frescura de evidencia.

A evidencia de teste expira quando o codigo muda, e isso esta certo: um numero
colhido antes da ultima edicao nao fala sobre o codigo depois dela.

O que estava errado era o CRITERIO. Ate 2026-09-16 qualquer `Edit`/`Write`
contava, sem olhar o caminho — e entao escrever a mensagem de commit num arquivo
do scratchpad, FORA do repositorio, zerava `verified` e subia `code_revision`.
Medido naquele dia: a evidencia de `1311 passed, 0 failed` foi invalidada por
tres escritas em `<scratchpad>/msg2.txt`, com a arvore de trabalho limpa e o
`git status` vazio.

O defeito e o mesmo de `test_deploy_drift` no mesmo dia, na outra direcao: um
portao que mede a coisa errada. Aquele perguntava "o worktree esta implantado?"
quando queria saber "o que roda e publicado?". Este perguntava "algum arquivo foi
escrito?" quando quer saber "o codigo sob teste mudou?".

E a consequencia e a que a regra de ouro nomeia: portao que nao pode ser
satisfeito honestamente sera contornado. Para satisfazer o criterio antigo era
preciso rodar a suite inteira e responder sem escrever arquivo nenhum — nem
rascunho, nem nota, nem mensagem de commit. O caminho honesto ficava mais caro
que o desonesto, e e assim que um portao morre.

O criterio novo: escrita DENTRO do repositorio conta; fora, nao. Arquivo de
documentacao dentro do repo continua contando de proposito — `tests/test_orfaos.py`
le `tools/README.md`, e ha testes que leem `SKILL.md`, entao um `.md` versionado
pode de fato mudar o resultado da suite.
"""
from __future__ import annotations

import os

SHELL_TOOLS = {"bash", "shell", "powershell"}


def touch_target(tool_name: str, file_path: str) -> str | None:
    normalized_tool = tool_name.strip().casefold()
    if file_path.strip():
        return file_path.strip()
    if normalized_tool in SHELL_TOOLS:
        return "<shell-command>"
    return None


def inside_root(file_path: str, root: str) -> bool:
    """O caminho esta dentro da raiz?

    **Na duvida, SIM** — fail-closed. Caminho que nao se resolve conta como
    dentro, e o pior caso vira expirar evidencia a mais. O erro oposto, deixar
    de expirar apos uma edicao real, faria um numero velho passar por fresco,
    que e a falha que o portao existe para impedir.

    **Drive ou share diferente e a excecao, e nao e duvida: e resposta.** O
    `commonpath` levanta `ValueError` quando os caminhos nao compartilham raiz
    (`C:` contra `Z:`, ou UNC contra local), e isso significa que sao lugares
    diferentes — nao lugares que nao se conseguiu comparar. Por isso o ramo
    devolve `False` mesmo sob a regra do "na duvida, sim".

    [superado: "drive diferente e justamente o caso do scratchpad em %TEMP%"] —
    medido pela sessao `apresentacao-alta-gestao-refinamento-ac9-f9` em
    2026-09-17: nesta maquina o repo e o `%TEMP%` estao os dois em `C:`, e o
    scratchpad e excluido pelo `commonpath` normal, nunca por esta excecao. A
    justificativa citava um caso que nao era o caso dela.

    O que a mesma sessao mediu e NAO derrubou: `subst D:` apontando para o
    repositorio devolve `True` pelos dois caminhos, porque o `realpath` acima
    resolve o drive virtual antes da comparacao. Junction e symlink caem na
    mesma protecao. Continua descoberto: uma raiz alcancavel por caminho que o
    `realpath` NAO resolva para o mesmo drive teria edicao real classificada
    como fora — cenario nao construido, porque `subst` nao o produz.
    """
    try:
        alvo = os.path.realpath(os.path.abspath(file_path.strip()))
        raiz = os.path.realpath(os.path.abspath(root.strip()))
    except (OSError, ValueError):
        return True
    if not raiz:
        return True
    try:
        comum = os.path.commonpath([os.path.normcase(alvo), os.path.normcase(raiz)])
    except ValueError:
        return False   # raizes incomparaveis = lugares diferentes, ver docstring
    return comum == os.path.normcase(raiz)


def counts_as_modified_file(tool_name: str, file_path: str, root: str | None = None) -> bool:
    """Esta escrita muda o codigo que a suite mede?

    `root` e opcional para nao quebrar chamador antigo: sem ele o comportamento
    e o de antes. Com ele, escrita fora do repositorio nao expira evidencia.
    """
    if not file_path.strip() or tool_name.strip().casefold() in SHELL_TOOLS:
        return False
    if root:
        return inside_root(file_path, root)
    return True


def fora_de_qualquer_repositorio(file_path: str, cwd: str, raiz_de) -> bool:
    """A sessao e o arquivo estao, os dois, fora de qualquer repositorio?

    `raiz_de` e `harness_paths.find_repo_root`, injetada para este modulo nao
    depender do resto do harness.

    Medido em 2026-09-25 na sessao "PPEGPS Digital Transformation
    presentation": cwd sem `.git` (uma pasta de apresentacao), `find_repo_root`
    devolve None, e com raiz vazia `counts_as_modified_file` conta TUDO. Tres
    `Write` de roteiro, notas e script promoviam a task de L0 para L1-feature
    (`write-spec-light -> tdd -> verify-against-spec`), e o portao de Stop
    passava a exigir evidencia de teste fresca numa pasta onde nao existe suite
    nenhuma para rodar. Portao que nao pode ser satisfeito honestamente sera
    contornado — ou, como ali, bloqueia toda resposta final.

    O criterio segue o de `counts_as_modified_file`: o que conta e mudanca no
    codigo sob teste. Arquivo fora de qualquer repositorio, escrito por sessao
    tambem fora de qualquer repositorio, nao esta sob teste nenhum.

    O caso "sem cwd" NAO muda (continua contando tudo, fail-closed; ver
    `harness-reclassify.sh`): sem cwd devolve False. Tambem devolve False
    quando o cwd esta num repositorio (a regra de dentro/fora da raiz ja cuida)
    e quando o arquivo esta num repositorio, mesmo com a sessao aberta numa
    pasta-mae — editar `projects/x/src/a.py` a partir de `projects/` continua
    contando. Qualquer erro devolve False: na duvida, conta.
    """
    if not str(cwd or "").strip() or not str(file_path or "").strip():
        return False
    try:
        if raiz_de(cwd):
            return False
        alvo = file_path.strip()
        if not os.path.isabs(alvo):
            alvo = os.path.join(cwd, alvo)
        return not raiz_de(os.path.dirname(os.path.abspath(alvo)))
    except Exception:
        return False
