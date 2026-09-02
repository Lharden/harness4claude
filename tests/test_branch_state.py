"""Testes para scripts/branch_state.py — registro de ramos do Branch Keeper.

O que estes testes travam:

- O estado do ramo e por projeto, nunca global. Dois projetos que ramificam no
  mesmo dia nao podem ver os ramos um do outro — e o mesmo bug que a auditoria
  de 2026-07-28 corrigiu para `state.json`, e ele nao volta por uma porta nova.
- As transicoes de status sao um automato fechado. `pending -> open -> closed`
  e `open -> recalled` sao os unicos caminhos; qualquer outro e recusado. Sem
  isso um ramo fechado poderia reabrir sozinho e o parking mentiria.
- "Agora nao" NUNCA descarta. A decisao do usuario foi explicita: recusar
  parkeia como `pending`. Um teste guarda isso porque e a diferenca entre a
  ferramenta resolver o problema e reproduzi-lo.
- O parking injetado no contexto tem custo limitado: no maximo 5 itens, tema
  truncado. Contexto e o recurso que a feature existe para poupar; gastar
  contexto para economizar contexto seria autodestrutivo.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
STATE_PATH = ROOT / "scripts" / "branch_state.py"


@pytest.fixture(scope="module")
def bs():
    """Carrega o modulo pelo path (scripts/ nao e um pacote instalavel)."""
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("branch_state", STATE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["branch_state"] = mod
    spec.loader.exec_module(mod)
    return mod


class TestRegistro:
    def test_arquivo_nasce_com_schema_e_lista_vazia(self, bs, tmp_path):
        data = bs.load(cwd=str(tmp_path))
        assert data["schema_version"] == 1
        assert data["branches"] == []

    def test_add_gera_slug_estavel_a_partir_do_nome(self, bs, tmp_path):
        b = bs.add(cwd=str(tmp_path), name="Sensor de Deriva", topic="deriva vs ramo")
        assert b["slug"] == "sensor-de-deriva"
        assert b["status"] == "pending"
        # uuid pre-atribuido no nascimento, nao na abertura: o pai precisa saber
        # o endereco do filho mesmo que o filho nunca chegue a ser aberto.
        assert b["session_id"]

    def test_slug_colidido_recebe_sufixo(self, bs, tmp_path):
        bs.add(cwd=str(tmp_path), name="Mesmo Nome", topic="a")
        b2 = bs.add(cwd=str(tmp_path), name="Mesmo Nome", topic="b")
        assert b2["slug"] == "mesmo-nome-2"

    def test_estado_e_por_projeto(self, bs, tmp_path):
        p1, p2 = tmp_path / "proj1", tmp_path / "proj2"
        p1.mkdir()
        p2.mkdir()
        bs.add(cwd=str(p1), name="So do Um", topic="x")
        assert [b["name"] for b in bs.load(cwd=str(p2))["branches"]] == []
        assert [b["name"] for b in bs.load(cwd=str(p1))["branches"]] == ["So do Um"]

    def test_session_id_e_uuid_valido(self, bs, tmp_path):
        import uuid

        b = bs.add(cwd=str(tmp_path), name="Ramo", topic="x")
        assert str(uuid.UUID(b["session_id"])) == b["session_id"]


class TestTransicoes:
    def test_pending_abre_e_fecha(self, bs, tmp_path):
        b = bs.add(cwd=str(tmp_path), name="Ramo", topic="x")
        assert bs.set_status(cwd=str(tmp_path), slug=b["slug"], status="open")["opened_at"]
        fim = bs.set_status(
            cwd=str(tmp_path), slug=b["slug"], status="closed", conclusion="deu certo"
        )
        assert fim["closed_at"] and fim["conclusion"] == "deu certo"

    def test_open_pode_ser_recalled(self, bs, tmp_path):
        b = bs.add(cwd=str(tmp_path), name="Ramo", topic="x")
        bs.set_status(cwd=str(tmp_path), slug=b["slug"], status="open")
        voltou = bs.set_status(cwd=str(tmp_path), slug=b["slug"], status="recalled")
        assert voltou["status"] == "recalled"

    def test_closed_nao_reabre(self, bs, tmp_path):
        b = bs.add(cwd=str(tmp_path), name="Ramo", topic="x")
        bs.set_status(cwd=str(tmp_path), slug=b["slug"], status="open")
        bs.set_status(cwd=str(tmp_path), slug=b["slug"], status="closed")
        with pytest.raises(ValueError):
            bs.set_status(cwd=str(tmp_path), slug=b["slug"], status="open")

    def test_status_desconhecido_recusado(self, bs, tmp_path):
        b = bs.add(cwd=str(tmp_path), name="Ramo", topic="x")
        with pytest.raises(ValueError):
            bs.set_status(cwd=str(tmp_path), slug=b["slug"], status="talvez")

    def test_slug_inexistente_recusado(self, bs, tmp_path):
        with pytest.raises(KeyError):
            bs.set_status(cwd=str(tmp_path), slug="nao-existe", status="open")


class TestOrcamento:
    def test_recusar_parkeia_em_vez_de_descartar(self, bs, tmp_path):
        """Decisao do usuario: "agora nao" nunca perde a ideia."""
        b = bs.add(cwd=str(tmp_path), name="Ramo", topic="x")
        assert b["status"] == "pending"
        assert bs.pending(cwd=str(tmp_path))[0]["slug"] == b["slug"]

    def test_descarte_e_explicito(self, bs, tmp_path):
        b = bs.add(cwd=str(tmp_path), name="Ramo", topic="x")
        bs.discard(cwd=str(tmp_path), slug=b["slug"])
        assert bs.pending(cwd=str(tmp_path)) == []

    def test_teto_de_ramos_abertos(self, bs, tmp_path, monkeypatch):
        monkeypatch.setenv("HARNESS_BRANCH_MAX_OPEN", "2")
        for i in range(3):
            b = bs.add(cwd=str(tmp_path), name=f"Ramo {i}", topic="x")
            if i < 2:
                bs.set_status(cwd=str(tmp_path), slug=b["slug"], status="open")
        assert bs.can_open(cwd=str(tmp_path)) is False

    def test_dedupe_por_topico_ja_registrado(self, bs, tmp_path):
        bs.add(cwd=str(tmp_path), name="Sensor", topic="detectar deriva por embedding")
        assert bs.already_seen(cwd=str(tmp_path), topic="detectar deriva por embedding") is True
        assert bs.already_seen(cwd=str(tmp_path), topic="renomear o launcher") is False


class TestParkingBlock:
    def test_bloco_vazio_quando_nao_ha_ramo(self, bs, tmp_path):
        assert bs.parked_block(cwd=str(tmp_path)) == ""

    def test_bloco_lista_abertos_e_pendentes(self, bs, tmp_path):
        a = bs.add(cwd=str(tmp_path), name="Aberto", topic="tema aberto")
        bs.set_status(cwd=str(tmp_path), slug=a["slug"], status="open")
        bs.add(cwd=str(tmp_path), name="Pendente", topic="tema pendente")
        bloco = bs.parked_block(cwd=str(tmp_path))
        assert "<harness-parked>" in bloco and "</harness-parked>" in bloco
        assert "tema aberto" in bloco and "tema pendente" in bloco

    def test_bloco_omite_fechados_e_recalled(self, bs, tmp_path):
        for nome, st in (("Fechado", "closed"), ("Voltou", "recalled")):
            b = bs.add(cwd=str(tmp_path), name=nome, topic=f"tema {nome}")
            bs.set_status(cwd=str(tmp_path), slug=b["slug"], status="open")
            bs.set_status(cwd=str(tmp_path), slug=b["slug"], status=st)
        assert bs.parked_block(cwd=str(tmp_path)) == ""

    def test_bloco_limita_a_cinco_itens(self, bs, tmp_path):
        for i in range(9):
            bs.add(cwd=str(tmp_path), name=f"Ramo {i}", topic=f"tema numero {i}")
        linhas = [ln for ln in bs.parked_block(cwd=str(tmp_path)).splitlines() if ln.startswith("- ")]
        assert len(linhas) == 5

    def test_tema_longo_e_truncado(self, bs, tmp_path):
        bs.add(cwd=str(tmp_path), name="Longo", topic="t" * 400)
        linha = next(
            ln for ln in bs.parked_block(cwd=str(tmp_path)).splitlines() if ln.startswith("- ")
        )
        assert len(linha) < 220
        assert "..." in linha


class TestPersistencia:
    def test_json_sobrevive_a_releitura(self, bs, tmp_path):
        b = bs.add(cwd=str(tmp_path), name="Ramo", topic="acento: ção ãé")
        path = bs.branches_path(cwd=str(tmp_path))
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        assert raw["branches"][0]["topic"] == "acento: ção ãé"
        assert bs.get(cwd=str(tmp_path), slug=b["slug"])["topic"] == "acento: ção ãé"

    def test_arquivo_corrompido_nao_derruba(self, bs, tmp_path):
        path = Path(bs.branches_path(cwd=str(tmp_path)))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{lixo", encoding="utf-8")
        assert bs.load(cwd=str(tmp_path))["branches"] == []


class TestComandosDaSkillRodamMesmo:
    """Os comandos escritos na SKILL.md tem que funcionar como escritos.

    A skill mandava `python "$CLAUDE_PLUGIN_ROOT/scripts/branch_state.py" add`.
    Essa variavel so existe no ambiente dos HOOKS: quando o modelo roda o
    comando pela ferramenta de shell ela esta vazia, e o caminho vira
    `/scripts/branch_state.py`. No PowerShell, que e o shell primario desta
    maquina, `$CLAUDE_PLUGIN_ROOT/...` nem e sintaxe valida de caminho.

    Isso e uma das razoes pelas quais `branches.json` nunca nasceu em nenhum
    dos 35 buckets: mesmo que a skill fosse invocada, o comando dela falharia.
    Estes testes rodam os comandos como um subprocesso de verdade, com o path
    resolvido do jeito que a skill agora manda resolver.
    """

    def _plugin_root(self):
        marcador = Path.home() / ".claude" / "harness" / "plugin-root"
        if marcador.exists():
            alvo = Path(marcador.read_text(encoding="utf-8").strip())
            if (alvo / "scripts" / "branch_state.py").exists():
                return alvo
        return Path(os.environ["HARNESS_PLUGIN_ROOT"])

    def _rodar(self, args, cwd_projeto, harness_dir):
        env = {**os.environ, "HARNESS_DIR": str(harness_dir), "PYTHONUTF8": "1"}
        script = self._plugin_root() / "scripts" / "branch_state.py"
        return subprocess.run(
            [sys.executable, str(script), *args, "--cwd", str(cwd_projeto)],
            capture_output=True, text=True, encoding="utf-8", env=env, timeout=60,
        )

    def test_o_marcador_plugin_root_resolve(self):
        """A skill le o caminho daqui. Se o arquivo mentir, tudo cai junto."""
        raiz = self._plugin_root()
        assert (raiz / "scripts" / "branch_state.py").exists()
        assert (raiz / "scripts" / "branch_sensor.py").exists()

    def test_add_pela_linha_de_comando_cria_o_registro(self, tmp_path):
        projeto = tmp_path / "proj"
        projeto.mkdir()
        proc = self._rodar(
            ["add", "--name", "Indice de Sessoes",
             "--topic", "indexar transcripts para busca cross-sessao",
             "--parent-session", "sessao-mae-uuid", "--origin-turn", "42"],
            projeto, tmp_path / "h",
        )
        assert proc.returncode == 0, proc.stderr
        criado = json.loads(proc.stdout)
        assert criado["slug"] == "indice-de-sessoes"
        assert criado["origin_turn"] == 42

        achados = list((tmp_path / "h").rglob("branches.json"))
        assert achados, "branches.json nao nasceu — o defeito historico"
        registro = json.loads(achados[0].read_text(encoding="utf-8"))
        assert registro["parent_session"] == "sessao-mae-uuid"

    def test_sem_parent_session_o_ramo_fica_orfao(self, tmp_path):
        """Documenta o custo de omitir a flag, para o teste acima ter contraste."""
        projeto = tmp_path / "proj"
        projeto.mkdir()
        proc = self._rodar(["add", "--name", "Ramo Solto", "--topic", "tema qualquer"],
                           projeto, tmp_path / "h")
        assert proc.returncode == 0, proc.stderr
        registro = json.loads(
            list((tmp_path / "h").rglob("branches.json"))[0].read_text(encoding="utf-8"))
        assert registro["parent_session"] is None
