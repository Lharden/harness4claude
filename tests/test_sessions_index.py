"""Testes do indice de sessoes — busca cross-sessao.

O que esta travado aqui, e por que:

- **O recorte e a decisao principal.** 96% do byte dos transcripts e
  `attachment`, `tool_use`, `tool_result` e sidechain de subagente: rastro de
  ferramenta, nao memoria de conversa. Indexar tudo custaria 40x mais e afogaria
  a busca no proprio boilerplate do harness. Os testes fixam o recorte porque
  afrouxa-lo por descuido e barato e o custo so aparece na qualidade da busca.
- **Fatiar, nunca truncar.** A primeira versao truncava o par de turno em 1200
  chars e indexou 615 KB de 5,7 MB — 89% descartado em silencio, justamente os
  turnos longos, que sao as specs e os planos.
- **Dedupe por sessao, nao por chunk.** Cinco chunks da mesma conversa no topo
  sao uma resposta, e devolver a mesma sessao cinco vezes gastaria o top-k
  inteiro.
- **Degradar, nunca levantar.** Indice ausente, Ollama fora, jsonl torto: a
  busca responde "nao disponivel". Uma ferramenta de memoria que quebra a
  sessao e pior que a ausencia dela.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])


def _load(nome: str, relativo: str):
    caminho = ROOT / relativo
    spec = importlib.util.spec_from_file_location(nome, caminho)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[nome] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def builder():
    return _load("build_sessions_index", "scripts/build_sessions_index.py")


@pytest.fixture(scope="module")
def emit():
    return _load("harness_emit_sess", "hooks/emit.py")


@pytest.fixture(scope="module")
def lifecycle():
    return _load("harness_lifecycle_sess", "hooks/harness-lifecycle.py")


@pytest.fixture(scope="module")
def sq():
    return _load("session_query", "tools/session_query.py")


def _transcript(dir_projeto: Path, session_id: str, turnos, *, titulo=None,
                sidechain=False, ts="2026-09-01T12:00:00.000Z"):
    """Escreve um jsonl no formato real do Claude Code."""
    dir_projeto.mkdir(parents=True, exist_ok=True)
    linhas = []
    if titulo:
        linhas.append({"type": "ai-title", "aiTitle": titulo, "sessionId": session_id})
    for prompt, resposta in turnos:
        linhas.append({
            "type": "user", "timestamp": ts, "cwd": str(dir_projeto),
            "isSidechain": sidechain,
            "message": {"content": [{"type": "text", "text": prompt}]},
        })
        if resposta is not None:
            linhas.append({
                "type": "assistant", "timestamp": ts, "cwd": str(dir_projeto),
                "isSidechain": sidechain,
                "message": {"content": [{"type": "text", "text": resposta}]},
            })
    caminho = dir_projeto / f"{session_id}.jsonl"
    caminho.write_text(
        "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in linhas),
        encoding="utf-8")
    return caminho


def _turnos(n, prefixo="assunto"):
    return [(f"{prefixo} pergunta numero {i} com texto suficiente para passar o minimo",
             f"{prefixo} resposta numero {i} com texto suficiente para passar o minimo")
            for i in range(n)]


class TestRecorte:
    def test_sessao_curta_nao_entra(self, builder, tmp_path):
        """One-shot nao e memoria: 255 das 343 sessoes reais tem 1 turno."""
        _transcript(tmp_path / "proj", "s-curta", _turnos(2))
        assert builder.parse_session(str(tmp_path / "proj" / "s-curta.jsonl")) is None

    def test_sessao_com_tres_turnos_entra(self, builder, tmp_path):
        p = _transcript(tmp_path / "proj", "s-ok", _turnos(3))
        assert builder.parse_session(str(p))["n_turns"] == 3

    def test_sidechain_e_ignorada(self, builder, tmp_path):
        """Contexto de subagente nao e memoria da conversa."""
        p = _transcript(tmp_path / "proj", "s-side", _turnos(5), sidechain=True)
        assert builder.parse_session(str(p)) is None

    def test_boilerplate_do_harness_nao_entra(self, builder, tmp_path):
        """Sem isto o cosseno mede o texto que o harness injeta, nao o assunto."""
        turnos = _turnos(3) + [
            ("<harness-classification> task_id: t-1 level: L2 e mais texto aqui", "ok"),
            ("[skill-hint] Skills possivelmente relevantes ranqueadas e tal", "ok"),
        ]
        p = _transcript(tmp_path / "proj", "s-ruido", turnos)
        sessao = builder.parse_session(str(p))
        corpo = " ".join(t["prompt"] for t in sessao["turns"])
        assert "harness-classification" not in corpo
        assert "skill-hint" not in corpo
        assert sessao["n_turns"] == 3

    def test_turno_curto_nao_entra(self, builder, tmp_path):
        turnos = _turnos(3) + [("ok", "ok")]
        p = _transcript(tmp_path / "proj", "s-curto", turnos)
        assert builder.parse_session(str(p))["n_turns"] == 3

    def test_janela_temporal_corta(self, builder, tmp_path):
        p = _transcript(tmp_path / "proj", "s-velha", _turnos(4),
                        ts="2020-01-01T00:00:00.000Z")
        import time

        corte = time.time() - 90 * 86400
        assert builder.parse_session(str(p), corte_epoch=corte) is None
        assert builder.parse_session(str(p), corte_epoch=0.0) is not None

    def test_jsonl_torto_nao_derruba(self, builder, tmp_path):
        d = tmp_path / "proj"
        d.mkdir(parents=True)
        (d / "s-torta.jsonl").write_text("{nao e json}\n\n{\"type\":\"user\"}\n",
                                         encoding="utf-8")
        assert builder.parse_session(str(d / "s-torta.jsonl")) is None

    def test_so_transcripts_da_raiz(self, builder, tmp_path):
        """761 arquivos de subagente vivem em subdiretorios e ficam de fora."""
        _transcript(tmp_path / "proj", "s-raiz", _turnos(3))
        sub = tmp_path / "proj" / "s-raiz" / "subagents"
        sub.mkdir(parents=True)
        (sub / "agent-x.jsonl").write_text("{}\n", encoding="utf-8")
        achados = builder.session_files(str(tmp_path))
        assert len(achados) == 1 and achados[0].endswith("s-raiz.jsonl")


class TestFatiamento:
    def test_turno_longo_vira_varios_chunks(self, builder, tmp_path):
        """Truncar descartaria 89% do conteudo — medido na primeira versao."""
        longo = "palavra " * 900  # ~7200 chars
        p = _transcript(tmp_path / "proj", "s-longa",
                        _turnos(2) + [(longo, "resposta curta mas suficiente aqui")])
        chunks = builder.session_chunks(builder.parse_session(str(p)))
        do_turno = [c for c in chunks if c["turn"] == 2]
        assert len(do_turno) > 1, "turno longo foi truncado em vez de fatiado"
        assert sum(len(c["description"]) for c in do_turno) > 5000

    def test_nenhum_chunk_passa_do_teto(self, builder, tmp_path):
        p = _transcript(tmp_path / "proj", "s-teto",
                        _turnos(2) + [("x " * 3000, "y " * 3000)])
        for c in builder.session_chunks(builder.parse_session(str(p))):
            assert len(c["description"]) <= builder.CHUNK_CHARS

    def test_chunk_carrega_o_endereco_da_sessao(self, builder, tmp_path):
        p = _transcript(tmp_path / "proj", "s-addr", _turnos(3), titulo="Titulo Real")
        c = builder.session_chunks(builder.parse_session(str(p)))[0]
        assert c["session_id"] == "s-addr"
        assert c["title"] == "Titulo Real"
        assert c["short_ref"] in c["aliases"]
        # contrato do skill_router
        assert {"id", "name", "description", "aliases", "usage_count"} <= set(c)


class TestCatalogo:
    def test_uma_linha_por_sessao_sem_vetor(self, builder, tmp_path):
        _transcript(tmp_path / "proj", "s-a", _turnos(3))
        _transcript(tmp_path / "proj", "s-b", _turnos(4))
        sessoes, _ = builder.scan_sessions(str(tmp_path), days=0)
        destino = tmp_path / "catalog.json"
        assert builder.build_catalog(sessoes, str(destino)) == 2
        dados = json.loads(destino.read_text(encoding="utf-8"))
        assert len(dados["sessions"]) == 2
        for linha in dados["sessions"]:
            assert "embedding" not in linha and "vec_row" not in linha
            assert linha["first_prompt"] and linha["n_turns"] >= 3


class TestBusca:
    def test_indice_ausente_degrada(self, sq, tmp_path):
        r = sq.query("qualquer coisa", index_dir=tmp_path / "nao-existe")
        assert r["available"] is False and r["hits"] == []

    def test_render_de_indice_ausente_nao_quebra(self, sq, tmp_path):
        texto = sq.render(sq.query("x", index_dir=tmp_path / "nada"))
        assert isinstance(texto, str) and texto

    def test_dedupe_por_sessao(self, sq):
        hits = [
            {"id": "a#0", "skill": {"session_id": "a"}},
            {"id": "a#1", "skill": {"session_id": "a"}},
            {"id": "b#0", "skill": {"session_id": "b"}},
        ]
        assert [h["id"] for h in sq.dedupe_by_session(hits, 5)] == ["a#0", "b#0"]

    def test_dedupe_respeita_top_k(self, sq):
        hits = [{"id": f"{s}#0", "skill": {"session_id": s}} for s in "abcdef"]
        assert len(sq.dedupe_by_session(hits, 3)) == 3

    def test_recent_sem_catalogo_devolve_vazio(self, sq, tmp_path):
        assert sq.recent("/qualquer", 3, tmp_path / "nao-existe.json") == []

    def test_recent_filtra_por_cwd(self, sq, tmp_path):
        cat = tmp_path / "cat.json"
        cat.write_text(json.dumps({"sessions": [
            {"short_ref": "aaa", "title": "t1", "cwd": "C:/x/projeto-a",
             "started_at": "2026-09-01", "n_turns": 5},
            {"short_ref": "bbb", "title": "t2", "cwd": "C:/x/projeto-b",
             "started_at": "2026-09-02", "n_turns": 4},
        ]}), encoding="utf-8")
        achados = sq.recent("C:/x/projeto-a", 3, cat)
        assert len(achados) == 1 and achados[0]["short_ref"] == "aaa"

    def test_pisos_declaram_que_nao_foram_calibrados(self):
        """Herdar numero do wiki sem medir foi o erro que deixou o piso do
        branch-sensor decorativo por meses. Aqui fica escrito."""
        fonte = (ROOT / "tools" / "session_query.py").read_text(encoding="utf-8")
        assert "NAO calibrado" in fonte or "nao calibrados" in fonte


class TestSessoesVivas:
    """"Sessoes vivas" resolvido por batimento em disco, nao por protocolo.

    Medido em 2026-09-01: das 61 sessoes peer que o `ListAgents` enumera, TODAS
    estavam offline ou idle. Construir troca de mensagens entre elas seria
    construir para um caso que nao acontece. Um arquivo por sessao com
    `last_seen` responde a pergunta real com um `ls`.
    """

    def test_flush_deixa_batimento(self, emit, tmp_path):
        import io as _io

        em = emit.Emitter("UserPromptSubmit", hook="t", session_id="s-viva",
                          cwd="C:/proj", root=tmp_path)
        em.add("k", "qualquer coisa").flush(stream=_io.StringIO())
        assert (tmp_path / "live" / "s-viva.json").exists()

    def test_sem_session_id_nao_grava(self, emit, tmp_path):
        import io as _io

        emit.Emitter("UserPromptSubmit", hook="t", root=tmp_path) \
            .add("k", "x").flush(stream=_io.StringIO())
        assert not (tmp_path / "live").exists()

    def test_batimento_velho_nao_conta_como_viva(self, emit, tmp_path):
        d = tmp_path / "live"
        d.mkdir(parents=True)
        (d / "antiga.json").write_text(json.dumps({
            "session_id": "antiga", "last_seen": "2020-01-01T00:00:00+00:00"}),
            encoding="utf-8")
        assert emit.live_sessions(tmp_path, max_age_s=600) == []

    def test_batimento_recente_aparece(self, emit, tmp_path):
        import io as _io

        emit.Emitter("UserPromptSubmit", hook="t", session_id="agora",
                     cwd="C:/proj", root=tmp_path).add("k", "x").flush(stream=_io.StringIO())
        vivas = emit.live_sessions(tmp_path, max_age_s=600)
        assert len(vivas) == 1 and vivas[0]["session_id"] == "agora"

    def test_arquivo_corrompido_nao_quebra(self, emit, tmp_path):
        d = tmp_path / "live"
        d.mkdir(parents=True)
        (d / "torta.json").write_text("{nao e json}", encoding="utf-8")
        assert emit.live_sessions(tmp_path) == []


class TestFechamentoDeSessao:
    def test_sessionend_marca_indice_sujo(self, lifecycle, tmp_path):
        lifecycle._fechar_sessao(
            {"session_id": "s-1", "cwd": "C:/proj/alvo"}, tmp_path)
        assert (tmp_path / "sessions-index" / ".stale").exists()

    def test_sessionend_escreve_cartao_no_vault(self, lifecycle, tmp_path, monkeypatch):
        """`wiki/sessions/` estava vazio desde sempre: o caminho projetado
        (traces -> vault_sync) exige rotacao acima de 50 KB e o maior trace em
        disco tem 3,3 KB. Escreve-se direto no destino."""
        vault = tmp_path / "vault" / "AI-Brain"
        (vault / "wiki").mkdir(parents=True)
        monkeypatch.setenv("AI_BRAIN_PATH", str(vault))
        lifecycle._fechar_sessao(
            {"session_id": "abc12345-0000-0000-0000-000000000000",
             "cwd": "C:/proj/meu-projeto"}, tmp_path)
        cartoes = list((vault / "wiki" / "sessions").glob("*.md"))
        assert len(cartoes) == 1
        texto = cartoes[0].read_text(encoding="utf-8")
        assert "type: session" in texto
        assert "claude --resume abc12345-0000-0000-0000-000000000000" in texto

    def test_sem_session_id_nao_faz_nada(self, lifecycle, tmp_path):
        lifecycle._fechar_sessao({"cwd": "C:/x"}, tmp_path)
        assert not (tmp_path / "sessions-index").exists()

    def test_vault_ausente_nao_quebra(self, lifecycle, tmp_path, monkeypatch):
        monkeypatch.setenv("AI_BRAIN_PATH", str(tmp_path / "nao-existe"))
        lifecycle._fechar_sessao({"session_id": "s-2", "cwd": "C:/x"}, tmp_path)
        assert (tmp_path / "sessions-index" / ".stale").exists()

    def test_sessionend_expira_pipeline_abandonado_de_outro_projeto(
            self, lifecycle, tmp_path):
        """A faxina alcanca bucket que ninguem visita — que e o caso que importa.

        `expire_stale_pipeline` roda sobre UM bucket, e so quando alguem abre
        sessao naquele diretorio. Projeto abandonado nunca recebe a visita, e o
        TTL de 24h vira decorativo justamente ali. Medido em 2026-09-02: 20
        pipelines vencidos na maquina, o mais velho com cinco semanas.
        """
        from datetime import datetime, timedelta, timezone

        d = tmp_path / "projects" / "abandonado"
        d.mkdir(parents=True)
        velho = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
        (d / "state.json").write_text(json.dumps({
            "task_id": "t-velha", "schema_version": 3, "classification": "L1-feature",
            "status": "active", "pipeline": ["tdd"], "current_step": "tdd",
            "artifacts_so_far": [], "started_at": velho,
        }), encoding="utf-8")

        lifecycle._fechar_sessao({"session_id": "s-1", "cwd": "C:/outro/projeto"}, tmp_path)

        estado = json.loads((d / "state.json").read_text(encoding="utf-8"))
        assert estado["status"] != "active"

    def test_sessionend_nao_expira_pipeline_fresco_alheio(self, lifecycle, tmp_path):
        """Faxina que destroi trabalho em andamento de outra janela e pior que sujeira."""
        from datetime import datetime, timezone

        d = tmp_path / "projects" / "em-andamento"
        d.mkdir(parents=True)
        (d / "state.json").write_text(json.dumps({
            "task_id": "t-viva", "schema_version": 3, "classification": "L2-feature",
            "status": "active", "pipeline": ["tdd"], "current_step": "tdd",
            "artifacts_so_far": [], "started_at": datetime.now(timezone.utc).isoformat(),
        }), encoding="utf-8")

        lifecycle._fechar_sessao({"session_id": "s-2", "cwd": "C:/outro"}, tmp_path)

        assert json.loads((d / "state.json").read_text(encoding="utf-8"))["status"] == "active"
