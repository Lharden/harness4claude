"""Testes do wiki_lint — health check read-only da wiki AI-Brain."""

import os
import time
from pathlib import Path

from tools.wiki_lint import (
    analyze_wiki,
    has_frontmatter,
    inbox_redundant_pairs,
    parse_wikilinks,
    stray_wiki_tree,
)

FRONTMATTER = """---
type: concept
created: 2026-01-01
updated: 2026-01-01
status: active
tags: [test]
---

"""


def write_page(root: Path, rel: str, body: str = "", *, frontmatter: bool = True) -> Path:
    """Cria uma página em wiki/ com frontmatter válido por padrão."""
    path = root / "wiki" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text((FRONTMATTER if frontmatter else "") + body, encoding="utf-8")
    return path


def write_index(root: Path, links: list[str]) -> None:
    """Escreve wiki/index.md apontando para cada link informado."""
    body = "# Index\n" + "\n".join(f"- [[{link}]]" for link in links) + "\n"
    write_page(root, "index.md", body)


def create_clean_vault(root: Path) -> None:
    """Vault mínimo sem nenhum finding: 2 páginas, ambas no index, interligadas."""
    write_page(root, "concepts/sdd.md", "Ver [[entities/leonardo]].")
    write_page(root, "entities/leonardo.md", "Ver [[concepts/sdd]].")
    write_index(root, ["concepts/sdd", "entities/leonardo"])
    (root / "raw" / "inbox").mkdir(parents=True, exist_ok=True)


def age_file(path: Path, days: int) -> None:
    """Envelhece o mtime de um arquivo em N dias."""
    stamp = time.time() - days * 86400
    os.utime(path, (stamp, stamp))


# --- helpers puros --------------------------------------------------------


def test_has_frontmatter_requires_leading_delimiter(tmp_path: Path) -> None:
    with_fm = tmp_path / "a.md"
    with_fm.write_text(FRONTMATTER + "corpo", encoding="utf-8")
    without_fm = tmp_path / "b.md"
    without_fm.write_text("# Titulo\n\ncorpo", encoding="utf-8")

    assert has_frontmatter(with_fm) is True
    assert has_frontmatter(without_fm) is False


def test_parse_wikilinks_strips_alias_and_anchor() -> None:
    text = "veja [[concepts/sdd|SDD]] e [[entities/leonardo#perfil]] e [[projects/x]]"

    assert parse_wikilinks(text) == ["concepts/sdd", "entities/leonardo", "projects/x"]


def test_parse_wikilinks_ignores_placeholders() -> None:
    text = "exemplo: [[concepts/...]], [[page-name]], [[link]], [[None]]"

    assert parse_wikilinks(text) == []


def test_inbox_redundant_pairs_detects_done_duplicates(tmp_path: Path) -> None:
    inbox = tmp_path / "raw" / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "today-2026-06-12.md").write_text("x", encoding="utf-8")
    (inbox / "today-2026-06-12.done.md").write_text("x", encoding="utf-8")
    (inbox / "today-2026-06-13.done.md").write_text("x", encoding="utf-8")

    assert inbox_redundant_pairs(tmp_path) == ["today-2026-06-12.md"]


def test_stray_wiki_tree_matches_only_the_bug_signature(tmp_path: Path) -> None:
    ai_brain = tmp_path / "AI-Brain"
    ai_brain.mkdir()
    unrelated = tmp_path / "wiki"
    unrelated.mkdir()
    (unrelated / "notas.md").write_text("x", encoding="utf-8")

    assert stray_wiki_tree(ai_brain) is False

    (unrelated / "log.md").write_text("x", encoding="utf-8")

    assert stray_wiki_tree(ai_brain) is True


# --- analyze_wiki ---------------------------------------------------------


def test_clean_vault_is_ready(tmp_path: Path) -> None:
    create_clean_vault(tmp_path)

    result = analyze_wiki(tmp_path)

    assert result["ready"] is True
    assert result["summary"]["error_count"] == 0
    assert result["summary"]["warning_count"] == 0


def test_missing_frontmatter_is_an_error(tmp_path: Path) -> None:
    create_clean_vault(tmp_path)
    write_page(tmp_path, "specs/spec-a.md", "Ver [[concepts/sdd]].", frontmatter=False)
    write_index(tmp_path, ["concepts/sdd", "entities/leonardo", "specs/spec-a"])

    result = analyze_wiki(tmp_path)

    assert result["ready"] is False
    assert result["summary"]["missing_frontmatter"] == ["specs/spec-a.md"]


def test_broken_wikilink_is_an_error(tmp_path: Path) -> None:
    create_clean_vault(tmp_path)
    write_page(tmp_path, "concepts/sdd.md", "Ver [[entities/fantasma]].")

    result = analyze_wiki(tmp_path)

    assert result["ready"] is False
    assert "fantasma" in result["summary"]["broken_wikilinks"]


def test_broken_wikilink_dedupes_relative_and_absolute_forms(tmp_path: Path) -> None:
    create_clean_vault(tmp_path)
    write_page(tmp_path, "concepts/sdd.md", "Ver [[projects/fantasma]].")
    write_page(tmp_path, "entities/leonardo.md", "Ver [[../projects/fantasma]].")

    result = analyze_wiki(tmp_path)

    assert result["summary"]["broken_wikilinks"] == ["fantasma"]


def test_wikilink_resolves_by_basename_and_relative_path(tmp_path: Path) -> None:
    create_clean_vault(tmp_path)
    write_page(tmp_path, "projects/a.md", "Ver [[sdd]] e [[../entities/leonardo]].")
    write_index(tmp_path, ["concepts/sdd", "entities/leonardo", "projects/a"])

    result = analyze_wiki(tmp_path)

    assert result["summary"]["broken_wikilinks"] == []


def test_graph_notes_resolve_as_targets_but_are_not_linted(tmp_path: Path) -> None:
    create_clean_vault(tmp_path)
    write_page(tmp_path, "graphs/repo/_COMMUNITY_x.md", "nó", frontmatter=False)
    write_page(tmp_path, "concepts/sdd.md", "Ver [[graphs/repo/_COMMUNITY_x]].")

    result = analyze_wiki(tmp_path)

    assert result["summary"]["broken_wikilinks"] == []
    assert result["summary"]["missing_frontmatter"] == []
    assert result["summary"]["graph_notes"] == 1


def test_orphan_page_is_a_warning(tmp_path: Path) -> None:
    create_clean_vault(tmp_path)
    write_page(tmp_path, "concepts/solta.md", "sem in-link")
    write_index(tmp_path, ["concepts/sdd", "entities/leonardo", "concepts/solta"])

    result = analyze_wiki(tmp_path)

    # in-link vindo do index nao conta como conexao real de conteudo
    assert result["summary"]["orphan_pages"] == ["concepts/solta.md"]
    assert result["summary"]["error_count"] == 0


def test_stale_page_needs_both_age_and_no_inlink(tmp_path: Path) -> None:
    create_clean_vault(tmp_path)
    linked = write_page(tmp_path, "concepts/velha-linkada.md", "antiga")
    write_page(tmp_path, "concepts/sdd.md", "Ver [[concepts/velha-linkada]].")
    orphan = write_page(tmp_path, "concepts/velha-solta.md", "antiga")
    write_index(
        tmp_path,
        ["concepts/sdd", "entities/leonardo", "concepts/velha-linkada", "concepts/velha-solta"],
    )
    age_file(linked, 200)
    age_file(orphan, 200)

    result = analyze_wiki(tmp_path, stale_days=90)

    assert result["summary"]["stale_pages"] == ["concepts/velha-solta.md"]


def test_index_drift_reports_both_directions(tmp_path: Path) -> None:
    create_clean_vault(tmp_path)
    write_page(tmp_path, "ops/runbook.md", "Ver [[concepts/sdd]].")
    write_index(tmp_path, ["concepts/sdd", "entities/leonardo", "projects/inexistente"])

    result = analyze_wiki(tmp_path)

    assert result["ready"] is False
    assert result["summary"]["pages_missing_from_index"] == ["ops/runbook.md"]
    assert result["summary"]["index_phantom_entries"] == ["projects/inexistente"]


def test_inbox_and_stray_tree_reach_the_findings(tmp_path: Path) -> None:
    vault_root = tmp_path / "vault"
    ai_brain = vault_root / "AI-Brain"
    create_clean_vault(ai_brain)
    inbox = ai_brain / "raw" / "inbox"
    (inbox / "today-2026-06-12.md").write_text("x", encoding="utf-8")
    (inbox / "today-2026-06-12.done.md").write_text("x", encoding="utf-8")
    stray = vault_root / "wiki"
    stray.mkdir(parents=True)
    (stray / "log.md").write_text("x", encoding="utf-8")

    result = analyze_wiki(ai_brain)

    checks = {finding["check"] for finding in result["findings"]}
    assert "inbox_redundant" in checks
    assert "stray_wiki_tree" in checks
    assert result["ready"] is False


def test_analyze_wiki_never_writes(tmp_path: Path) -> None:
    create_clean_vault(tmp_path)
    page = tmp_path / "wiki" / "concepts" / "sdd.md"
    age_file(page, 5)
    before = page.stat().st_mtime

    analyze_wiki(tmp_path)

    assert page.stat().st_mtime == before
