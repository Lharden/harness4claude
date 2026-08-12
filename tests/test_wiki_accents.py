"""Testes do wiki_accents — restauração de acentos que não chuta.

A ferramenta reescreve prosa do usuário. O risco não e deixar de corrigir: e **corromper
texto já correto**. Estes testes travam as fronteiras — o que ela NÃO pode tocar pesa
mais que o que ela corrige.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from tools.wiki_accents import (
    AMBIGUAS,
    SEGURAS,
    fix_text,
    report_ambiguous,
    target_pages,
)

FM = "---\ntype: concept\ncreated: 2026-01-01\nupdated: 2026-01-01\nstatus: active\n---\n\n"


# --- dicionário explicito -------------------------------------------------


def test_troca_palavra_sem_homografo() -> None:
    novo, n = fix_text("A decisao nao foi tomada.")

    assert novo == "A decisão não foi tomada."
    assert n == 2


def test_preserva_caixa() -> None:
    novo, _ = fix_text("Nao e assim. NAO mesmo.")

    assert "Não e assim" in novo
    assert "NÃO mesmo" in novo


# --- regras morfologicas --------------------------------------------------


def test_sufixo_cobre_palavra_fora_do_dicionario() -> None:
    """O ponto das regras: cobrir o que ainda não foi escrito."""
    novo, n = fix_text("A consolidacao das operacoes e reversivel.")

    assert "consolidação" in novo
    assert "operações" in novo
    assert "reversível" in novo
    assert n == 3
    assert "consolidacao" not in SEGURAS  # nenhuma delas esta no dicionário


def test_sufixo_hiato_ui() -> None:
    novo, _ = fix_text("Texto reconstruido e distribuido.")

    assert "reconstruído" in novo
    assert "distribuído" in novo


def test_sufixo_com_excecao_fica_de_fora() -> None:
    """"-logia" e "-aria" tem palavra sem acento — sufixo com exceção não e regra."""
    novo, n = fix_text("A metodologia e a cronologia da maquinaria.")

    assert n == 0
    assert "metodologia" in novo


# --- o que NÃO pode ser tocado --------------------------------------------


def test_nao_toca_frontmatter() -> None:
    """`tags: [decisao]` e identificador; acentuar mudaria a tag."""
    texto = "---\ntipo: decisao\ntags: [decisao]\n---\n\nA decisao foi tomada.\n"

    novo, _ = fix_text(texto)

    assert "tipo: decisao" in novo
    assert "tags: [decisao]" in novo
    assert "A decisão foi tomada." in novo


def test_nao_toca_bloco_de_codigo() -> None:
    texto = "Prosa com decisao.\n\n```python\nversao = load_versao()\n```\n"

    novo, _ = fix_text(texto)

    assert "versao = load_versao()" in novo
    assert "Prosa com decisão." in novo


def test_nao_toca_codigo_inline_nem_wikilink() -> None:
    texto = "Ver [[decisions/assimilacoes-2026]] e rodar `build_versao.py`."

    novo, n = fix_text(texto)

    assert novo == texto
    assert n == 0


def test_nao_toca_url() -> None:
    texto = "Fonte: https://exemplo.com/decisao/operacao.html"

    novo, n = fix_text(texto)

    assert novo == texto
    assert n == 0


def test_nao_toca_caminho_de_arquivo() -> None:
    """Barra antes ou depois impede o match — caminho não e prosa."""
    novo, n = fix_text("Ver docs/decisao/versao.md no repo.")

    assert n == 0


# --- ambiguas: relata, nunca troca ----------------------------------------


def test_ambigua_nao_e_trocada() -> None:
    """"o lint valida" x "a regra e válida": trocar corromperia metade dos casos."""
    texto = "O lint valida a pagina. Esta critica publica."

    novo, _ = fix_text(texto)

    assert "valida" in novo and "válida" not in novo
    assert "critica" in novo and "crítica" not in novo


def test_ambigua_e_relatada_para_revisao() -> None:
    achadas = report_ambiguous("O lint valida e esta pratica so serve aqui.")

    assert set(achadas) >= {"valida", "esta", "pratica", "so"}


def test_ambigua_dentro_de_codigo_nao_e_relatada() -> None:
    assert report_ambiguous("`x = valida(y)`") == []


def test_listas_nao_se_sobrepoem() -> None:
    """Palavra em SEGURAS seria trocada; em AMBIGUAS, so relatada. Nas duas, contradição."""
    assert not (set(SEGURAS) & set(AMBIGUAS))


# --- fronteira de escopo --------------------------------------------------


def escrever(root: Path, rel: str) -> None:
    p = root / "wiki" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(FM + "A decisao foi tomada.\n", encoding="utf-8")


def test_espelhadas_ficam_de_fora_por_padrao(tmp_path: Path) -> None:
    """Corrigir copia espelhada a faria divergir da origem no repo."""
    escrever(tmp_path, "concepts/x.md")
    escrever(tmp_path, "specs/y.md")
    escrever(tmp_path, "sessions/z.md")

    alvos = [p.name for p in target_pages(tmp_path)]

    assert alvos == ["x.md"]


def test_flag_inclui_espelhadas(tmp_path: Path) -> None:
    escrever(tmp_path, "concepts/x.md")
    escrever(tmp_path, "specs/y.md")

    alvos = {p.name for p in target_pages(tmp_path, incluir_espelhadas=True)}

    assert alvos == {"x.md", "y.md"}


def test_subarvore_gerada_fica_de_fora(tmp_path: Path) -> None:
    escrever(tmp_path, "concepts/x.md")
    escrever(tmp_path, "graphs/repo/no.md")

    assert [p.name for p in target_pages(tmp_path)] == ["x.md"]


def test_idempotente(tmp_path: Path) -> None:
    """Rodar duas vezes não pode reacentuar o que já esta acentuado."""
    texto = "A decisao das operacoes nao e reversivel."

    uma, n1 = fix_text(texto)
    duas, n2 = fix_text(uma)

    assert uma == duas
    assert n1 > 0 and n2 == 0


def test_digrafo_gu_qu_cu_nao_recebe_hiato() -> None:
    """O acento em `-uído` marca hiato; depois de `gu`/`qu`/`cu` o u não e vogal plena."""
    texto = "tres falhas seguidas, a meta conseguida, e tome cuidado"

    resultado, _ = fix_text(texto)

    assert "seguidas" in resultado
    assert "conseguida" in resultado
    assert "cuidado" in resultado


def test_hiato_legitimo_continua_acentuado() -> None:
    """A guarda não pode custar o caso que a regra existe para pegar."""
    resultado, _ = fix_text("o indice construido com chaves distribuidas")

    assert "construído" in resultado
    assert "distribuídas" in resultado
