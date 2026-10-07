"""De qual arvore o contrato foi lido, e o que acontece quando ela nao esta la.

Ate 2026-09-05 havia ONZE arvores de contrato nesta maquina e nenhuma linha de
codigo elegendo dona: cada programa se amarrava a copia adjacente a si mesmo por
`__file__`, sem script de sincronizacao, e `grep -rn harness4contract` neste
repositorio nao retornava nada.

`arvore_do_contrato` passou a preferir a canonica declarada no `master-harness`
— e a **cair no vizinho em qualquer tropeco**: `mh` nao instalado, flag em
`vizinho`, canonica ausente. Dependencia dura sobre o `mh` num sistema de uso
diario seria trocar duplicidade por fragilidade, e o custo de errar aqui e o
harness nao subir.

O que estes testes travam, e cada um existe por um defeito ja pago nesta casa:

1. **Sem `mh`, nada quebra.** O caminho de degradacao e mais importante que o
   feliz: quem instala o harness numa maquina limpa nao tem `master-harness`.
2. **A origem viaja no relatorio.** Cair para o vizinho em silencio seria a mesma
   classe de B-14 (`server_info` dizendo `hybrid` sobre um matcher lexical),
   B-16 (session_id vazio cegando o delivery_report) e B-17 (frescor que nunca
   fica verde): continua funcionando, ninguem fica sabendo.
3. **Raiz explicita e honrada sem consultar nada.** Quem passa `root` esta sendo
   especifico — e quase todo chamador desses e teste com fixture.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(nome: str, relativo: str):
    spec = importlib.util.spec_from_file_location(nome, ROOT / relativo)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def adapter():
    return _load("contract_adapter_origem", "scripts/contract_adapter.py")


class TestRaizExplicita:
    def test_raiz_explicita_nao_consulta_nada(self, adapter, tmp_path: Path) -> None:
        arvore, origem = adapter.arvore_do_contrato(tmp_path)

        assert arvore == tmp_path / "contract"
        assert origem == "vizinho:raiz-explicita"

    def test_raiz_explicita_vale_mesmo_com_mh_disponivel(self, adapter, tmp_path: Path) -> None:
        """Se o `root` fosse ignorado, todo teste com fixture leria a canonica."""
        arvore, _ = adapter.arvore_do_contrato(tmp_path)

        assert "master-harness" not in str(arvore)


class TestDegradacao:
    """O caminho que protege quem nao tem `master-harness` instalado."""

    def test_sem_mh_o_relatorio_continua_conforme(self) -> None:
        codigo = (
            "import sys, json, builtins\n"
            f"sys.path.insert(0, r'{ROOT / 'scripts'}')\n"
            "_ri = builtins.__import__\n"
            "def _bloqueia(nome, *a, **k):\n"
            "    if nome == 'mh' or nome.startswith('mh.'):\n"
            "        raise ImportError('simulado')\n"
            "    return _ri(nome, *a, **k)\n"
            "builtins.__import__ = _bloqueia\n"
            "import contract_adapter as ca\n"
            "arv, origem = ca.arvore_do_contrato()\n"
            "r = ca.build_capability_report()\n"
            "print(json.dumps({'arvore': str(arv), 'origem': origem,"
            " 'conformant': r['conformant'], 'lock': r['snapshot_lock_valid'],"
            " 'caps': len(r['capabilities'])}))\n"
        )
        p = subprocess.run(
            [sys.executable, "-c", codigo], capture_output=True, text=True, timeout=180
        )

        assert p.returncode == 0, p.stderr
        d = json.loads(p.stdout.strip().splitlines()[-1])
        # "Vizinho" e o `contract/` adjacente ao `scripts/` que foi carregado —
        # este repositorio, seja qual for o nome do diretorio que o contem. A
        # assercao antiga exigia que o checkout se chamasse `harness4claude`, e
        # por isso falhava dentro de qualquer worktree (`.claude/worktrees/<x>`),
        # que e onde o trabalho deste repositorio de fato acontece.
        assert Path(d["arvore"]) == ROOT / "contract"
        assert d["origem"].startswith("vizinho:")
        assert d["conformant"] is True
        assert d["lock"] is True
        assert d["caps"] == 22


class TestOrigemNoRelatorio:
    def test_o_relatorio_diz_de_onde_veio(self, adapter) -> None:
        """Sem isto, um relatorio da canonica e um do vizinho sao indistinguiveis."""
        r = adapter.build_capability_report(ROOT)

        assert "contract_origem" in r
        assert r["contract_origem"]

    def test_a_origem_e_uma_das_formas_previstas(self, adapter) -> None:
        _, origem = adapter.arvore_do_contrato()

        assert origem == "mh" or origem.startswith("vizinho:")


class TestInvarianteDaMigracao:
    """O relatorio nao pode mudar por causa de QUAL arvore foi lida.

    Se mudar, as arvores divergiram — e a migracao deixou de ser uma troca de
    endereco para virar uma troca de conteudo.
    """

    def test_vizinho_e_canonica_produzem_o_mesmo_relatorio(self, adapter) -> None:
        try:
            from mh import contrato  # noqa: PLC0415
        except ImportError:
            pytest.skip("master-harness nao instalado nesta maquina")
        if not (contrato.CANONICA / "capabilities.json").is_file():
            pytest.skip("canonica ausente")

        do_vizinho = adapter.build_capability_report(ROOT)
        do_vizinho.pop("contract_origem", None)

        # Le a canonica pela mesma funcao, passando a raiz do master-harness.
        pela_canonica = adapter.build_capability_report(contrato.CANONICA.parent)
        pela_canonica.pop("contract_origem", None)

        assert do_vizinho["pipeline_fingerprint"] == pela_canonica["pipeline_fingerprint"]
        assert set(do_vizinho["capabilities"]) == set(pela_canonica["capabilities"])


_BLOQUEIO_DO_MH = r'''
import importlib.abc, importlib.machinery, json, sys
FALSO = sys.argv[1]
class _SoOFalso(importlib.abc.MetaPathFinder):
    """O `mh` instalado nao e importavel; so o que o marcador aponta e.

    Imita o Python do sistema medido em 2026-10-07: um `master-harness` editavel
    cujo finder aponta para um worktree apagado.
    """
    def find_spec(self, nome, path, target=None):
        if nome != "mh" and not nome.startswith("mh."):
            return None
        spec = importlib.machinery.PathFinder.find_spec(nome, path)
        if spec is not None and str(spec.origin or "").startswith(FALSO):
            return spec
        raise ModuleNotFoundError(f"No module named {nome!r}")
sys.meta_path.insert(0, _SoOFalso())
sys.path.insert(0, sys.argv[2])
import contract_adapter as ca
arv, origem = ca.arvore_do_contrato()
print(json.dumps({"arvore": str(arv), "origem": origem}))
'''


class TestPeloMarcador:
    """O `mh` e achado pelo marcador `~/.master-harness/mh-root`, como a presenca e o dreno ja fazem.

    Medido em 2026-10-07: o Python do sistema tem um `master-harness` editavel
    apontando para um worktree apagado. Com ele, `import mh` falha e
    `arvore_do_contrato()` devolvia `vizinho:ModuleNotFoundError` — os hooks
    chamados com `python` puro, `confirm_classification.py` e `state_cli.py`
    ficavam fora da flag `contrato = preferido` sem ninguem saber.
    """

    @pytest.fixture()
    def falso(self, tmp_path: Path) -> Path:
        """Um `mh` minimo: `contrato.CANONICA` e `flags.get`, o que o adaptador usa."""
        raiz = tmp_path / "mh-falso"
        canon = tmp_path / "canonica"
        canon.mkdir()
        (canon / "capabilities.json").write_text("{}", encoding="utf-8")
        pacote = raiz / "mh"
        pacote.mkdir(parents=True)
        (pacote / "__init__.py").write_text("", encoding="utf-8")
        (pacote / "contrato.py").write_text(
            f"from pathlib import Path\nCANONICA = Path({str(canon)!r})\n", encoding="utf-8")
        (pacote / "flags.py").write_text("def get(nome):\n    return 'preferido'\n", encoding="utf-8")
        return raiz

    def _rodar(self, casa: Path, falso: Path) -> dict:
        import os

        env = dict(os.environ, MASTER_HARNESS_HOME=str(casa))
        p = subprocess.run(
            [sys.executable, "-c", _BLOQUEIO_DO_MH, str(falso), str(ROOT / "scripts")],
            capture_output=True, text=True, timeout=120, env=env,
        )
        assert p.returncode == 0, p.stderr
        return json.loads(p.stdout.strip().splitlines()[-1])

    def test_marcador_resolve_com_o_mh_inimportavel(self, falso: Path, tmp_path: Path) -> None:
        casa = tmp_path / "casa"
        casa.mkdir()
        (casa / "mh-root").write_text(str(falso) + "\n", encoding="utf-8")

        d = self._rodar(casa, falso)

        assert d["origem"] == "mh"
        assert Path(d["arvore"]) == tmp_path / "canonica"

    def test_sem_marcador_e_sem_mh_cai_no_vizinho(self, falso: Path, tmp_path: Path) -> None:
        casa = tmp_path / "casa-vazia"
        casa.mkdir()

        d = self._rodar(casa, falso)

        assert d["origem"].startswith("vizinho:")
        assert Path(d["arvore"]) == ROOT / "contract"

    def test_marcador_para_pasta_inexistente_cai_no_vizinho(self, falso: Path, tmp_path: Path) -> None:
        casa = tmp_path / "casa"
        casa.mkdir()
        (casa / "mh-root").write_text(str(tmp_path / "apagado") + "\n", encoding="utf-8")

        d = self._rodar(casa, falso)

        assert d["origem"].startswith("vizinho:")
