"""Testes do guarda de orfao — a pergunta que este repositorio nao fazia.

A suite ja prova que o codigo FUNCIONA. Ela nunca provou que ele e USADO, e as
duas coisas sao indistinguiveis por todos os indicadores existentes: suite verde,
revisao feita, cobertura alta. Medido em 2026-09-16: 419 funcoes publicas de topo,
67 sem caminho ate qualquer raiz que o host execute, 38 esquecidas contra 29
ferramentas de mao declaradas.

O bloco B nao e defensivo: sao TRES ERROS QUE ACONTECERAM, os tres num unico dia,
enquanto o instrumento era construido. O terceiro e o proprio defeito que este
guarda existe para cacar — `tests/test_harness_paths.py` estava servindo de raiz
de producao para o modulo `harness_paths`, por substring. O detector de
teste-virando-chamador estava fazendo isso.

`test_repositorio_real_passa_o_guarda` E o guarda. Os demais testam o scanner.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])


@pytest.fixture(scope="module")
def orf():
    spec = importlib.util.spec_from_file_location("orfaos", ROOT / "tools" / "orfaos.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["orfaos"] = mod
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------
# arvore sintetica: nenhum teste do bloco A/B/C le o repositorio real
# --------------------------------------------------------------------------
def arvore(tmp_path: Path, *, scripts=None, hooks=None, tests=None, declaracoes=None) -> Path:
    raiz = tmp_path / "repo"
    for sub, arquivos in (("scripts", scripts), ("hooks", hooks), ("tests", tests)):
        (raiz / sub).mkdir(parents=True, exist_ok=True)
        for nome, texto in (arquivos or {}).items():
            (raiz / sub / nome).write_text(texto, encoding="utf-8")
    if declaracoes is not None:
        (raiz / "tools").mkdir(parents=True, exist_ok=True)
        (raiz / "tools" / "orfaos.json").write_text(
            json.dumps({"versao": 1, "declaracoes": declaracoes}, ensure_ascii=False),
            encoding="utf-8",
        )
    return raiz


def declarar(modulo, nome, arquivo, categoria="ORFAO", motivo="motivo escrito a mao para o teste"):
    return {"modulo": modulo, "nome": nome, "arquivo": arquivo,
            "categoria": categoria, "motivo": motivo}


HOOK_QUE_CHAMA = '#!/bin/sh\npython "$PR/scripts/alvo.py" --flag\n'

#: `antes_orfa` E chamada por `usada`, que e alcancavel a partir do hook. Serve
#: para o caso em que a allowlist ficou para tras depois de alguem ligar a funcao.
ALVO_QUE_GANHOU_CHAMADOR = (
    "def usada():\n    return antes_orfa()\n\n\n"
    "def antes_orfa():\n    return 2\n\n\n"
    "usada()\n"
)


# ==========================================================================
# BLOCO A — comportamento
# ==========================================================================
class TestAlcance:
    def test_funcao_chamada_por_hook_sh_e_viva(self, orf, tmp_path):
        raiz = arvore(
            tmp_path,
            scripts={"alvo.py": "def usada():\n    return 1\n\n\nusada()\n"},
            hooks={"h.sh": HOOK_QUE_CHAMA},
        )
        r = orf.varrer(raiz)
        assert r["vereditos"][("alvo", "usada")] == "viva"

    def test_orfao_nao_declarado_reprova_com_arquivo_e_linha(self, orf, tmp_path):
        raiz = arvore(
            tmp_path,
            scripts={"alvo.py": "def usada():\n    return 1\n\n\ndef esquecida():\n    return 2\n\n\nusada()\n"},
            hooks={"h.sh": HOOK_QUE_CHAMA},
            declaracoes=[],
        )
        p = orf.conferir(raiz)
        assert p["ready"] is False
        alvos = {(o["nome"], o["linha"]) for o in p["orfaos_nao_declarados"]}
        assert ("esquecida", 5) in alvos
        msg = orf.mensagem_de_falha(p)
        assert "scripts/alvo.py:5" in msg and "esquecida" in msg

    def test_orfao_declarado_passa(self, orf, tmp_path):
        raiz = arvore(
            tmp_path,
            scripts={"alvo.py": "def usada():\n    return 1\n\n\ndef reservada():\n    return 2\n\n\nusada()\n"},
            hooks={"h.sh": HOOK_QUE_CHAMA},
            declaracoes=[declarar("alvo", "reservada", "scripts/alvo.py")],
        )
        p = orf.conferir(raiz)
        assert p["orfaos_nao_declarados"] == []
        assert p["ready"] is True

    def test_referencia_so_em_teste_nao_torna_viva(self, orf, tmp_path):
        raiz = arvore(
            tmp_path,
            scripts={"alvo.py": "def usada():\n    return 1\n\n\ndef so_teste():\n    return 2\n\n\nusada()\n"},
            hooks={"h.sh": HOOK_QUE_CHAMA},
            tests={"test_alvo.py": "import alvo\n\n\ndef test_x():\n    assert alvo.so_teste() == 2\n"},
            declaracoes=[],
        )
        p = orf.conferir(raiz)
        assert "so_teste" in {o["nome"] for o in p["orfaos_nao_declarados"]}

    def test_recursao_nao_torna_viva(self, orf, tmp_path):
        raiz = arvore(
            tmp_path,
            scripts={"alvo.py": (
                "def usada():\n    return 1\n\n\n"
                "def recursiva(n):\n    return 1 if n <= 0 else recursiva(n - 1)\n\n\n"
                "usada()\n"
            )},
            hooks={"h.sh": HOOK_QUE_CHAMA},
            declaracoes=[],
        )
        p = orf.conferir(raiz)
        assert "recursiva" in {o["nome"] for o in p["orfaos_nao_declarados"]}

    def test_alcance_e_transitivo_a_partir_da_raiz(self, orf, tmp_path):
        """Duas orfas que so se chamam entre si sao DUAS orfas, nao duas vivas."""
        raiz = arvore(
            tmp_path,
            scripts={"alvo.py": (
                "def usada():\n    return 1\n\n\n"
                "def a():\n    return b()\n\n\n"
                "def b():\n    return 2\n\n\n"
                "usada()\n"
            )},
            hooks={"h.sh": HOOK_QUE_CHAMA},
            declaracoes=[],
        )
        p = orf.conferir(raiz)
        nomes = {o["nome"] for o in p["orfaos_nao_declarados"]}
        assert {"a", "b"} <= nomes

    def test_funcao_alcancada_por_outra_viva_e_viva(self, orf, tmp_path):
        raiz = arvore(
            tmp_path,
            scripts={"alvo.py": "def usada():\n    return ajuda()\n\n\ndef ajuda():\n    return 2\n\n\nusada()\n"},
            hooks={"h.sh": HOOK_QUE_CHAMA},
            declaracoes=[],
        )
        p = orf.conferir(raiz)
        assert p["orfaos_nao_declarados"] == []

    def test_zero_raizes_reprova(self, orf, tmp_path):
        """Nenhuma raiz e resultado suspeito, nao normal: aprovar tudo como orfao
        transformaria um scanner cego num relatorio com cara de completo."""
        raiz = arvore(tmp_path, scripts={"alvo.py": "def f():\n    return 1\n"}, declaracoes=[])
        p = orf.conferir(raiz)
        assert p["ready"] is False
        assert p["raizes"] == 0
        assert "raiz" in orf.mensagem_de_falha(p).lower()

    def test_arquivo_que_nao_parseia_reprova_com_nome(self, orf, tmp_path):
        raiz = arvore(
            tmp_path,
            scripts={"alvo.py": "def usada():\n    return 1\n\n\nusada()\n",
                     "quebrado.py": "def (:\n"},
            hooks={"h.sh": HOOK_QUE_CHAMA},
            declaracoes=[],
        )
        p = orf.conferir(raiz)
        assert "scripts/quebrado.py" in p["arquivos_nao_analisados"]
        assert p["ready"] is False


# ==========================================================================
# BLOCO B — as armadilhas de substring, medidas em 2026-09-16
# ==========================================================================
class TestArmadilhasMedidas:
    def test_import_com_as_no_heredoc_conta(self, orf, tmp_path):
        """ERRO REAL: `find_repo_root` apareceu orfa porque o heredoc de
        hooks/harness-classify.sh:742 a importa como
        `from harness_paths import find_repo_root as _raiz`, e a regex nao
        tirava o ` as `."""
        raiz = arvore(
            tmp_path,
            scripts={"alvo.py": "def find_repo_root():\n    return 1\n\n\ndef outra():\n    return 2\n"},
            hooks={"h.sh": 'python - <<EOF\nfrom alvo import find_repo_root as _raiz, outra as _o\nEOF\n'},
            declaracoes=[],
        )
        r = orf.varrer(raiz)
        assert r["vereditos"][("alvo", "find_repo_root")] == "viva"
        assert r["vereditos"][("alvo", "outra")] == "viva"

    def test_modulo_carregado_por_spec_from_file_location_conta(self, orf, tmp_path):
        """ERRO REAL: `mark_stale` apareceu orfa. Ela chega por
        hooks/harness-lifecycle.py:103, num modulo carregado por
        `importlib.util.spec_from_file_location` — a variavel nao tem nome de
        modulo, entao `mod.mark_stale()` nao resolvia."""
        raiz = arvore(
            tmp_path,
            scripts={"alvo.py": "def mark_stale(d):\n    return d\n"},
            hooks={"carregador.py": (
                "import importlib.util\n\n\n"
                "def roda():\n"
                "    spec = importlib.util.spec_from_file_location('alvo', 'x')\n"
                "    mod = importlib.util.module_from_spec(spec)\n"
                "    mod.mark_stale('/tmp')\n\n\n"
                "roda()\n"
            ),
             "h.sh": '#!/bin/sh\npython "$PR/hooks/carregador.py"\n'},
            declaracoes=[],
        )
        r = orf.varrer(raiz)
        assert r["vereditos"][("alvo", "mark_stale")] == "viva"

    def test_teste_nao_vira_raiz_de_producao(self, orf, tmp_path):
        """ERRO REAL, e e o proprio defeito que este guarda caca:
        `tests/test_harness_paths.py` casava como raiz do modulo
        `harness_paths` por substring. Nove funcoes apareciam vivas porque o
        teste delas as certificava — dentro do detector disso."""
        raiz = arvore(
            tmp_path,
            scripts={"alvo.py": "def usada():\n    return 1\n\n\ndef f():\n    return 2\n\n\nusada()\n"},
            hooks={"h.sh": HOOK_QUE_CHAMA,
                   "probes.json": '{"cap": ["tests/test_alvo.py::test_x"]}'},
            declaracoes=[],
        )
        p = orf.conferir(raiz)
        assert "f" in {o["nome"] for o in p["orfaos_nao_declarados"]}, (
            "tests/test_alvo.py virou raiz de producao do modulo alvo"
        )

    def test_prefixo_nao_vira_raiz(self, orf, tmp_path):
        """`scripts/build_wiki_index.py` casava como raiz do modulo `wiki_index`."""
        raiz = arvore(
            tmp_path,
            scripts={"indice.py": "def f():\n    return 1\n",
                     "build_indice.py": "def usada():\n    return 1\n\n\nusada()\n"},
            hooks={"h.sh": '#!/bin/sh\npython "$PR/scripts/build_indice.py"\n'},
            declaracoes=[],
        )
        p = orf.conferir(raiz)
        assert "f" in {o["nome"] for o in p["orfaos_nao_declarados"]}

    def test_sufixo_nao_vira_raiz(self, orf, tmp_path):
        raiz = arvore(
            tmp_path,
            scripts={"m.py": "def f():\n    return 1\n",
                     "outrom.py": "def usada():\n    return 1\n\n\nusada()\n"},
            hooks={"h.sh": '#!/bin/sh\npython "$PR/scripts/outrom.py"\n'},
            declaracoes=[],
        )
        p = orf.conferir(raiz)
        assert "f" in {o["nome"] for o in p["orfaos_nao_declarados"]}


# ==========================================================================
# BLOCO C — contrato da allowlist, linha obsoleta e o comando
# ==========================================================================
BASE = {"scripts": {"alvo.py": "def usada():\n    return 1\n\n\ndef orfa():\n    return 2\n\n\nusada()\n"},
        "hooks": {"h.sh": HOOK_QUE_CHAMA}}


class TestContratoDaAllowlist:
    def test_motivo_vazio_reprova(self, orf, tmp_path):
        raiz = arvore(tmp_path, **BASE,
                      declaracoes=[declarar("alvo", "orfa", "scripts/alvo.py", motivo="   ")])
        p = orf.conferir(raiz)
        assert p["ready"] is False
        assert ("orfa", "motivo_vazio") in {(d["nome"], d["por_que"]) for d in p["declaracoes_invalidas"]}

    def test_motivo_que_so_repete_o_nome_reprova(self, orf, tmp_path):
        """Motivo que nao informa nao e motivo. Sem isto a allowlist vira
        `# noqa` em massa e o guarda deixa de discriminar no primeiro mes."""
        raiz = arvore(tmp_path, **BASE,
                      declaracoes=[declarar("alvo", "orfa", "scripts/alvo.py", motivo="orfa.")])
        p = orf.conferir(raiz)
        assert ("orfa", "motivo_nao_informa") in {(d["nome"], d["por_que"]) for d in p["declaracoes_invalidas"]}

    def test_categoria_fora_do_vocabulario_reprova(self, orf, tmp_path):
        raiz = arvore(tmp_path, **BASE,
                      declaracoes=[declarar("alvo", "orfa", "scripts/alvo.py", categoria="TALVEZ")])
        p = orf.conferir(raiz)
        assert ("orfa", "categoria_invalida") in {(d["nome"], d["por_que"]) for d in p["declaracoes_invalidas"]}
        assert "ORFAO" in orf.mensagem_de_falha(p)

    def test_dupla_inclusao_allowlist_e_codigo(self, orf, tmp_path):
        """O invariante e a dupla inclusao, nunca uma contagem. `assert len == 67`
        quebraria a suite de quem escreve funcao nova."""
        raiz = arvore(tmp_path, **BASE,
                      declaracoes=[declarar("alvo", "orfa", "scripts/alvo.py")])
        p = orf.conferir(raiz)
        assert p["ready"] is True
        assert p["orfaos_nao_declarados"] == [] and p["declaracoes_obsoletas"] == []


class TestLinhaObsoleta:
    def test_linha_obsoleta_por_ganhar_chamador_reprova(self, orf, tmp_path):
        """O caso que mais precisa sair da lista: o unico que representa
        progresso. Deixar o progresso virar sedimento e pior que deixar o erro."""
        raiz = arvore(
            tmp_path,
            scripts={"alvo.py": ALVO_QUE_GANHOU_CHAMADOR},
            hooks={"h.sh": HOOK_QUE_CHAMA},
            declaracoes=[declarar("alvo", "antes_orfa", "scripts/alvo.py")],
        )
        p = orf.conferir(raiz)
        assert p["ready"] is False
        assert ("antes_orfa", "ganhou_chamador") in {
            (d["nome"], d["por_que"]) for d in p["declaracoes_obsoletas"]
        }

    def test_linha_obsoleta_por_alvo_sumido_reprova(self, orf, tmp_path):
        raiz = arvore(tmp_path, **BASE,
                      declaracoes=[declarar("alvo", "orfa", "scripts/alvo.py"),
                                   declarar("alvo", "nunca_existiu", "scripts/alvo.py")])
        p = orf.conferir(raiz)
        assert ("nunca_existiu", "alvo_sumiu") in {
            (d["nome"], d["por_que"]) for d in p["declaracoes_obsoletas"]
        }

    def test_sync_remove_linha_obsoleta_e_deixa_verde(self, orf, tmp_path):
        """Falhar por linha obsoleta so nao pune quem conserta porque o comando
        da mensagem conserta INTEIRAMENTE este caso."""
        raiz = arvore(
            tmp_path,
            scripts={"alvo.py": ALVO_QUE_GANHOU_CHAMADOR},
            hooks={"h.sh": HOOK_QUE_CHAMA},
            declaracoes=[declarar("alvo", "antes_orfa", "scripts/alvo.py")],
        )
        assert orf.conferir(raiz)["ready"] is False
        orf.sincronizar(raiz)
        assert orf.conferir(raiz)["ready"] is True

    def test_sync_acrescenta_orfao_novo_mas_nao_aprova(self, orf, tmp_path):
        """Sincronizar nao e aprovar: falta o julgamento humano, que e o produto."""
        raiz = arvore(tmp_path, **BASE, declaracoes=[])
        orf.sincronizar(raiz)
        dados = json.loads((raiz / "tools" / "orfaos.json").read_text(encoding="utf-8"))
        nova = next(d for d in dados["declaracoes"] if d["nome"] == "orfa")
        assert nova["categoria"] == "ORFAO" and nova["motivo"] == ""
        assert orf.conferir(raiz)["ready"] is False

    def test_sync_nunca_apaga_motivo_escrito(self, orf, tmp_path):
        """A allowlist e o produto desta feature, e um `git mv` nao pode
        destrui-la: 11 linhas de julgamento sumiriam num rename."""
        raiz = arvore(tmp_path, **BASE,
                      declaracoes=[declarar("alvo", "sumida", "scripts/alvo.py",
                                            motivo="julgamento que custou caro para ser escrito")])
        orf.sincronizar(raiz)
        bruto = (raiz / "tools" / "orfaos.json").read_text(encoding="utf-8")
        assert "julgamento que custou caro" in bruto


class TestComandoDaMensagem:
    def test_mensagem_distingue_os_dois_casos(self, orf, tmp_path):
        """So o caso de linha obsoleta ganha promessa de verde. Prometer o
        contrario repetiria o defeito que a mensagem existe para corrigir."""
        raiz = arvore(tmp_path, **BASE, declaracoes=[])
        msg = orf.mensagem_de_falha(orf.conferir(raiz))
        assert "motivo" in msg.lower()

    def test_comando_que_a_mensagem_imprime_de_fato_roda(self, orf, tmp_path):
        """Segue `5ca9d4e`: extrai a linha da propria mensagem e a executa.
        Uma mensagem que entrega instrucao impossivel de seguir repete o defeito
        que ela veio consertar — entao quem a verifica executa em vez de ler."""
        raiz = arvore(tmp_path, **BASE, declaracoes=[])
        msg = orf.mensagem_de_falha(orf.conferir(raiz))

        linha = next(x for x in msg.splitlines() if "orfaos.py" in x and "--sync" in x)
        argv = [a.strip('"') for a in shlex.split(linha.strip(), posix=False)]
        corte = next(i for i, a in enumerate(argv) if a.endswith("orfaos.py")) + 1

        p = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "orfaos.py"), *argv[corte:],
             "--raiz", str(raiz)],
            capture_output=True, text=True, timeout=120,
        )
        assert p.returncode == 0, f"o comando sugerido nao roda:\n{p.stdout}\n{p.stderr}"


# ==========================================================================
# BLOCO D — o repositorio real. `test_repositorio_real_passa_o_guarda` E o guarda.
# ==========================================================================
class TestRepositorioReal:
    def test_repositorio_real_passa_o_guarda(self, orf):
        p = orf.conferir(ROOT)
        assert p["ready"] is True, orf.mensagem_de_falha(p)

    def test_o_proprio_scanner_aparece_como_vivo(self, orf):
        """Corroboracao, NAO prova. O scanner atestando a si mesmo mede
        consistencia, nao vida — a prova e o pytest reprovando com orfao
        plantado, registrada na verificacao."""
        r = orf.varrer(ROOT)
        do_scanner = {k: v for k, v in r["vereditos"].items() if k[0] == "orfaos"}
        assert do_scanner, "o scanner nao se enxerga"
        assert all(v == "viva" for v in do_scanner.values()), (
            [k for k, v in do_scanner.items() if v != "viva"]
        )

    def test_health_check_invoca_o_scanner(self):
        """Segundo chamador, independente do pytest. Sem ele, so ha um caminho
        ate o guarda, e derruba-lo derrubaria a prova junto."""
        texto = (ROOT / "scripts" / "health-check.sh").read_text(encoding="utf-8", errors="replace")
        assert "tools/orfaos.py" in texto

    def test_skills_de_spec_exigem_consumidor_nomeado(self):
        """A regra nao pode ficar so no texto que ninguem confere."""
        for skill in ("write-spec", "write-spec-light"):
            texto = (ROOT / "skills" / skill / "SKILL.md").read_text(encoding="utf-8", errors="replace")
            assert "consumidor:" in texto, f"{skill} nao exige consumidor nomeado"

    def test_tools_readme_declara_o_scanner(self):
        texto = (ROOT / "tools" / "README.md").read_text(encoding="utf-8", errors="replace")
        assert "orfaos.py" in texto
