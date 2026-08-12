"""Testes do tools/arsenal.py.

O que estes testes protegem, em ordem de importancia:

1. **A regra central como codigo.** "O registry guarda apenas julgamento" so vale
   se `check` REPROVAR ao encontrar campo mensuravel. Sem esse teste a regra e
   um comentario, e comentario nao impede ninguem de escrever `versao = "1.2"`.

2. **A semente do agregado.** Se ela viesse das skills encontradas, um plugin sem
   skill (browser-use, playwright, prisma...) seria invisivel ao reconcile e
   entraria no sistema sem nunca passar por decisao. O teste fixa a semente em
   enabledPlugins.

3. **A separacao uso-de-skill x uso-de-plugin.** hookify tem 43.816 invocacoes de
   plugin e zero de skill. Um relatorio que somasse as duas coisas mandaria
   desinstalar o componente mais usado do sistema.

4. **O prefixo vindo do manifest.** data-engineering se expoe como
   "astronomer-data"; montar o id com a chave de instalacao produzia um id que
   nunca casa com skillUsage, e o uso lia 0 para sempre sem erro nenhum.

Nenhum teste toca o HARNESS_DIR real nem o vault real — arvores sinteticas em
tmp_path, no contrato do conftest.py.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])


@pytest.fixture(scope="module")
def ars():
    """Importa tools/arsenal.py pelo caminho: tools/ nao e pacote instalavel."""
    sys.path.insert(0, str(ROOT / "tools"))
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("arsenal", ROOT / "tools" / "arsenal.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["arsenal"] = mod
    spec.loader.exec_module(mod)
    return mod


def _registry(**over):
    base = {
        "schema_version": 1,
        "updated": "2026-08-12",
        "tools": [{
            "id": "superpowers", "kind": "plugin", "decisao": "adotado",
            "decidido_em": "2026-08-12", "por_que": "pipeline L2 depende de brainstorming",
            "rollback": "claude plugin disable superpowers --scope user",
        }],
    }
    base["tools"][0].update(over)
    return base


# ---------------------------------------------------------------- contrato

class TestContratoDoRegistry:
    def test_registry_minimo_valido_passa(self, ars):
        assert ars.validate_registry(_registry(), {}) == []

    @pytest.mark.parametrize(
        ("campo", "valor"),
        [("versao", "1.2.0"), ("instalado", True), ("custo_tokens", 900),
         ("usage_count", 40), ("n_skills", 14), ("enabled", True)],
    )
    def test_campo_mensuravel_reprova(self, ars, campo, valor):
        """A regra central. Fato guardado no registry vira a segunda verdade que deriva."""
        erros = ars.validate_registry(_registry(**{campo: valor}), {})
        assert any(campo in e and "julgamento" in e for e in erros), erros

    def test_erro_de_campo_mensuravel_diz_onde_ler(self, ars):
        """Recusa sem alternativa vira atrito. A mensagem tem que ser acionavel."""
        erros = ars.validate_registry(_registry(usage_count=3), {})
        assert any("skillUsage" in e for e in erros), erros

    def test_prova_sem_prazo_reprova(self, ars):
        erros = ars.validate_registry(_registry(decisao="prova"), {})
        assert any("prova_ate" in e for e in erros)

    def test_prova_com_prazo_passa(self, ars):
        assert ars.validate_registry(_registry(decisao="prova", prova_ate="2026-09-12"), {}) == []

    def test_dispensado_nao_e_decisao(self, ars):
        """Recusado sai do indice para dispensados.toml — nao vira status ativo."""
        erros = ars.validate_registry(_registry(decisao="dispensado"), {})
        assert any("dispensados.toml" in e for e in erros)

    def test_id_nos_dois_arquivos_reprova(self, ars):
        dispensados = {"superpowers": {"motivo": "x", "decidido_em": "2026-08-12"}}
        erros = ars.validate_registry(_registry(), dispensados)
        assert any("decida uma coisa so" in e.replace("só", "so") for e in erros)

    def test_id_duplicado_reprova(self, ars):
        reg = _registry()
        reg["tools"].append(dict(reg["tools"][0]))
        assert any("duplicado" in e for e in ars.validate_registry(reg, {}))

    def test_campo_obrigatorio_ausente_reprova(self, ars):
        reg = _registry()
        del reg["tools"][0]["rollback"]
        assert any("rollback" in e for e in ars.validate_registry(reg, {}))

    def test_data_nao_iso_reprova(self, ars):
        assert any("ISO" in e for e in ars.validate_registry(_registry(decidido_em="12/08/2026"), {}))

    def test_fonte_sem_data_de_captura_reprova(self, ars):
        """Fonte sem data nao e verificavel: nao da para saber o que foi lido, nem quando."""
        erros = ars.validate_registry(_registry(fonte="github:obra/superpowers"), {})
        assert any("capturado_em" in e for e in erros)

    def test_dispensado_sem_motivo_reprova(self, ars):
        erros = ars.validate_registry(_registry(), {"x": {"decidido_em": "2026-08-12"}})
        assert any("motivo" in e for e in erros)


# ------------------------------------------------------------------ disco

@pytest.fixture
def fake_claude(tmp_path, ars, monkeypatch):
    """Arvore sintetica de plugins. Devolve um helper para montar cenarios."""
    def montar(plugins, skill_usage=None, plugin_usage=None):
        instalados, habilitados = {}, {}
        for nome, cfg in plugins.items():
            raiz = tmp_path / "cache" / nome
            (raiz / ".claude-plugin").mkdir(parents=True, exist_ok=True)
            (raiz / ".claude-plugin" / "plugin.json").write_text(
                json.dumps({"name": cfg.get("manifest_name", nome)}), encoding="utf-8")
            for sk, desc in (cfg.get("skills") or {}).items():
                d = raiz / "skills" / sk
                d.mkdir(parents=True, exist_ok=True)
                (d / "SKILL.md").write_text(
                    f"---\nname: {sk}\ndescription: {desc}\n---\ncorpo\n", encoding="utf-8")
            for cm, desc in (cfg.get("commands") or {}).items():
                d = raiz / "commands"
                d.mkdir(parents=True, exist_ok=True)
                (d / f"{cm}.md").write_text(f"---\ndescription: {desc}\n---\ncorpo\n", encoding="utf-8")
            chave = f"{nome}@mkt"
            instalados[chave] = [{"installPath": str(raiz)}]
            habilitados[chave] = cfg.get("enabled", True)

        p_inst = tmp_path / "installed_plugins.json"
        p_set = tmp_path / "settings.json"
        p_cla = tmp_path / "claude.json"
        p_inst.write_text(json.dumps({"plugins": instalados}), encoding="utf-8")
        p_set.write_text(json.dumps({"enabledPlugins": habilitados}), encoding="utf-8")
        p_cla.write_text(json.dumps({
            "skillUsage": skill_usage or {}, "pluginUsage": plugin_usage or {},
        }), encoding="utf-8")

        bsi = sys.modules["build_skills_index"]
        monkeypatch.setattr(bsi, "INSTALLED_JSON", str(p_inst))
        monkeypatch.setattr(bsi, "SETTINGS_JSON", str(p_set))
        monkeypatch.setattr(bsi, "CLAUDE_JSON", str(p_cla))
        monkeypatch.setattr(bsi, "PERSONAL_SKILLS_DIR", str(tmp_path / "sem-skills-pessoais"))
        monkeypatch.setattr(bsi, "ALIASES_JSON", str(tmp_path / "sem-aliases.json"))
        return tmp_path
    return montar


class TestAgregado:
    def test_plugin_sem_skill_aparece_no_agregado(self, ars, fake_claude):
        """browser-use, playwright, prisma... nao tem skill. Se sumissem do agregado,
        entrariam no sistema sem nunca passar por decisao."""
        fake_claude({"so-mcp": {"skills": {}}, "com-skill": {"skills": {"a": "faz A"}}})
        ag = ars.agregado_por_ferramenta(ars.roster() + ars.comandos())
        assert "so-mcp" in ag
        assert ag["so-mcp"]["n_skills"] == 0
        assert ag["so-mcp"]["chars"] == 0
        assert ag["so-mcp"]["enabled"] is True

    def test_comandos_entram_no_custo(self, ars, fake_claude):
        """commands/*.md tambem viram linha do roster. Orcamento que os ignora
        subestima — e orcamento que subestima autoriza gasto que nao cabe."""
        fake_claude({"p": {"skills": {"s": "descricao da skill"}, "commands": {"c": "descricao do cmd"}}})
        ag = ars.agregado_por_ferramenta(ars.roster() + ars.comandos())
        assert ag["p"]["n_skills"] == 1
        assert ag["p"]["n_comandos"] == 1
        assert ag["p"]["chars"] > len("descricao da skill")

    def test_desabilitado_nao_custa(self, ars, fake_claude):
        fake_claude({"off": {"skills": {"a": "x" * 50}, "enabled": False}})
        ag = ars.agregado_por_ferramenta(ars.roster() + ars.comandos())
        assert ag["off"]["enabled"] is False
        assert ag["off"]["chars"] == 0

    def test_prefixo_vem_do_manifest_nao_da_chave(self, ars, fake_claude):
        """data-engineering se expoe como astronomer-data. Com a chave de
        instalacao o id nunca casaria com skillUsage e o uso leria 0 para sempre."""
        fake_claude(
            {"data-engineering": {"manifest_name": "astronomer-data", "skills": {"airflow": "dags"}}},
            skill_usage={"astronomer-data:airflow": {"usageCount": 7}},
        )
        skills = ars.roster()
        assert [s["id"] for s in skills] == ["astronomer-data:airflow"]
        assert skills[0]["usage_count"] == 7

    def test_uso_de_plugin_soma_chaves_do_mesmo_plugin(self, ars, fake_claude):
        """O mesmo plugin aparece sob '@inline' (instalacao velha) e '@mkt'."""
        fake_claude({"hookify": {"skills": {"h": "d"}}},
                    plugin_usage={"hookify@inline": {"usageCount": 40}, "hookify@mkt": {"usageCount": 2}})
        assert ars.uso_de_plugin()["hookify"] == 42

    def test_uso_de_skill_e_de_plugin_nao_se_misturam(self, ars, fake_claude):
        """hookify: 43.816 usos de plugin, 0 de skill. Somar os dois manda
        desinstalar o componente mais exercitado do sistema."""
        fake_claude({"hookify": {"skills": {"h": "descricao"}}},
                    plugin_usage={"hookify@mkt": {"usageCount": 43816}})
        ag = ars.agregado_por_ferramenta(ars.roster() + ars.comandos())
        assert ag["hookify"]["usos"] == 0
        assert ag["hookify"]["usos_plugin"] == 43816


class TestReconcile:
    def test_habilitado_sem_decisao_e_fantasma(self, ars, fake_claude, tmp_path):
        fake_claude({"novo": {"skills": {"a": "d"}}})
        res = ars.command_reconcile(tmp_path / "vault-sem-registry")
        tipos = {a["tipo"] for a in res["achados"]}
        assert "fantasma" in tipos
        assert res["ready"] is False

    def test_adotado_e_habilitado_nao_gera_achado(self, ars, fake_claude, tmp_path):
        fake_claude({"superpowers": {"skills": {"a": "d"}}})
        _escreve_registry(tmp_path, _registry())
        res = ars.command_reconcile(tmp_path)
        assert res["achados"] == []
        assert res["ready"] is True

    def test_adotado_mas_desabilitado_e_orfa(self, ars, fake_claude, tmp_path):
        fake_claude({"superpowers": {"skills": {"a": "d"}, "enabled": False}})
        _escreve_registry(tmp_path, _registry())
        res = ars.command_reconcile(tmp_path)
        assert [a["tipo"] for a in res["achados"]] == ["orfa"]

    def test_dispensado_mas_habilitado_e_recaida(self, ars, fake_claude, tmp_path):
        """A reversao silenciosa de uma decisao. Mais grave que nunca ter decidido."""
        fake_claude({"huggingface-skills": {"skills": {"a": "d"}}})
        _escreve_registry(tmp_path, {"schema_version": 1, "updated": "2026-08-12", "tools": []},
                          dispensados=[{"id": "huggingface-skills", "motivo": "3.4k tok, zero uso",
                                        "decidido_em": "2026-08-12"}])
        res = ars.command_reconcile(tmp_path)
        assert [a["tipo"] for a in res["achados"]] == ["recaida"]
        assert res["ready"] is False

    def test_prova_vencida_sem_uso_avisa_mas_nao_reprova(self, ars, fake_claude, tmp_path):
        fake_claude({"superpowers": {"skills": {"a": "d"}}})
        _escreve_registry(tmp_path, _registry(decisao="prova", prova_ate="2020-01-01"))
        res = ars.command_reconcile(tmp_path)
        assert [a["tipo"] for a in res["achados"]] == ["prova_falhou"]
        assert res["ready"] is True

    def test_dispensado_nao_e_acusado_de_fantasma(self, ars, fake_claude, tmp_path):
        """Dispensado e desabilitado e o estado final correto: silencio."""
        fake_claude({"x": {"skills": {"a": "d"}, "enabled": False}})
        _escreve_registry(tmp_path, {"schema_version": 1, "updated": "2026-08-12", "tools": []},
                          dispensados=[{"id": "x", "motivo": "m", "decidido_em": "2026-08-12"}])
        assert ars.command_reconcile(tmp_path)["achados"] == []


class TestBudget:
    def test_estouro_de_teto_reprova(self, ars, fake_claude):
        fake_claude({"gordo": {"skills": {f"s{i}": "d" * 400 for i in range(10)}}})
        res = ars.command_budget(teto=100)
        assert res["ready"] is False
        assert "estourado" in res["errors"][0]

    def test_dentro_do_teto_passa(self, ars, fake_claude):
        fake_claude({"magro": {"skills": {"s": "curta"}}})
        assert ars.command_budget(teto=10_000)["ready"] is True

    def test_chars_e_exato_e_tokens_declarado_como_aproximacao(self, ars, fake_claude):
        fake_claude({"p": {"skills": {"s": "d" * 100}}})
        res = ars.command_budget(teto=10_000)
        assert res["resumo"]["chars_exatos"] > 100
        assert "aproxima" in res["resumo"]["nota"]

    def test_load_bearing_e_separado_de_peso_morto(self, ars, fake_claude):
        """Custa roster e nao tem uso de skill, mas o plugin e muito usado:
        o conselho certo e encurtar a descricao, nao desinstalar."""
        fake_claude({"hookify": {"skills": {"h": "d" * 200}}},
                    plugin_usage={"hookify@mkt": {"usageCount": 43816}})
        res = ars.command_budget(teto=10_000)
        assert res["resumo"]["dessas_load_bearing"] == 1
        assert any("nao desinstale" in w.replace("ã", "a").replace("ç", "c") for w in res["warnings"])


class TestCollisions:
    """Colisao ENTRE plugins e DENTRO de um plugin nao sao o mesmo problema.

    Entre plugins: duas ferramentas disputam o mesmo trabalho, o agente escolhe a
    errada, e voce decide qual fica. Dentro de um: o autor escreveu descricoes
    redundantes, e voce nao conserta sem forkar.

    Medido em 2026-08-12 sobre 158 skills: maximo interno 0.894, maximo cruzado
    0.833. Um limiar unico calibrado no interno esconderia TODAS as colisoes
    acionaveis — inclusive discord:access x telegram:access.
    """

    def _falso_indice(self, ars, monkeypatch, tmp_path, registros, vetores):
        import struct
        dim = len(vetores[0])
        (tmp_path / "skills-index.json").write_text(
            json.dumps({"skills": registros}), encoding="utf-8")
        (tmp_path / "meta.json").write_text(
            json.dumps({"dim": dim, "fingerprint": {"x": "y"}}), encoding="utf-8")
        flat = [v for vec in vetores for v in vec]
        (tmp_path / "embeddings.f16.bin").write_bytes(struct.pack(f"<{len(flat)}e", *flat))
        monkeypatch.setattr(ars, "IDX_DIR", tmp_path)

    def test_cruzado_alto_e_erro_interno_alto_nao_e(self, ars, monkeypatch, tmp_path):
        regs = [
            {"id": "discord:access", "plugin": "discord@mkt", "enabled": True, "vec_row": 0},
            {"id": "telegram:access", "plugin": "telegram@mkt", "enabled": True, "vec_row": 1},
            {"id": "deepeval:a", "plugin": "deepeval@mkt", "enabled": True, "vec_row": 2},
            {"id": "deepeval:b", "plugin": "deepeval@mkt", "enabled": True, "vec_row": 3},
        ]
        # 0/1 quase iguais (cruzado); 2/3 quase iguais (interno).
        self._falso_indice(ars, monkeypatch, tmp_path, regs, [
            [1.0, 0.0], [0.99, 0.141], [0.0, 1.0], [0.141, 0.99],
        ])
        res = ars.command_collisions(minimo=0.5)
        por_par = {(p["a"], p["b"]): p for p in res["pares"]}
        cruzado = por_par[("discord:access", "telegram:access")]
        interno = por_par[("deepeval:a", "deepeval:b")]
        assert cruzado["cruzado"] is True and cruzado["nivel"] == "error"
        assert interno["cruzado"] is False and interno["nivel"] != "error"
        assert res["ready"] is False

    def test_indice_stale_avisa_alto_e_nao_levanta(self, ars, monkeypatch, tmp_path, fake_claude):
        """Indice velho responde com confianca sobre um disco que mudou."""
        fake_claude({"p": {"skills": {"s": "d"}}})
        regs = [{"id": "a:x", "plugin": "a@mkt", "enabled": True, "vec_row": 0},
                {"id": "b:y", "plugin": "b@mkt", "enabled": True, "vec_row": 1}]
        self._falso_indice(ars, monkeypatch, tmp_path, regs, [[1.0, 0.0], [0.0, 1.0]])
        res = ars.command_collisions(minimo=0.5)
        assert any("STALE" in w for w in res["warnings"]), res["warnings"]

    def test_indice_ausente_degrada_sem_excecao(self, ars, monkeypatch, tmp_path):
        """Contrato herdado do skill_router: busca nunca derruba quem chama."""
        monkeypatch.setattr(ars, "IDX_DIR", tmp_path / "nao-existe")
        res = ars.command_collisions(minimo=0.5)
        assert res["ready"] is True
        assert res["pares"] == []

    def test_ponto_cego_de_mcp_fica_declarado(self, ars, monkeypatch, tmp_path):
        """context7 carregado duas vezes e invisivel aqui. Limite conhecido tem
        que aparecer na saida, senao a ausencia de achado le como ausencia de
        problema."""
        monkeypatch.setattr(ars, "IDX_DIR", tmp_path / "nao-existe")
        assert "MCP" in ars.command_collisions(minimo=0.5)["resumo"]["ponto_cego"]


def _escreve_registry(root: Path, registry: dict, dispensados: list | None = None) -> None:
    d = Path(root) / "arsenal"
    d.mkdir(parents=True, exist_ok=True)
    linhas = [f"schema_version = {registry['schema_version']}",
              f'updated = "{registry["updated"]}"', ""]
    for t in registry["tools"]:
        linhas.append("[[tools]]")
        linhas += [f'{k} = "{v}"' for k, v in t.items()]
        linhas.append("")
    (d / "tools.toml").write_text("\n".join(linhas), encoding="utf-8")
    if dispensados is not None:
        blocos = []
        for t in dispensados:
            blocos.append("[[dispensados]]")
            blocos += [f'{k} = "{v}"' for k, v in t.items()]
            blocos.append("")
        (d / "dispensados.toml").write_text("\n".join(blocos), encoding="utf-8")
