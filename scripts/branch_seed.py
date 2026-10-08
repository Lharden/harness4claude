#!/usr/bin/env python
"""branch_seed.py — semente e launcher de um ramo do Branch Keeper.

A semente e o prompt inicial da conversa filha: o unico fio que liga um contexto
limpo a decisao que o originou. Ela e escrita pelo modelo (que tem o contexto),
nunca pelo hook (que tem so o texto do turno). Este modulo grava o texto pronto
(`write`) e o launcher; ele NAO confere as secoes da semente.

Ate 2026-10-07 havia aqui um `render_seed` que montava a semente a partir de
campos, e o `SKILL.md` do branch-out dizia que "o renderizador recusa semente
incompleta". Nenhum caminho de producao o chamava: o `write` sempre leu o
arquivo escrito pelo modelo. Foi apagado por decisao do usuario (decisao 7 de
master-harness/docs/decisoes-capacidades-orfas.md), com a frase do SKILL
corrigida, em vez de ligado.

**Paths e decisoes, jamais conteudo colado.** Ramificar existe para parar de
gastar janela com assunto suspenso; encher a semente com o arquivo inteiro
mudaria o desperdicio de lugar em vez de acabar com ele.

**O launcher e um arquivo `.ps1`, nao uma string.** A cadeia de execucao e
`wt -> pwsh -> claude -> prompt multilinha`, e a maquina real tem `Program
Files` no caminho. Cada nivel tem sua regra de aspas, e o erro so aparece na
hora de abrir a janela — tarde demais. Um arquivo em disco elimina a categoria
inteira e, de quebra, te deixa reabrir o ramo depois clicando nele.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import branch_config
import branch_state


# ---------------------------------------------------------------------------
# PowerShell
# ---------------------------------------------------------------------------


def ps_quote(value: str) -> str:
    """String literal de PowerShell: aspas simples, apostrofo dobrado.

    Literal simples e o unico modo em que `$`, backtick e barra invertida nao
    significam nada — exatamente o que se quer para um caminho do Windows.
    """
    return "'" + str(value).replace("'", "''") + "'"


def render_launcher(*, branch: dict, cwd: str, seed_path: str) -> str:
    """Script que abre a sessao do ramo. Re-executavel a qualquer momento."""
    return f"""# Branch Keeper — launcher do ramo "{branch['name']}"
# Gerado automaticamente. Pode ser reexecutado: retoma a MESMA sessao.
$ErrorActionPreference = 'Stop'

Set-Location -LiteralPath {ps_quote(cwd)}
$seed = Get-Content -Raw -LiteralPath {ps_quote(seed_path)}

# O ramo e sessao de primeira classe, nao subprocesso da mae. A janela nasce da
# arvore de processos dela e herda CLAUDE_CODE_CHILD_SESSION; com esse marcador
# o CLI desliga a gravacao do transcript, e um ramo sem transcript nao entra no
# sessions-index nem responde a session_query — vira a sessao orfa que
# ramificar existe para evitar.
Remove-Item Env:CLAUDE_CODE_CHILD_SESSION -ErrorAction SilentlyContinue
$env:CLAUDE_CODE_FORCE_SESSION_PERSISTENCE = '1'

claude --session-id {ps_quote(branch['session_id'])} -n {ps_quote(branch['name'])} $seed
"""


def write_branch_files(*, cwd: str, branch: dict, seed_text: str) -> dict:
    """Grava semente e launcher no bucket do projeto. Devolve os dois paths."""
    destino = Path(branch_state.branches_dir(cwd))
    destino.mkdir(parents=True, exist_ok=True)
    seed_path = destino / f"{branch['slug']}.seed.md"
    launcher_path = destino / f"{branch['slug']}.launch.ps1"
    seed_path.write_text(seed_text, encoding="utf-8")
    launcher_path.write_text(
        render_launcher(branch=branch, cwd=cwd, seed_path=str(seed_path)),
        encoding="utf-8",
    )
    return {"seed_path": str(seed_path), "launcher_path": str(launcher_path)}


# ---------------------------------------------------------------------------
# Abertura da janela
# ---------------------------------------------------------------------------


def launch_command(*, branch: dict, cwd: str, launcher_path: str) -> list[str]:
    """Argv do `wt.exe`. Lista vazia quando o host esta desligado.

    `-w -1` forca JANELA nova, nao aba: a aba nasceria escondida atras da aba
    atual e o ramo cairia no mesmo esquecimento que a feature combate.
    """
    if branch_config.get_str("HARNESS_BRANCH_HOST").strip().lower() != "wt":
        return []
    wt = shutil.which("wt") or shutil.which("wt.exe") or "wt.exe"
    return [
        wt,
        "-w",
        "-1",
        "new-tab",
        "--title",
        str(branch["name"]),
        "-d",
        str(cwd),
        "pwsh",
        "-NoExit",
        "-File",
        str(launcher_path),
    ]


def launch(*, branch: dict, cwd: str, launcher_path: str) -> bool:
    """Abre a janela do ramo. False quando desligado ou quando o host falha."""
    argv = launch_command(branch=branch, cwd=cwd, launcher_path=launcher_path)
    if not argv:
        return False
    try:
        subprocess.Popen(argv, close_fds=True)
        return True
    except (OSError, ValueError):
        return False


def main() -> int:
    import argparse
    import json

    p = argparse.ArgumentParser(description="Semente e launcher de um ramo.")
    p.add_argument("acao", choices=["write", "launch", "command"])
    p.add_argument("--cwd", default=None)
    p.add_argument("--slug", required=True)
    p.add_argument("--seed-file", default=None, help="arquivo com o texto da semente")
    args = p.parse_args()
    cwd = args.cwd or os.getcwd()
    branch = branch_state.get(cwd=cwd, slug=args.slug)

    if args.acao == "write":
        if not args.seed_file:
            p.error("write exige --seed-file")
        texto = Path(args.seed_file).read_text(encoding="utf-8")
        paths = write_branch_files(cwd=cwd, branch=branch, seed_text=texto)
        branch_state.attach_files(cwd=cwd, slug=args.slug, **paths)
        print(json.dumps(paths, ensure_ascii=False))
    elif args.acao == "command":
        print(
            json.dumps(
                launch_command(
                    branch=branch, cwd=cwd, launcher_path=branch.get("launcher_path") or ""
                ),
                ensure_ascii=False,
            )
        )
    else:
        ok = launch(
            branch=branch, cwd=cwd, launcher_path=branch.get("launcher_path") or ""
        )
        print("launched" if ok else "skipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
