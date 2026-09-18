"""A receita que o manual manda copiar satisfaz a isencao que existe para ela.

`is_state_management` (`hooks/harness-transactional.py:152-173`) isenta o CLI do
proprio harness do contador de escrita, porque sem isso nenhuma task podia ser
fechada: `state_cli.py complete` so pode ser invocado por shell, e a invocacao
invalidava, no mesmo PostToolUse, a evidencia que o `complete` exige.

A isencao tem uma condicao — o comando nao pode ter composicao de shell. E ate
2026-09-17 os blocos `bash` das SKILLs a violavam: `;`, `$( )` e continuacao com
barra invertida (nova linha TAMBEM e composicao). Tres sessoes chegaram ao mesmo
laco por caminhos independentes porque as tres seguiram o manual.

Nada ligava a doc a condicao que ela precisava satisfazer, e foi por isso que a
distancia entre as duas sobreviveu desde 2026-09-02. Este arquivo e esse elo.
Medido no mapa `revisao-que-invalida` §6.4 e §5.1.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


hook = _load("receita_hook", "hooks/harness-transactional.py")


def _clis_isentos() -> list[str]:
    """Os nomes que a PRODUCAO isenta, derivados de `STATE_CLI_PATTERNS`.

    Relistar os nomes aqui seria reimplementar a regra medida: um CLI novo
    entraria na isencao e o teste continuaria olhando para a lista velha.
    """
    nomes = []
    for padrao in hook.STATE_CLI_PATTERNS:
        nomes.append(padrao.replace(r"\b", "").replace(r"\.", "."))
    return nomes


CLIS = _clis_isentos()


def _blocos_bash(texto: str) -> list[list[str]]:
    """Blocos ```bash ... ``` do markdown, tolerando fence indentado.

    O bloco do passo 2 do `harness-workflow` vive dentro de uma lista numerada,
    entao a cerca vem com tres espacos na frente. Uma regex ancorada em coluna 0
    nao o veria, e o teste passaria vazio sobre o unico bloco que originou o
    incidente.
    """
    blocos: list[list[str]] = []
    atual: list[str] | None = None
    for linha in texto.splitlines():
        despido = linha.strip()
        if atual is None:
            if despido.startswith("```bash"):
                atual = []
            continue
        if despido.startswith("```"):
            blocos.append(atual)
            atual = None
            continue
        atual.append(linha)
    return blocos


def _comandos(bloco: list[str]) -> list[str]:
    """Comandos logicos do bloco, COM as quebras de linha preservadas.

    [superado: "uma linha do bloco e um comando"] — a primeira versao deste
    arquivo varria linha a linha e devolveu 3 de 3 isentas tanto no `SKILL.md`
    de antes quanto no de depois. Ou seja: passava verde sobre o defeito que
    existe para pegar. A causa e que composicao mora ENTRE as linhas —
    `_scan_composition` conta `chr(10)` como operador, e `splitlines()` tinha
    acabado de apagar exatamente o caracter que reprova.

    Um comando continua no proximo linha quando a atual termina em barra
    invertida, e o texto entregue ao hook nesse caso inclui as quebras. E esse
    texto, e nao a linha isolada, que `is_state_management` julga.
    """
    comandos: list[str] = []
    atual: list[str] = []
    for linha in bloco:
        if not linha.strip() or linha.strip().startswith("#"):
            # Comentario nao e comando: ninguem o copia para o shell.
            if atual:
                comandos.append("\n".join(atual))
                atual = []
            continue
        atual.append(linha.strip())
        if not linha.rstrip().endswith("\\"):
            comandos.append("\n".join(atual))
            atual = []
    if atual:
        comandos.append("\n".join(atual))
    return comandos


def _receitas() -> tuple[list[tuple[Path, str]], list[tuple[Path, str]]]:
    """(comandos que nomeiam um CLI isento, todos os comandos desses blocos).

    A segunda lista existe porque o custo da receita nao esta so na chamada
    isenta: `ROOT=...; PR="$(cat ...)"` na linha de cima ja subiu o contador
    antes de o leitor chegar na linha que importa.
    """
    de_cli: list[tuple[Path, str]] = []
    vizinhos: list[tuple[Path, str]] = []
    for skill in sorted((ROOT / "skills").rglob("SKILL.md")):
        for bloco in _blocos_bash(skill.read_text(encoding="utf-8")):
            comandos = _comandos(bloco)
            deste_bloco = [c for c in comandos if any(cli in c for cli in CLIS)]
            if not deste_bloco:
                continue
            de_cli.extend((skill, c) for c in deste_bloco)
            vizinhos.extend((skill, c) for c in comandos)
    return de_cli, vizinhos


LINHAS, VIZINHOS = _receitas()


def test_o_scanner_acha_alguma_receita():
    """Censo antes do veredito.

    Sem esta linha, uma cerca renomeada ou um bloco movido faria o teste passar
    varrendo lista vazia — "nada encontrado" e "ninguem procurou" sao resultados
    diferentes, e so um deles e verde de verdade.
    """
    assert LINHAS, (
        "nenhuma linha de CLI de estado encontrada em skills/**/SKILL.md; "
        f"padroes procurados: {CLIS}"
    )


@pytest.mark.parametrize(
    "skill, comando",
    LINHAS,
    ids=[f"{s.parent.name}:{i}" for i, (s, _) in enumerate(LINHAS)],
)
def test_receita_do_manual_e_isenta(skill: Path, comando: str):
    assert hook.is_state_management(comando), (
        f"{skill.relative_to(ROOT)}: este comando do manual NAO e isento e vai "
        f"subir code_revision ao ser copiado.\n  {comando!r}\n"
        "Causa provavel: `;`, `&&`, `|`, `$( )` ou continuacao com barra "
        "invertida. Quebre em uma chamada por linha, com caminho literal."
    )


@pytest.mark.parametrize(
    "skill, comando",
    VIZINHOS,
    ids=[f"{s.parent.name}:{i}" for i, (s, _) in enumerate(VIZINHOS)],
)
def test_receita_de_estado_nao_tem_composicao(skill: Path, comando: str):
    """O bloco inteiro, nao so a chamada isenta.

    De nada adianta a linha do `state_cli.py` ser atomica se a linha acima dela
    e `ROOT="..."; PR="$(cat ...)"`: quem copia o bloco paga o contador nas duas
    de cima antes de chegar na que importa. A receita e o bloco.
    """
    assert not hook._has_unquoted_shell_composition(comando), (
        f"{skill.relative_to(ROOT)}: comando composto num bloco de CLI de "
        f"estado.\n  {comando!r}\n"
        "Resolva o valor num comando e use o literal no seguinte."
    )


def test_a_receita_do_portao_tambem_e_receita():
    """O scanner acima le `skills/**/SKILL.md` e nao ve a mensagem do gate.

    `_motivo_do_gate` imprime uma linha de `state_cli.py` para o leitor copiar —
    e uma receita pelo mesmo motivo que as dos SKILL.md sao. Ela ficou composta
    (`PR="$(cat ...)"; python ...`) depois de `223c53f`, que consertou as outras,
    porque o elo entre doc e condicao so cobria arquivo de doc.

    O teste de comportamento esta em
    `test_transactional_hook.py::test_receita_do_portao_nao_invalida_a_propria_evidencia`.
    Este aqui e o elo, na altura em que a regra e enunciada.
    """
    comando = hook.comando_de_evidencia(Path("/balde/da/sessao"), "t-1")
    assert any(cli in comando for cli in CLIS), (
        "a mensagem do portao deixou de citar um CLI de estado; este teste "
        "estaria passando sobre nada"
    )
    assert hook.is_state_management(comando), (
        "a receita que o PORTAO imprime nao e isenta e vai subir code_revision "
        f"ao ser copiada.\n  {comando!r}"
    )
    assert not hook.shell_write_targets(comando), (
        "a receita do portao virou alvo de escrita para o extrator"
    )


def test_composicao_continua_nao_isenta():
    """A outra metade: a isencao nao pode virar carta branca.

    `state_cli.py ... && sed -i ...` escreve de verdade na segunda metade. Se
    esta asserção cair, o conserto da doc virou afrouxamento da regra.
    """
    assert hook.is_state_management('python state_cli.py complete --task t-1') is True
    assert hook.is_state_management('python state_cli.py complete && sed -i s/a/b/ x.py') is False
    assert hook.is_state_management('python state_cli.py complete; rm -rf build') is False
    assert hook.is_state_management('X=$(python state_cli.py show)') is False
    assert hook.is_state_management('python state_cli.py complete \\\n  --task t-1') is False
    assert hook.is_state_management('python -m pytest -q') is False


def test_substituicao_entre_aspas_duplas_nao_e_vista_LIMITE_CONHECIDO():
    """Buraco medido em 2026-09-17, declarado em vez de escondido.

    `_scan_composition` (`hooks/harness-transactional.py:52-80`) para de olhar
    operadores assim que entra em aspas — e o bash executa `$( )` dentro de
    aspas DUPLAS do mesmo jeito. Entao `python state_cli.py --home "$(pwd)"`
    passa por isento, e um `"$(sed -i ...)"` no lugar do `pwd` escreveria de
    verdade sem subir o contador. A direcao do erro e a perigosa.

    NAO esta consertado aqui, e a razao e de escopo, nao de conveniencia: a
    mesma varredura decide `is_trusted_verification`. Apertar a regra faria
    comando de teste com substituicao entre aspas parar de contar como
    evidencia — mudanca de comportamento com falsificacao propria, fora de R4.
    Escrito no plano `portao-mede-a-arvore-verification.md` como aberto.

    Se este teste REPROVAR, o buraco foi fechado por outra mudanca: apague este
    teste e atualize aquele documento.
    """
    assert hook.is_state_management('X="$(python state_cli.py show)"') is True
    assert hook._has_unquoted_shell_composition('echo "$(date)"') is False
