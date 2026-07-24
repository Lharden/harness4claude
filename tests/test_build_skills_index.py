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
