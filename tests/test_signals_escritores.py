"""Escritores de `signals.json` se excluem: nenhum apaga o que o outro gravou.

Tres escritores fazem read-modify-write do documento INTEIRO:

- `record_signal.record` (e `expire_stale_pipeline`, que chama `record`) grava
  `tasks` e `aggregates`;
- `branch_state.signal` grava os contadores do bloco `branch`;
- `migrate_state.run` reescreve o documento na migracao para v3.

Ate 2026-09-30 so `signal` tomava o `_Lock`, e os outros dois nao. Um lock que
so um dos escritores respeita nao exclui ninguem: quem le antes da escrita do
outro e grava depois devolve o documento velho, e a task registrada no DONE, ou
o contador de ofertas do Branch Keeper, some sem erro.

Os testes param um escritor ENTRE a leitura e a escrita, por shim, e soltam o
outro. O ponto de soltura nao e tempo: e "o outro terminou" ou "o outro bateu
no lockdir ocupado" (shim em `os.mkdir`), o que acontecer primeiro. Sem lock
comum o outro termina e a escrita parada apaga o que ele gravou; com o lock ele
espera, e le o documento ja com a escrita do primeiro.

O prazo so guarda contra deadlock; nenhuma decisao do teste depende dele.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import threading
import types
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
SCRIPTS = ROOT / "scripts"

_PRAZO = 30.0


def _carrega(nome: str):
    spec = importlib.util.spec_from_file_location(nome, SCRIPTS / f"{nome}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[nome] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def mods(monkeypatch):
    """Modulos frescos, na ordem de dependencia.

    `record_signal` e `migrate_state` importam `branch_state`: carregar
    `branch_state` primeiro garante que o `_Lock` deles e o do modulo em que o
    teste ajusta o prazo, e nao uma copia deixada por outro arquivo de teste.
    """
    monkeypatch.syspath_prepend(str(SCRIPTS))
    for nome in ("branch_state", "migrate_state", "record_signal"):
        monkeypatch.delitem(sys.modules, nome, raising=False)
    bs = _carrega("branch_state")
    ms = _carrega("migrate_state")
    rs = _carrega("record_signal")
    monkeypatch.setattr(bs, "LOCK_TIMEOUT_S", _PRAZO)
    return types.SimpleNamespace(bs=bs, ms=ms, rs=rs)


class _Corrida:
    """Dois participantes em threads nomeadas, com pontos de sincronizacao."""

    def __init__(self, monkeypatch):
        self.parado_leu = threading.Event()
        self.parado_segue = threading.Event()
        self.outro_decidiu = threading.Event()
        self.erros: dict[str, BaseException] = {}
        self.threads: list[threading.Thread] = []
        self.outro_nome = ""
        mkdir_real = os.mkdir

        def mkdir(path, *a, **kw):
            try:
                return mkdir_real(path, *a, **kw)
            except FileExistsError:
                if (
                    threading.current_thread().name == self.outro_nome
                    and str(path).endswith("signals.json.lockdir")
                ):
                    self.outro_decidiu.set()
                raise

        monkeypatch.setattr(os, "mkdir", mkdir)

    def pausa_depois_de_ler(self, nome: str, ler):
        """Envolve a leitura: na thread `nome`, le, avisa e espera a soltura."""
        def envolvida(*a, **kw):
            valor = ler(*a, **kw)
            if threading.current_thread().name == nome:
                self.parado_leu.set()
                assert self.parado_segue.wait(_PRAZO), f"{nome} nunca foi solto"
            return valor
        return envolvida

    def inicia(self, nome: str, corpo, *, outro: bool = False) -> threading.Thread:
        if outro:
            self.outro_nome = nome

        def alvo():
            try:
                corpo()
            except BaseException as exc:  # noqa: BLE001 - reportado no confere
                self.erros[nome] = exc
            finally:
                if outro:
                    self.outro_decidiu.set()

        t = threading.Thread(target=alvo, name=nome, daemon=True)
        self.threads.append(t)
        t.start()
        return t

    def roda(self, parado: str, corpo_parado, outro: str, corpo_outro) -> None:
        self.inicia(parado, corpo_parado)
        assert self.parado_leu.wait(_PRAZO), f"{parado} nunca leu signals.json"
        self.inicia(outro, corpo_outro, outro=True)
        # Soltura por evento: o outro terminou (sem lock comum) ou bateu no
        # lockdir que o parado segura (com lock comum).
        assert self.outro_decidiu.wait(_PRAZO), f"{outro} nem terminou nem esperou o lock"
        self.parado_segue.set()
        for t in self.threads:
            t.join(_PRAZO)
            assert not t.is_alive(), f"{t.name} nao terminou"
        assert not self.erros, f"participante levantou: {self.erros!r}"


def _task(rs, task_id: str = "t-corrida") -> dict:
    return rs.build_task(
        {"task_id": task_id, "classification": "L1-bug"},
        {"count": 2},
        completed=True,
        steps=["tdd"],
        reason=None,
        timestamp="2026-09-30T00:00:00+00:00",
    )


def _doc(raiz: Path) -> dict:
    return json.loads((raiz / "signals.json").read_text(encoding="utf-8"))


def _semeia(raiz: Path, **extra) -> None:
    doc = {"version": 3, "harness_version": "v3", "tasks": [], "aggregates": {}}
    doc.update(extra)
    (raiz / "signals.json").write_text(json.dumps(doc), encoding="utf-8")


class TestRecordESignalSeExcluem:
    def test_contador_gravado_durante_o_record_sobrevive(self, mods, tmp_path, monkeypatch):
        """record le, signal grava `branch.offers`, record grava: o contador fica."""
        _semeia(tmp_path)
        corrida = _Corrida(monkeypatch)
        monkeypatch.setattr(
            mods.rs, "load_json", corrida.pausa_depois_de_ler("record", mods.rs.load_json)
        )
        corrida.roda(
            "record", lambda: mods.rs.record(tmp_path, _task(mods.rs)),
            "signal", lambda: mods.bs.signal("offers", tmp_path),
        )
        doc = _doc(tmp_path)
        assert [t["task_id"] for t in doc["tasks"]] == ["t-corrida"]
        assert doc.get("branch", {}).get("offers") == 1, (
            f"o contador gravado por signal durante o record sumiu: {doc.get('branch')!r}"
        )

    def test_task_gravada_durante_o_signal_sobrevive(self, mods, tmp_path, monkeypatch):
        """signal le, record grava a task, signal grava: a task fica."""
        _semeia(tmp_path)
        corrida = _Corrida(monkeypatch)
        json_parado = types.SimpleNamespace(
            loads=corrida.pausa_depois_de_ler("signal", json.loads),
            dumps=json.dumps,
        )
        monkeypatch.setattr(mods.bs, "json", json_parado)
        corrida.roda(
            "signal", lambda: mods.bs.signal("offers", tmp_path),
            "record", lambda: mods.rs.record(tmp_path, _task(mods.rs)),
        )
        doc = _doc(tmp_path)
        assert doc.get("branch", {}).get("offers") == 1
        assert [t["task_id"] for t in doc["tasks"]] == ["t-corrida"], (
            f"a task gravada por record durante o signal sumiu: {doc['tasks']!r}"
        )


class TestSignalSemLockNaoGrava:
    def test_lock_ocupado_perde_o_contador_e_nao_o_documento(self, mods, tmp_path, monkeypatch):
        """Fail-open de `signal` e desistir do contador, nao gravar sem exclusao.

        Gravar sem o lock e exatamente a corrida acima com o prazo no lugar do
        shim: quem segura o lock pode estar entre a leitura e a escrita de uma
        task, e o documento que `signal` grava por cima nao tem essa task.
        """
        _semeia(tmp_path)
        antes = (tmp_path / "signals.json").read_bytes()
        # Lock vivo de outro escritor: recente, longe de stale.
        os.mkdir(tmp_path / "signals.json.lockdir")
        monkeypatch.setattr(mods.bs, "LOCK_TIMEOUT_S", 0)
        mods.bs.signal("offers", tmp_path)
        assert (tmp_path / "signals.json").read_bytes() == antes, (
            "signal gravou signals.json sem ter o lock"
        )


class TestMigracaoNaoApagaOutroEscritor:
    def test_migracao_preserva_o_bloco_branch(self, mods, tmp_path):
        """Sem corrida: `branch` e campo legitimo do schema e sai da migracao."""
        _semeia(tmp_path, version=2, branch={"offers": 4})
        (tmp_path / "state.json").write_text(json.dumps({}), encoding="utf-8")
        assert mods.ms.run(tmp_path, ROOT / "schemas", dry_run=False, do_backup=False) == 0
        doc = _doc(tmp_path)
        assert doc["version"] == 3
        assert doc.get("branch") == {"offers": 4}, f"migracao apagou o bloco branch: {doc!r}"

    def test_contador_gravado_durante_a_migracao_sobrevive(self, mods, tmp_path, monkeypatch):
        _semeia(tmp_path, version=2)
        (tmp_path / "state.json").write_text(json.dumps({}), encoding="utf-8")
        corrida = _Corrida(monkeypatch)
        ler = mods.ms.load_json

        def ler_signals_parado(path, *a, **kw):
            if Path(path).name != "signals.json":
                return ler(path, *a, **kw)
            return corrida.pausa_depois_de_ler("migrate", ler)(path, *a, **kw)

        monkeypatch.setattr(mods.ms, "load_json", ler_signals_parado)
        resultado: dict[str, int] = {}

        def migra():
            resultado["rc"] = mods.ms.run(tmp_path, ROOT / "schemas", dry_run=False, do_backup=False)

        corrida.roda("migrate", migra, "signal", lambda: mods.bs.signal("offers", tmp_path))
        assert resultado["rc"] == 0
        doc = _doc(tmp_path)
        assert doc["version"] == 3
        assert doc.get("branch", {}).get("offers") == 1, (
            f"o contador gravado por signal durante a migracao sumiu: {doc.get('branch')!r}"
        )
