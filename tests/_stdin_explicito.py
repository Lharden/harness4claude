# DERIVADO - nao edite este arquivo.
#
# A fonte e `master-harness/tests/_stdin_explicito.py`, e ela e a unica
# dona do nucleo do guarda do stdin explicito. O repositorio do host `claude`
# nao importa o master-harness: o que muda entre os repositorios (`VARRIDAS`,
# `ISENCOES`) mora em `tests/test_stdin_explicito.py`, que nao e derivado.
#
# Para mudar o que esta aqui: edite a fonte e rode `mh identidade semear`.
# Para saber se derivou: `mh identidade check` (sai 1 se divergir).
#
# ---- fim do cabecalho vendorizado; daqui para baixo e copia exata ----
"""Nucleo do guarda: toda chamada de subprocess declara os tres fluxos do filho (stdin, stdout, stderr).

Este arquivo e o MESMO nos tres repositorios (master-harness, harness4claude, harness4codex): o master-harness e o
dono, e as copias dos hosts sao geradas por `mh identidade semear --artefato stdin_explicito`. O que muda entre os
repositorios (as pastas varridas e as isencoes) mora no `tests/test_stdin_explicito.py` de cada um, que nao e
derivado. Mudar a configuracao local nao toca este arquivo; editar este arquivo num host da `divergente` no
`mh identidade check`.

Defeito (medido em 2026-10-10): `subprocess.run/Popen` sem fluxo explicito herda o handle
que o Win32 guarda como STD_INPUT/OUTPUT/ERROR_HANDLE. A captura de descritores do pytest
(a padrao) faz `dup2` sobre os fds 0, 1 e 2, o que fecha o handle original e deixa o do
Win32 apontando para um handle fechado (ou reaproveitado: por isso e intermitente). O
`Popen` morre dentro de `_make_inheritable` com
`OSError: [WinError 6] Identificador invalido`. Com `-s` ou `--capture=sys` o mesmo teste
passa, o que esconde o defeito.

Regra: a chamada passa `stdin=` (`subprocess.DEVNULL`, ou o pipe quando escreve no filho)
ou `input=`; e passa `capture_output=True` ou `stdout=` e `stderr=` (`check_output` ja fixa
o stdout). `**kwargs` e aceito: o chamador responde pelos fluxos.
Em hook, o stdin do host carrega o payload; um filho que nao o le nao deve herda-lo.

Limite conhecido: `input=<variavel>` conta como stdin explicito, mas vale `None` se a
variavel for opcional (input=None nao cria o pipe). Os tres casos opcionais conhecidos
usam `stdin=subprocess.DEVNULL if entrada is None else None`; so a literal `input=None`
e detectada aqui.

A falsificacao esta no fim: a mesma varredura tem de reprovar a fonte plantada.
"""

from __future__ import annotations

import ast
from pathlib import Path

_FUNCS = {"run", "Popen", "call", "check_output", "check_call"}


def chamadas_que_herdam_fluxo(fonte: str) -> list[tuple[int, str]]:
    """`(linha, fluxo)` das chamadas de subprocess que deixam um fluxo herdado."""
    arvore = ast.parse(fonte)
    modulos: set[str] = set()
    nomes: dict[str, str] = {}
    for n in ast.walk(arvore):
        if isinstance(n, ast.Import):
            modulos.update(a.asname or a.name for a in n.names if a.name == "subprocess")
        elif isinstance(n, ast.ImportFrom) and n.module == "subprocess":
            nomes.update({a.asname or a.name: a.name for a in n.names if a.name in _FUNCS})
    achadas = []
    for n in ast.walk(arvore):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        if isinstance(f, ast.Attribute) and f.attr in _FUNCS and isinstance(f.value, ast.Name):
            funcao = f.attr if f.value.id in modulos else None
        elif isinstance(f, ast.Name):
            funcao = nomes.get(f.id)
        else:
            funcao = None
        if funcao is None:
            continue
        chaves = {k.arg: k.value for k in n.keywords}
        if None in chaves:
            continue
        entrada = chaves.get("input")
        sem_input = entrada is None or (isinstance(entrada, ast.Constant) and entrada.value is None)
        if "stdin" not in chaves and sem_input:
            achadas.append((n.lineno, "stdin"))
        if "capture_output" in chaves:
            continue
        if "stdout" not in chaves and funcao != "check_output":
            achadas.append((n.lineno, "stdout"))
        if "stderr" not in chaves:
            achadas.append((n.lineno, "stderr"))
    return sorted(achadas)


def arquivos(raiz: Path, varridas: tuple[str, ...]) -> list[Path]:
    """Os `.py` das pastas `varridas` sob `raiz`, sem pasta oculta nem `__pycache__`."""
    achados = []
    for pasta in varridas:
        achados += sorted((raiz / pasta).rglob("*.py"))
    # relativo a raiz: o caminho absoluto pode ter pasta oculta (.claude/worktrees/...) e zerar a varredura
    return [
        p
        for p in achados
        if not any(parte.startswith(".") or parte == "__pycache__" for parte in p.relative_to(raiz).parts)
    ]


def violacoes(raiz: Path, varridas: tuple[str, ...]) -> dict[str, list[tuple[int, str]]]:
    saida = {}
    for p in arquivos(raiz, varridas):
        achadas = chamadas_que_herdam_fluxo(p.read_text(encoding="utf-8"))
        if achadas:
            saida[p.relative_to(raiz).as_posix()] = achadas
    return saida


def violacoes_fora_das_isencoes(raiz: Path, varridas: tuple[str, ...], isencoes) -> dict[str, list[tuple[int, str]]]:
    """O que reprova: as violacoes cujo caminho nao comeca por nenhum prefixo de `isencoes`."""
    return {
        rel: ls
        for rel, ls in violacoes(raiz, varridas).items()
        if not any(rel.startswith(prefixo) for prefixo in isencoes)
    }


def isencoes_mortas(raiz: Path, varridas: tuple[str, ...], isencoes) -> list[str]:
    """As isencoes sem violacao por tras: contorno com prazo, que sai quando a causa some."""
    achadas = violacoes(raiz, varridas)
    return [p for p in isencoes if not any(rel.startswith(p) for rel in achadas)]


class TestFalsificacao:
    """O guarda pega o defeito que existe para pegar, e deixa passar o conserto."""

    def test_pega_run_e_popen_sem_fluxo_nenhum(self):
        fonte = "import subprocess\nsubprocess.run(['x'])\nsubprocess.Popen(['y'])\n"
        assert chamadas_que_herdam_fluxo(fonte) == [
            (2, "stderr"), (2, "stdin"), (2, "stdout"), (3, "stderr"), (3, "stdin"), (3, "stdout"),
        ]

    def test_pega_so_o_stdin_quando_a_saida_esta_capturada(self):
        assert chamadas_que_herdam_fluxo("import subprocess\nsubprocess.run(['x'], capture_output=True)\n") == [
            (2, "stdin")
        ]

    def test_pega_o_stderr_esquecido(self):
        fonte = "import subprocess\nsubprocess.run(['x'], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE)\n"
        assert chamadas_que_herdam_fluxo(fonte) == [(2, "stderr")]

    def test_pega_input_none_literal(self):
        fonte = "import subprocess\nsubprocess.run(['x'], input=None, capture_output=True)\n"
        assert chamadas_que_herdam_fluxo(fonte) == [(2, "stdin")]

    def test_pega_pelo_alias_e_pelo_from_import(self):
        assert chamadas_que_herdam_fluxo("import subprocess as sp\nsp.check_output(['x'])\n") == [
            (2, "stderr"), (2, "stdin")
        ]
        assert chamadas_que_herdam_fluxo("from subprocess import run\nrun(['x'])\n") == [
            (2, "stderr"), (2, "stdin"), (2, "stdout")
        ]

    def test_aceita_a_chamada_com_os_tres_fluxos_e_a_com_kwargs(self):
        fonte = (
            "import subprocess\n"
            "subprocess.run(['x'], stdin=subprocess.DEVNULL, capture_output=True)\n"
            "subprocess.run(['x'], input='a', capture_output=True)\n"
            "subprocess.Popen(['x'], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)\n"
            "subprocess.check_output(['x'], stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
            "subprocess.Popen(['x'], **kw)\n"
        )
        assert chamadas_que_herdam_fluxo(fonte) == []

    def test_ignora_o_que_nao_e_subprocess(self):
        assert chamadas_que_herdam_fluxo("import os\nos.path.join('a')\nrun(['x'])\n") == []

    def test_a_varredura_pega_a_violacao_plantada_num_arquivo(self, tmp_path):
        """A mesma varredura que reprova o repositorio: um arquivo plantado reprova, e uma isencao o libera."""
        (tmp_path / "pasta").mkdir()
        (tmp_path / "pasta" / "ruim.py").write_text("import subprocess\nsubprocess.run(['x'])\n", encoding="utf-8")
        assert violacoes_fora_das_isencoes(tmp_path, ("pasta",), {}) == {
            "pasta/ruim.py": [(2, "stderr"), (2, "stdin"), (2, "stdout")]
        }
        assert violacoes_fora_das_isencoes(tmp_path, ("pasta",), {"pasta/": ()}) == {}
        assert isencoes_mortas(tmp_path, ("pasta",), {"pasta/": (), "outra/": ()}) == ["outra/"]
