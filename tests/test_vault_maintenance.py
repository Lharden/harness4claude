from pathlib import Path

from tools.vault_maintenance import (
    accentuate_prose,
    apply_text_normalization,
    audit_vault,
    find_broken_wikilinks,
    normalize_markdown,
    organize_notes,
    repair_mojibake,
    update_wikilinks,
)

# Texto que a normalizacao muda: tres linhas em branco viram uma, e "usuario" ganha acento.
NORMALIZAVEL = "# T\n\n\n\nusuario\n"


def test_repair_mojibake_restores_common_portuguese_characters() -> None:
    text = "20:36â€“20:43 â€” revisÃ£o tÃ©cnica"

    assert repair_mojibake(text) == "20:36–20:43 — revisão técnica"


def test_repair_mojibake_restores_symbols_greek_and_emoji() -> None:
    text = "Î·6 âœ“; 12â†’19; Â§1; OlÃ¡! ðŸ‘‹"

    assert repair_mojibake(text) == "η6 ✓; 12→19; §1; Olá! 👋"


def test_normalize_markdown_preserves_fenced_code() -> None:
    text = "#Titulo  \r\n\r\n\t-Item  \r\n\r\n\r\n\r\n```python\r\nx = '  '\r\n```\r\n"

    assert normalize_markdown(text) == (
        "# Titulo\n\n    - Item\n\n```python\nx = '  '\n```\n"
    )


def test_normalize_markdown_preserves_blank_lines_inside_fenced_code() -> None:
    text = "```text\nlinha 1\n\n\n\nlinha 2\n```\n\n\n\nFim\n"

    assert normalize_markdown(text) == ("```text\nlinha 1\n\n\n\nlinha 2\n```\n\nFim\n")


def test_normalize_markdown_preserves_and_repairs_bold_lines() -> None:
    text = (
        "**Resumo**\n"
        "* *Seção corrompida**\n"
        "* *Objetivo:** texto após o rótulo.\n"
        "*Item\n"
    )

    assert normalize_markdown(text) == (
        "**Resumo**\n**Seção corrompida**\n**Objetivo:** texto após o rótulo.\n* Item\n"
    )


def test_normalize_markdown_preserves_and_repairs_obsidian_tags() -> None:
    text = "#Titulo\n#tag\n# tag-principal #outra-tag\n"

    assert normalize_markdown(text) == "# Titulo\n#tag\n#tag-principal #outra-tag\n"


def test_accentuate_prose_does_not_change_code_urls_or_wikilink_targets() -> None:
    text = (
        "Indice com validacao e decisoes. "
        "`validacao` [fonte](https://example.com/validacao) "
        "[[Decisoes Confirmadas Timeline|decisoes confirmadas]]."
    )

    assert accentuate_prose(text) == (
        "Índice com validação e decisões. "
        "`validacao` [fonte](https://example.com/validacao) "
        "[[Decisoes Confirmadas Timeline|decisões confirmadas]]."
    )


def test_update_wikilinks_preserves_alias_heading_and_embed() -> None:
    text = (
        "[[Decisoes Confirmadas Timeline]] "
        "[[Decisoes Confirmadas Timeline#Resumo|linha do tempo]] "
        "![[00 Indice|Índice]]"
    )
    renames = {
        "Decisoes Confirmadas Timeline": "Decisões Confirmadas - Linha do Tempo",
        "00 Indice": "00 Índice",
    }

    assert update_wikilinks(text, renames) == (
        "[[Decisões Confirmadas - Linha do Tempo]] "
        "[[Decisões Confirmadas - Linha do Tempo#Resumo|linha do tempo]] "
        "![[00 Índice|Índice]]"
    )


def test_find_broken_wikilinks_resolves_paths_basenames_and_aliases(
    tmp_path: Path,
) -> None:
    (tmp_path / "Área").mkdir()
    (tmp_path / "Área" / "Nota Existente.md").write_text(
        "# Nota Existente\n", encoding="utf-8"
    )
    (tmp_path / "Origem.md").write_text(
        "[[Nota Existente]] [[Área/Nota Existente|ok]] [[Nota Ausente]]\n",
        encoding="utf-8",
    )

    assert find_broken_wikilinks(tmp_path) == {
        "Origem.md": ["Nota Ausente"],
    }


def test_organize_notes_moves_files_and_updates_wikilinks(tmp_path: Path) -> None:
    (tmp_path / "Origem").mkdir()
    (tmp_path / "Origem" / "Indice.md").write_text("# Indice\n", encoding="utf-8")
    (tmp_path / "Home.md").write_text(
        "[[Indice]] [[Origem/Indice|abrir]]\n", encoding="utf-8"
    )

    result = organize_notes(
        tmp_path,
        {"Origem/Indice.md": "Destino/Índice.md"},
    )

    assert result == {"moved_notes": 1, "updated_link_sources": 1}
    assert not (tmp_path / "Origem" / "Indice.md").exists()
    assert (tmp_path / "Destino" / "Índice.md").exists()
    assert (tmp_path / "Home.md").read_text(encoding="utf-8") == (
        "[[Índice]] [[Destino/Índice|abrir]]\n"
    )


def test_organize_notes_updates_shared_basename_when_destinations_agree(
    tmp_path: Path,
) -> None:
    for folder in ("A", "B"):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "Indice.md").write_text("# Indice\n", encoding="utf-8")
    (tmp_path / "Home.md").write_text("[[Indice]]\n", encoding="utf-8")

    organize_notes(
        tmp_path,
        {
            "A/Indice.md": "A/Índice.md",
            "B/Indice.md": "B/Índice.md",
        },
    )

    assert (tmp_path / "Home.md").read_text(encoding="utf-8") == "[[Índice]]\n"


def test_organize_notes_is_idempotent_for_case_only_rename(tmp_path: Path) -> None:
    (tmp_path / "Titulo.md").write_text("# Titulo\n", encoding="utf-8")
    renames = {"Titulo.md": "titulo.md"}

    first = organize_notes(tmp_path, renames)
    second = organize_notes(tmp_path, renames)

    assert first["moved_notes"] == 1
    assert second["moved_notes"] == 0
    assert [path.name for path in tmp_path.glob("*.md")] == ["titulo.md"]


def test_audit_vault_reports_duplicates_empty_mojibake_and_formatting(
    tmp_path: Path,
) -> None:
    (tmp_path / "A.md").write_text("# A\n", encoding="utf-8")
    (tmp_path / "B.md").write_text("# A\n", encoding="utf-8")
    (tmp_path / "Vazia.md").write_text("", encoding="utf-8")
    (tmp_path / "Mojibake.md").write_text("OlÃ¡!", encoding="utf-8")

    report = audit_vault(tmp_path)

    assert report["notes"] == 4
    assert report["duplicate_groups"] == 1
    assert report["duplicate_notes"] == 2
    assert report["empty_notes"] == ["Vazia.md"]
    assert report["mojibake_sources"] == ["Mojibake.md"]
    assert report["formatting_issue_sources"] == ["Mojibake.md", "Vazia.md"]


# --- paginas espelhadas pelo vault_sync ficam fora da manutencao -------------
#
# Desde 2026-09-24 o vault_sync guarda o hash do que escreveu e RECUSA pagina que
# mudou depois. Uma pagina espelhada normalizada aqui deixaria de receber a fonte.
# A exclusao e por destino exato: `wiki/decisions` mistura `<slug>-context.md`
# (espelho do CONTEXT.md) com decisoes escritas no vault, e `raw/inbox` recebe
# `today-*.md` do sync e notas humanas.


def _nota(root: Path, relativo: str, texto: str = NORMALIZAVEL) -> Path:
    caminho = root / relativo
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(texto, encoding="utf-8")
    return caminho


def test_normalize_nao_toca_pagina_espelhada(tmp_path: Path) -> None:
    """A lista de espelhos e do vault_sync (a paridade com o que ele escreve e travada
    em test_vault_sync.py); aqui, a traducao do caminho a partir da raiz do vault."""
    espelhadas = [
        _nota(tmp_path, "AI-Brain/wiki/specs/x-spec.md"),
        _nota(tmp_path, "AI-Brain/wiki/decisions/projeto-x-context.md"),
        _nota(tmp_path, "AI-Brain/raw/inbox/today-2026-08-06.done.md"),
    ]
    humanas = [
        _nota(tmp_path, "AI-Brain/wiki/decisions/decisao-humana.md"),
        _nota(tmp_path, "AI-Brain/raw/inbox/Bem-vindo ao Obsidian.md"),
        _nota(tmp_path, "wiki/specs/fora-do-ai-brain.md"),
        _nota(tmp_path, "Notas/Comum.md"),
    ]

    resultado = apply_text_normalization(tmp_path)

    assert [p.read_text(encoding="utf-8") for p in espelhadas] == [NORMALIZAVEL] * 3
    assert all(p.read_text(encoding="utf-8") != NORMALIZAVEL for p in humanas)
    assert resultado["mirrored_skipped"] == 3


def test_organize_nao_reescreve_link_dentro_de_pagina_espelhada(tmp_path: Path) -> None:
    _nota(tmp_path, "Origem/Indice.md", "# Indice\n")
    espelho = _nota(tmp_path, "AI-Brain/wiki/specs/x-spec.md", "[[Indice]]\n")
    comum = _nota(tmp_path, "Home.md", "[[Indice]]\n")

    organize_notes(tmp_path, {"Origem/Indice.md": "Destino/Índice.md"})

    assert espelho.read_text(encoding="utf-8") == "[[Indice]]\n"
    assert comum.read_text(encoding="utf-8") == "[[Índice]]\n"
