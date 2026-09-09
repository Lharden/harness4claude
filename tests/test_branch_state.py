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


def _active_transaction(bs, cwd: Path, session_id: str = "session-branch"):
    project_home = bs.harness_paths.ensure_state_dir(cwd=str(cwd))
    (project_home / "branch-sensor.json").write_text(
        json.dumps({"session_id": session_id, "turn": 10}), encoding="utf-8"
    )
    session_home = bs.harness_paths.ensure_state_dir(cwd=str(cwd), session_id=session_id)
    database = bs.HarnessDatabase(session_home)
    task = database.start_task(
        scope_id=f"{session_id}|repo|worktree",
        legacy_level="L2-feature",
        tier="L2",
        kind="feature",
        pipeline=["discuss", "tdd"],
        prompt="branch work",
    )
    (session_home / "state.json").write_text(
        json.dumps({"task_id": task["task_id"], "scope_id": task["scope_id"]}),
        encoding="utf-8",
    )
    return database, task


def _sweeps(bs) -> int:
    """Quantas vezes a varredura de buckets ACHOU o dono, segundo `signals.json`.

    A medicao de 2026-09-09 mostrou por que isto precisa existir: os testes de
    "o ramo fecha" asserem so o resultado, entao ficam verdes tanto quando a
    pista resolve quanto quando a varredura salva. Dois deles ja tinham trocado
    de mecanismo em silencio. Resultado igual, caminho diferente — e era o
    caminho que estava sendo testado.
    """
    caminho = Path(bs.harness_paths.signals_dir(None)) / "signals.json"
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    bloco = dados.get("branch") if isinstance(dados, dict) else None
    return int(bloco.get("sweep", 0)) if isinstance(bloco, dict) else 0


def _proibir_varredura(bs, monkeypatch):
    """Falha o teste se a varredura for acionada.

    Quem monta um cenario para provar que a PISTA resolve precisa que a rede
    embaixo dela nao entre em campo. Sem isto, remover o candidato
    `parent_session` inteiro de `_transaction_context` deixava a suite verde:
    a varredura assumia o trabalho e ninguem via.
    """

    def _recusa(*_a, **_k):
        pytest.fail("resolveu pela varredura, nao pela pista que o teste monta")

    monkeypatch.setattr(bs, "_sweep_sessions", _recusa)


def _ramo_legado(bs, cwd, slug: str):
    """Deixa o registro do ramo na forma que o codigo ANTIGO gravava.

    Um ramo legado nao tem ponteiro proprio: quem responde por ele e o campo do
    ARQUIVO, herdado do primeiro ramo do projeto. E a forma que vai existir em
    disco depois do merge, e a unica que mantem o cenario da varredura fiel ao
    que ela protege — diferente de "o chamador mentiu o uuid", que e artificial
    e some quando o campo por ramo existir.
    """
    dados = bs.load(cwd=str(cwd))
    for ramo in dados["branches"]:
        if ramo.get("slug") == slug:
            ramo.pop("parent_session_id", None)
    bs.save(dados, cwd=str(cwd))
    return dados


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

    def test_ramo_fecha_depois_que_o_sensor_troca_de_sessao(self, bs, tmp_path, monkeypatch):
        """O ramo tem de fechar A PARTIR DO RAMO, que e onde a skill manda fechar.

        Medido em 2026-09-04, na primeira vez que um ramo real tentou se fechar:
        `branch not found: 5c54af66-...`. O registro transacional do ramo vive no
        `harness.db` da sessao que o CRIOU (a mae), mas `_transaction_context`
        resolvia o banco pelo `session_id` do `branch-sensor.json` do projeto —
        um campo unico, sobrescrito por quem rodou por ultimo.

        Consequencia: `close` falhava dos DOIS lados. Do ramo, porque o registro
        esta na mae; da mae, porque o sensor ja apontava para o ramo. Um ramo
        aberto nao tinha caminho nenhum de volta, que e exatamente o trabalho
        que ramificar existe para preservar.

        O cenario aqui e o da PISTA `parent_session` funcionando: a mae esta na
        lista de candidatos e tem a linha. A varredura fica proibida de propos —
        senao este teste passa a provar a rede em vez do fio que ele nomeia.
        """
        _proibir_varredura(bs, monkeypatch)
        _active_transaction(bs, tmp_path, session_id="sessao-mae")
        b = bs.add(cwd=str(tmp_path), name="Ramo", topic="x",
                   parent_session="sessao-mae")
        semente = tmp_path / "semente.md"
        semente.write_text("# semente", encoding="utf-8")
        bs.set_status(cwd=str(tmp_path), slug=b["slug"], status="open",
                      seed_path=str(semente))

        # A janela do ramo sobe e roda: o sensor do PROJETO passa a apontar
        # para ela, e o banco dela nao conhece ramo nenhum.
        _active_transaction(bs, tmp_path, session_id="sessao-filha")

        fechado = bs.set_status(
            cwd=str(tmp_path), slug=b["slug"], status="closed",
            conclusion="mediu, reprovou a hipotese, achou outra causa",
        )
        assert fechado["status"] == "closed"
        assert fechado["conclusion"].startswith("mediu")
        assert fechado["closed_at"]

    def test_ramo_fecha_sem_task_ativa_em_candidato_nenhum(self, bs, tmp_path, monkeypatch):
        """A busca por CONTEUDO nao pode depender de haver task ativa.

        Medido em 2026-09-09, no segundo ramo real: `branch not found`. O filtro
        `if not task_id: continue` descartava todo candidato cujo `state.json`
        nao tivesse task, e o laco nunca chegava a perguntar aos bancos QUEM
        conhece o ramo. A linha estava em disco, com `status=open`, e o CLI
        dizia que ela nao existia.

        O agravante e que o ciclo de vida normal PRODUZ essa condicao: o ramo,
        ao terminar, expira a task travada da mae — ou seja, um ramo que faz o
        seu trabalho remove o `task_id` de que o resolvedor dependia para
        fecha-lo depois.

        A pista `parent_session` continua apontando para a mae, que tem a linha
        — o que se removeu foi so a exigencia de task ativa. Varredura proibida:
        o que este teste prova e que o candidato SEM task passa a ser
        consultado, nao que exista uma rede depois dele.
        """
        _proibir_varredura(bs, monkeypatch)
        _active_transaction(bs, tmp_path, session_id="sessao-mae")
        b = bs.add(cwd=str(tmp_path), name="Ramo", topic="x",
                   parent_session="sessao-mae")
        semente = tmp_path / "semente.md"
        semente.write_text("# semente", encoding="utf-8")
        bs.set_status(cwd=str(tmp_path), slug=b["slug"], status="open",
                      seed_path=str(semente))

        # A task da mae expira e o `state.json` dela perde o `task_id`; o do
        # projeto nunca teve. O sensor aponta para a janela do ramo, que e uma
        # sessao sem bucket nenhum.
        mae = bs.harness_paths.ensure_state_dir(cwd=str(tmp_path), session_id="sessao-mae")
        (mae / "state.json").write_text(
            json.dumps({"task_id": None, "status": "idle"}), encoding="utf-8"
        )
        projeto = bs.harness_paths.ensure_state_dir(cwd=str(tmp_path))
        (projeto / "state.json").write_text(
            json.dumps({"task_id": None, "status": "idle"}), encoding="utf-8"
        )
        (projeto / "branch-sensor.json").write_text(
            json.dumps({"session_id": "sessao-filha-sem-bucket", "turn": 12}),
            encoding="utf-8",
        )

        fechado = bs.set_status(
            cwd=str(tmp_path), slug=b["slug"], status="closed",
            conclusion="fechou pelo CLI, sem task ativa em lugar nenhum",
        )
        assert fechado["status"] == "closed"
        # E o registro transacional acompanhou: fechar so no JSON deixaria o
        # banco dizendo `open` para sempre, que e o estado que este ramo
        # precisou desfazer a mao.
        assert bs.HarnessDatabase(mae).branch(b["session_id"])["status"] == "closed"

    def test_ramo_fecha_quando_nenhum_ponteiro_aponta_para_o_banco_dono(self, bs, tmp_path):
        """As pistas apontam para tres sessoes; o ramo esta numa quarta.

        Medido em 2026-09-09, quando este proprio ramo tentou se fechar pelo
        CLI depois de a busca por conteudo passar a rodar: falhou de novo. A
        lista de candidatos e montada de duas pistas — o `session_id` do
        `branch-sensor.json`, sobrescrito por quem rodou por ultimo, e o
        `parent_session` de `branches.json`, que e do ARQUIVO e nao do ramo:
        quem escreve primeiro fica, entao o segundo ramo do projeto herda o
        ponteiro do primeiro. Nenhuma das duas precisa apontar para a sessao
        que criou o ramo, e quando nenhuma aponta o banco dono fica fora da
        lista — busca por conteudo sobre candidatos que nao incluem o dono nao
        e busca por conteudo.

        Por isso, esgotadas as pistas, os buckets de sessao do projeto sao
        varridos. So no caminho de erro: o custo fica onde ja se ia falhar.

        O contador de `sweep` e parte do teste, nao enfeite: sem ele a assercao
        de resultado ficaria verde tambem quando alguma pista resolvesse por
        acaso, e a varredura poderia sair da cobertura sem a suite mudar de cor
        (medido em 2026-09-09).
        """
        _active_transaction(bs, tmp_path, session_id="sessao-criadora")
        # `parent_session` de branches.json aponta para OUTRA sessao, como faz
        # o segundo ramo de qualquer projeto.
        b = bs.add(cwd=str(tmp_path), name="Ramo", topic="x",
                   parent_session="sessao-antiga")
        semente = tmp_path / "semente.md"
        semente.write_text("# semente", encoding="utf-8")
        bs.set_status(cwd=str(tmp_path), slug=b["slug"], status="open",
                      seed_path=str(semente))
        # Forma de registro legado: sem ponteiro proprio, so o campo do arquivo.
        _ramo_legado(bs, tmp_path, b["slug"])

        # A sessao apontada existe e tem banco proprio, mas nao conhece o ramo;
        # e o sensor ja migrou para uma terceira.
        _active_transaction(bs, tmp_path, session_id="sessao-antiga")
        _active_transaction(bs, tmp_path, session_id="sessao-atual")

        antes = _sweeps(bs)
        fechado = bs.set_status(
            cwd=str(tmp_path), slug=b["slug"], status="closed",
            conclusion="fechou pelo CLI com todos os ponteiros errados",
        )
        assert fechado["status"] == "closed"
        criadora = bs.harness_paths.ensure_state_dir(
            cwd=str(tmp_path), session_id="sessao-criadora"
        )
        assert bs.HarnessDatabase(criadora).branch(b["session_id"])["status"] == "closed"
        assert _sweeps(bs) == antes + 1, "fechou, mas nao foi a varredura que achou"

    def test_add_grava_a_mae_no_proprio_ramo(self, bs, tmp_path):
        """A mae e propriedade do RAMO, nao do arquivo.

        `parent_session` no topo de `branches.json` e escrito uma vez — quem
        escreve primeiro fica (`add`, guarda `if not data.get(...)`). Do segundo
        ramo do projeto em diante ele nomeia a sessao errada, e tres leitores
        acreditam nele: o resolvedor, o parking e o indice de sessoes.
        """
        _active_transaction(bs, tmp_path, session_id="mae-a")
        b = bs.add(cwd=str(tmp_path), name="Ramo", topic="x", parent_session="mae-a")
        registro = bs.get(cwd=str(tmp_path), slug=b["slug"])
        assert registro["parent_session_id"] == "mae-a"
        # O campo do arquivo continua escrito: e o fallback do registro legado,
        # e a garantia de que reverter nao exige limpar dado.
        assert bs.load(cwd=str(tmp_path))["parent_session"] == "mae-a"

    def test_segundo_ramo_do_projeto_resolve_pela_propria_mae(self, bs, tmp_path, monkeypatch):
        """O ponteiro herdado do primeiro ramo nao pode responder pelo segundo.

        Medido em `RSL_Project-aa99cfb0` (2026-09-09): o arquivo dizia
        `49fde96d` para um ramo criado por `1f008ff6`. Aqui o cenario e o mesmo,
        reduzido: mae-A cria o primeiro ramo e fixa o campo do arquivo; mae-B
        cria o segundo; o sensor migra para uma terceira sessao. A unica pista
        que pode achar o banco de mae-B e o campo do PROPRIO ramo.

        Varredura proibida de proposito — ela e a rede, e o que este teste
        precisa provar e que o fio existe.
        """
        _proibir_varredura(bs, monkeypatch)
        _active_transaction(bs, tmp_path, session_id="mae-a")
        bs.add(cwd=str(tmp_path), name="Primeiro", topic="tema a", parent_session="mae-a")

        _active_transaction(bs, tmp_path, session_id="mae-b")
        segundo = bs.add(cwd=str(tmp_path), name="Segundo", topic="tema b",
                         parent_session="mae-b")
        assert bs.load(cwd=str(tmp_path))["parent_session"] == "mae-a", (
            "premissa: o campo do arquivo continua sendo o do primeiro ramo"
        )

        _active_transaction(bs, tmp_path, session_id="sessao-atual")

        fechado = bs.set_status(
            cwd=str(tmp_path), slug=segundo["slug"], status="closed",
            conclusion="fechou pela propria mae, nao pela do primeiro ramo",
        )
        assert fechado["status"] == "closed"
        mae_b = bs.harness_paths.ensure_state_dir(cwd=str(tmp_path), session_id="mae-b")
        assert bs.HarnessDatabase(mae_b).branch(segundo["session_id"])["status"] == "closed"

    def test_ramo_legado_ainda_responde_pelo_campo_do_arquivo(self, bs, tmp_path, monkeypatch):
        """Registro gravado pelo codigo antigo nao tem ponteiro proprio.

        O fallback e o que impede que a mudanca transforme todo ramo ja em disco
        em orfao. Sem ele, `.get("parent_session_id")` devolve `None` e o
        resolvedor perde a unica pista que aquele registro tem.
        """
        _proibir_varredura(bs, monkeypatch)
        _active_transaction(bs, tmp_path, session_id="mae-a")
        b = bs.add(cwd=str(tmp_path), name="Ramo", topic="x", parent_session="mae-a")
        _ramo_legado(bs, tmp_path, b["slug"])
        assert "parent_session_id" not in bs.get(cwd=str(tmp_path), slug=b["slug"])

        _active_transaction(bs, tmp_path, session_id="sessao-atual")

        fechado = bs.set_status(cwd=str(tmp_path), slug=b["slug"], status="closed",
                                conclusion="legado fechou pelo campo do arquivo")
        assert fechado["status"] == "closed"

    def test_attach_files_resolve_o_banco_pela_mesma_pista(self, bs, tmp_path, monkeypatch):
        """`attach_files` nao tinha UM teste, e e o caminho real de abrir ramo.

        O unico chamador em producao e `branch_seed.py`, quando escreve semente
        e launcher. A funcao passa `parent_session` a `_transaction_context`
        exatamente como `set_status`, e esse call site ia ser editado sem rede
        nenhuma — descoberto na medicao de cobertura de 2026-09-09.
        """
        _proibir_varredura(bs, monkeypatch)
        _active_transaction(bs, tmp_path, session_id="sessao-mae")
        b = bs.add(cwd=str(tmp_path), name="Ramo", topic="x",
                   parent_session="sessao-mae")
        semente = tmp_path / "semente.md"
        launcher = tmp_path / "launcher.ps1"
        semente.write_text("# semente", encoding="utf-8")
        launcher.write_text("# launcher", encoding="utf-8")

        # O sensor migra: a pista que sobra e o `parent_session` do registro.
        _active_transaction(bs, tmp_path, session_id="sessao-filha")

        ligado = bs.attach_files(
            cwd=str(tmp_path), slug=b["slug"],
            seed_path=str(semente), launcher_path=str(launcher),
        )
        assert ligado["seed_path"] == str(semente)
        assert ligado["launcher_path"] == str(launcher)
        # E o registro transacional recebeu a semente, nao so o JSON.
        mae = bs.harness_paths.ensure_state_dir(cwd=str(tmp_path), session_id="sessao-mae")
        assert bs.HarnessDatabase(mae).branch(b["session_id"])["seed_path"] == str(semente)

    def test_decide_park_resolve_o_banco_pela_mesma_pista(self, bs, tmp_path, monkeypatch):
        """`decide` so era exercitado com `parent_session` nulo.

        `test_descarte_e_explicito` nao tem transacao nenhuma — o bloco
        transacional inteiro e pulado. Com mae registrada, a pista precisa
        levar `decide` ao banco certo, e "agora nao" tem de PARKEAR: o ramo
        continua no registro.
        """
        _proibir_varredura(bs, monkeypatch)
        _active_transaction(bs, tmp_path, session_id="sessao-mae")
        b = bs.add(cwd=str(tmp_path), name="Ramo", topic="x",
                   parent_session="sessao-mae")

        _active_transaction(bs, tmp_path, session_id="sessao-filha")

        parkeado = bs.decide(cwd=str(tmp_path), slug=b["slug"], decision="park")
        assert parkeado["slug"] == b["slug"]
        # Recusar parkeia; so `discard` apaga.
        assert [r["slug"] for r in bs.load(cwd=str(tmp_path))["branches"]] == [b["slug"]]
        mae = bs.harness_paths.ensure_state_dir(cwd=str(tmp_path), session_id="sessao-mae")
        assert bs.HarnessDatabase(mae).branch(b["session_id"])["status"] == "pending"

    def test_ramo_ausente_de_todo_banco_continua_falhando_alto(self, bs, tmp_path):
        """O fallback existe para o erro vir do banco, nao de um `None` calado.

        A busca por conteudo nao pode transformar "ramo realmente inexistente"
        em sucesso silencioso: enquanto houver um candidato com task ativa, um
        `branch_id` que banco nenhum conhece tem de levantar.
        """
        _active_transaction(bs, tmp_path, session_id="sessao-mae")
        b = bs.add(cwd=str(tmp_path), name="Ramo", topic="x",
                   parent_session="sessao-mae")
        semente = tmp_path / "semente.md"
        semente.write_text("# semente", encoding="utf-8")

        dados = bs.load(cwd=str(tmp_path))
        dados["branches"][0]["session_id"] = "uuid-que-banco-nenhum-conhece"
        bs.save(dados, cwd=str(tmp_path))

        with pytest.raises(ValueError, match="branch not found"):
            bs.set_status(cwd=str(tmp_path), slug=b["slug"], status="open",
                          seed_path=str(semente))


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

    def test_fluxo_publico_aplica_gate_e_limite_transacionais(self, bs, tmp_path, monkeypatch):
        monkeypatch.setenv("HARNESS_BRANCH_MAX_OPEN", "1")
        monkeypatch.setenv("HARNESS_BRANCH_MAX_OFFERS", "3")
        database, task = _active_transaction(bs, tmp_path)

        first = bs.add(
            cwd=str(tmp_path), name="Primeiro", topic="tema um", origin_turn=10
        )
        second = bs.add(
            cwd=str(tmp_path), name="Segundo", topic="tema dois", origin_turn=18
        )

        assert database.branch(first["session_id"])["approved_at"] is None
        assert database.task(task["task_id"])["pending_gate"].startswith("branch-open:")

        opened = bs.set_status(
            cwd=str(tmp_path),
            slug=first["slug"],
            status="open",
            seed_path="first.seed.md",
            launcher_path="first.launch.ps1",
        )
        assert opened["status"] == "open"
        assert database.branch(first["session_id"])["status"] == "open"

        with pytest.raises(ValueError, match="open branch limit"):
            bs.set_status(
                cwd=str(tmp_path),
                slug=second["slug"],
                status="open",
                seed_path="second.seed.md",
            )
        assert bs.get(cwd=str(tmp_path), slug=second["slug"])["status"] == "pending"

    def test_fluxo_publico_aplica_cooldown_transacional(self, bs, tmp_path, monkeypatch):
        monkeypatch.setenv("HARNESS_BRANCH_MAX_OFFERS", "3")
        monkeypatch.setenv("HARNESS_BRANCH_COOLDOWN_TURNS", "8")
        _active_transaction(bs, tmp_path)

        bs.add(cwd=str(tmp_path), name="Primeiro", topic="tema um", origin_turn=10)
        with pytest.raises(ValueError, match="cooldown"):
            bs.add(cwd=str(tmp_path), name="Cedo", topic="tema cedo", origin_turn=12)
        assert [branch["name"] for branch in bs.load(cwd=str(tmp_path))["branches"]] == [
            "Primeiro"
        ]

    def test_park_resolve_gate_e_abertura_posterior_cria_nova_aprovacao(self, bs, tmp_path):
        database, task = _active_transaction(bs, tmp_path)
        branch = bs.add(
            cwd=str(tmp_path), name="Depois", topic="tema futuro", origin_turn=10
        )

        parked = bs.decide(cwd=str(tmp_path), slug=branch["slug"], decision="park")

        assert parked["status"] == "pending"
        assert database.task(task["task_id"])["pending_gate"] is None

        opened = bs.set_status(
            cwd=str(tmp_path),
            slug=branch["slug"],
            status="open",
            seed_path="later.seed.md",
        )
        assert opened["status"] == "open"
        assert database.branch(branch["session_id"])["approved_at"] is not None


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
        configured = Path(os.environ.get("HARNESS_PLUGIN_ROOT", ""))
        if (configured / "scripts" / "branch_state.py").exists():
            return configured
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
        # O campo do RAMO e quem manda; o do arquivo sobrevive como fallback de
        # registro legado. Assertar so o de arquivo deixava a camada inteira
        # poder ser esquecida sem a suite mudar de cor (medido 2026-09-09).
        assert registro["branches"][0]["parent_session_id"] == "sessao-mae-uuid"
        assert registro["parent_session"] == "sessao-mae-uuid"

    def test_sem_parent_session_o_ramo_fica_orfao(self, tmp_path):
        """Documenta o custo de omitir a flag, para o teste acima ter contraste.

        E o lugar onde a decisao de D1 fica VISIVEL em teste: o campo por ramo
        guarda a mae DECLARADA, entao omitir a flag deixa os dois nulos. Se
        algum dia ele passar a guardar o bucket de disco, este teste fica
        vermelho — que e o comportamento correto para uma troca dessas.
        """
        projeto = tmp_path / "proj"
        projeto.mkdir()
        proc = self._rodar(["add", "--name", "Ramo Solto", "--topic", "tema qualquer"],
                           projeto, tmp_path / "h")
        assert proc.returncode == 0, proc.stderr
        registro = json.loads(
            list((tmp_path / "h").rglob("branches.json"))[0].read_text(encoding="utf-8"))
        assert registro["parent_session"] is None
        assert registro["branches"][0]["parent_session_id"] is None


class TestConclusaoVoltaParaAMae:
    """O ramo existe para tirar um assunto do pai — mas o resultado tem que voltar.

    Sem isso, ramificar vira PERDER o assunto em vez de organiza-lo: a proxima
    vez que alguem tocar no tema na conversa pai comeca do zero, e o ramo virou
    um buraco em vez de uma gaveta.

    Entrega UMA vez. Reinjetar a cada turno transformaria a conclusao no ruido
    de fundo que o proprio parking existe para evitar.
    """

    def _ramo_fechado(self, bs, cwd, conclusao="o piso 0.55 nunca vetava nada"):
        b = bs.add(cwd=str(cwd), name="Calibrar Piso",
                      topic="calibrar os pisos do sensor de ramo")
        bs.set_status(cwd=str(cwd), slug=b["slug"], status="closed",
                         conclusion=conclusao)
        return b

    def test_conclusao_aparece_no_bloco(self, bs, tmp_path):
        self._ramo_fechado(bs, tmp_path)
        bloco = bs.parked_block(str(tmp_path))
        assert "FECHOU" in bloco and "0.55 nunca vetava" in bloco

    def test_entrega_uma_vez_so(self, bs, tmp_path):
        self._ramo_fechado(bs, tmp_path)
        assert "FECHOU" in bs.parked_block(str(tmp_path))
        assert "FECHOU" not in bs.parked_block(str(tmp_path))

    def test_ramo_fechado_sem_conclusao_nao_entrega(self, bs, tmp_path):
        b = bs.add(cwd=str(tmp_path), name="Sem Nada", topic="tema qualquer")
        bs.set_status(cwd=str(tmp_path), slug=b["slug"], status="closed")
        assert "FECHOU" not in bs.parked_block(str(tmp_path))

    def test_conclusao_longa_e_truncada(self, bs, tmp_path):
        self._ramo_fechado(bs, tmp_path, conclusao="x" * 900)
        bloco = bs.parked_block(str(tmp_path))
        assert len(bloco) < 600 and "..." in bloco

    def test_sem_ramo_nenhum_o_bloco_e_vazio(self, bs, tmp_path):
        assert bs.parked_block(str(tmp_path)) == ""


class TestRegistroEPorProjeto:
    """`branches.json` fica no bucket do PROJETO, nunca no da sessao.

    O escopo por sessao (`projects/<slug>/sessions/<uuid>/`) e certo para o
    pipeline SDD, e errado para o parking: se cada sessao tivesse o seu
    registro, a conversa pai nao veria o ramo que ela mesma abriu no turno
    anterior, e o parking — que existe para atravessar sessoes — deixaria de
    funcionar em silencio.

    Hoje `branch_sensor` chama `state_dir(cwd=cwd)` sem `session_id` e o
    caminho sai certo. Este teste existe para que propagar o `session_id` para
    ca vire vermelho, e nao uma regressao invisivel.
    """

    def test_caminho_ignora_session_id(self, bs, tmp_path):
        import harness_paths

        sem = harness_paths.state_dir(cwd=str(tmp_path))
        com = harness_paths.state_dir(cwd=str(tmp_path), session_id="uma-sessao-qualquer")
        assert str(bs.branches_path(str(tmp_path))).startswith(str(sem))
        assert str(sem) != str(com), "premissa do teste: o session_id muda o bucket"


class TestParkingSoFalaComAMae:
    """O parking e endereçado a mae, e o ramo roda no mesmo diretorio dela.

    `parked_block` so recebia `cwd`. O ramo nasce com `Set-Location` no mesmo
    repo, entao lia o bloco da mae — inclusive a linha "NAO desenvolver aqui"
    sobre o proprio tema que ele existe para desenvolver — e, pior, consumia a
    entrega unica da conclusao: `_marcar_entregues` roda em quem le primeiro.

    Medido em 2026-09-04, no primeiro ramo real: as tres emissoes `parked` do
    `emissions.jsonl` saíram com o session_id do RAMO (5c54af66), a mae
    (7249629f) nunca recebeu, e `conclusion_delivered` ja estava `true`. A
    conclusao do ramo morreu no proprio ramo.
    """

    def _com_conclusao(self, bs, tmp_path, parent):
        b = bs.add(cwd=str(tmp_path), name="Ramo", topic="tema do ramo",
                   parent_session=parent)
        bs.set_status(cwd=str(tmp_path), slug=b["slug"], status="open")
        bs.set_status(cwd=str(tmp_path), slug=b["slug"], status="closed",
                      conclusion="a hipotese morreu")
        return b

    def test_ramo_nao_recebe_o_parking_da_mae(self, bs, tmp_path):
        mae = "11111111-2222-3333-4444-555555555555"
        filho = "99999999-8888-7777-6666-555555555555"
        b = bs.add(cwd=str(tmp_path), name="Ramo", topic="tema do ramo",
                   parent_session=mae)
        bs.set_status(cwd=str(tmp_path), slug=b["slug"], status="open")

        assert bs.parked_block(cwd=str(tmp_path), session_id=filho) == ""
        assert "tema do ramo" in bs.parked_block(cwd=str(tmp_path), session_id=mae)

    def test_leitura_do_ramo_nao_consome_a_entrega_da_conclusao(self, bs, tmp_path):
        mae = "11111111-2222-3333-4444-555555555555"
        filho = "99999999-8888-7777-6666-555555555555"
        b = self._com_conclusao(bs, tmp_path, mae)

        assert bs.parked_block(cwd=str(tmp_path), session_id=filho) == ""
        entregue = bs.parked_block(cwd=str(tmp_path), session_id=mae)
        assert "a hipotese morreu" in entregue, "a mae precisa receber a conclusao"
        assert bs.parked_block(cwd=str(tmp_path), session_id=mae) == "", "entrega e unica"

    def test_sem_session_id_o_comportamento_nao_muda(self, bs, tmp_path):
        """Chamador que nao sabe quem e continua recebendo — degradar, nao bloquear."""
        b = bs.add(cwd=str(tmp_path), name="Ramo", topic="tema do ramo",
                   parent_session="11111111-2222-3333-4444-555555555555")
        bs.set_status(cwd=str(tmp_path), slug=b["slug"], status="open")
        assert "tema do ramo" in bs.parked_block(cwd=str(tmp_path))


class TestLockNaoDerrubaOLockAlheio:
    """`_Lock` e fail-open, e ate 2026-09-09 o fail-open contaminava terceiros.

    `__enter__` devolvia `self` no timeout SEM ter adquirido, e `__exit__` fazia
    `os.rmdir` incondicional. Duas sessoes dividem um `branches.json` — o
    caminho vem de `state_dir(cwd)` sem `session_id` —, entao a que furou o lock
    apagava o lockdir da que o tinha, e uma TERCEIRA entrava enquanto a primeira
    ainda escrevia. O fail-open nao degradava a exclusao mutua so para quem
    furou: quebrava para todo mundo.

    A escrita seguinte e `save()`, que reescreve o documento INTEIRO. O que se
    perde nao e um campo: e a arvore de ramos de quem carregou primeiro. E `add`
    ja commitou `create_branch` no sqlite antes do `save`, entao o ramo some do
    JSON e PERMANECE no banco — e `can_open` conta pelo banco. Tres fantasmas e
    `may_offer` devolve `max_open` para sempre: o Branch Keeper cala em
    definitivo, com aparencia de funcionamento normal.

    Nao havia um unico teste de `_Lock`, de fail-open ou de escrita concorrente.
    """

    def _lock_ocupado(self, bs, tmp_path, monkeypatch):
        """Segura o lock de fora e encurta o timeout para o teste nao dormir."""
        monkeypatch.setattr(bs, "LOCK_TIMEOUT_S", 0.05)
        monkeypatch.setattr(bs, "LOCK_STALE_S", 3600)
        alvo = bs.branches_path(str(tmp_path))
        Path(alvo).parent.mkdir(parents=True, exist_ok=True)
        lockdir = Path(alvo + ".lockdir")
        lockdir.mkdir()
        return lockdir

    def test_quem_nao_adquiriu_nao_remove_o_lockdir(self, bs, tmp_path, monkeypatch):
        lockdir = self._lock_ocupado(bs, tmp_path, monkeypatch)
        alvo = bs.branches_path(str(tmp_path))

        with bs._Lock(alvo) as segundo:
            assert segundo.owned is False, "furou o lock e acha que e dono"
        assert lockdir.is_dir(), "quem furou o lock apagou o lockdir do dono"

    def test_quem_adquiriu_remove_ao_sair(self, bs, tmp_path, monkeypatch):
        monkeypatch.setattr(bs, "LOCK_TIMEOUT_S", 0.05)
        alvo = bs.branches_path(str(tmp_path))
        Path(alvo).parent.mkdir(parents=True, exist_ok=True)

        with bs._Lock(alvo) as dono:
            assert dono.owned is True
            assert Path(alvo + ".lockdir").is_dir()
        assert not Path(alvo + ".lockdir").exists()

    def test_escrita_aborta_em_vez_de_sobrescrever(self, bs, tmp_path, monkeypatch):
        """Ramo nao registrado e recuperavel; ramo fantasma no teto nao e."""
        bs.add(cwd=str(tmp_path), name="Ja Existia", topic="tema anterior")
        self._lock_ocupado(bs, tmp_path, monkeypatch)

        with pytest.raises(bs.LockUnavailable):
            bs.add(cwd=str(tmp_path), name="Concorrente", topic="tema novo")

        # A arvore de quem estava la continua intacta.
        nomes = [b["name"] for b in bs.load(cwd=str(tmp_path))["branches"]]
        assert nomes == ["Ja Existia"]

    def test_leitura_nao_e_bloqueada_por_lock_ocupado(self, bs, tmp_path, monkeypatch):
        """`load` e `parked_block` rodam no caminho quente e nao podem travar."""
        b = bs.add(cwd=str(tmp_path), name="Ramo", topic="tema do ramo")
        bs.set_status(cwd=str(tmp_path), slug=b["slug"], status="open")
        self._lock_ocupado(bs, tmp_path, monkeypatch)

        assert bs.load(cwd=str(tmp_path))["branches"], "leitura travou por lock alheio"
        assert "tema do ramo" in bs.parked_block(cwd=str(tmp_path))

    def test_marcar_entregues_desiste_em_silencio(self, bs, tmp_path, monkeypatch):
        """Perder a marca custa uma repeticao; sobrescrever custa a arvore.

        `_marcar_entregues` nunca levanta, entao a recusa do lock vira "nao
        marquei" — e a conclusao e entregue duas vezes em vez de o registro ser
        clobbered. E o custo que a docstring dela ja declarava aceitavel.
        """
        b = bs.add(cwd=str(tmp_path), name="Ramo", topic="x")
        bs.set_status(cwd=str(tmp_path), slug=b["slug"], status="closed",
                      conclusion="a hipotese morreu")
        self._lock_ocupado(bs, tmp_path, monkeypatch)

        assert "a hipotese morreu" in bs.parked_block(cwd=str(tmp_path))
        registro = bs.get(cwd=str(tmp_path), slug=b["slug"])
        assert not registro.get("conclusion_delivered"), "marcou apesar do lock ocupado"


class TestParkingComDuasMaes:
    """Duas maes no mesmo projeto — o estado que `add` tornava impossivel.

    Enquanto a mae era um campo do ARQUIVO, escrito uma vez, nenhuma fixture da
    suite conseguia montar dois ramos de maes diferentes. Com um ramo so, campo
    de arquivo e campo de ramo carregam o mesmo valor, e os testes de parking
    passavam com qualquer implementacao — inclusive com uma errada.

    E o estado que a Fase 1 passou a permitir, e que o parking precisa entender:
    cada sessao ve os ramos que ELA abriu, e a conclusao volta para quem perdeu
    o assunto.
    """

    MAE_A = "aaaaaaaa-1111-2222-3333-444444444444"
    MAE_B = "bbbbbbbb-1111-2222-3333-444444444444"

    def _duas_maes(self, bs, tmp_path):
        a = bs.add(cwd=str(tmp_path), name="Ramo de A", topic="tema da mae A",
                   parent_session=self.MAE_A)
        b = bs.add(cwd=str(tmp_path), name="Ramo de B", topic="tema da mae B",
                   parent_session=self.MAE_B)
        for ramo in (a, b):
            bs.set_status(cwd=str(tmp_path), slug=ramo["slug"], status="open")
        return a, b

    def test_cada_mae_ve_so_o_proprio_ramo(self, bs, tmp_path):
        self._duas_maes(bs, tmp_path)

        bloco_a = bs.parked_block(cwd=str(tmp_path), session_id=self.MAE_A)
        bloco_b = bs.parked_block(cwd=str(tmp_path), session_id=self.MAE_B)

        assert "tema da mae A" in bloco_a and "tema da mae B" not in bloco_a
        assert "tema da mae B" in bloco_b and "tema da mae A" not in bloco_b

    def test_a_conclusao_de_uma_mae_nao_e_consumida_pela_outra(self, bs, tmp_path):
        """A entrega e UNICA — entregar para a mae errada mata a conclusao.

        `_marcar_entregues` marca por slug, sem olhar mae. Filtrar so a lista de
        ramos vivos e deixar a de conclusoes sem filtro reproduz, mais sutil, o
        bug de 2026-09-04: quem le primeiro queima a entrega do outro.
        """
        a, b = self._duas_maes(bs, tmp_path)
        for ramo, texto in ((a, "A mediu e reprovou"), (b, "B achou outra causa")):
            bs.set_status(cwd=str(tmp_path), slug=ramo["slug"], status="closed",
                          conclusion=texto)

        bloco_a = bs.parked_block(cwd=str(tmp_path), session_id=self.MAE_A)
        assert "A mediu e reprovou" in bloco_a
        assert "B achou outra causa" not in bloco_a

        bloco_b = bs.parked_block(cwd=str(tmp_path), session_id=self.MAE_B)
        assert "B achou outra causa" in bloco_b, "a leitura de A queimou a entrega de B"

    def test_ramo_orfao_continua_visivel_para_qualquer_sessao(self, bs, tmp_path):
        """Sem mae registrada, calar seria PERDER o ramo, nao proteger ninguem.

        `add` sem `--parent-session` e caminho suportado, e registro gravado pelo
        codigo antigo tambem chega aqui sem ponteiro. Um filtro ingenuo
        (`origem == session_id`) faz o orfao sumir de TODO bloco assim que o
        chamador se identifica — e em producao o sensor sempre se identifica.
        Perda silenciosa no caminho quente, que e o pior modo de falha deste
        modulo.
        """
        b = bs.add(cwd=str(tmp_path), name="Ramo Solto", topic="tema sem mae")
        bs.set_status(cwd=str(tmp_path), slug=b["slug"], status="open")

        assert "tema sem mae" in bs.parked_block(cwd=str(tmp_path), session_id=self.MAE_A)
        assert "tema sem mae" in bs.parked_block(cwd=str(tmp_path), session_id=self.MAE_B)
        assert "tema sem mae" in bs.parked_block(cwd=str(tmp_path))

    def test_ramo_legado_responde_a_mae_do_arquivo(self, bs, tmp_path):
        """Registro antigo nao tem ponteiro proprio — o fallback e quem responde."""
        b = bs.add(cwd=str(tmp_path), name="Legado", topic="tema legado",
                   parent_session=self.MAE_A)
        bs.set_status(cwd=str(tmp_path), slug=b["slug"], status="open")
        _ramo_legado(bs, tmp_path, b["slug"])

        assert "tema legado" in bs.parked_block(cwd=str(tmp_path), session_id=self.MAE_A)
        assert bs.parked_block(cwd=str(tmp_path), session_id=self.MAE_B) == ""
