import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import build_skills_index as bsi


def test_frontmatter_simple():
    text = "---\nname: graphify\ndescription: builds knowledge graphs\n---\n# corpo"
    fm = bsi.parse_frontmatter(text)
    assert fm["name"] == "graphify"
    assert fm["description"] == "builds knowledge graphs"


def test_frontmatter_quoted():
    text = '---\nname: "write-spec"\ndescription: \'Gera specs: formais\'\n---\n'
    fm = bsi.parse_frontmatter(text)
    assert fm["name"] == "write-spec"
    assert fm["description"] == "Gera specs: formais"


def test_frontmatter_folded_block():
    text = (
        "---\nname: dataviz\ndescription: >-\n"
        "  Use this skill whenever you create charts.\n"
        "  Read it BEFORE writing chart code.\n---\n"
    )
    fm = bsi.parse_frontmatter(text)
    assert fm["description"] == (
        "Use this skill whenever you create charts. Read it BEFORE writing chart code."
    )


def test_frontmatter_missing():
    assert bsi.parse_frontmatter("# sem frontmatter") == {}
    assert bsi.parse_frontmatter("---\nmetadata:\n  x: 1\n---\n") == {}


import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


class _FakeOllama(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        vecs = [[1.0, 2.0, 2.0] for _ in body["input"]]  # norma 3 -> [1/3,2/3,2/3]
        out = json.dumps({"embeddings": vecs}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


def _mk_skill(root, plugin_dirname, skill, desc):
    d = os.path.join(root, plugin_dirname, "skills", skill)
    os.makedirs(d)
    with open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write(f"---\nname: {skill}\ndescription: {desc}\n---\n# x\n")
    return os.path.join(root, plugin_dirname)


def test_scan_skills(tmp_path, monkeypatch):
    root = str(tmp_path)
    p1 = _mk_skill(root, "alpha", "alpha-skill", "faz coisas alpha")
    p2 = _mk_skill(root, "beta", "beta-skill", "faz coisas beta")
    personal = os.path.join(root, "personal")
    os.makedirs(os.path.join(personal, "meu-skill"))
    with open(os.path.join(personal, "meu-skill", "SKILL.md"), "w", encoding="utf-8") as f:
        f.write("---\nname: meu-skill\ndescription: pessoal\n---\n")

    installed = os.path.join(root, "installed_plugins.json")
    with open(installed, "w", encoding="utf-8") as f:
        json.dump({"plugins": {
            "alpha@mkt": [{"installPath": p1}],
            "beta@mkt": [{"installPath": p2}],
            "gone@mkt": [{"installPath": os.path.join(root, "nao-existe")}],
        }}, f)
    settings = os.path.join(root, "settings.json")
    with open(settings, "w", encoding="utf-8") as f:
        json.dump({"enabledPlugins": {"alpha@mkt": True, "beta@mkt": False}}, f)
    cjson = os.path.join(root, "claude.json")
    with open(cjson, "w", encoding="utf-8") as f:
        json.dump({"skillUsage": {"alpha:alpha-skill": {"usageCount": 7, "lastUsedAt": 123},
                                  "meu-skill": {"usageCount": 2, "lastUsedAt": 456}}}, f)
    monkeypatch.setattr(bsi, "ALIASES_JSON", os.path.join(root, "aliases.json"))
    with open(os.path.join(root, "aliases.json"), "w", encoding="utf-8") as f:
        json.dump({"alpha:alpha-skill": ["apelido"]}, f)

    skills = bsi.scan_skills(installed, settings, cjson, personal)
    by_id = {s["id"]: s for s in skills}
    assert set(by_id) == {"alpha:alpha-skill", "beta:beta-skill", "meu-skill"}
    assert by_id["alpha:alpha-skill"]["enabled"] is True
    assert by_id["alpha:alpha-skill"]["usage_count"] == 7
    assert by_id["alpha:alpha-skill"]["aliases"] == ["apelido"]
    assert by_id["beta:beta-skill"]["enabled"] is False
    assert by_id["meu-skill"]["source"] == "personal"
    assert by_id["meu-skill"]["enabled"] is True
    assert by_id["meu-skill"]["usage_count"] == 2
    assert all(s["vec_row"] == -1 for s in skills)
    assert [s["id"] for s in skills] == sorted(s["id"] for s in skills)


def _fake_skills(tmp_path):
    md = tmp_path / "SKILL.md"
    md.write_text("---\nname: x\ndescription: d\n---\n", encoding="utf-8")
    return [{"id": "p:x", "name": "x", "plugin": "p@m", "source": "marketplace",
             "enabled": True, "path": str(md), "description": "d", "desc_chars": 1,
             "aliases": [], "usage_count": 0, "last_used_at": None, "vec_row": -1}]


def test_build_no_embed_and_check_stale(tmp_path):
    skills = _fake_skills(tmp_path)
    out = str(tmp_path / "idx")
    n = bsi.build(out_dir=out, no_embed=True, skills=skills)
    assert n == 1
    idx = json.load(open(os.path.join(out, "skills-index.json"), encoding="utf-8"))
    meta = json.load(open(os.path.join(out, "meta.json"), encoding="utf-8"))
    assert idx["dim"] == 0 and idx["skills"][0]["vec_row"] == -1
    assert meta["count"] == 1
    assert os.path.getsize(os.path.join(out, "embeddings.f16.bin")) == 0
    # mesmo conteudo => fresco
    assert bsi.check_stale(out, skills=skills) is False
    # mudou o SKILL.md => stale
    p = skills[0]["path"]
    with open(p, "a", encoding="utf-8") as f:
        f.write("mudou\n")
    os.utime(p, (os.path.getmtime(p) + 5, os.path.getmtime(p) + 5))
    assert bsi.check_stale(out, skills=skills) is True


def test_check_stale_missing_index(tmp_path):
    assert bsi.check_stale(str(tmp_path / "nao-existe"), skills=[]) is True


def test_build_with_fake_embeddings(tmp_path, monkeypatch):
    srv = HTTPServer(("127.0.0.1", 0), _FakeOllama)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(bsi, "OLLAMA_URL", f"http://127.0.0.1:{srv.server_port}")
    try:
        skills = _fake_skills(tmp_path)
        out = str(tmp_path / "idx")
        assert bsi.build(out_dir=out, skills=skills) == 1
        idx = json.load(open(os.path.join(out, "skills-index.json"), encoding="utf-8"))
        assert idx["dim"] == 3 and idx["skills"][0]["vec_row"] == 0
        import struct as st
        raw = open(os.path.join(out, "embeddings.f16.bin"), "rb").read()
        v = st.unpack("<3e", raw)
        assert abs(v[0] - 1 / 3) < 1e-2 and abs(sum(x * x for x in v) - 1.0) < 1e-2
    finally:
        srv.shutdown()
