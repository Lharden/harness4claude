import importlib.util
import io
import json
import os
import sys
from pathlib import Path

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_contract_snapshot_lock_and_capability_report_are_complete():
    adapter = _load("contract_adapter", ROOT / "scripts" / "contract_adapter.py")
    report = adapter.build_capability_report(ROOT)

    assert report["contract_version"] == "1.1.0"
    assert report["adapter"] == "harness4claude"
    assert report["snapshot_lock_valid"] is True
    assert report["conformant"] is True
    assert len(report["capabilities"]) == 22
    assert all(item["status"] in {"native", "equivalent"} for item in report["capabilities"].values())


def test_claude_pipelines_are_the_canonical_contract_pipelines():
    adapter = _load("contract_adapter_pipelines", ROOT / "scripts" / "contract_adapter.py")
    configured = json.loads((ROOT / "scripts" / "pipelines.json").read_text(encoding="utf-8"))["pipelines"]

    assert configured == adapter.load_contract()["pipelines"]["pipelines"]


def test_transactional_state_and_policy_engines_are_shipped():
    assert (ROOT / "scripts" / "transactional_state.py").exists()
    assert (ROOT / "scripts" / "command_policy.py").exists()
    assert (ROOT / "scripts" / "state_cli.py").exists()


def test_release_version_and_lifecycle_are_synchronized():
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    marketplace = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]

    assert plugin["version"] == marketplace["version"] == marketplace["plugins"][0]["version"] == "4.0.0"
    assert {
        "SessionStart",
        "UserPromptSubmit",
        "PreToolUse",
        "PostToolUse",
        "PostToolUseFailure",
        "PreCompact",
        "PostCompact",
        "SubagentStart",
        "SubagentStop",
        "Stop",
        "SessionEnd",
    } <= set(hooks)
    assert (ROOT / "skills" / "science-evidence" / "SKILL.md").exists()


def test_science_intent_routes_evidence_prompts(monkeypatch, capsys):
    hook = _load("science_intent", ROOT / "hooks" / "science_intent.py")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"prompt": "Review scientific evidence"})))

    assert hook.main() == 0

    response = json.loads(capsys.readouterr().out)
    # A chave mudou em 2026-09-01: `systemMessage` e canal de UI e nao entra no
    # contexto do modelo — nos 343 transcripts, 100% das linhas com ele no
    # stdout tem `content` vazio. Este assert le so a chave nova de proposito:
    # aceitar as duas deixaria a regressao passar despercebida.
    assert "systemMessage" not in response
    message = response["hookSpecificOutput"]["additionalContext"]
    assert "science-evidence" in message
    assert "read-only" in message
    assert "provenance" in message


def test_classifier_core_is_portable_and_used_by_the_hook():
    classifier = _load("classify_prompt", ROOT / "scripts" / "classify_prompt.py")

    assert classifier.classify_prompt("corrija este bug inesperado") == ("L1", "bug")
    assert classifier.classify_prompt("crie um novo sistema completo") == ("L2", "feature")
    hook = (ROOT / "hooks" / "harness-classify.sh").read_text(encoding="utf-8")
    assert "from classify_prompt import classify_prompt" in hook
    assert "level, task_type = classify_prompt(msg)" in hook


def test_workflow_encodes_drop_constrain_retain_gate():
    workflow = (ROOT / "skills" / "harness-workflow" / "SKILL.md").read_text(encoding="utf-8")

    assert "DROP / CONSTRAIN / RETAIN" in workflow
    assert "Antes de propagar qualquer artefato" in workflow
    assert "fronteira" in workflow
    assert "evidência" in workflow


# --- A evidencia tem que existir de verdade (incidente 2026-09-03) ------------
#
# `build_capability_report` fazia `record.split("#", 1)[0]` e checava so o
# arquivo. O nome do teste depois do `#` era descartado, entao
# `tests/x.py#test_que_nunca_existiu` carimbava a capacidade como "equivalent".
#
# A conta de equipotencia com o harness4codex se apoia nesse carimbo. Verificar
# so o arquivo transforma "existe um teste que prova isto" em "existe um arquivo
# com esse nome" — que e outra afirmacao, bem mais fraca.


adapter = _load("contract_adapter_evidencia", ROOT / "scripts" / "contract_adapter.py")


def test_nome_do_teste_ausente_degrada_a_capacidade(tmp_path, monkeypatch):
    """Arquivo presente e teste ausente nao pode passar por equivalencia."""
    monkeypatch.setitem(adapter.EVIDENCE, "cap.sonda", ["tests/sonda_falsa.py#test_que_nao_existe"])
    (ROOT / "tests" / "sonda_falsa.py").write_text(
        "def test_outro_nome():\n    assert True\n", encoding="utf-8")
    try:
        assert adapter.evidence_is_valid(ROOT, ["tests/sonda_falsa.py#test_que_nao_existe"]) is False
        assert adapter.evidence_is_valid(ROOT, ["tests/sonda_falsa.py#test_outro_nome"]) is True
    finally:
        (ROOT / "tests" / "sonda_falsa.py").unlink()


def test_arquivo_ausente_continua_degradando():
    assert adapter.evidence_is_valid(ROOT, ["tests/nao_existe_de_jeito_nenhum.py#test_x"]) is False


def test_registro_sem_ancora_verifica_so_o_arquivo():
    """Sem `#`, a afirmacao e sobre o arquivo — e continua valendo como tal."""
    assert adapter.evidence_is_valid(ROOT, ["scripts/contract_adapter.py"]) is True


def test_toda_evidencia_declarada_hoje_aponta_para_teste_que_existe():
    """O portao. Vermelho aqui = o contrato afirma prova que ninguem escreveu."""
    quebrados = []
    for capacidade, registros in adapter.EVIDENCE.items():
        for registro in registros:
            if not adapter.evidence_is_valid(ROOT, [registro]):
                quebrados.append(f"{capacidade} -> {registro}")
    assert not quebrados, "evidencia declarada que nao existe:\n  " + "\n  ".join(quebrados)
