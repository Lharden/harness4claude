import os
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
