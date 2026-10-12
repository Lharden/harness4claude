"""Guarda: toda chamada de subprocess declara os tres fluxos do filho (stdin, stdout, stderr).

A regra, a varredura e a falsificacao moram em `tests/_stdin_explicito.py`, que e GERADO: o master-harness e o dono e
este repositorio recebe a copia por `mh identidade semear --artefato stdin_explicito`. Este arquivo e o dado local
deste repositorio (as pastas varridas e as isencoes), e e o unico que difere entre os tres.
"""

from __future__ import annotations

from pathlib import Path

# O nucleo compartilhado; a classe entra no namespace para o pytest coleta-la aqui.
from _stdin_explicito import TestFalsificacao, arquivos, isencoes_mortas, violacoes_fora_das_isencoes  # noqa: F401

RAIZ = Path(__file__).resolve().parent.parent
#: pastas varridas (o que roda sob o pytest ou dentro de um teste)
VARRIDAS = ("scripts", "tools", "tests", "hooks")
#: isencoes declaradas: prefixo -> (causa, acao, prazo). Cada uma tem de ainda ter violacao.
ISENCOES: dict[str, tuple[str, str, str]] = {}


def test_a_varredura_enxerga_arquivos():
    """Varredura vazia passa por construcao; so vale se ela olha o repositorio de fato."""
    assert len(arquivos(RAIZ, VARRIDAS)) >= 10


def test_nenhuma_chamada_de_subprocess_herda_um_fluxo():
    fora = violacoes_fora_das_isencoes(RAIZ, VARRIDAS, ISENCOES)
    assert not fora, f"subprocess herdando stdin/stdout/stderr (WinError 6 sob a captura do pytest): {fora}"


def test_isencao_declarada_ainda_e_necessaria():
    """Isencao e contorno com prazo: quando a causa some, a linha sai daqui."""
    mortas = isencoes_mortas(RAIZ, VARRIDAS, ISENCOES)
    assert not mortas, f"isencao sem violacao por tras (remova): {mortas}"
