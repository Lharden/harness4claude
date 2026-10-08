"""O hook `science_intent` so manda usar o `science_harness` quando o Claude o tem.

Ate 2026-10-07 o hook disparava pela regex e injetava "use science_harness" sem
o MCP registrado no Claude: 995 emissoes em `emissions.jsonl`, de 2026-09-02 a
2026-10-07, todas mandando o modelo usar uma ferramenta que nao existia. A skill
`science-evidence` ja se cercava pela presenca do MCP; o hook nao.

Decisao 5 de `master-harness/docs/decisoes-capacidades-orfas.md`: registrar o
MCP e cercar o hook pela presenca dele. A presenca e lida do `.claude.json`
(escopo de usuario e escopo local do projeto), sem subir processo nem tocar a
rede: o hook roda em todo prompt.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
HOOK = ROOT / "hooks" / "science_intent.py"

# Ultimo commit antes do cerco: o controle roda o hook que emitia sem o MCP.
OLD_REF = "22d0442ded849449575a082c47367d73d8cf448b"

PROMPT_CIENTIFICO = "Review the scientific evidence for this claim in the corpus"
PROMPT_DE_INFRA = "Rode a suite e junte a evidência de que o gate de stop reprova"
SERVIDOR = {"type": "stdio", "command": sys.executable, "args": ["-c", "pass"], "env": {}}


def _config(tmp_path: Path, conteudo: dict | str | None) -> Path:
    base = tmp_path / "claude-config"
    base.mkdir(exist_ok=True)
    if conteudo is not None:
        texto = conteudo if isinstance(conteudo, str) else json.dumps(conteudo)
        (base / ".claude.json").write_text(texto, encoding="utf-8")
    return base


def _rodar(hook: Path, config_dir: Path, cwd: Path, prompt: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["CLAUDE_CONFIG_DIR"] = str(config_dir)
    return subprocess.run(
        [sys.executable, str(hook)],
        input=json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": "s-sci",
                          "cwd": str(cwd), "prompt": prompt}),
        capture_output=True, text=True, encoding="utf-8", check=False, env=env,
    )


def _emitiu(resultado: subprocess.CompletedProcess) -> bool:
    assert resultado.returncode == 0, resultado.stderr
    return "science-evidence" in resultado.stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    d = tmp_path / "repo"
    d.mkdir()
    return d


def test_mcp_ausente_e_prompt_cientifico_nao_emite(tmp_path, repo):
    config = _config(tmp_path, {"mcpServers": {"obsidian": SERVIDOR}})
    assert not _emitiu(_rodar(HOOK, config, repo, PROMPT_CIENTIFICO))


def test_prompt_de_infra_com_evidencia_e_mcp_ausente_nao_emite(tmp_path, repo):
    config = _config(tmp_path, {"mcpServers": {}})
    assert not _emitiu(_rodar(HOOK, config, repo, PROMPT_DE_INFRA))


def test_mcp_presente_e_prompt_cientifico_emite(tmp_path, repo):
    config = _config(tmp_path, {"mcpServers": {"science_harness": SERVIDOR}})
    resultado = _rodar(HOOK, config, repo, PROMPT_CIENTIFICO)
    assert _emitiu(resultado)
    mensagem = json.loads(resultado.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "science_harness" in mensagem and "read-only" in mensagem


def test_mcp_presente_e_prompt_sem_ciencia_nao_emite(tmp_path, repo):
    config = _config(tmp_path, {"mcpServers": {"science_harness": SERVIDOR}})
    assert not _emitiu(_rodar(HOOK, config, repo, "renomeie a variavel x para y"))


def test_sem_arquivo_de_config_nao_emite(tmp_path, repo):
    assert not _emitiu(_rodar(HOOK, _config(tmp_path, None), repo, PROMPT_CIENTIFICO))


def test_config_corrompida_nao_emite(tmp_path, repo):
    config = _config(tmp_path, '{"mcpServers": {"science_harness": ')
    assert not _emitiu(_rodar(HOOK, config, repo, PROMPT_CIENTIFICO))


def test_registro_com_executavel_inexistente_nao_conta(tmp_path, repo):
    morto = dict(SERVIDOR, command=str(tmp_path / "nao-existe" / "shs.exe"))
    config = _config(tmp_path, {"mcpServers": {"science_harness": morto}})
    assert not _emitiu(_rodar(HOOK, config, repo, PROMPT_CIENTIFICO))


def test_escopo_local_do_projeto_conta(tmp_path, repo):
    config = _config(tmp_path, {"projects": {
        repo.as_posix(): {"mcpServers": {"science_harness": SERVIDOR}}}})
    assert _emitiu(_rodar(HOOK, config, repo, PROMPT_CIENTIFICO))


def test_escopo_local_vale_para_subdiretorio_do_projeto(tmp_path, repo):
    sub = repo / "src" / "pkg"
    sub.mkdir(parents=True)
    config = _config(tmp_path, {"projects": {
        repo.as_posix(): {"mcpServers": {"science_harness": SERVIDOR}}}})
    assert _emitiu(_rodar(HOOK, config, sub, PROMPT_CIENTIFICO))


def test_escopo_local_de_outro_projeto_nao_conta(tmp_path, repo):
    outro = tmp_path / "outro"
    outro.mkdir()
    config = _config(tmp_path, {"projects": {
        outro.as_posix(): {"mcpServers": {"science_harness": SERVIDOR}}}})
    assert not _emitiu(_rodar(HOOK, config, repo, PROMPT_CIENTIFICO))


def test_desligado_no_projeto_pelo_mcp_nao_conta(tmp_path, repo):
    """`/mcp` desliga um servidor do usuario por projeto em `disabledMcpServers`."""
    config = _config(tmp_path, {
        "mcpServers": {"science_harness": SERVIDOR},
        "projects": {repo.as_posix(): {"disabledMcpServers": ["science_harness"]}},
    })
    assert not _emitiu(_rodar(HOOK, config, repo, PROMPT_CIENTIFICO))


def test_CONTROLE_codigo_antigo_emitia_sem_mcp(tmp_path, repo):
    """Metade de falsificacao: o hook de OLD_REF, sem o MCP, emite.

    Sem isto os testes de "nao emite" acima passariam tambem com um hook que nao
    roda ou que nao le o prompt, e seriam indistinguiveis do cerco.
    """
    antigo = tmp_path / "antigo" / "hooks"
    antigo.mkdir(parents=True)
    for nome in ("science_intent.py", "emit.py"):
        conteudo = subprocess.run(
            ["git", "show", f"{OLD_REF}:hooks/{nome}"],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=True,
        ).stdout
        (antigo / nome).write_text(conteudo, encoding="utf-8")
    config = _config(tmp_path, {"mcpServers": {}})

    assert _emitiu(_rodar(antigo / "science_intent.py", config, repo, PROMPT_CIENTIFICO))
    assert _emitiu(_rodar(antigo / "science_intent.py", config, repo, PROMPT_DE_INFRA))
