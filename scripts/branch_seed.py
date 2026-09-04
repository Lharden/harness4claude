#!/usr/bin/env python
"""branch_seed.py — semente e launcher de um ramo do Branch Keeper.

A semente e o prompt inicial da conversa filha: o unico fio que liga um contexto
limpo a decisao que o originou. Ela e escrita pelo modelo (que tem o contexto),
nunca pelo hook (que tem so o texto do turno) — este modulo so da forma.

**Paths e decisoes, jamais conteudo colado.** Ramificar existe para parar de
gastar janela com assunto suspenso; encher a semente com o arquivo inteiro
mudaria o desperdicio de lugar em vez de acabar com ele. `MAX_SEED_CHARS` e o
guarda-corpo disso.

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
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import branch_config
import branch_state

#: Secoes que uma semente precisa ter para o ramo comecar andando.
REQUIRED_SECTIONS = (
    "## Origem",
    "## O ramo",
    "## Por que saiu da conversa pai",
    "## Contexto minimo",
    "## Primeira acao",
    "## Como reportar de volta",
)

#: Teto da semente. Estourou, a lista de contexto e cortada — nunca o texto.
MAX_SEED_CHARS = 6000
MAX_CONTEXT_ITEMS = 12


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Semente
# ---------------------------------------------------------------------------


_AVISO_CORTE = (
    chr(10) * 2 + "---" + chr(10)
    + "**Semente truncada** por exceder o teto. O que ficou de fora estava no "
    + "resumo ou na justificativa, nao nos paths. Consulte o pai com "
    + "`session_query.py --session <parent>` se faltar contexto." + chr(10)
)


def render_seed(
    *,
    branch: dict,
    parent_name: str,
    parent_session: str,
    project: str,
    summary: str,
    why_split: str,
    context_items: list[str] | tuple[str, ...],
    first_action: str,
    siblings: list | tuple = (),
) -> str:
    """Monta o prompt-semente. Falta de acao concreta e erro, nao aviso."""
    if not str(first_action).strip():
        raise ValueError("semente sem primeira acao concreta nao abre ramo")
    if not str(summary).strip():
        raise ValueError("semente sem descricao do ramo nao abre ramo")

    itens = [str(i).strip() for i in context_items if str(i).strip()][:MAX_CONTEXT_ITEMS]
    bloco_contexto = "\n".join(f"- `{i}`" for i in itens) or "- (nenhum path relevante)"

    # Irmas: ramos vivos do mesmo pai. O registro ja os lista desde 2026-08-27 e
    # nada os renderizava — entao cada ramo nascia achando-se filho unico, e dois
    # irmaos podiam refazer o mesmo trabalho sem nunca se ver.
    nomes = [
        f"`{b.get('slug')}` ({b.get('status')})"
        for b in siblings
        if b.get("slug") and b.get("slug") != branch.get("slug")
    ][:MAX_CONTEXT_ITEMS]
    bloco_irmas = (
        "\n- Irmas deste pai: " + ", ".join(nomes) +
        "\n  Elas saem do mesmo contexto e podem ter tocado no que voce precisa."
        if nomes else ""
    )

    texto = f"""# {branch['name']}

## Origem
- Conversa pai: **{parent_name}** (`{parent_session}`)
- Projeto: `{project}`
- Este ramo: `{branch['session_id']}` — retome com `claude --resume {branch['session_id']}`
- Criado em: {_now()}{bloco_irmas}

### Consultar a conversa pai sem carrega-la
```
python "$H4C/tools/session_query.py" "<o que voce precisa saber>" --session {parent_session}
```
Devolve o trecho e o turno, nao a sessao inteira. `claude --resume
{parent_session}` carrega tudo e substitui esta sessao — e o caminho caro, e
raramente o certo: este ramo existe justamente para nao pagar aquele contexto.

## O ramo
{summary.strip()}

## Por que saiu da conversa pai
{why_split.strip()}

O tema esta **parkeado** na conversa pai: la ele nao sera desenvolvido. Se
precisar devolve-lo, o comando de la e `/branch recall {branch['slug']}`.

## Contexto minimo
Paths e decisoes — leia o que precisar, nada foi colado aqui de proposito.

{bloco_contexto}

## Primeira acao
{first_action.strip()}

## Como reportar de volta
Ao concluir (ou ao decidir que nao vale seguir), rode aqui:

    /branch close {branch['slug']}

A conclusao volta sozinha para a conversa pai: ela entra no bloco
`<harness-parked>` do proximo turno de la, UMA vez. Sem `close`, o que voce
descobriu aqui morre nesta sessao — e ramificar vira perder o assunto em vez
de organiza-lo.
"""
    if len(texto) > MAX_SEED_CHARS and len(itens) > 1:
        # So recursiona enquanto ha o que cortar. `max(1, len // 2)` travava em
        # 1 e recursionava para sempre quando o excesso vinha do `summary` ou do
        # `why_split` — cortar contexto nao encolhe texto que nao esta no
        # contexto. Abrir ramo com resumo longo derrubava a skill inteira com
        # RecursionError, e nao havia teste cobrindo esse caminho.
        corte = len(itens) // 2
        return render_seed(
            branch=branch,
            parent_name=parent_name,
            parent_session=parent_session,
            project=project,
            summary=summary,
            why_split=why_split,
            context_items=itens[:corte],
            first_action=first_action,
            siblings=siblings,
        )
    if len(texto) > MAX_SEED_CHARS:
        # Ultimo recurso: o excesso nao esta no contexto, entao trunca o texto.
        # Semente cortada e ruim; semente que nao abre e pior — e uma que
        # recursiona ate estourar a pilha nao abre nem avisa por que.
        corte = MAX_SEED_CHARS - len(_AVISO_CORTE)
        texto = texto[:corte].rstrip() + _AVISO_CORTE
    return texto


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
