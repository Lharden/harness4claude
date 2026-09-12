"""O anuncio nao pode prometer uma task que o banco nao recebeu.

Incidente 2026-09-12, achado secundario. `harness-classify.sh` escreve o
`state.json` com o `task_id` novo ANTES do dual-write, envolve o `start_task`
num `except Exception` que so anexa a um log, e emite

    HARNESS v3 CLASSIFIED: ... Task ID: t-...

**fora** desse `try`. Se o banco falha, o hook anuncia um id que existe apenas no
`state.json` — sem `revision`, sem `scope_id`, e ausente da tabela `tasks`. O
modelo entao abre pipeline sobre ele e toda transicao morre em `_locked_task`,
que nao acha a linha.

O incidente que deu origem a este ramo nao foi causado por este caminho (nao
havia `transactional-state-error.log` em nenhum dos dois baldes — a task existia,
so que no balde de outro projeto). Mas a busca pela causa passou por aqui, e o
caminho estava mesmo aberto.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
SABOTAGEM = 'raise RuntimeError("transactional_state sabotado pelo teste")\n'
PROMPT = "implementar uma feature nova de autenticacao com refatoracao de arquitetura"


def _bash() -> str | None:
    achado = shutil.which("bash")
    if achado:
        return achado
    for c in (
        Path(r"C:\Program Files\Git\bin\bash.exe"),
        Path(r"C:\Program Files\Git\usr\bin\bash.exe"),
        Path.home() / "AppData" / "Local" / "Programs" / "Git" / "usr" / "bin" / "bash.exe",
    ):
        if c.exists():
            return str(c)
    return None


@pytest.fixture(scope="module")
def plugin_copy(tmp_path_factory):
    """Copia do plugin para poder sabotar `scripts/` sem tocar no repo."""
    dst = tmp_path_factory.mktemp("plugin")
    for sub in ("hooks", "scripts", "schemas", "contract"):
        src = ROOT / sub
        if src.is_dir():
            shutil.copytree(src, dst / sub, dirs_exist_ok=True)
    return dst


# Sem `pytest.skip` de modulo: `TestGuardaEstatica` so le texto e vale em host
# nenhum-bash. Quem depende de bash e so a classe registrada em
# `conftest.BASH_REQUIRED_CLASSES`, que o gate de plataforma pula por marca.
BASH: str = _bash() or "bash"


def _classificar(plugin: Path, cwd: Path, sessao: str) -> subprocess.CompletedProcess:
    """Uma sessao POR TESTE.

    `harness_dir` do conftest e class-scoped, entao os testes desta classe
    dividem o mesmo `HARNESS_DIR`. Reusar a sessao faria o segundo prompt cair no
    caminho CONTINUING da task que o primeiro deixou ativa — e o teste mediria a
    continuacao em vez do anuncio.
    """
    payload = {"session_id": sessao, "cwd": str(cwd), "prompt": PROMPT}
    env = {**os.environ, "PYTHONUTF8": "1"}
    return subprocess.run(
        [BASH, str(plugin / "hooks" / "harness-classify.sh")],
        input=json.dumps(payload), capture_output=True, text=True, timeout=60, env=env,
    )


def _repo(base: Path, nome: str) -> Path:
    d = base / nome
    (d / ".git").mkdir(parents=True)
    return d


class TestAnuncioSoPromete0QueOBancoRecebeu:
    def test_intacto_anuncia_classified(self, plugin_copy, tmp_path):
        """Controle: sem sabotagem o caminho normal segue anunciando."""
        res = _classificar(plugin_copy, _repo(tmp_path, "alpha"), "s-controle")
        assert res.returncode == 0, res.stderr
        assert "HARNESS v3 CLASSIFIED" in res.stdout, res.stdout

    def test_banco_que_falha_nao_vira_classified(self, plugin_copy, tmp_path):
        alvo = plugin_copy / "scripts" / "transactional_state.py"
        original = alvo.read_text(encoding="utf-8")
        alvo.write_text(SABOTAGEM, encoding="utf-8")
        try:
            res = _classificar(plugin_copy, _repo(tmp_path, "beta"), "s-sabotado")
        finally:
            alvo.write_text(original, encoding="utf-8")

        assert res.returncode == 0, res.stderr
        assert "HARNESS v3 CLASSIFIED" not in res.stdout, (
            "anunciou uma task que a tabela `tasks` nunca recebeu:\n" + res.stdout
        )
        assert "HARNESS v3 WARNING" in res.stdout, res.stdout
        assert "NAO esta na tabela" in res.stdout, res.stdout

    def test_o_aviso_desaconselha_o_pipeline(self, plugin_copy, tmp_path):
        """Avisar sem dizer o que fazer devolve o problema para quem le."""
        alvo = plugin_copy / "scripts" / "transactional_state.py"
        original = alvo.read_text(encoding="utf-8")
        alvo.write_text(SABOTAGEM, encoding="utf-8")
        try:
            res = _classificar(plugin_copy, _repo(tmp_path, "gama"), "s-conselho")
        finally:
            alvo.write_text(original, encoding="utf-8")

        assert "harness-workflow" in res.stdout, res.stdout
        assert "transactional-state-error.log" in res.stdout, res.stdout


class TestGuardaEstatica:
    """O `try` pode voltar a engolir a falha numa edicao futura sem que nenhum
    teste de comportamento perceba, porque o caminho feliz nao muda."""

    def test_o_bloco_classified_depende_do_resultado_do_banco(self):
        hook = (ROOT / "hooks" / "harness-classify.sh").read_text(encoding="utf-8")
        assert "transactional_ok = True" in hook, (
            "o dual-write voltou a nao registrar se deu certo"
        )
        anuncio = hook.index('_falar("classified"')
        guarda = hook.index("elif not transactional_ok:")
        assert guarda < anuncio, (
            "o bloco CLASSIFIED deixou de ser protegido pela checagem do banco"
        )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
