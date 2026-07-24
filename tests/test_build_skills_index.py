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
