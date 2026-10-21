"""A sonda de `integration.science-harness` (`scripts/science_probe.py`) contra um servidor dublê, nos tres estados.

D-136. Ate aqui a sonda provava so o roteamento do hook `science_intent`: com o `shs.exe` quebrado ela continuava
verde. Agora o hook saiu e a sonda e um script que le o registro do `science_harness` no `.claude.json`, sobe o
servidor registrado e faz `initialize` e `tools/list` (o handshake mora em `scripts/sonda_mcp.py`, copia derivada do
master-harness). A saida e uma linha JSON e o codigo 0 (passa), 1 (reprova) ou 2 (nao verificado), a convencao do
`mh paridade`: o pytest nao tem terceiro estado sem `skip`, entao os tres estados saem pelo canal da sonda.

Tudo aqui e hermetico: `CLAUDE_CONFIG_DIR` aponta para o `tmp_path` e o servidor e `tests/fixtures/fake_claims_mcp.py`,
subido com `sys.executable`. Nenhum teste precisa do `shs` e nenhum pula.

As duas metades: cada caso de reprova usa o mesmo duble do caso de passa com uma chave a menos, e o caso de passa e a
metade que mostra que a sonda nao reprova tudo.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
SONDA = ROOT / "scripts" / "science_probe.py"
DUBLE = ROOT / "tests" / "fixtures" / "fake_claims_mcp.py"

#: As ferramentas que a sonda exige (DU-6, 21/10): as que a skill `science-evidence` manda usar, cruzadas com as que o
#: Codex aprova. Escrita aqui, independente do nucleo, de proposito: derivar do codigo sob teste tornaria o teste
#: tautologico (chave F-040 do `tools/fatos.json` do master-harness). `list_corpora` existe no servidor e nao entra.
FERRAMENTAS_EXIGIDAS = ("server_info", "search_claims", "get_claim")

PRAZO_DA_SONDA_S = 60.0


def _spec(*flags: str, env: dict | None = None, command: str | None = None) -> dict:
    return {"type": "stdio", "command": command or sys.executable, "args": [str(DUBLE), *flags], "env": env or {}}


def _config(tmp_path: Path, conteudo: dict | str | None) -> Path:
    base = tmp_path / "claude-config"
    base.mkdir(parents=True, exist_ok=True)
    if conteudo is not None:
        texto = conteudo if isinstance(conteudo, str) else json.dumps(conteudo)
        (base / ".claude.json").write_text(texto, encoding="utf-8")
    return base


def _rodar(config_dir: Path, cwd: Path, *extra: str) -> tuple[int, dict]:
    """Roda o script como em producao e devolve `(codigo, relato)`. A saida e conferida ANTES do `json.loads`: um
    script que sai 0 sem imprimir reprova aqui, como constatacao, e nao como excecao do `json`."""
    env = os.environ.copy()
    env["CLAUDE_CONFIG_DIR"] = str(config_dir)
    env["PYTHONUTF8"] = "1"
    resultado = subprocess.run(
        [sys.executable, str(SONDA), "--json", "--cwd", str(cwd), *extra],
        stdin=subprocess.DEVNULL, capture_output=True,
        text=True, encoding="utf-8", check=False, env=env, timeout=PRAZO_DA_SONDA_S,
    )
    linhas = resultado.stdout.strip().splitlines()
    assert len(linhas) == 1, f"a sonda tem de imprimir uma linha JSON; saiu {resultado.stdout!r} / {resultado.stderr!r}"
    return resultado.returncode, json.loads(linhas[0])


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    d = tmp_path / "repo"
    d.mkdir()
    return d


def _vivo(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = ctypes.c_void_p
        k32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        handle = k32.OpenProcess(0x1000, 0, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            codigo = ctypes.c_uint32()
            k32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
            ok = k32.GetExitCodeProcess(handle, ctypes.byref(codigo))
            return bool(ok) and codigo.value == 259  # STILL_ACTIVE
        finally:
            k32.CloseHandle.argtypes = [ctypes.c_void_p]
            k32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _ler_pid(arquivo: Path) -> int:
    fim = time.monotonic() + 10
    while time.monotonic() < fim:
        texto = arquivo.read_text(encoding="utf-8").strip() if arquivo.exists() else ""
        if texto:
            return int(texto)
        time.sleep(0.05)
    raise AssertionError(f"o duble nao gravou o pid em {arquivo}")


# ------------------------------------------------------------------------------------------ o no canonico (L-65)


def test_sonda_alcanca_o_mcp_registrado_nos_tres_estados(tmp_path, repo):
    """Sonda de `integration.science-harness`: registro -> servidor subido -> `tools/list`, nos tres estados."""
    # passa (0): o duble completo, registrado no escopo do usuario.
    bom = _config(tmp_path / "bom", {"mcpServers": {"science_harness": _spec()}})
    codigo, relato = _rodar(bom, repo)
    assert (codigo, relato["estado"], relato["motivo"]) == (0, "passa", "ok")
    assert set(FERRAMENTAS_EXIGIDAS) <= set(relato["ferramentas_vistas"])
    assert relato["ferramentas_exigidas"] == list(FERRAMENTAS_EXIGIDAS)
    assert relato["capacidade"] == "integration.science-harness" and relato["host"] == "claude"

    # reprova (1): o mesmo duble sem `search_claims`. Um script que sempre passa nao chega aqui.
    quebrado = _config(tmp_path / "quebrado", {"mcpServers": {"science_harness": _spec("--sem", "search_claims")}})
    codigo, relato = _rodar(quebrado, repo)
    assert (codigo, relato["estado"], relato["motivo"]) == (1, "reprova", "ferramenta_ausente")
    assert "search_claims" in relato["detalhe"]

    # nao verificado (2): nenhum registro. Um script que sempre reprova (ou sempre passa) nao chega aqui.
    vazio = _config(tmp_path / "vazio", {"mcpServers": {}})
    codigo, relato = _rodar(vazio, repo)
    assert (codigo, relato["estado"], relato["motivo"]) == (2, "nao_verificado", "nao_registrado")


# ----------------------------------------------------------------------------------- reprova: registrado e quebrado


@pytest.mark.parametrize("faltando", FERRAMENTAS_EXIGIDAS)
def test_cada_ferramenta_exigida_ausente_reprova(tmp_path, repo, faltando):
    config = _config(tmp_path, {"mcpServers": {"science_harness": _spec("--sem", faltando)}})
    codigo, relato = _rodar(config, repo)
    assert (codigo, relato["estado"], relato["motivo"]) == (1, "reprova", "ferramenta_ausente")
    assert faltando in relato["detalhe"]


def test_ferramenta_fora_da_lista_exigida_nao_reprova(tmp_path, repo):
    """Metade de falsificacao: `list_corpora` existe, o Codex nao a aprova (DU-6) e a sonda passa sem ela."""
    config = _config(tmp_path, {"mcpServers": {"science_harness": _spec("--sem", "list_corpora")}})
    codigo, relato = _rodar(config, repo)
    assert (codigo, relato["estado"], relato["motivo"]) == (0, "passa", "ok")
    assert "list_corpora" not in relato["ferramentas_vistas"]


def test_executavel_registrado_e_ausente_reprova(tmp_path, repo):
    """Registrado e quebrado reprova: e o caso do D-136. O hook antigo contava isto como 'ausente' e seguia verde."""
    morto = _spec(command=str(tmp_path / "nao-existe" / "shs.exe"))
    config = _config(tmp_path, {"mcpServers": {"science_harness": morto}})
    codigo, relato = _rodar(config, repo)
    assert (codigo, relato["estado"], relato["motivo"]) == (1, "reprova", "executavel_ausente")


def test_servidor_que_nunca_responde_reprova_por_prazo_e_nao_sobrevive(tmp_path, repo):
    arquivo_pid = tmp_path / "pid-mudo.txt"
    config = _config(tmp_path, {"mcpServers": {"science_harness": _spec("--mudo", "--pid", str(arquivo_pid))}})
    codigo, relato = _rodar(config, repo, "--prazo", "2")
    assert (codigo, relato["estado"], relato["motivo"]) == (1, "reprova", "prazo")
    assert relato["duracao_s"] < 30
    assert not _vivo(_ler_pid(arquivo_pid)), "o duble sobreviveu a sonda"


def test_servidor_que_responde_nao_sobrevive_a_sonda(tmp_path, repo):
    """Metade do teste acima: tambem o servidor que passa sai com a sonda (sem orfao)."""
    arquivo_pid = tmp_path / "pid-bom.txt"
    config = _config(tmp_path, {"mcpServers": {"science_harness": _spec("--pid", str(arquivo_pid))}})
    codigo, relato = _rodar(config, repo)
    assert (codigo, relato["motivo"]) == (0, "ok")
    assert not _vivo(_ler_pid(arquivo_pid))


def test_servidor_que_morre_antes_reprova(tmp_path, repo):
    config = _config(tmp_path, {"mcpServers": {"science_harness": _spec("--morre")}})
    codigo, relato = _rodar(config, repo)
    assert (codigo, relato["estado"], relato["motivo"]) == (1, "reprova", "saiu_antes")


@pytest.mark.parametrize("flag", ["--invalido", "--erro"])
def test_resposta_invalida_reprova(tmp_path, repo, flag):
    config = _config(tmp_path, {"mcpServers": {"science_harness": _spec(flag)}})
    codigo, relato = _rodar(config, repo)
    assert (codigo, relato["estado"], relato["motivo"]) == (1, "reprova", "resposta_invalida")


def test_valor_de_env_do_registro_nunca_sai(tmp_path, repo):
    """O duble ecoa o valor-sentinela no stderr e morre; a cauda do stderr vai no relato com o valor como `***`."""
    sentinela = "valor-sentinela-d136-9f3a"
    spec = _spec("--eco-env", "SEGREDO_DA_SONDA", "--morre", env={"SEGREDO_DA_SONDA": sentinela})
    config = _config(tmp_path, {"mcpServers": {"science_harness": spec}})
    env = os.environ.copy()
    env["CLAUDE_CONFIG_DIR"] = str(config)
    resultado = subprocess.run(
        [sys.executable, str(SONDA), "--json", "--cwd", str(repo)],
        stdin=subprocess.DEVNULL, capture_output=True,
        text=True, encoding="utf-8", check=False, env=env, timeout=PRAZO_DA_SONDA_S,
    )
    assert resultado.returncode == 1
    assert sentinela not in resultado.stdout and sentinela not in resultado.stderr
    assert "***" in resultado.stdout, "a metade que prova que o duble ecoou o valor e a sonda o escondeu"


# ----------------------------------------------------------------------------------- o leitor do registro (escopos)


def test_escopo_local_do_projeto_conta(tmp_path, repo):
    config = _config(tmp_path, {"projects": {repo.as_posix(): {"mcpServers": {"science_harness": _spec()}}}})
    codigo, relato = _rodar(config, repo)
    assert (codigo, relato["motivo"]) == (0, "ok")


def test_escopo_local_vale_para_subdiretorio_do_projeto(tmp_path, repo):
    sub = repo / "src" / "pkg"
    sub.mkdir(parents=True)
    config = _config(tmp_path, {"projects": {repo.as_posix(): {"mcpServers": {"science_harness": _spec()}}}})
    codigo, relato = _rodar(config, sub)
    assert (codigo, relato["motivo"]) == (0, "ok")


def test_escopo_local_de_outro_projeto_nao_conta(tmp_path, repo):
    outro = tmp_path / "outro"
    outro.mkdir()
    config = _config(tmp_path, {"projects": {outro.as_posix(): {"mcpServers": {"science_harness": _spec()}}}})
    codigo, relato = _rodar(config, repo)
    assert (codigo, relato["estado"], relato["motivo"]) == (2, "nao_verificado", "nao_registrado")


def test_escopo_do_projeto_vale_antes_do_escopo_do_usuario(tmp_path, repo):
    """O projeto registra o duble sem `search_claims` (reprova) e o usuario registra o duble bom: vale o do projeto."""
    config = _config(tmp_path, {
        "mcpServers": {"science_harness": _spec()},
        "projects": {repo.as_posix(): {"mcpServers": {"science_harness": _spec("--sem", "search_claims")}}},
    })
    codigo, relato = _rodar(config, repo)
    assert (codigo, relato["motivo"]) == (1, "ferramenta_ausente")


def test_escopo_do_usuario_vale_sem_registro_no_projeto(tmp_path, repo):
    """Metade do teste acima: o mesmo usuario, com o projeto presente no arquivo mas sem registro, passa."""
    config = _config(tmp_path, {"mcpServers": {"science_harness": _spec()}, "projects": {repo.as_posix(): {}}})
    codigo, relato = _rodar(config, repo)
    assert (codigo, relato["motivo"]) == (0, "ok")


def test_desligado_no_projeto_pelo_mcp_e_nao_verificado(tmp_path, repo):
    """`/mcp` desliga um servidor do usuario por projeto em `disabledMcpServers`: a maquina nao o declarou aqui."""
    config = _config(tmp_path, {
        "mcpServers": {"science_harness": _spec()},
        "projects": {repo.as_posix(): {"disabledMcpServers": ["science_harness"]}},
    })
    codigo, relato = _rodar(config, repo)
    assert (codigo, relato["estado"], relato["motivo"]) == (2, "nao_verificado", "desligado")


def test_desligar_outro_servidor_nao_desliga_o_science(tmp_path, repo):
    """Metade do teste acima: `disabledMcpServers` com outro nome nao afeta o `science_harness`."""
    config = _config(tmp_path, {
        "mcpServers": {"science_harness": _spec()},
        "projects": {repo.as_posix(): {"disabledMcpServers": ["obsidian"]}},
    })
    codigo, relato = _rodar(config, repo)
    assert (codigo, relato["motivo"]) == (0, "ok")


def test_config_corrompida_e_nao_verificado(tmp_path, repo):
    config = _config(tmp_path, '{"mcpServers": {"science_harness": ')
    codigo, relato = _rodar(config, repo)
    assert (codigo, relato["estado"], relato["motivo"]) == (2, "nao_verificado", "config_ilegivel")


def test_sem_arquivo_de_config_e_nao_verificado(tmp_path, repo):
    codigo, relato = _rodar(_config(tmp_path, None), repo)
    assert (codigo, relato["estado"], relato["motivo"]) == (2, "nao_verificado", "config_ausente")


def test_outro_servidor_registrado_nao_conta_como_science(tmp_path, repo):
    config = _config(tmp_path, {"mcpServers": {"obsidian": _spec()}})
    codigo, relato = _rodar(config, repo)
    assert (codigo, relato["estado"], relato["motivo"]) == (2, "nao_verificado", "nao_registrado")


def test_registro_por_url_e_nao_verificado(tmp_path, repo):
    config = _config(tmp_path, {"mcpServers": {"science_harness": {"type": "http", "url": "http://127.0.0.1:1/mcp"}}})
    codigo, relato = _rodar(config, repo)
    assert (codigo, relato["estado"], relato["motivo"]) == (2, "nao_verificado", "transporte_url")
