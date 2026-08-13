"""Testes do hooks/harness-arsenal-gate.sh — a unica barreira dura do sistema.

O caso que originou este arquivo: em 2026-08-13 o gate bloqueou a propria sessao
que o testava. A linha era um `printf` montando um payload JSON que CONTINHA o
comando de instalacao; o gate casava a string em qualquer posicao e barrou como
se fosse instalacao de verdade.

Guard que dispara em texto SOBRE a acao, e nao na acao, gasta a paciencia de quem
o usa ate virar `--no-verify`. Por isso metade destes casos e de mencao, nao de
invocacao: e o lado que o teste anterior nao cobria.

Os payloads ficam montados a partir de fragmentos justamente para que o comando
do pytest nao contenha a string-gatilho — senao rodar a suite dispararia o gate
da sessao.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
HOOK = ROOT / "hooks" / "harness-arsenal-gate.sh"
BASH = shutil.which("bash") or r"C:\Program Files\Git\bin\bash.exe"

# Montado em pedacos de proposito — ver o docstring.
_CP = "claude" + " plugin "
INSTALL = _CP + "install "
ENABLE = _CP + "enable "
DISABLE = _CP + "disable "


@pytest.fixture(scope="module")
def vault_vazio(tmp_path_factory):
    """Vault sem registry: toda ferramenta e desconhecida, que e o cenario de bloqueio."""
    return tmp_path_factory.mktemp("vault")


def roda(comando: str, vault: Path, harness: Path) -> tuple[int, str]:
    payload = json.dumps({"tool_input": {"command": comando}})
    env = dict(
        os.environ,
        HARNESS_DIR=str(harness),
        AI_BRAIN_PATH=str(vault),
        PYTHONUTF8="1",
        PYTHONIOENCODING="utf-8",
    )
    r = subprocess.run([BASH, str(HOOK)], input=payload, capture_output=True,
                       text=True, env=env, timeout=90)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


@pytest.fixture
def gate(vault_vazio, tmp_path):
    def _call(comando: str):
        return roda(comando, vault_vazio, tmp_path)
    return _call


class TestInvocacaoBloqueia:
    def test_install_sem_decisao(self, gate):
        code, saida = gate(INSTALL + "pinecone@mkt")
        assert code == 2, saida
        assert "sem decis" in saida or "decis" in saida

    def test_enable_sem_decisao(self, gate):
        assert gate(ENABLE + "pinecone")[0] == 2

    def test_depois_de_and(self, gate):
        """Comando encadeado ainda e invocacao."""
        assert gate("cd /tmp && " + INSTALL + "pinecone@mkt")[0] == 2

    def test_depois_de_ponto_e_virgula(self, gate):
        assert gate("echo oi; " + INSTALL + "pinecone")[0] == 2

    def test_com_prefixo_de_env(self, gate):
        assert gate("FOO=1 " + INSTALL + "pinecone")[0] == 2


class TestMencaoNaoBloqueia:
    """O lado que faltava. Cada um destes foi bloqueado pela primeira versao."""

    def test_printf_montando_payload(self, gate):
        """O caso literal que travou a sessao de 2026-08-13."""
        assert gate("printf 'rode: " + INSTALL + "pinecone'")[0] == 0

    def test_grep_procurando_o_comando(self, gate):
        assert gate("grep -r '" + INSTALL + "' docs/")[0] == 0

    def test_echo_escrevendo_documentacao(self, gate):
        assert gate("echo '" + INSTALL + "x' > nota.txt")[0] == 0

    def test_comentario_em_script(self, gate):
        assert gate("bash -c 'true'  # " + INSTALL + "pinecone")[0] == 0


class TestPassaDireto:
    def test_disable_nunca_pede_permissao(self, gate):
        """Tirar coisa do sistema nao precisa de decisao previa."""
        assert gate(DISABLE + "foo --scope user")[0] == 0

    def test_uninstall_passa(self, gate):
        assert gate(_CP + "uninstall foo")[0] == 0

    def test_list_passa(self, gate):
        assert gate(_CP + "list")[0] == 0

    def test_comando_qualquer(self, gate):
        assert gate("ls -la")[0] == 0


class TestFalhaAberta:
    def test_payload_desconhecido_avisa_e_passa(self, vault_vazio, tmp_path):
        """Travar todo Bash da maquina numa mudanca de schema do host seria pior
        que o problema. Mas nunca em silencio."""
        r = subprocess.run(
            [BASH, str(HOOK)], input='{"toolInput":{"command":"ls"}}',
            capture_output=True, text=True, timeout=90,
            env=dict(os.environ, HARNESS_DIR=str(tmp_path), AI_BRAIN_PATH=str(vault_vazio)),
        )
        assert r.returncode == 0
        assert "nao reconheceu" in (r.stdout + r.stderr)

    def test_json_invalido_nao_derruba(self, vault_vazio, tmp_path):
        r = subprocess.run([BASH, str(HOOK)], input="isto nao e json",
                           capture_output=True, text=True, timeout=90,
                           env=dict(os.environ, HARNESS_DIR=str(tmp_path),
                                    AI_BRAIN_PATH=str(vault_vazio)))
        assert r.returncode == 0
