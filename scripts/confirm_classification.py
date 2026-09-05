#!/usr/bin/env python
"""Confirma (ou corrige) a classificacao sugerida pelo regex, fechando o loop.

O problema que este script resolve (auditoria 2026-07-28):
`hooks/harness-classify.sh` grava `classification_meta.agreed = None` e, para
L1+, `final = None`, delegando a confirmacao a um passo chamado
`wf-classify-semantic`. Esse passo NUNCA existiu como codigo — so como protocolo
em Markdown. Como `migrate_state.recompute_aggregates` conta apenas tasks com
`agreed is not None`, o bloco `aggregates.classify` era matematicamente incapaz
de sair de zero: `total_classified: 0` e `avg_classify_accuracy: null` para
sempre, independente de quantas tasks rodassem.

Quem chama isto e a skill `harness-workflow`, no ponto em que ela ja le a
classificacao do hook e decide se concorda. O mecanismo passa a ser a skill; nao
ha camada semantica separada a construir.

Uso:
    python confirm_classification.py --final L2-feature
    python confirm_classification.py --final L1-bug --source human_override
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

VALID_SOURCES = ("semantic", "human_override")


def default_harness_dir() -> Path:
    """Diretorio de estado: HARNESS_DIR se definida, senao ~/.claude/harness."""
    env = os.environ.get("HARNESS_DIR")
    if env:
        return Path(env).expanduser().resolve()
    return Path.home() / ".claude" / "harness"


def load_pipelines(scripts_dir: Path | None = None) -> dict[str, list[str]]:
    """A tabela de pipelines, lida da arvore de contrato que estiver valendo.

    Ate 2026-09-05 esta funcao lia `scripts/pipelines.json`, e o docstring dizia
    "fonte unica". Nao era: o arquivo era byte-identico a `contract/pipelines.json`
    (md5 97ab5894...) e os dois tinham leitores diferentes — o caminho que
    moldava o comportamento (hook -> HARNESS_SCRIPTS_DIR) nunca tocava
    `contract/`. Duas copias com a autoridade disputada por um comentario.

    Agora as duas viram uma so: a resolucao passa pelo mesmo
    `arvore_do_contrato` que o adaptador usa, entao a tabela que classifica e a
    tabela que o contrato declara sao literalmente o mesmo arquivo.

    `scripts_dir` explicito continua sendo honrado sem consultar nada — quem
    passa diretorio esta sendo especifico, e e assim que os testes montam o caso
    de tabela ausente.
    """
    if scripts_dir is not None:
        try:
            with (Path(scripts_dir) / "pipelines.json").open(encoding="utf-8") as fh:
                return json.load(fh).get("pipelines", {})
        except (OSError, ValueError):
            return {}
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from contract_adapter import arvore_do_contrato

        arvore, _ = arvore_do_contrato()
        with (arvore / "pipelines.json").open(encoding="utf-8") as fh:
            return json.load(fh).get("pipelines", {})
    except Exception:  # noqa: BLE001 - tabela vazia degrada, nao bloqueia
        return {}


def apply_confirmation(
    state: dict,
    final: str,
    source: str,
    *,
    confidence: float | None = None,
    pipelines: dict[str, list[str]] | None = None,
) -> dict:
    """Aplica a confirmacao ao state (funcao pura sobre o dict).

    `agreed` compara contra `suggested` — a classificacao que o regex propos.
    Cair para `state["classification"]` quando `suggested` falta cobre states
    escritos antes do schema v3 ter `classification_meta`.
    """
    meta = dict(state.get("classification_meta") or {})
    suggested = meta.get("suggested") or state.get("classification")

    meta["suggested"] = suggested
    meta["final"] = final
    meta["source"] = source
    meta["confidence"] = confidence
    meta["agreed"] = (final == suggested)
    state["classification_meta"] = meta

    # Corrigir o nivel sem corrigir o pipeline deixaria a task rodando as fases
    # erradas — o oposto do que a confirmacao serve para fazer.
    if final != suggested:
        state["classification"] = final
        table = pipelines if pipelines is not None else load_pipelines()
        new_pipeline = table.get(final)
        if new_pipeline is not None:
            state["pipeline"] = list(new_pipeline)
            state["status"] = "done" if final.startswith("L0") else "active"
            if state.get("current_step") not in new_pipeline:
                state["current_step"] = None
        elif final.startswith("L0"):
            state["pipeline"] = []
            state["status"] = "done"
            state["current_step"] = None

    return state


def _atomic_write_json(path: Path, data: dict) -> None:
    """tmp -> flush+fsync -> os.replace, consistente com harness-classify.sh."""
    tmp = path.parent / f"{path.name}.tmp-{os.getpid()}"
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def main() -> int:
    """Ponto de entrada CLI. Exit 2 se nao ha state utilizavel."""
    parser = argparse.ArgumentParser(description="Confirma a classificacao no state.json.")
    parser.add_argument("--final", required=True, help="classificacao confirmada, ex.: L2-feature")
    parser.add_argument("--source", default="semantic", choices=VALID_SOURCES,
                        help="semantic = a skill confirmou; human_override = o usuario corrigiu")
    parser.add_argument("--confidence", type=float, default=None)
    parser.add_argument("--harness-dir", type=Path, default=None)
    parser.add_argument("--expect-task", default=None, metavar="TASK_ID",
                        help="aborta se o state.json contiver outra task "
                             "(protecao contra state trocado por sessao paralela)")
    args = parser.parse_args()

    harness_dir = Path(args.harness_dir or default_harness_dir())
    state_path = harness_dir / "state.json"
    try:
        with state_path.open(encoding="utf-8") as fh:
            state = json.load(fh)
    except (OSError, ValueError) as exc:
        print(f"erro: state.json ilegivel em {state_path}: {exc}", file=sys.stderr)
        return 2

    if args.expect_task and state.get("task_id") != args.expect_task:
        print(
            f"erro: state.json contem task {state.get('task_id')}, esperado "
            f"{args.expect_task} — nada gravado",
            file=sys.stderr,
        )
        return 2

    if not state.get("task_id"):
        print("erro: state.json sem task_id (idle?) — nada a confirmar", file=sys.stderr)
        return 2

    # O rotulo tem de existir na tabela ANTES de qualquer escrita. Sem esta
    # porta, `--final L0` chegava ao `args.final.split("-", 1)` la embaixo e
    # saia como `not enough values to unpack (expected 2, got 1)` — erro que nao
    # diz ao chamador o que fazer, e justamente no caminho que existe para
    # corrigir o palpite do regex. O rotulo certo (`L0-question`) esta a uma
    # palavra de distancia, e a mensagem agora a entrega.
    # Tabela vazia (pipelines.json ilegivel) NAO bloqueia: degradar e melhor que
    # impedir a confirmacao por falta de um arquivo de apoio.
    tabela = load_pipelines()
    if tabela and args.final not in tabela:
        print(
            f"erro: '{args.final}' nao e classificacao valida — nada gravado.\n"
            f"       use uma de: {', '.join(sorted(tabela))}",
            file=sys.stderr,
        )
        return 2

    apply_confirmation(state, args.final, args.source, confidence=args.confidence)
    database_path = harness_dir / "harness.db"
    if database_path.exists():
        try:
            from transactional_state import HarnessDatabase

            tier, kind = args.final.split("-", 1)
            task = HarnessDatabase(harness_dir).confirm_classification(
                state["task_id"],
                tier=tier,
                kind=kind,
                pipeline=load_pipelines().get(args.final, []),
                source=args.source,
                confidence=args.confidence if args.confidence is not None else 1.0,
            )
            state.update({
                "revision": task["revision"],
                "code_revision": task["code_revision"],
                "owner_epoch": task["owner_epoch"],
                "verified": task["verified"],
                "pending_gate": task["pending_gate"],
                "scope_id": task["scope_id"],
                "current_step": task["phase"],
            })
        except Exception as exc:
            print(f"erro: confirmacao transacional falhou: {exc}", file=sys.stderr)
            return 2
    _atomic_write_json(state_path, state)

    meta = state["classification_meta"]
    print(
        f"confirmado {state['task_id']}: suggested={meta['suggested']} "
        f"final={meta['final']} agreed={meta['agreed']} source={meta['source']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
