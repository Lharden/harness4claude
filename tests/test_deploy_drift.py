"""Drift entre o repo e o plugin instalado (incidente 2026-09-02).

## O que aconteceu

Duas sessoes de Claude trabalhavam no mesmo plugin por checkouts diferentes —
uma em `Documents/projects/harness4claude`, outra em
`.claude/plugins/marketplaces/harness4claude` — e as duas faziam deploy com
`cp` ad-hoc para o MESMO cache. O cache virou uma mistura: parte de uma
sessao, parte da outra, e nenhum dos dois repos igual ao que rodava.

O sintoma chegou como um teste vermelho aparentemente trivial (`branch_state.py
add` recusando `--parent-session`). A causa era que o codigo em execucao vinha
de uma branch que nao existia em nenhum repo local.

## Por que este teste e separado do teste da CLI

`TestComandosDaSkillRodamMesmo` roda o CLI contra `HARNESS_PLUGIN_ROOT`, que o
`conftest` aponta para o repo. Isso esta certo: um teste de unidade deve medir
o codigo do repo, nao o que por acaso esta instalado. Mas com isso ele deixou
de detectar deploy velho — e detectar deploy velho era metade do valor dele.

Sao duas perguntas diferentes e cada uma merece o seu teste:

  - "o codigo do repo esta correto?"        -> testes de unidade, contra o repo
  - "o que roda e o codigo do repo?"        -> este arquivo, contra o cache

## Por que ele pula em vez de falhar quando nao ha cache

Em CI nao existe plugin instalado, e nao deve existir: a maquina de CI nao e
uma maquina de trabalho. Um teste que exige o cache falharia em CI por um
motivo que nao e defeito. `skip` diz a verdade — nao ha o que comparar.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
sys.path.insert(0, str(ROOT / "scripts"))

import deploy_to_cache as dtc  # noqa: E402


def _instalado():
    alvo = dtc.installed_root()
    if alvo is None:
        pytest.skip("nenhum plugin instalado nesta maquina — nada a comparar")
    return alvo


class TestInventario:
    """A lista de arquivos que viajam tem que ser derivada, nunca escrita a mao."""

    def test_usa_os_arquivos_versionados(self):
        arquivos = dtc.shipped_files(ROOT)
        assert arquivos, "inventario vazio — o git ls-files nao rodou"
        assert Path("scripts/branch_state.py") in arquivos
        assert Path("hooks/hooks.json") in arquivos

    def test_nao_inclui_o_que_nao_viaja(self):
        arquivos = dtc.shipped_files(ROOT)
        assert not [p for p in arquivos if p.parts[0] == ".github"]
        assert not [p for p in arquivos if "__pycache__" in p.parts]
        assert not [p for p in arquivos if p.parts[0] == "worktrees"]


class TestComparacao:
    """Fim de linha nao e divergencia; conteudo e."""

    def test_crlf_nao_conta_como_drift(self, tmp_path):
        a, b = tmp_path / "a", tmp_path / "b"
        a.write_bytes(b"linha um\nlinha dois\n")
        b.write_bytes(b"linha um\r\nlinha dois\r\n")
        assert dtc.same_content(a, b)

    def test_conteudo_diferente_conta(self, tmp_path):
        a, b = tmp_path / "a", tmp_path / "b"
        a.write_bytes(b"linha um\n")
        b.write_bytes(b"linha dois\n")
        assert not dtc.same_content(a, b)

    def test_ausente_no_destino_conta_como_drift(self, tmp_path):
        origem, destino = tmp_path / "src", tmp_path / "dst"
        (origem / "scripts").mkdir(parents=True)
        destino.mkdir()
        (origem / "scripts" / "x.py").write_text("oi\n", encoding="utf-8")
        divergentes = dtc.drift(origem, destino, [Path("scripts/x.py")])
        assert divergentes == [Path("scripts/x.py")]


class TestOQueRodaEOQueEstaNoRepo:
    def test_sem_drift_entre_repo_e_plugin_instalado(self):
        """O portao. Vermelho aqui = o que roda nao e o que esta versionado."""
        alvo = _instalado()
        divergentes = dtc.drift(ROOT, alvo, dtc.shipped_files(ROOT))
        assert not divergentes, (
            f"{len(divergentes)} arquivo(s) divergem do plugin instalado em {alvo}:\n  "
            + "\n  ".join(str(p) for p in divergentes[:20])
            + "\n\nrode: python scripts/deploy_to_cache.py --apply"
        )

    def test_o_check_pela_linha_de_comando_concorda_com_a_funcao(self):
        _instalado()
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "deploy_to_cache.py"), "--check"],
            capture_output=True, text=True, encoding="utf-8", timeout=120,
        )
        divergentes = dtc.drift(ROOT, _instalado(), dtc.shipped_files(ROOT))
        assert proc.returncode == (1 if divergentes else 0), proc.stdout + proc.stderr
