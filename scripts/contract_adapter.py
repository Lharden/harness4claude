#!/usr/bin/env python
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

EVIDENCE = {
    "classification.deterministic-suggestion": [
        "tests/test_sondas_de_producao.py#test_classify_hook_grava_sugestao_deterministica",
    ],
    "classification.semantic-confirmation": [
        "tests/test_sondas_de_producao.py#test_confirm_cli_grava_confirmacao_semantica",
    ],
    "classification.human-override": ["tests/test_sondas_de_producao.py#test_confirm_cli_grava_human_override"],
    "state.session-worktree-isolation": [
        "tests/test_sondas_de_producao.py#test_classify_hook_isola_duas_sessoes_no_mesmo_repo",
    ],
    "state.transactional-fsm": [
        "tests/test_transactional_hook.py#test_atomic_test_command_records_fresh_evidence",
        "tests/test_transactional_hook.py#test_stop_blocks_twice_then_opens_escalation_gate",
    ],
    "state.ttl-signals": ["tests/test_sondas_de_producao.py#test_session_start_hook_abandona_task_vencida"],
    "workflow.sdd-v3": ["tests/test_sondas_de_producao.py#test_classify_hook_grava_pipeline_do_contrato"],
    "workflow.human-gates": [
        "tests/test_transition_portao_pendente.py#test_linha_impressa_para_branch_open_parkeia_e_destrava",
    ],
    "workflow.adversarial-agents": ["tests/test_sondas_de_producao.py#test_workflow_de_contexto_nomeia_o_no_morto"],
    "workflow.spec-verification": [
        "tests/test_sondas_de_producao.py#test_verify_multimodel_com_dimensao_morta_nao_aprova",
    ],
    "context.graphify": ["tests/test_sondas_de_producao.py#test_graphify_autosetup_dispara_update_sem_grafo"],
    "context.skill-router": ["tests/test_skill_router.py#test_main_runs_layer_b_when_layer_a_empty"],
    "capability.arsenal": ["tests/test_arsenal_gate.py#test_install_sem_decisao"],
    "memory.wiki-vault": ["tests/test_sondas_de_producao.py#test_precompact_hook_espelha_spec_no_vault"],
    "memory.operational-search": ["tests/test_sondas_de_producao.py#test_wiki_query_cli_acha_por_alias"],
    "conversation.branch-keeper": [
        "tests/test_branch_state.py#test_add_pela_linha_de_comando_cria_o_registro",
        "tests/test_sondas_de_producao.py#test_branch_state_cli_recusa_abrir_alem_do_limite",
    ],
    "safety.command-policy": ["tests/test_host_contract_resilience.py#test_bloqueia_destrutivo"],
    "integration.harness-lite": [
        "tests/test_harness_lite_adapter.py#test_a_passed_bundle_with_artifacts_is_acceptable",
    ],
    "integration.science-harness": ["tests/test_contract_adapter.py#test_science_intent_routes_evidence_prompts"],
    "lifecycle.full-hooks": ["tests/test_sondas_de_producao.py#test_todo_comando_de_hooks_json_roda"],
    "observability.health-telemetry": [
        "tests/test_sondas_de_producao.py#test_check_hook_liveness_cli_sai_zero_e_reprova_evento_mudo",
    ],
    "editorial.drop-constrain-retain": [
        "tests/test_sondas_de_producao.py#test_classify_hook_manda_carregar_workflow_com_drop_constrain_retain",
    ],
}


def _mh_pelo_marcador() -> None:
    """Poe no `sys.path` a raiz que `~/.master-harness/mh-root` declara, se ela existir.

    E o protocolo do ecossistema para achar o `mh` sem depender de ele estar
    instalado — o mesmo de `_mh()` na presenca e do dreno do SessionStart. Ate
    2026-10-07 este adaptador so tentava `import mh`, e o Python do sistema tinha
    um `master-harness` editavel apontando para um worktree apagado: os hooks com
    `python` puro, `confirm_classification.py` e `state_cli.py` liam o vizinho
    (`vizinho:ModuleNotFoundError`) com a flag em `preferido`.

    Sem marcador, nada muda e o `import mh` de baixo decide, como antes. E o caso
    do kit S1, que roda com `-I` e a casa no temporario: la nao ha marcador, e o
    `mh` vem do site-packages do venv.
    """
    casa = os.environ.get("MASTER_HARNESS_HOME") or os.path.join(os.path.expanduser("~"), ".master-harness")
    try:
        with open(os.path.join(casa, "mh-root"), encoding="utf-8") as fh:
            raiz = fh.readline(4096).strip()
    except OSError:
        return
    if raiz and os.path.isdir(raiz) and raiz not in sys.path:
        sys.path.insert(0, raiz)


def arvore_do_contrato(root: str | Path | None = None) -> tuple[Path, str]:
    """Devolve (arvore, origem): de onde o contrato foi lido, e por que dali.

    Ate 2026-09-05 esta funcao nao existia e a arvore era sempre a copia
    adjacente (`parents[1]/'contract'`). Havia onze arvores na maquina e nenhuma
    linha de codigo elegendo dona — cada programa se amarrava a vizinha por
    `__file__`.

    A resolucao agora prefere a canonica do master-harness, mas **cai no vizinho
    em qualquer tropeco**: `mh` nao instalado, flag em `vizinho`, canonica
    ausente. Dependencia dura sobre o `mh` num sistema de uso diario seria trocar
    duplicidade por fragilidade, e o custo de errar aqui e o harness nao subir.

    A origem viaja junto de proposito. Cair para o vizinho em silencio seria a
    mesma classe de defeito que B-14, B-16 e B-17: continua funcionando, ninguem
    fica sabendo, e o proximo a investigar comeca do zero.

    `root` explicito e honrado sem consultar nada: quem passa raiz esta sendo
    especifico, e a maioria desses chamadores e teste com fixture.
    """
    vizinho = Path(root or Path(__file__).resolve().parents[1]) / "contract"
    if root is not None:
        return vizinho, "vizinho:raiz-explicita"
    try:
        _mh_pelo_marcador()
        from mh import contrato as _mh_contrato
        from mh import flags as _mh_flags

        if _mh_flags.get("contrato") == "vizinho":
            return vizinho, "vizinho:flag"
        if (_mh_contrato.CANONICA / "capabilities.json").is_file():
            return _mh_contrato.CANONICA, "mh"
        return vizinho, "vizinho:canonica-ausente"
    except Exception as exc:  # noqa: BLE001 - o fallback nao pode ter buraco
        return vizinho, f"vizinho:{type(exc).__name__}"


def load_contract(root: str | Path | None = None) -> dict[str, Any]:
    contract, origem = arvore_do_contrato(root)
    return {
        "capabilities": json.loads((contract / "capabilities.json").read_text(encoding="utf-8")),
        "pipelines": json.loads((contract / "pipelines.json").read_text(encoding="utf-8")),
        "lock": json.loads((contract / "contract.lock.json").read_text(encoding="utf-8")),
        "root": contract,
        "origem": origem,
    }


def verify_lock(contract: dict[str, Any]) -> bool:
    lock = contract["lock"]
    digest = hashlib.sha256()
    for relative in lock.get("files", []):
        path = contract["root"] / relative
        if not path.is_file():
            return False
        digest.update(Path(relative).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(_snapshot_bytes(path))
        digest.update(b"\0")
    return digest.hexdigest() == lock.get("sha256")


def _snapshot_bytes(path: Path) -> bytes:
    if path.suffix.lower() != ".json":
        return path.read_bytes()
    value = json.loads(path.read_text(encoding="utf-8"))
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def evidence_is_valid(root: str | Path, records: list) -> bool:
    """A evidencia declarada existe de verdade?

    Ate 2026-09-03 esta checagem era `(plugin / record.split("#", 1)[0]).exists()`:
    o nome do teste depois do `#` era descartado, entao
    `tests/x.py#test_que_nunca_existiu` carimbava a capacidade como "equivalent".

    A conta de equipotencia com o harness4codex se apoia nesse carimbo. Checar
    so o arquivo troca "existe um teste que prova isto" por "existe um arquivo
    com esse nome" — que e outra afirmacao, bem mais fraca.

    Registro sem `#` continua sendo afirmacao sobre o arquivo, e continua
    valendo como tal: nem toda evidencia e um teste nomeado.

    A busca do nome e textual (`def <nome>` no arquivo). Nao importa o teste
    para nao executar codigo arbitrario ao montar um relatorio, e nao usa AST
    porque a classe que envolve o metodo tornaria a regra mais fragil, nao mais
    forte — o que se quer saber e se o nome esta escrito ali.
    """
    base = Path(root)
    if not records:
        return False
    for record in records:
        arquivo, _, ancora = str(record).partition("#")
        caminho = base / arquivo
        if not caminho.is_file():
            return False
        if not ancora:
            continue
        nome = ancora.split("::")[-1].strip()
        if not nome:
            continue
        try:
            texto = caminho.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        if f"def {nome}(" not in texto:
            return False
    return True


def build_capability_report(root: str | Path | None = None) -> dict[str, Any]:
    plugin = Path(root or Path(__file__).resolve().parents[1]).resolve()
    # `plugin` continua servindo a EVIDENCIA (os testes nomeados vivem no repo),
    # mas a arvore do contrato passa a se resolver sozinha. Eram dois papeis no
    # mesmo argumento, e so um deles muda de dono nesta etapa.
    contract = load_contract()
    required = [item["id"] for item in contract["capabilities"]["capabilities"] if item["level"] == "required"]
    capabilities = {}
    for capability in required:
        records = EVIDENCE.get(capability, [])
        valid = evidence_is_valid(plugin, records)
        capabilities[capability] = {
            "status": "equivalent" if valid else "degraded",
            "evidence": records if valid else ["missing conformance evidence"],
        }
    lock_valid = verify_lock(contract)
    canonical_pipelines = json.dumps(
        contract["pipelines"].get("pipelines") or {},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "contract_version": contract["capabilities"]["contract_version"],
        # De qual arvore este relatorio saiu. Sem isto, um relatorio lido da
        # canonica e um lido do vizinho sao indistinguiveis — e a diferenca
        # entre os dois e justamente o que esta migracao muda.
        "contract_origem": contract["origem"],
        "adapter": "harness4claude",
        "capabilities": capabilities,
        "snapshot_lock_valid": lock_valid,
        "pipeline_fingerprint": hashlib.sha256(canonical_pipelines.encode("utf-8")).hexdigest(),
        "conformant": lock_valid and all(item["status"] == "equivalent" for item in capabilities.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("check", nargs="?")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    report = build_capability_report(args.root)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["conformant"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
