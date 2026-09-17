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
    """O caminho esta dentro da raiz? Na duvida, SIM.

    Fail-closed de proposito: um caminho que nao se consegue resolver e tratado
    como dentro, e o pior caso vira expirar evidencia a mais. O erro oposto —
    deixar de expirar apos uma edicao real — deixaria passar um numero velho
    como se fosse fresco, que e a falha que o portao existe para impedir.
    """
    try:
        alvo = os.path.realpath(os.path.abspath(file_path.strip()))
        raiz = os.path.realpath(os.path.abspath(root.strip()))
    except (OSError, ValueError):
        return True
    if not raiz:
        return True
    try:
        # `commonpath` levanta ValueError entre drives diferentes no Windows,
        # e drive diferente e justamente o caso do scratchpad em `%TEMP%`.
        comum = os.path.commonpath([os.path.normcase(alvo), os.path.normcase(raiz)])
    except ValueError:
        return False
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
