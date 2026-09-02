"""Testes para scripts/branch_config.py — o registry de knobs do Branch Keeper.

O que esta travado aqui, e por que:

- **Documentacao errada e pior que documentacao ausente.** O `CLAUDE.md` do
  usuario listou por semanas `MAX_OFFERS=2`, `FLOOR=0.55`, `DRIFT_FLOOR=0.35`,
  `DRIFT_SAMPLE=2` — quatro nomes que o codigo **nunca leu**. Quem exportasse
  `FLOOR=0.7` acreditando afrouxar o piso mudava nada, e o sensor ja era
  silencioso por outros motivos: "nao mudou nada" era indistinguivel de
  "funcionou". O design doc tinha a mesma doenca em prosa, dizendo cooldown de
  5 turnos enquanto o codigo usava 8.
- **Um registry sozinho nao impede drift.** O que impede e o teste que le a
  documentacao real e compara. Por isso estes testes apontam para arquivos de
  verdade, nao para fixtures.
- **O verificador nao pode punir o aviso.** Uma linha que adverte sobre um nome
  errado precisa citar o nome errado. Se o check acusasse essa linha, a saida
  racional seria apagar o aviso para calar o check — deixando a documentacao
  pior do que estava.
- **Knob novo sem registro e o drift de amanha.** O teste que varre o codigo
  atras de `HARNESS_BRANCH*` existe para que esquecer o registry doa na hora,
  nao meses depois.
"""
from __future__ import annotations

import importlib.util
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
CONFIG_PATH = ROOT / "scripts" / "branch_config.py"
CLAUDE_MD = Path.home() / ".claude" / "CLAUDE.md"
DESIGN_DOC = ROOT / "docs" / "specs" / "branch-keeper-design.md"

# Modulos que leem knobs do Branch Keeper.
FONTES = (
    ROOT / "scripts" / "branch_sensor.py",
    ROOT / "scripts" / "branch_state.py",
    ROOT / "scripts" / "branch_seed.py",
)


@pytest.fixture(scope="module")
def cfg():
    spec = importlib.util.spec_from_file_location("branch_config", CONFIG_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["branch_config"] = mod
    spec.loader.exec_module(mod)
    return mod


class TestRegistryCobreOCodigo:
    def test_todo_knob_lido_esta_registrado(self, cfg):
        """Knob lido do ambiente sem entrada no registry e o drift de amanha."""
        lidos = set()
        for fonte in FONTES:
            if not fonte.exists():
                continue
            texto = fonte.read_text(encoding="utf-8")
            # So as leituras de ambiente: `_f("NOME"`, `_i("NOME"`,
            # `os.environ.get("NOME"`. Mencao em comentario nao conta.
            for m in re.finditer(r'(?:_[fis]|environ\.get)\(\s*"(HARNESS_BRANCH[A-Z_]*)"', texto):
                lidos.add(m.group(1))
        faltando = lidos - set(cfg.KNOBS)
        assert not faltando, f"knobs lidos e nao registrados: {sorted(faltando)}"

    def test_todo_knob_tem_justificativa(self, cfg):
        """Default sem 'por que' vira numero magico na primeira duvida."""
        for nome, knob in cfg.KNOBS.items():
            assert len(knob.why) > 40, f"{nome} sem justificativa util"
            assert knob.unit, f"{nome} sem unidade"

    def test_os_dois_pisos_declaram_que_nao_estao_calibrados(self, cfg):
        """A medicao de 2026-09-01 saiu anticorrelacionada.

        Quem for mexer nesses dois precisa saber disso antes de escolher outro
        numero a olho — foi o erro original.
        """
        for nome in ("HARNESS_BRANCH_FLOOR", "HARNESS_BRANCH_DRIFT_FLOOR"):
            assert "CALIBRA" in cfg.KNOBS[nome].why.upper()


class TestDocumentacaoReal:
    def test_claude_md_bate_com_o_registry(self, cfg):
        if not CLAUDE_MD.exists():
            pytest.skip("CLAUDE.md do usuario nao existe nesta maquina")
        problemas = cfg.divergencias(CLAUDE_MD.read_text(encoding="utf-8"))
        assert not problemas, "\n".join(problemas)

    def test_design_doc_bate_com_o_registry(self, cfg):
        if not DESIGN_DOC.exists():
            pytest.skip("design doc ausente")
        problemas = cfg.divergencias(DESIGN_DOC.read_text(encoding="utf-8"))
        assert not problemas, "\n".join(problemas)


class TestDeteccaoDeDrift:
    def test_nome_fantasma_e_acusado_pelo_nome(self, cfg):
        problemas = cfg.divergencias("- Config: `MAX_OFFERS=2`, `FLOOR=0.55`")
        texto = "\n".join(problemas)
        assert "MAX_OFFERS" in texto and "FLOOR" in texto
        assert "HARNESS_BRANCH_MAX_OFFERS" in texto  # diz qual e o nome certo

    def test_valor_divergente_e_acusado(self, cfg):
        problemas = cfg.divergencias("`HARNESS_BRANCH_COOLDOWN_TURNS=5`")
        assert any("diverge" in p and "8" in p for p in problemas)

    def test_knob_inexistente_e_acusado(self, cfg):
        problemas = cfg.divergencias("`HARNESS_BRANCH_INVENTADA=7`")
        assert any("nao existe no registry" in p for p in problemas)

    def test_drift_em_prosa_tambem_e_pego(self, cfg):
        """A forma exata do drift que sobreviveu no design doc."""
        problemas = cfg.divergencias(
            "- cooldown de 5 turnos entre ofertas (`HARNESS_BRANCH_COOLDOWN_TURNS`)"
        )
        assert any("escrito de memoria" in p for p in problemas)

    def test_prosa_com_o_numero_certo_passa(self, cfg):
        assert cfg.divergencias(
            "- cooldown de 8 chamadas entre ofertas (`HARNESS_BRANCH_COOLDOWN_TURNS`)"
        ) == []

    def test_linha_de_advertencia_nao_e_punida(self, cfg):
        """Sem esta excecao, calar o check exigiria apagar o aviso."""
        assert cfg.divergencias(
            "- os nomes curtos nao existiam: setar `FLOOR=0.7` nao fazia nada"
        ) == []

    def test_doc_que_ensina_a_desligar_nao_e_drift(self, cfg):
        assert cfg.divergencias("`HARNESS_BRANCH=0` desliga o sensor") == []


class TestLeitura:
    def test_default_quando_nao_ha_env(self, cfg, monkeypatch):
        monkeypatch.delenv("HARNESS_BRANCH_FLOOR", raising=False)
        assert cfg.get_float("HARNESS_BRANCH_FLOOR") == 0.55

    def test_env_sobrescreve(self, cfg, monkeypatch):
        monkeypatch.setenv("HARNESS_BRANCH_MAX_OFFERS", "7")
        assert cfg.get_int("HARNESS_BRANCH_MAX_OFFERS") == 7

    def test_lixo_no_env_cai_no_default(self, cfg, monkeypatch):
        """Config errada nao pode calar o sensor — silencio e a pior falha."""
        monkeypatch.setenv("HARNESS_BRANCH_MAX_OFFERS", "dois")
        assert cfg.get_int("HARNESS_BRANCH_MAX_OFFERS") == 2

    @pytest.mark.parametrize("valor,esperado",
                             [("0", False), ("false", False), ("OFF", False),
                              ("1", True), ("sim", True)])
    def test_bool(self, cfg, monkeypatch, valor, esperado):
        monkeypatch.setenv("HARNESS_BRANCH", valor)
        assert cfg.get_bool("HARNESS_BRANCH") is esperado

    def test_effective_diz_de_onde_veio_o_valor(self, cfg, monkeypatch):
        monkeypatch.setenv("HARNESS_BRANCH_FLOOR", "0.9")
        monkeypatch.delenv("HARNESS_BRANCH_MAX_OPEN", raising=False)
        eff = cfg.effective()
        assert eff["HARNESS_BRANCH_FLOOR"]["source"] == "env"
        assert eff["HARNESS_BRANCH_MAX_OPEN"]["source"] == "default"
