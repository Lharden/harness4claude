"""Remocao propaga no deploy (defeito medido em 2026-10-09).

O contrato foi de 1.3.0 para 1.4.0 removendo `node-result.schema.json`. Depois
de `deploy_to_cache.py --apply` o arquivo continuou no cache: o script so copiava
e `--check` so olhava repo -> cache. O `--publicado` ja acusava o arquivo
("sobrando"), mas o `--apply` que a mensagem manda rodar nao o apagava — o
conserto sugerido nao consertava, e `test_o_cache_reflete_o_publicado` ficou
vermelho na maquina por causa disso.

Apagar todo extra nao serve: o cache tem centenas (bytecode, `.in_use`, copia de
outras ferramentas). So sai o que um deploy implantou antes, provado por
manifesto (`MANIFESTO`, o mesmo arquivo e formato que `mh deploy` escreve no
mesmo cache) ou, no legado sem manifesto, pelo historico git.

Os testes entram por `main()`, o caminho que o usuario executa: `--apply`
escreve no plugin instalado, entao `repo_root` e `installed_root` sao trocados
por pastas de `tmp_path`.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
sys.path.insert(0, str(ROOT / "scripts"))

import deploy_to_cache as dtc  # noqa: E402

SCHEMA = "contract/schemas/node-result.schema.json"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=str(repo), capture_output=True, text=True, timeout=120, check=True,
    )


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    (r / "scripts").mkdir(parents=True)
    (r / "scripts" / "base.py").write_bytes(b"x = 1\n")
    (r / "contract" / "schemas").mkdir(parents=True)
    (r / "contract" / "schemas" / "node-result.schema.json").write_bytes(b'{"x": 1}\n')
    (r / "contract" / "schemas" / "fica.schema.json").write_bytes(b'{"y": 2}\n')
    (r / ".github").mkdir()
    (r / ".github" / "w.yml").write_bytes(b"on: push\n")
    _git(r, "init", "-q", ".")
    _git(r, "checkout", "-q", "-B", "main")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "base")
    return r


@pytest.fixture()
def cache(tmp_path: Path) -> Path:
    c = tmp_path / "cache"
    c.mkdir()
    return c


@pytest.fixture()
def apontado(repo: Path, cache: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """`main()` passa a ler `repo` e a escrever em `cache`."""
    monkeypatch.setattr(dtc, "repo_root", lambda: repo)
    monkeypatch.setattr(dtc, "installed_root", lambda: cache)
    return cache


def _remover_do_repo(repo: Path, rel: str = SCHEMA) -> None:
    _git(repo, "rm", "-q", rel)
    _git(repo, "commit", "-q", "-m", "remove")


class TestRemocaoPropaga:
    # ---- metade 1: o que tem de sumir ----

    def test_implantado_e_removido_do_repo_some_no_apply_seguinte(self, repo, apontado):
        assert dtc.main(["--apply"]) == 0
        assert (apontado / SCHEMA).exists()
        _remover_do_repo(repo)

        assert dtc.main(["--apply"]) == 0

        assert not (apontado / SCHEMA).exists()
        assert (apontado / "contract" / "schemas" / "fica.schema.json").exists()

    def test_check_acusa_antes_do_apply(self, repo, apontado, capsys):
        dtc.main(["--apply"])
        _remover_do_repo(repo)
        capsys.readouterr()

        codigo = dtc.main([])

        out = capsys.readouterr().out
        assert codigo == 1, out
        assert "node-result.schema.json" in out
        assert (apontado / SCHEMA).exists(), "--check nao apaga"

    def test_check_volta_a_zero_depois_do_apply(self, repo, apontado):
        dtc.main(["--apply"])
        _remover_do_repo(repo)
        dtc.main(["--apply"])

        assert dtc.main([]) == 0

    def test_apply_repetido_remove_zero(self, repo, apontado):
        dtc.main(["--apply"])
        _remover_do_repo(repo)
        dtc.main(["--apply"])

        assert dtc.removidos_do_repo(repo, apontado) == []

    def test_dir_que_ficou_vazio_tambem_sai(self, repo, apontado):
        (repo / "so" / "um").mkdir(parents=True)
        (repo / "so" / "um" / "f.txt").write_bytes(b"f\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "so um")
        dtc.main(["--apply"])
        _remover_do_repo(repo, "so/um/f.txt")

        dtc.main(["--apply"])

        assert not (apontado / "so").exists()
        assert apontado.exists()

    # ---- metade 2: o que nao pode sumir ----

    def test_extra_que_o_deploy_nunca_implantou_fica_intacto(self, repo, apontado):
        dtc.main(["--apply"])
        intruso = apontado / "trabalho_de_outra_sessao.py"
        intruso.write_text("nao me apague\n", encoding="utf-8")
        em_uso = apontado / ".in_use" / "1234"
        em_uso.parent.mkdir()
        em_uso.write_text("pid\n", encoding="utf-8")
        bytecode = apontado / "scripts" / "__pycache__" / "base.cpython-312.pyc"
        bytecode.parent.mkdir()
        bytecode.write_bytes(b"\0")
        _remover_do_repo(repo)

        dtc.main(["--apply"])

        assert intruso.exists() and em_uso.exists() and bytecode.exists()
        assert not (apontado / SCHEMA).exists()

    def test_arquivo_ainda_versionado_nunca_e_removido(self, repo, apontado):
        dtc.main(["--apply"])

        dtc.main(["--apply"])

        assert (apontado / "scripts" / "base.py").exists()

    def test_arquivo_que_so_esta_fora_do_inventario_filtrado_nao_e_removido(self, repo, apontado):
        """`mh deploy` entrega `.github/w.yml`; este script nao. Estar no manifesto
        e deixar de estar no inventario FILTRADO nao e ter sido removido do repo."""
        alvo = apontado / ".github" / "w.yml"
        alvo.parent.mkdir()
        alvo.write_bytes(b"on: push\n")
        dtc.gravar_manifesto(apontado, {".github/w.yml"})

        dtc.main(["--apply"])

        assert alvo.exists()

    def test_check_nao_escreve_manifesto(self, repo, apontado):
        dtc.main([])

        assert not (apontado / dtc.MANIFESTO).exists()

    def test_apply_escreve_manifesto_mesmo_sem_nada_a_copiar(self, repo, apontado):
        dtc.main(["--apply"])
        (apontado / dtc.MANIFESTO).unlink()

        dtc.main(["--apply"])

        assert "scripts/base.py" in dtc.ler_manifesto(apontado)

    def test_manifesto_fica_de_fora_do_publicado(self, repo, cache):
        dtc.extract_ref(repo, "main", cache)
        dtc.gravar_manifesto(cache, {"scripts/base.py"})

        assert dtc.drift_publicado(repo, cache, "main") == []

    def test_manifesto_tem_o_formato_combinado_com_mh_deploy(self, repo, apontado):
        dtc.main(["--apply"])

        dados = json.loads((apontado / dtc.MANIFESTO).read_text(encoding="utf-8"))

        assert dtc.MANIFESTO == ".mh-deploy-manifest.json"
        assert dados["versao"] == 1
        assert dados["arquivos"] == sorted(dados["arquivos"])
        assert "scripts/base.py" in dados["arquivos"]

    def test_manifesto_escrito_pelo_mh_e_honrado(self, repo, apontado):
        """O formato e o do `mh deploy`: `{"versao": 1, "arquivos": [...]}`."""
        dtc.main(["--apply"])
        (apontado / dtc.MANIFESTO).write_text(
            json.dumps({"versao": 1, "arquivos": [SCHEMA, "scripts/base.py"]}), encoding="utf-8"
        )
        _remover_do_repo(repo)

        dtc.main(["--apply"])

        assert not (apontado / SCHEMA).exists()

    def test_entrada_fora_do_destino_e_ignorada(self, repo, apontado, tmp_path):
        fora = tmp_path / "fora.txt"
        fora.write_text("de fora\n", encoding="utf-8")
        (apontado / dtc.MANIFESTO).write_text(
            json.dumps({"versao": 1, "arquivos": ["../fora.txt", str(fora)]}), encoding="utf-8"
        )

        dtc.main(["--apply"])

        assert fora.exists()

    def test_manifesto_corrompido_nao_apaga_nada(self, repo, apontado):
        dtc.main(["--apply"])
        (apontado / dtc.MANIFESTO).write_text("{nao e json", encoding="utf-8")
        intruso = apontado / "extra.txt"
        intruso.write_bytes(b"x")

        dtc.main(["--apply"])

        assert intruso.exists()


class TestLegadoSemManifesto:
    """Destino de antes do manifesto: so o historico git prova que era do deploy."""

    def _legado(self, repo: Path, apontado: Path) -> None:
        dtc.main(["--apply"])
        (apontado / dtc.MANIFESTO).unlink()
        _remover_do_repo(repo)

    def test_copia_fiel_de_arquivo_removido_do_repo_sai(self, repo, apontado):
        self._legado(repo, apontado)

        assert dtc.removidos_do_repo(repo, apontado) == [Path(SCHEMA)]
        dtc.main(["--apply"])

        assert not (apontado / SCHEMA).exists()

    def test_copia_com_crlf_e_fiel(self, repo, apontado):
        dtc.main(["--apply"])
        (apontado / dtc.MANIFESTO).unlink()
        alvo = apontado / SCHEMA
        alvo.write_bytes(alvo.read_bytes().replace(b"\n", b"\r\n"))
        _remover_do_repo(repo)

        dtc.main(["--apply"])

        assert not alvo.exists()

    def test_copia_alterada_fica(self, repo, apontado):
        self._legado(repo, apontado)
        (apontado / SCHEMA).write_bytes(b'{"x": "trabalho de outra sessao"}\n')

        dtc.main(["--apply"])

        assert (apontado / SCHEMA).exists()

    def test_arquivo_que_o_historico_nao_conhece_fica(self, repo, apontado):
        self._legado(repo, apontado)
        nunca = apontado / "contract" / "schemas" / "nunca-foi-do-repo.json"
        nunca.write_bytes(b"{}\n")

        dtc.main(["--apply"])

        assert nunca.exists()
        assert not (apontado / SCHEMA).exists()
