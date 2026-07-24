import io
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hooks"))
import skill_router as sr


def _skill(sid, enabled=True, usage=0, aliases=None):
    return {"id": sid, "name": sid.split(":")[-1], "plugin": f"{sid.split(':')[0]}@m",
            "source": "marketplace", "enabled": enabled, "path": "x", "description": f"desc {sid}",
            "desc_chars": 9, "aliases": aliases or [], "usage_count": usage,
            "last_used_at": None, "vec_row": -1}


def test_layer_a_name_and_alias():
    skills = [_skill("p:write-spec"), _skill("q:deck-maker", aliases=["deck"])]
    hits = sr.layer_a("quero um write-spec e um deck bonito", skills)
    assert {h["id"] for h in hits} == {"p:write-spec", "q:deck-maker"}
    assert all(h["score"] == 1.0 and h["layer"] == "A" for h in hits)
    # sem match parcial dentro de palavra
    assert sr.layer_a("undeckable", [_skill("q:deck-maker", aliases=["deck"])]) == []


def test_layer_b_scores_and_boost():
    skills = [_skill("a:x", usage=0), _skill("b:y", usage=50)]
    skills[0]["vec_row"], skills[1]["vec_row"] = 0, 1
    vecs = [(1.0, 0.0), (1.0, 0.0)]
    out = sr.layer_b((1.0, 0.0), skills, vecs)
    assert out[0]["id"] == "b:y"          # empate em cos, boost de uso decide
    assert abs(out[0]["cos"] - 1.0) < 1e-9
    assert out[0]["score"] > out[1]["score"]


def test_pick_thresholds_and_disabled_bar():
    def hit(sid, cos, enabled=True):
        return {"id": sid, "cos": cos, "score": cos, "layer": "B",
                "skill": _skill(sid, enabled=enabled)}
    # mediana dos 5 = 0.30; corte = max(MIN_COS, mediana+margem) na prática
    b = [hit("a:1", 0.80), hit("a:2", 0.50), hit("a:3", 0.30),
         hit("a:4", 0.20), hit("a:5", 0.10)]
    got = [h["id"] for h in sr.pick([], b)]
    assert got == ["a:1", "a:2"]          # 0.30 falha margem; 0.20/0.10 falham MIN_COS
    # desabilitada precisa de 0.60 E lead de 0.08 sobre a melhor habilitada
    b2 = [hit("on:1", 0.55), hit("off:1", 0.61, enabled=False), hit("off:2", 0.70, enabled=False)]
    got2 = [h["id"] for h in sr.pick([], b2)]
    assert "off:2" in got2 and "off:1" not in got2   # so 1 desabilitada, a melhor
    # camada A pinada na frente e sem duplicar
    a = [{"id": "a:1", "score": 1.0, "layer": "A", "skill": _skill("a:1")}]
    got3 = [h["id"] for h in sr.pick(a, b)]
    assert got3[0] == "a:1" and got3.count("a:1") == 1 and len(got3) <= sr.TOP_K


def test_guards(tmp_path):
    st = tmp_path / "state.json"
    assert sr.passes_guards("prompt curto", state_json=str(st)) is False  # 12 chars < 20
    assert sr.passes_guards("x" * 30001, state_json=str(st)) is False
    assert sr.passes_guards(
        "You are summarizing a Claude Code session for handoff purposes ok",
        state_json=str(st)) is False
    ok = "refatore o modulo de autenticacao por favor"
    assert sr.passes_guards(ok, state_json=str(st)) is True  # state ausente = ok
    st.write_text(json.dumps({"status": "active", "pipeline": ["tdd"]}), encoding="utf-8")
    assert sr.passes_guards(ok, state_json=str(st)) is False  # pipeline ativo suprime
    st.write_text("{{{lixo", encoding="utf-8")
    assert sr.passes_guards(ok, state_json=str(st)) is True   # torn read = sem pipeline


def test_dedupe(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "ROUTER_DIR", str(tmp_path))
    h = [{"id": "a:1", "score": 1.0, "layer": "A", "skill": _skill("a:1")}]
    assert [x["id"] for x in sr.apply_dedupe(list(h), "s1")] == ["a:1"]
    assert sr.apply_dedupe(list(h), "s1") == []            # mesmo conjunto consecutivo
    h2 = h + [{"id": "b:2", "score": 0.9, "layer": "B", "cos": 0.9,
               "skill": _skill("b:2")}]
    assert len(sr.apply_dedupe(list(h2), "s1")) == 2       # conjunto diferente, a:1 2a oferta
    # 4a chamada: a:1 estourou MAX_OFFERS_PER_SKILL (2 ofertas) e sai; sobra b:2
    assert [x["id"] for x in sr.apply_dedupe(list(h2), "s1")] == ["b:2"]


def test_render_hint_disabled_line():
    on = {"id": "a:1", "score": 1.0, "layer": "A", "skill": _skill("a:1")}
    off = {"id": "off:9", "score": 0.7, "cos": 0.7, "layer": "B",
           "skill": _skill("off:9", enabled=False)}
    text = sr.render_hint([on, off])
    assert text.startswith("[skill-hint]")
    assert "a:1" in text and "/plugin enable off" in text
    assert "ignore este bloco" in text


def test_main_gates_layer_b_on_layer_a_hit(tmp_path, monkeypatch, capsys):
    idx = {"skills": [_skill("p:deckmaker", aliases=["deck"])], "dim": 2}
    idx["skills"][0]["vec_row"] = 0
    monkeypatch.setattr(sr, "load_index", lambda *a, **k: (idx, [(1.0, 0.0)]))
    calls = []
    def _boom(prompt):
        calls.append(prompt)  # tracked out-of-band: main()'s except Exception
        raise AssertionError("embed_query must NOT run when Layer A hits")  # would swallow a bare raise
    monkeypatch.setattr(sr, "embed_query", _boom)
    monkeypatch.setattr(sr, "ROUTER_DIR", str(tmp_path))
    monkeypatch.setattr(sr, "passes_guards", lambda *a, **k: True)
    monkeypatch.setattr("sys.stdin",
                        io.StringIO(json.dumps({"session_id": "g", "prompt": "quero um deck bonito"})))
    rc = sr.main()
    out = capsys.readouterr().out
    assert rc == 0
    assert "p:deckmaker" in out
    assert calls == []  # embed_query genuinely not invoked (not just its exception swallowed)


def test_main_runs_layer_b_when_layer_a_empty(tmp_path, monkeypatch):
    idx = {"skills": [_skill("p:foo")], "dim": 2}
    idx["skills"][0]["vec_row"] = 0
    monkeypatch.setattr(sr, "load_index", lambda *a, **k: (idx, [(1.0, 0.0)]))
    called = {"embed": 0}
    def _fake_embed(prompt):
        called["embed"] += 1
        return [1.0, 0.0]
    monkeypatch.setattr(sr, "embed_query", _fake_embed)
    monkeypatch.setattr(sr, "ROUTER_DIR", str(tmp_path))
    monkeypatch.setattr(sr, "passes_guards", lambda *a, **k: True)
    monkeypatch.setattr("sys.stdin",
                        io.StringIO(json.dumps({"session_id": "h", "prompt": "algo totalmente novo aqui"})))
    sr.main()
    assert called["embed"] == 1


def test_main_subprocess_no_index(tmp_path):
    """Sem indice e sem state: stdout vazio, exit 0 (contrato nunca-falhar)."""
    env = dict(os.environ,
               HOME=str(tmp_path), USERPROFILE=str(tmp_path),
               HARNESS_SKILLS_INDEX=str(tmp_path / "nao-existe"))
    p = subprocess.run(
        [sys.executable, os.path.join(os.path.dirname(sr.__file__), "skill_router.py")],
        input=json.dumps({"session_id": "t", "prompt": "refatore o modulo de login"}),
        capture_output=True, text=True, env=env, timeout=30)
    assert p.returncode == 0 and p.stdout == ""
