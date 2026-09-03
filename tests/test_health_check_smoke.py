"""Meta-teste do smoke-test de hooks do health-check (2026-07-29).

Ate agora o health-check so conferia que os arquivos de hook existiam em disco.
Um hook presente e inerte — dependencia quebrada, evento renomeado pelo CLI
host, payload em formato novo — passava como `[OK]`. E o mesmo defeito que
originou a auditoria (codigo presente != codigo rodando), um nivel acima.

Um check que nao falha quando deveria e pior que nenhum check, porque produz
confianca. Estes testes sabotam hooks de proposito e exigem que o health-check
reprove. Sem eles, o smoke-test seria teatro.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])

BASH = "bash"
if sys.platform == "win32":
    for candidate in (
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
        "bash",
    ):
        if Path(candidate).exists() or candidate == "bash":
            BASH = candidate
            break

INERT_HOOK = "#!/usr/bin/env bash\nexit 0\n"


@pytest.fixture(scope="module")
def plugin_copy(tmp_path_factory):
    """Copia so o necessario para o health-check rodar — copiar o repo todo
    arrastaria .git, worktrees e caches, e deixaria a suite lenta."""
    dst = tmp_path_factory.mktemp("plugin")
    # `tools` entrou quando o arsenal-gate passou a existir: o hook chama
    # tools/arsenal.py, e sem a pasta ele degradava para exit 0 com aviso — o
    # comportamento honesto, mas que fazia o smoke medir a ausencia da pasta em
    # vez do gate.
    for sub in ("hooks", "scripts", "skills", "schemas", "tools", ".claude-plugin"):
        src = ROOT / sub
        if src.is_dir():
            shutil.copytree(src, dst / sub, dirs_exist_ok=True)
    return dst


def _run_health_check(plugin_dir: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["CLAUDE_PLUGIN_ROOT"] = str(plugin_dir)
    return subprocess.run(
        [BASH, str(plugin_dir / "scripts" / "health-check.sh")],
        capture_output=True, text=True, timeout=300, env=env,
    )


def _smoke_section(out: str) -> str:
    """So o bloco de execucao — o resto do relatorio tem falhas nao relacionadas
    (ruff/pyright ausentes, Obsidian off) que abafariam a assercao."""
    marker = "--- Hooks (execucao) ---"
    if marker not in out:
        return ""
    return out.split(marker, 1)[1].split("--- ", 1)[0]


class TestSmokeDetectaSabotagem:
    def test_hook_intacto_passa(self, plugin_copy):
        secao = _smoke_section(_run_health_check(plugin_copy).stdout)
        assert secao, "secao de execucao ausente do relatorio"
        assert "[FAIL]" not in secao, f"hooks intactos reprovaram:\n{secao}"

    def test_classify_inerte_reprova(self, plugin_copy, tmp_path):
        alvo = plugin_copy / "hooks" / "harness-classify.sh"
        original = alvo.read_text(encoding="utf-8")
        alvo.write_text(INERT_HOOK, encoding="utf-8")
        try:
            res = _run_health_check(plugin_copy)
            secao = _smoke_section(res.stdout)
            assert "[FAIL]" in secao, f"hook inerte passou despercebido:\n{secao}"
            assert res.returncode != 0, "health-check saiu 0 com hook inerte"
        finally:
            alvo.write_text(original, encoding="utf-8")

    def test_git_guard_que_para_de_bloquear_reprova(self, plugin_copy):
        """A regressao de seguranca: o guard some e nada avisa."""
        alvo = plugin_copy / "hooks" / "harness-git-guard.sh"
        original = alvo.read_text(encoding="utf-8")
        alvo.write_text(INERT_HOOK, encoding="utf-8")
        try:
            secao = _smoke_section(_run_health_check(plugin_copy).stdout)
            assert "git-guard bloqueia destrutivo" in secao
            assert "[FAIL]   git-guard bloqueia destrutivo" in secao, (
                f"guard desativado passou despercebido:\n{secao}"
            )
        finally:
            alvo.write_text(original, encoding="utf-8")

    def test_arsenal_gate_que_para_de_bloquear_reprova(self, plugin_copy):
        """Mesma sabotagem no gate de orcamento.

        Sem este teste, o smoke do arsenal-gate seria teatro: ele passaria
        igualmente com o hook funcionando e com um `exit 0` no lugar dele. E um
        gate que para de bloquear em silencio e pior que gate nenhum, porque o
        roster volta a crescer sozinho enquanto voce acredita estar protegido.
        """
        alvo = plugin_copy / "hooks" / "harness-arsenal-gate.sh"
        original = alvo.read_text(encoding="utf-8")
        alvo.write_text(INERT_HOOK, encoding="utf-8")
        try:
            secao = _smoke_section(_run_health_check(plugin_copy).stdout)
            assert "arsenal-gate bloqueia install sem decisao" in secao
            assert "[FAIL]   arsenal-gate bloqueia install sem decisao" in secao, (
                f"gate desativado passou despercebido:\n{secao}"
            )
        finally:
            alvo.write_text(original, encoding="utf-8")


class TestSmokeNaoTocaEstadoReal:
    def test_usa_harness_dir_temporario(self, plugin_copy):
        """O smoke-test escreve state; se usasse o HARNESS_DIR real, rodar o
        diagnostico corromperia o pipeline que ele deveria diagnosticar."""
        fonte = (ROOT / "scripts" / "health-check.sh").read_text(encoding="utf-8")
        bloco = fonte.split("--- Hooks (execucao) ---", 1)[1].split("Cobertura de eventos", 1)[0]
        assert 'HARNESS_DIR="$SMOKE_DIR/state"' in bloco, (
            "smoke-test precisa forcar HARNESS_DIR temporario em cada invocacao"
        )
        assert "mktemp -d" in bloco
        assert 'rm -rf "$SMOKE_DIR"' in bloco, "dir temporario precisa ser limpo"

    def test_estado_real_intacto_apos_health_check(self, plugin_copy):
        real = Path.home() / ".claude" / "harness" / "state.json"
        antes = real.read_bytes() if real.exists() else None
        _run_health_check(plugin_copy)
        depois = real.read_bytes() if real.exists() else None
        assert antes == depois, "health-check alterou o state.json real"


def _secao_obsidian(out: str) -> str:
    marker = "--- Obsidian integration ---"
    if marker not in out:
        return ""
    return out.split(marker, 1)[1].split("--- ", 1)[0]


class TestObsidianDistingueQuebraDeIndisponibilidade:
    """Doutor quebrado e REST fora sao diagnosticos diferentes (2026-09-03).

    O health-check rodava `python -m tools.vault_sync_doctor` e, em QUALQUER
    saida nao-zero, reportava "app fechado / REST off?". O doutor estava
    quebrado — `ModuleNotFoundError: No module named 'console'`, porque o bloco
    `__main__` supunha `tools/` em `sys.path[0]`, o que nao vale sob `-m` — e o
    relatorio culpava o Obsidian.

    Medido no momento do diagnostico: Obsidian com 3 processos vivos, porta
    27124 LISTENING, TLS estrito devolvendo 200, chave de 64 chars exportada,
    e o MCP respondendo `initialize`. Tudo no ar, e o relatorio dizia que nao.

    Mandar consertar a coisa errada e pior que nao dizer nada: gasta o tempo de
    quem le e deixa o defeito real de pe.
    """

    def test_doutor_quebrado_nao_e_reportado_como_obsidian_fora(self, plugin_copy):
        alvo = plugin_copy / "tools" / "console.py"
        if not alvo.is_file():
            pytest.skip("tools/console.py ausente nesta copia")
        guardado = alvo.read_text(encoding="utf-8")
        alvo.unlink()
        try:
            secao = _secao_obsidian(_run_health_check(plugin_copy).stdout)
            assert secao, "secao Obsidian ausente do relatorio"
            assert "app fechado" not in secao, (
                "doutor quebrado reportado como Obsidian fora:\n" + secao
            )
            assert "doctor" in secao.casefold()
        finally:
            alvo.write_text(guardado, encoding="utf-8")

    def test_secao_obsidian_nunca_reprova_o_health_check(self, plugin_copy):
        """Obsidian aberto nao e pre-requisito: a secao e WARN-only, por design."""
        secao = _secao_obsidian(_run_health_check(plugin_copy).stdout)
        assert "[FAIL]" not in secao, "integracao opcional nao pode reprovar:\n" + secao
