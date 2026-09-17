"""Testes para os Workflow scripts do Harness (Bloco D).

Garante:
- os Workflow scripts existem
- todos passam pelo validador de sintaxe + meta (validate_workflows.cjs via node)
- cada um declara `export const meta` com name/description/phases
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
WF_DIR = ROOT / "scripts" / "workflows"
VALIDATOR = WF_DIR / "validate_workflows.cjs"


def test_workflows_exist():
    assert (WF_DIR / "wf-verify-multimodel.js").is_file()
    assert (WF_DIR / "wf-context-scan.js").is_file()


def test_validator_passes():
    node = shutil.which("node")
    if not node:
        pytest.skip("node nao disponivel no PATH")
    result = subprocess.run(
        [node, str(VALIDATOR)], capture_output=True, text=True, encoding="utf-8"
    )
    assert result.returncode == 0, f"validador falhou:\n{result.stdout}\n{result.stderr}"
    assert "ERRO" not in result.stdout


def test_each_workflow_has_meta():
    workflows = list(WF_DIR.glob("*.js"))
    assert workflows, "nenhum Workflow .js encontrado"
    for wf in workflows:
        content = wf.read_text(encoding="utf-8")
        assert "export const meta" in content, f"{wf.name} sem 'export const meta'"
        for field in ("name:", "description:", "phases:"):
            assert field in content, f"{wf.name} meta sem campo '{field}'"


def test_todo_fan_out_tem_censo_de_nos():
    """Agente morto nao pode sumir em silencio.

    `filter(Boolean)` descarta no morto sem avisar: num fan-out o relatorio sai
    com cara de completo. Todo script que abre `parallel(` precisa contar os
    retornos contra o esperado. Absorvido de graph-engineering-claude (2026-08-19).
    """
    for wf in WF_DIR.glob("*.js"):
        content = wf.read_text(encoding="utf-8")
        if "parallel(" not in content:
            continue
        assert "censoNos(" in content, f"{wf.name} abre fan-out sem censo de nos"


def _prompt_de_refutacao(content: str) -> list[str]:
    """Devolve o texto do argumento-prompt de cada `agent()` que manda refutar.

    O prompt e uma concatenacao de template literals entre `agent(` e o objeto de
    opcoes que comeca em `{ label:`. Recortar ate ali isola o que o refutador le,
    sem varrer o resto do arquivo (onde `rationale` aparece legitimamente, no
    retorno para o humano).
    """
    trechos = []
    inicio = 0
    while True:
        i = content.find("REFUT", inicio)
        if i == -1:
            return trechos
        abre = content.rfind("agent(", 0, i)
        fecha = content.find("{ label:", i)
        if abre == -1:
            abre = max(0, i - 400)
        if fecha == -1:
            fecha = min(len(content), i + 1200)
        trechos.append(content[abre:fecha])
        inicio = i + 5


def test_adjudicador_nao_recebe_rationale():
    """A aresta do refutador carrega a alegacao, nunca o raciocinio de quem a fez.

    O no adversarial so vale porque a janela dele e nova. Injetar a justificativa
    do produtor recorrela as duas pontas e devolve o vies de confirmacao que o
    fan-out existe para eliminar. Absorvido de @mstockton (2026-08-26); mesma
    familia do censo de nos — defeito que o fan-out introduz e cujo retorno
    continua parecendo completo.
    """
    for wf in WF_DIR.glob("*.js"):
        content = wf.read_text(encoding="utf-8")
        for trecho in _prompt_de_refutacao(content):
            assert "rationale" not in trecho, (
                f"{wf.name}: prompt de refutacao interpola o rationale do produtor"
            )
            assert "ustificativa" not in trecho, (
                f"{wf.name}: prompt de refutacao entrega a justificativa do produtor"
            )


def test_verify_nao_aprova_com_cobertura_incompleta():
    """`pass: true` nunca sai de um review em que uma dimensao morreu."""
    content = (WF_DIR / "wf-verify-multimodel.js").read_text(encoding="utf-8")
    assert "nos_mortos" in content
    assert "nosMortos.length === 0" in content, "pass nao esta amarrado ao censo"
    assert "pass: true," not in content, "ainda existe pass:true incondicional"


# ---------------------------------------------------------------------------
# Um workflow so existe se puder ser INVOCADO
# ---------------------------------------------------------------------------
CR = bytes([13])


def sem_cr(caminho) -> bool:
    """O arquivo esta livre de retorno de carro?

    Existe como funcao para que o proprio teste possa ser falsificado: sem o
    controle abaixo, um `sem_cr` que devolvesse `True` sempre deixaria a suite
    verde e o defeito de pe. Foi assim que a primeira medicao deste defeito
    errou — um `grep -c` cujo padrao degenerou para vazio devolvia o mesmo
    numero com e sem CRLF, entao confirmava qualquer hipotese.
    """
    return CR not in Path(caminho).read_bytes()


def test_CONTROLE_sem_cr_reprova_quando_ha_cr(tmp_path):
    """Metade 1. Sem isto o teste abaixo nao distingue nada."""
    com = tmp_path / "com.js"
    com.write_bytes(b"a" + CR + b"\nb\n")
    sem = tmp_path / "sem.js"
    sem.write_bytes(b"a\nb\n")
    assert not sem_cr(com)
    assert sem_cr(sem)


def test_workflow_pode_ser_invocado_pela_ferramenta():
    """CRLF num `.js` torna o workflow INVOCAVEL EM LUGAR NENHUM no Windows.

    A camada de permissao do Claude Code recusa o script com "script contains
    control characters that would be hidden in the approval dialog". Medido em
    2026-09-17: `wf-grill` e `wf-verify-multimodel` estao declarados no mapa de
    fases da skill `harness-workflow`, tem testes neste mesmo arquivo, e as
    duas fases do pipeline L2 que apontam para eles eram inalcancaveis.

    E o caso que `tools/orfaos.py` existe para acusar, um nivel acima: nao uma
    funcao sem chamador, mas uma CAPACIDADE declarada, testada e sem porta.
    Nenhum indicador existente mostrava isso — os testes deste arquivo passam
    lendo o conteudo do script, e ler nao e invocar.
    """
    arquivos = sorted(WF_DIR.glob("*.js")) + sorted(WF_DIR.glob("*.cjs"))
    assert arquivos, "nenhum workflow encontrado"
    com_cr = [f.name for f in arquivos if not sem_cr(f)]
    assert not com_cr, (
        f"workflow(s) com CRLF, e portanto nao invocaveis: {com_cr}. "
        "Confira que `.gitattributes` declara `*.js`/`*.cjs` com eol=lf e rode: "
        "git add --renormalize scripts/workflows/"
    )


def test_gitattributes_trava_o_eol_dos_workflows():
    """O teste acima le a arvore de trabalho; isto trava a CAUSA.

    Sem a regra no `.gitattributes`, um checkout novo no Windows recria o CRLF
    e o defeito volta sem ninguem ter editado nada.
    """
    linhas = [
        linha.split()
        for linha in (WF_DIR.parents[1] / ".gitattributes").read_text(encoding="utf-8").splitlines()
        if linha.strip() and not linha.lstrip().startswith("#")
    ]
    # `*.json` CONTEM `*.js`: comparar por linha, nunca por substring. A guarda
    # que escreveu esta regra deu falso positivo por exatamente isso.
    padroes = {partes[0]: partes[1:] for partes in linhas}
    for alvo in ("*.js", "*.cjs"):
        assert alvo in padroes, f"{alvo} nao declarado em .gitattributes"
        assert "eol=lf" in padroes[alvo], f"{alvo} declarado sem eol=lf"
