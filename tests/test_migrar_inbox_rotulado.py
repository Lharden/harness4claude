"""Migracao unica das notas diarias legadas de `raw/inbox/` para o nome com rotulo.

Ate 2026-09-24 o vault_sync gravava `.remember/today-X.md` como `raw/inbox/today-X.md`,
e so o repo de nota mais nova sobrevivia. Depois do conserto, o sync grava
`<repo>--today-X.md`. Sem migracao, as paginas antigas ficariam lado a lado com as
novas: 41 duplicatas no AI-Brain real. A migracao renomeia a pagina cujo dono se prova
e deixa o resto intocado e listado. Escreve no vault real, que tem Obsidian Sync pago:
por isso ensaia por padrao, exige backup e sabe desfazer.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import vault_sync as vs

from tools import migrar_inbox_rotulado as mig

NOTA = "today-2026-08-06.done.md"


def _repo(base: Path, nome: str, notas: dict[str, str]) -> Path:
    raiz = base / nome
    (raiz / ".git").mkdir(parents=True)
    (raiz / ".remember").mkdir()
    for arquivo, texto in notas.items():
        (raiz / ".remember" / arquivo).write_text(texto, encoding="utf-8")
    return raiz


def _legado(vault: Path, nome: str, texto: str) -> Path:
    pagina = vault / "raw" / "inbox" / nome
    pagina.parent.mkdir(parents=True, exist_ok=True)
    pagina.write_text(texto, encoding="utf-8")
    return pagina


def _foto(pasta: Path) -> dict[str, bytes]:
    return {p.relative_to(pasta).as_posix(): p.read_bytes() for p in pasta.rglob("*") if p.is_file()}


@pytest.fixture
def cena(tmp_path: Path):
    vault = tmp_path / "ai-brain"
    harness = tmp_path / "harness"
    harness.mkdir()
    global_ = tmp_path / "remember-global"
    global_.mkdir()
    return vault, harness, global_


def _planejar(vault: Path, repos: list[Path], global_: Path) -> mig.Plano:
    return mig.planejar(vault, repos, remember_global=global_)


def test_ensaio_nao_escreve_nada(tmp_path: Path, cena) -> None:
    vault, harness, global_ = cena
    a = _repo(tmp_path, "repo-a", {NOTA: "# nota\n"})
    _legado(vault, NOTA, "# nota\n")
    antes = _foto(tmp_path)

    plano = _planejar(vault, [a], global_)

    assert [r.novo for r in plano.renomear] == [f"repo-a--{NOTA}"]
    assert _foto(tmp_path) == antes


def test_dono_provado_pelos_bytes_e_renomeado_e_o_sync_nao_duplica(tmp_path: Path, cena) -> None:
    vault, harness, global_ = cena
    a = _repo(tmp_path, "repo-a", {})
    (a / ".remember" / NOTA).write_bytes(b"# nota\n")
    # O Obsidian no Windows grava CRLF. Bytes explicitos: `write_text` com "\r\n" no
    # Windows grava "\r\r\n".
    _legado(vault, NOTA, "").write_bytes(b"# nota\r\n")
    manifesto = harness / vs.MANIFESTO

    plano = _planejar(vault, [a], global_)
    mig.aplicar(plano, vault, manifesto=manifesto, backup=tmp_path / "backup")

    inbox = vault / "raw" / "inbox"
    assert [r.prova for r in plano.renomear] == ["bytes"]
    assert not (inbox / NOTA).exists()
    assert (inbox / f"repo-a--{NOTA}").read_bytes() == b"# nota\r\n"
    contagem = vs.sync(vault, harness, a, raiz=harness, remember_global=global_)
    assert contagem["inbox"] == 0, "a pagina renomeada e a do sync: nada a escrever"
    assert sorted(p.name for p in inbox.glob("*.md")) == [f"repo-a--{NOTA}"]


def test_copia_velha_do_unico_dono_e_renomeada_e_o_sync_a_atualiza(tmp_path: Path, cena) -> None:
    vault, harness, global_ = cena
    a = _repo(tmp_path, "repo-a", {NOTA: "# nota, versao nova\n"})
    _legado(vault, NOTA, "# nota, versao velha\n")
    manifesto = harness / vs.MANIFESTO

    plano = _planejar(vault, [a], global_)
    mig.aplicar(plano, vault, manifesto=manifesto, backup=tmp_path / "backup")

    assert [r.prova for r in plano.renomear] == ["nome-unico"]
    contagem = vs.sync(vault, harness, a, raiz=harness, remember_global=global_)
    pagina = vault / "raw" / "inbox" / f"repo-a--{NOTA}"
    assert contagem["inbox"] == 1
    assert pagina.read_text(encoding="utf-8") == "# nota, versao nova\n"


def test_pagina_que_nao_bate_com_nenhum_de_dois_donos_fica_e_e_listada(tmp_path: Path, cena) -> None:
    vault, harness, global_ = cena
    a = _repo(tmp_path, "repo-a", {NOTA: "# do A\n"})
    b = _repo(tmp_path, "repo-b", {NOTA: "# do B\n"})
    pagina = _legado(vault, NOTA, "# de ninguem\n")

    plano = _planejar(vault, [a, b], global_)
    mig.aplicar(plano, vault, manifesto=harness / vs.MANIFESTO, backup=tmp_path / "backup")

    assert plano.renomear == []
    assert plano.ambiguas == [NOTA]
    assert pagina.read_text(encoding="utf-8") == "# de ninguem\n"


def test_bytes_iguais_aos_de_dois_repos_e_ambigua(tmp_path: Path, cena) -> None:
    vault, harness, global_ = cena
    a = _repo(tmp_path, "repo-a", {NOTA: "# igual\n"})
    b = _repo(tmp_path, "repo-b", {NOTA: "# igual\n"})
    _legado(vault, NOTA, "# igual\n")

    plano = _planejar(vault, [a, b], global_)

    assert plano.renomear == []
    assert plano.ambiguas == [NOTA]


def test_pagina_sem_fonte_viva_fica_e_e_listada(tmp_path: Path, cena) -> None:
    vault, harness, global_ = cena
    a = _repo(tmp_path, "repo-a", {})
    _legado(vault, NOTA, "# orfa\n")

    plano = _planejar(vault, [a], global_)

    assert plano.renomear == []
    assert plano.sem_fonte == [NOTA]


def test_ensaio_lista_as_notas_perdidas_por_colisao_que_vao_entrar(tmp_path: Path, cena) -> None:
    """A versao de B nunca chegou ao vault: a nota de A ocupava o nome."""
    vault, harness, global_ = cena
    a = _repo(tmp_path, "repo-a", {NOTA: "# do A\n"})
    b = _repo(tmp_path, "repo-b", {NOTA: "# do B\n"})
    _legado(vault, NOTA, "# do A\n")

    plano = _planejar(vault, [a, b], global_)

    assert [r.novo for r in plano.renomear] == [f"repo-a--{NOTA}"]
    assert plano.novas == [f"repo-b--{NOTA}"]


def test_pagina_ja_processada_e_marcada_no_ensaio(tmp_path: Path, cena) -> None:
    vault, harness, global_ = cena
    a = _repo(tmp_path, "repo-a", {NOTA: "# nota\n"})
    _legado(vault, NOTA, "# nota\n")
    processadas = vault / "raw" / "inbox" / "_processed"
    processadas.mkdir()
    (processadas / NOTA).write_text("# nota\n", encoding="utf-8")

    plano = _planejar(vault, [a], global_)

    assert [r.processada for r in plano.renomear] == [True]


def test_destino_ja_ocupado_e_conflito_e_nao_e_sobrescrito(tmp_path: Path, cena) -> None:
    vault, harness, global_ = cena
    a = _repo(tmp_path, "repo-a", {NOTA: "# nota\n"})
    _legado(vault, NOTA, "# nota\n")
    ocupado = _legado(vault, f"repo-a--{NOTA}", "# ja existia\n")

    plano = _planejar(vault, [a], global_)
    mig.aplicar(plano, vault, manifesto=harness / vs.MANIFESTO, backup=tmp_path / "backup")

    assert plano.renomear == []
    assert plano.conflitos == [NOTA]
    assert ocupado.read_text(encoding="utf-8") == "# ja existia\n"


def test_aplicar_recusa_backup_dentro_do_vault_ou_ja_usado(tmp_path: Path, cena) -> None:
    vault, harness, global_ = cena
    a = _repo(tmp_path, "repo-a", {NOTA: "# nota\n"})
    _legado(vault, NOTA, "# nota\n")
    plano = _planejar(vault, [a], global_)
    usado = tmp_path / "backup-usado"
    usado.mkdir()
    (usado / "qualquer").write_text("x", encoding="utf-8")

    with pytest.raises(ValueError):
        mig.aplicar(plano, vault, manifesto=harness / vs.MANIFESTO, backup=vault / "backup")
    with pytest.raises(ValueError):
        mig.aplicar(plano, vault, manifesto=harness / vs.MANIFESTO, backup=usado)
    assert (vault / "raw" / "inbox" / NOTA).is_file(), "recusa antes de mexer em qualquer coisa"


def test_aplicar_duas_vezes_nao_faz_nada_na_segunda(tmp_path: Path, cena) -> None:
    vault, harness, global_ = cena
    a = _repo(tmp_path, "repo-a", {NOTA: "# nota\n"})
    _legado(vault, NOTA, "# nota\n")
    manifesto = harness / vs.MANIFESTO
    mig.aplicar(_planejar(vault, [a], global_), vault, manifesto=manifesto, backup=tmp_path / "b1")
    depois = _foto(vault)

    segundo = _planejar(vault, [a], global_)
    mig.aplicar(segundo, vault, manifesto=manifesto, backup=tmp_path / "b2")

    assert segundo.renomear == []
    assert _foto(vault) == depois


def test_restaurar_devolve_nomes_e_manifesto(tmp_path: Path, cena) -> None:
    """Restaurar e cirurgico no manifesto: tira so o que a migracao pos, e a entrada que
    o sync gravou depois dela continua la."""
    vault, harness, global_ = cena
    a = _repo(tmp_path, "repo-a", {NOTA: "# nota\n"})
    _legado(vault, NOTA, "# nota\n")
    manifesto = harness / vs.MANIFESTO
    posterior = {"destino": "d", "fonte": "f", "origem": "o", "mtime": 1.0}
    manifesto.write_text(json.dumps({"versao": 1, "paginas": {}}), encoding="utf-8")
    antes_vault = _foto(vault)
    backup = tmp_path / "backup"
    mig.aplicar(_planejar(vault, [a], global_), vault, manifesto=manifesto, backup=backup)
    gravado = json.loads(manifesto.read_text(encoding="utf-8"))
    gravado["paginas"]["gravada-pelo-sync-depois"] = posterior
    manifesto.write_text(json.dumps(gravado), encoding="utf-8")

    relatorio = mig.restaurar(backup, vault, manifesto=manifesto)

    assert relatorio["restauradas"] == [NOTA]
    depois = {k: v for k, v in _foto(vault).items() if k != "wiki/log.md"}
    assert depois == {k: v for k, v in antes_vault.items() if k != "wiki/log.md"}
    paginas = json.loads(manifesto.read_text(encoding="utf-8"))["paginas"]
    assert paginas == {"gravada-pelo-sync-depois": posterior}


def test_restaurar_nao_desfaz_pagina_editada_depois_da_migracao(tmp_path: Path, cena) -> None:
    vault, harness, global_ = cena
    a = _repo(tmp_path, "repo-a", {NOTA: "# nota\n"})
    _legado(vault, NOTA, "# nota\n")
    backup = tmp_path / "backup"
    mig.aplicar(_planejar(vault, [a], global_), vault, manifesto=harness / vs.MANIFESTO,
                backup=backup)
    nova = vault / "raw" / "inbox" / f"repo-a--{NOTA}"
    nova.write_text("# editada no Obsidian\n", encoding="utf-8")

    relatorio = mig.restaurar(backup, vault, manifesto=harness / vs.MANIFESTO)

    assert relatorio["mantidas"] == [f"repo-a--{NOTA}"]
    assert nova.read_text(encoding="utf-8") == "# editada no Obsidian\n"
