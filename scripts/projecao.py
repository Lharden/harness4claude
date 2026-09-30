"""Gravar JSON de estado de forma atomica, num lugar so.

A projecao `state.json` (e o contador ao lado dela) e escrita por cinco
programas: classify, PostToolUse transacional, reclassify, confirm_classification
e o disjuntor de TTL. Ate 2026-09-23 cada um tinha a sua copia do laco
tmp -> replace, e as copias divergiram:

- o PostToolUse usava tmp de NOME FIXO: dois PostToolUse concorrentes (comandos
  em segundo plano que terminam juntos) escreviam o mesmo tmp;
- o reclassify nem tinha laco: `open(path, 'w')` TRUNCA antes de escrever, e um
  leitor no meio lia arquivo vazio;
- ninguem tinha retentativa. No Windows `os.replace` falha enquanto outro
  processo tem o destino aberto. Medido no ramo ciclo-de-vida-da-task: com o
  escritor antigo, 56% das escritas levantavam com 1 concorrente e 86% com 4.

A funcao NUNCA levanta. Devolve o erro para quem chama decidir; quem decide se ha
task viva e o banco, nao a projecao, entao projecao atrasada e degradacao — e
hook morto por causa dela seria o defeito maior.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

#: Esperas entre tentativas, em segundos. A janela do replace e de milissegundos;
#: seis tentativas somam ~87 ms no pior caso, dentro do orcamento de um hook.
ESPERAS = (0.0, 0.002, 0.005, 0.01, 0.02, 0.05)


def gravar_json_atomico(caminho: str | Path, dados: Any, *, indent: int = 2) -> OSError | None:
    """tmp de nome unico -> replace com retentativa. `None` se gravou."""
    destino = Path(caminho)
    tmp = destino.with_name(f"{destino.name}.{os.getpid()}.{time.monotonic_ns()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(dados, fh, indent=indent, ensure_ascii=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
    except OSError as exc:
        _apagar(tmp)
        return exc
    ultimo: OSError | None = None
    for espera in ESPERAS:
        if espera:
            time.sleep(espera)
        try:
            os.replace(tmp, destino)
            return None
        except OSError as exc:
            ultimo = exc
    _apagar(tmp)
    return ultimo


def _apagar(caminho: Path) -> None:
    try:
        caminho.unlink()
    except OSError:
        pass


def projetar(home: str | Path, task: dict[str, Any], campos: dict[str, Any], banco: Any) -> str | None:
    """Escreve `campos` da `task` em `home/state.json` sem misturar duas tasks.

    Uma projecao descreve UMA task. O `task_id` que ja esta no arquivo e a task
    viva do escopo, que o banco responde, decidem entre tres casos:

    - a projecao ja e desta task: `campos` sobre ela, no lugar. O que so a
      projecao guarda (`prompt_excerpt`, `prompt_len`, o `started_at` do
      classify) fica.
    - outra task e a viva do escopo: nada e gravado, e o `task_id` dela volta.
      O PostToolUse acha a task pelo `task_id` daqui
      (`harness-transactional.py:_database_for_payload`); apontar a projecao
      para outra desviaria toque e evidencia da task viva ate o proximo prompt
      reparar.
    - senao: projecao NOVA, montada do banco, com o `classification_meta` do
      banco. Nada da task anterior sobrevive.

    Ate 2026-09-28 `state_cli._sync` e `branch_state._sync_task` faziam
    `update` sobre o que estivesse no arquivo. Um `complete` depois de um `yes`
    que tinha aberto task L0 deixou o `state.json` com `task_id` e `status` da
    L1 e `classification_meta`, `started_at`, `prompt_len` e `prompt_excerpt`
    do `yes` — e o `record_signal` le o meta daqui para a telemetria de
    acuracia. Ver `docs/specs/sim-nao-fecha-entrega-diagnostico.md`.

    "Viva" e o status do banco (`current_task`, `ACTIVE_STATUSES`), nao a
    resposta de `continuation_policy.task_viva`: quem chama aqui nao tem prompt,
    e sem prompt a politica diz NENHUMA para a entrega que espera o `sim` —
    justamente a task que precisa ficar com a projecao. O indice
    `one_active_task_per_scope` garante no maximo uma viva por escopo. Banco
    ilegivel levanta antes de gravar: "nao consegui perguntar" nunca vira
    "ninguem e dono".

    Arquivo ilegivel conta como ausente: a projecao nova sai inteira do banco,
    que e a autoridade, e perde so o cache do prompt, que ninguem le.

    O `task_id` do arquivo e relido no ultimo instante, como no
    `_sync_projection` do PostToolUse (F7): se outro escritor pos ali uma
    terceira task enquanto esta decidia, nada e gravado. Releitura vazia ou
    ilegivel nao prova troca nenhuma e nao impede a escrita — mesma regra do F7.
    A janela que sobra e a do proprio `replace`.

    `banco` e o `HarnessDatabase` do balde (`current_task`, `classification`).
    Devolve None quando gravou, ou o `task_id` da task que ficou com a projecao
    (a viva do escopo, ou a que outro escritor acabou de por). Esgotada a
    retentativa do `replace`, levanta o `OSError`, como os dois escritores ja
    faziam.
    """
    from transactional_state import StateTransitionError

    caminho = Path(home) / "state.json"
    atual = _ler_projecao(caminho)
    dona_lida = atual.get("task_id")
    if dona_lida != task["task_id"]:
        viva = banco.current_task(task["scope_id"])
        if viva is not None and viva["task_id"] != task["task_id"]:
            return str(viva["task_id"])
        atual = _projecao_nova(task)
        try:
            atual["classification_meta"] = banco.classification(task["task_id"])
        except StateTransitionError:
            pass  # task sem linha em `classifications`: ausente, nunca emprestado de outra
    atual.update(campos)
    dona_agora = _ler_projecao(caminho).get("task_id")
    if dona_agora and dona_agora not in (dona_lida, task["task_id"]):
        return str(dona_agora)
    erro = gravar_json_atomico(caminho, atual)
    if erro is not None:
        raise erro
    return None


def _ler_projecao(caminho: Path) -> dict[str, Any]:
    try:
        valor = json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return valor if isinstance(valor, dict) else {}


def _projecao_nova(task: dict[str, Any]) -> dict[str, Any]:
    """Tudo o que o banco sabe da task, no formato da projecao."""
    return {
        "task_id": task["task_id"],
        "schema_version": 3,
        "classification": task["legacy_level"],
        "status": task["status"],
        "pipeline": task["pipeline"],
        "current_step": task["phase"],
        "artifacts_so_far": [a["path"] for a in task.get("artifacts", [])],
        "started_at": task["started_at"],
        "revision": task["revision"],
        "code_revision": task["code_revision"],
        "owner_epoch": task["owner_epoch"],
        "verified": task["verified"],
        "pending_gate": task["pending_gate"],
        "scope_id": task["scope_id"],
    }
