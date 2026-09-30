"""checar_triagem.py: DONE exige destino escrito para cada achado nao adjudicado.

Spec: docs/specs/verify-adjudica-tudo-spec.md, US-2 (AC-2.1 a AC-2.15).
Sem o checador, o `pass: false` da US-1 so trocaria o problema de lugar: o
agente leria `false` e nao teria como sair do laco (L-05).
"""
from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
sys.path.insert(0, str(ROOT / "scripts"))

import checar_triagem

DADOS = Path(__file__).parent / "data" / "verify-rodadas-2026-09-30"
SECAO = "## Destino dos achados não adjudicados"


def _saida(
    pasta: Path,
    rodada: str,
    achados: list[tuple[str, bool | None]],
    *,
    critical_count: int = 0,
    nos_mortos: tuple[str, ...] = (),
    formato: str = "host",
    started: int = 1_000,
    com_n: bool = True,
) -> Path:
    """Escreve a copia de uma rodada. `adjudicado=None` omite o campo."""
    findings = []
    for i, (titulo, adjudicado) in enumerate(achados, 1):
        f = {"title": titulo, "severity": "medium", "file": "a.py", "line": i, "rationale": "r"}
        if com_n:
            f["n"] = i
        if adjudicado is not None:
            f["adjudicado"] = adjudicado
        findings.append(f)
    result = {"pass": False, "critical_count": critical_count, "nos_mortos": list(nos_mortos), "findings": findings}
    if formato == "host":
        dado = {
            "summary": "s",
            "result": result,
            "workflowProgress": [{"type": "workflow_agent", "startedAt": started, "tokens": 1}],
        }
    else:
        dado = result
    pasta.mkdir(parents=True, exist_ok=True)
    caminho = pasta / f"{rodada}.json"
    caminho.write_text(json.dumps(dado), encoding="utf-8")
    return caminho


def _plano(tmp_path: Path, linhas: list[str], *, antes: str = "", depois: str = "") -> Path:
    corpo = [
        "# Closure Plan",
        antes,
        SECAO,
        "",
        "| Rodada | # | Achado | Destino | Referência |",
        "|---|---|---|---|---|",
        *linhas,
        "",
        "## Outra seção",
        depois,
    ]
    p = tmp_path / "closure-plan.md"
    p.write_text("\n".join(corpo), encoding="utf-8")
    return p


def _linha(rodada: str, n: int, titulo: str, destino: str = "consertado", ref: str = "test_conserto_x") -> str:
    return f"| {rodada} | {n} | {titulo[:30]} | {destino} | {ref} |"


TITULOS = [
    "confirmado um que ja foi adjudicado",
    "confirmado dois que ja foi adjudicado",
    "codigo de saida lido do texto do modelo",
    "bloco forjado pela descricao do job",
    "confirmado cinco que ja foi adjudicado",
    "confirmado seis que ja foi adjudicado",
    "lancamento sem tool_use_id fica pendente",
]
PENDENTES = {3, 4, 7}


def _rodada_a(pasta: Path, **kw) -> Path:
    return _saida(pasta, "wf_a", [(t, (i not in PENDENTES)) for i, t in enumerate(TITULOS, 1)], **kw)


def _linhas_validas(rodada: str = "wf_a") -> list[str]:
    return [_linha(rodada, n, TITULOS[n - 1]) for n in sorted(PENDENTES)]


def _checar(plano: Path, *saidas: Path) -> tuple[int, str]:
    codigo, mensagens = checar_triagem.checar(plano, list(saidas))
    return codigo, "\n".join(mensagens)


# --------------------------------------------------------------- AC-2.1 ----


def test_tudo_decidido_sai_0(tmp_path):
    verify = tmp_path / "verify"
    _rodada_a(verify)
    codigo, texto = _checar(_plano(tmp_path, _linhas_validas()), verify)
    assert codigo == 0, texto


# ----------------------------------------------------------- AC-2.2/2.3 ----


def test_linha_faltando_sai_1_e_imprime_a_linha_pronta(tmp_path):
    verify = tmp_path / "verify"
    _rodada_a(verify)
    codigo, texto = _checar(_plano(tmp_path, _linhas_validas()[:2]), verify)
    assert codigo == 1
    assert "wf_a #7" in texto
    assert TITULOS[6] in texto
    prontas = [ln for ln in texto.splitlines() if ln.startswith("| wf_a | 7 |")]
    assert len(prontas) == 1
    assert "<consertado|limite|nao-defeito>" in prontas[0]
    assert "<teste, commit ou motivo>" in prontas[0]


def test_linha_pronta_colada_sem_editar_reprova(tmp_path):
    verify = tmp_path / "verify"
    _rodada_a(verify)
    _, texto = _checar(_plano(tmp_path, _linhas_validas()[:2]), verify)
    pronta = next(ln for ln in texto.splitlines() if ln.startswith("| wf_a | 7 |"))
    codigo, _ = _checar(_plano(tmp_path, [*_linhas_validas()[:2], pronta]), verify)
    assert codigo == 1


# --------------------------------------------------------------- AC-2.4 ----


def test_destino_fora_dos_tres_reprova_dizendo_valor_e_linha(tmp_path):
    verify = tmp_path / "verify"
    _rodada_a(verify)
    linhas = [*_linhas_validas()[:2], _linha("wf_a", 7, TITULOS[6], destino="corrigido")]
    plano = _plano(tmp_path, linhas)
    codigo, texto = _checar(plano, verify)
    assert codigo == 1
    assert "corrigido" in texto
    numero = plano.read_text(encoding="utf-8").splitlines().index(linhas[2]) + 1
    assert f"closure-plan.md:{numero}" in texto


def test_cobertura_nao_e_destino(tmp_path):
    """Os destinos sao os tres de L-01; `cobertura` saiu no grill (#13)."""
    verify = tmp_path / "verify"
    _rodada_a(verify)
    linhas = [*_linhas_validas()[:2], _linha("wf_a", 7, TITULOS[6], destino="cobertura")]
    assert _checar(_plano(tmp_path, linhas), verify)[0] == 1


# --------------------------------------------------------------- AC-2.5 ----


@pytest.mark.parametrize("ref", ["", "ok", "n/a", "-", "?", "TBD", "todo", "abc12", "<teste, commit ou motivo>"])
def test_referencia_vazia_curta_ou_proibida_reprova(tmp_path, ref):
    verify = tmp_path / "verify"
    _rodada_a(verify)
    linhas = [*_linhas_validas()[:2], _linha("wf_a", 7, TITULOS[6], ref=ref)]
    assert _checar(_plano(tmp_path, linhas), verify)[0] == 1


def test_referencia_com_sinais_de_menor_e_maior_passa(tmp_path):
    """Neste repo e comum citar `<task-notification>`; so o marcador literal reprova (grill #78)."""
    verify = tmp_path / "verify"
    _rodada_a(verify)
    linhas = [*_linhas_validas()[:2], _linha("wf_a", 7, TITULOS[6], ref="bloco <task-notification> recusado")]
    assert _checar(_plano(tmp_path, linhas), verify)[0] == 0


# --------------------------------------------------------------- AC-2.6 ----


@pytest.mark.parametrize("n", [5, 99])
def test_numero_que_nao_e_pendente_da_rodada_reprova(tmp_path, n):
    verify = tmp_path / "verify"
    _rodada_a(verify)
    titulo = TITULOS[n - 1] if n <= len(TITULOS) else "achado que nao existe na rodada"
    assert _checar(_plano(tmp_path, [*_linhas_validas(), _linha("wf_a", n, titulo)]), verify)[0] == 1


# --------------------------------------------------------------- AC-2.7 ----


def test_titulo_de_outra_execucao_reprova(tmp_path):
    verify = tmp_path / "verify"
    _rodada_a(verify)
    linhas = [*_linhas_validas()[:2], _linha("wf_a", 7, "um achado de outra rodada qualquer")]
    codigo, texto = _checar(_plano(tmp_path, linhas), verify)
    assert codigo == 1
    assert "wf_a #7" in texto


def test_titulo_curto_demais_nao_casa(tmp_path):
    """Pelo menos 20 caracteres (ou o titulo inteiro, se menor)."""
    verify = tmp_path / "verify"
    _rodada_a(verify)
    linhas = [*_linhas_validas()[:2], "| wf_a | 7 | lancamento | consertado | test_conserto_x |"]
    assert _checar(_plano(tmp_path, linhas), verify)[0] == 1


def test_titulo_compara_sem_caixa_e_sem_espaco_extra(tmp_path):
    verify = tmp_path / "verify"
    _rodada_a(verify)
    linhas = [*_linhas_validas()[:2], "| wf_a | 7 | LANCAMENTO   sem TOOL_USE_ID | consertado | test_conserto_x |"]
    assert _checar(_plano(tmp_path, linhas), verify)[0] == 0


def test_titulo_com_barra_vertical_nao_quebra_a_tabela(tmp_path):
    verify = tmp_path / "verify"
    _saida(verify, "wf_b", [("pass | fail invertidos no retorno do hook", False)])
    _, texto = _checar(_plano(tmp_path, []), verify)
    pronta = next(ln for ln in texto.splitlines() if ln.startswith("| wf_b | 1 |"))
    editada = pronta.replace("<consertado|limite|nao-defeito>", "limite").replace(
        "<teste, commit ou motivo>", "motivo: formato do host"
    )
    assert _checar(_plano(tmp_path, [editada]), verify)[0] == 0


def test_nao_defeito_aceita_acento_e_caixa(tmp_path):
    verify = tmp_path / "verify"
    _rodada_a(verify)
    linhas = [*_linhas_validas()[:2], _linha("wf_a", 7, TITULOS[6], destino=" Não-Defeito ")]
    assert _checar(_plano(tmp_path, linhas), verify)[0] == 0


# --------------------------------------------------------------- AC-2.8 ----


def test_linha_duplicada_reprova(tmp_path):
    verify = tmp_path / "verify"
    _rodada_a(verify)
    assert _checar(_plano(tmp_path, [*_linhas_validas(), _linhas_validas()[0]]), verify)[0] == 1


# --------------------------------------------------------------- AC-2.9 ----


def test_linha_fora_da_secao_e_ignorada(tmp_path):
    verify = tmp_path / "verify"
    _rodada_a(verify)
    fora = "\n".join(["| Rodada | # | Achado | Destino | Referência |", "|---|---|---|---|---|", *_linhas_validas()])
    assert _checar(_plano(tmp_path, [], antes=fora), verify)[0] == 1


def test_linha_em_bloco_de_codigo_e_ignorada(tmp_path):
    verify = tmp_path / "verify"
    _rodada_a(verify)
    cercada = ["```", _linha("wf_a", 7, "exemplo que nao e decisao", destino="xyz"), "```"]
    assert _checar(_plano(tmp_path, [*_linhas_validas(), *cercada]), verify)[0] == 0


def test_linha_com_numero_errado_de_celulas_reprova(tmp_path):
    verify = tmp_path / "verify"
    _rodada_a(verify)
    assert _checar(_plano(tmp_path, [*_linhas_validas(), "| wf_a | 7 | consertado |"]), verify)[0] == 1


# -------------------------------------------------------------- AC-2.10 ----


@pytest.mark.parametrize("bloqueio", [{"critical_count": 1}, {"nos_mortos": ("security",)}])
def test_bloqueio_na_ultima_rodada_reprova(tmp_path, bloqueio):
    verify = tmp_path / "verify"
    _rodada_a(verify, **bloqueio)
    codigo, texto = _checar(_plano(tmp_path, _linhas_validas()), verify)
    assert codigo == 1
    assert "gap-closure" in texto


# -------------------------------------------------------------- AC-2.11 ----


def test_varias_rodadas_confere_todas_e_bloqueio_so_da_ultima(tmp_path):
    verify = tmp_path / "verify"
    _rodada_a(verify, critical_count=1, started=1_000)
    _saida(verify, "wf_b", [("achado da rodada nova ainda aberto", False)], started=2_000)
    linhas = [*_linhas_validas(), _linha("wf_b", 1, "achado da rodada nova ainda aberto")]
    assert _checar(_plano(tmp_path, linhas), verify)[0] == 0
    codigo, texto = _checar(_plano(tmp_path, linhas[1:]), verify)
    assert codigo == 1
    assert "wf_a #3" in texto


def test_rodada_final_por_mtime_sem_workflow_progress(tmp_path):
    verify = tmp_path / "verify"
    velha = _saida(verify, "wf_velha", [("achado velho ja decidido aqui", False)], formato="retorno", critical_count=1)
    nova = _saida(verify, "wf_nova", [("achado novo ja decidido aqui", False)], formato="retorno")
    os.utime(velha, (1_000, 1_000))
    os.utime(nova, (2_000, 2_000))
    linhas = [
        _linha("wf_velha", 1, "achado velho ja decidido aqui"),
        _linha("wf_nova", 1, "achado novo ja decidido aqui"),
    ]
    assert _checar(_plano(tmp_path, linhas), verify)[0] == 0
    os.utime(velha, (3_000, 3_000))
    assert _checar(_plano(tmp_path, linhas), verify)[0] == 1


# -------------------------------------------------------------- AC-2.12 ----


def test_plano_ausente_com_pendente_sai_1_com_todas_as_linhas(tmp_path):
    verify = tmp_path / "verify"
    _rodada_a(verify)
    codigo, texto = _checar(tmp_path / "nao-existe.md", verify)
    assert codigo == 1
    assert sum(ln.startswith("| wf_a |") for ln in texto.splitlines()) == 3


def test_plano_ausente_sem_pendente_nem_bloqueio_sai_0(tmp_path):
    verify = tmp_path / "verify"
    _saida(verify, "wf_a", [("so achado adjudicado aqui dentro", True)])
    assert _checar(tmp_path / "nao-existe.md", verify)[0] == 0


# -------------------------------------------------------------- AC-2.13 ----


def _ilegivel(tmp_path: Path, caso: str) -> Path:
    verify = tmp_path / "verify"
    verify.mkdir()
    alvo = verify / "wf_x.json"
    if caso == "ausente":
        return verify / "nao-existe.json"
    if caso == "vazio":
        return verify
    if caso == "json-invalido":
        alvo.write_text("{nao e json", encoding="utf-8")
    elif caso == "sem-findings":
        alvo.write_text(json.dumps({"result": {"critical_count": 0, "nos_mortos": []}}), encoding="utf-8")
    elif caso == "sem-critical-count":
        alvo.write_text(json.dumps({"result": {"findings": [], "nos_mortos": []}}), encoding="utf-8")
    elif caso == "sem-nos-mortos":
        alvo.write_text(json.dumps({"result": {"findings": [], "critical_count": 0}}), encoding="utf-8")
    elif caso == "n-fora-da-posicao":
        _saida(verify, "wf_x", [("um", False), ("dois", False)])
        dado = json.loads(alvo.read_text(encoding="utf-8"))
        dado["result"]["findings"][1]["n"] = 1
        alvo.write_text(json.dumps(dado), encoding="utf-8")
    return alvo


@pytest.mark.parametrize(
    "caso",
    ["ausente", "vazio", "json-invalido", "sem-findings", "sem-critical-count", "sem-nos-mortos", "n-fora-da-posicao"],
)
def test_entrada_ilegivel_sai_2(tmp_path, caso):
    codigo, texto = _checar(_plano(tmp_path, []), _ilegivel(tmp_path, caso))
    assert codigo == 2, texto
    assert texto


# -------------------------------------------------------------- AC-2.14 ----


def test_achado_sem_campo_adjudicado_conta_como_pendente(tmp_path):
    verify = tmp_path / "verify"
    _saida(verify, "wf_a", [("achado sem o campo adjudicado", None)])
    codigo, texto = _checar(_plano(tmp_path, []), verify)
    assert codigo == 1
    assert "wf_a #1" in texto


# -------------------------------------------------------------- AC-2.15 ----


def test_formato_do_retorno_e_lido(tmp_path):
    verify = tmp_path / "verify"
    _saida(verify, "wf_a", [(t, (i not in PENDENTES)) for i, t in enumerate(TITULOS, 1)], formato="retorno")
    assert _checar(_plano(tmp_path, _linhas_validas()), verify)[0] == 0


def test_saida_antiga_sem_n_usa_a_posicao(tmp_path):
    verify = tmp_path / "verify"
    _saida(verify, "wf_a", [(t, (i not in PENDENTES)) for i, t in enumerate(TITULOS, 1)], com_n=False)
    assert _checar(_plano(tmp_path, _linhas_validas()), verify)[0] == 0


def _linhas_da_rodada_1() -> list[str]:
    """Tabela de destino da rodada 1 da mae, montada do destinos.csv medido."""
    destino = {"defeito": "consertado", "limite-depois-defeito": "consertado", "cobertura": "consertado"}
    rodada = json.loads((DADOS / "wf_6bd9e71a-c4f" / "workflow.output").read_text(encoding="utf-8"))
    titulos = [f["title"] for f in rodada["result"]["findings"]]
    linhas = []
    with (DADOS / "destinos.csv").open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["rodada"] != "1":
                continue
            n = int(r["n"])
            titulo = titulos[n - 1][:40].replace("|", "/")
            # "D7" sozinho e curto demais para a regra de referencia; no plano real
            # ele aparece dentro de uma frase que diz de onde vem.
            ref = f"closure-plan {r['ref']}"
            linhas.append(f"| wf_6bd9e71a-c4f | {n} | {titulo} | {destino.get(r['destino'], r['destino'])} | {ref} |")
    return linhas


def test_dado_real_da_rodada_1_com_a_triagem_medida_sai_0(tmp_path):
    verify = tmp_path / "verify"
    verify.mkdir()
    shutil.copy(DADOS / "wf_6bd9e71a-c4f" / "workflow.output", verify / "wf_6bd9e71a-c4f.json")
    linhas = _linhas_da_rodada_1()
    assert len(linhas) == 23
    codigo, texto = _checar(_plano(tmp_path, linhas), verify)
    assert codigo == 0, texto
    codigo, texto = _checar(_plano(tmp_path, [ln for ln in linhas if "| 19 |" not in ln]), verify)
    assert codigo == 1
    assert "wf_6bd9e71a-c4f #19" in texto


# --------------------------------------- verify da task, rodada wf_d91bfd29-d22 ----


def test_linha_que_cita_rodada_ausente_reprova(tmp_path):
    """Achados #1 e #13: linha de rodada sem copia era ignorada em silencio. Um id
    digitado errado, ou a copia de uma rodada esquecida, passava sem aviso."""
    verify = tmp_path / "verify"
    _rodada_a(verify)
    linhas = [*_linhas_validas(), _linha("wf_b", 1, "achado de uma rodada sem copia no balde")]
    plano = _plano(tmp_path, linhas)
    codigo, texto = _checar(plano, verify)
    assert codigo == 1
    numero = plano.read_text(encoding="utf-8").splitlines().index(linhas[-1]) + 1
    assert f"closure-plan.md:{numero}" in texto
    assert "wf_b" in texto


def test_digito_unicode_no_numero_reprova_sem_traceback(tmp_path):
    """Achados #4 e #10: '²'.isdigit() e True e int('²') levanta."""
    verify = tmp_path / "verify"
    _rodada_a(verify)
    codigo, texto = _checar(_plano(tmp_path, [*_linhas_validas(), "| wf_a | ² | x | limite | motivo longo |"]), verify)
    assert codigo == 1
    assert "nao e numero" in texto


def test_linha_pronta_de_titulo_com_quebra_de_linha_fica_numa_linha(tmp_path):
    """Achados #5 e #8: a linha que o checador manda colar nao pode partir."""
    verify = tmp_path / "verify"
    _saida(verify, "wf_b", [("Primeira linha do titulo\nsegunda linha com mais texto", False)])
    _, texto = _checar(_plano(tmp_path, []), verify)
    pronta = next(ln for ln in texto.splitlines() if ln.startswith("| wf_b | 1 |"))
    assert pronta.endswith("|")
    editada = pronta.replace("<consertado|limite|nao-defeito>", "limite").replace(
        "<teste, commit ou motivo>", "motivo: formato do host"
    )
    assert _checar(_plano(tmp_path, [editada]), verify)[0] == 0


def test_plano_que_nao_e_utf8_sai_2(tmp_path):
    """Achado #9: traceback saia com 1, que no contrato quer dizer pendente."""
    verify = tmp_path / "verify"
    _rodada_a(verify)
    plano = tmp_path / "closure-plan.md"
    plano.write_bytes(f"{SECAO}\n| wf_a | 3 | não | limite | motivo longo |\n".encode("cp1252"))
    codigo, texto = _checar(plano, verify)
    assert codigo == 2
    assert "closure-plan.md" in texto


def test_cabecalho_e_destino_em_nfd_casam(tmp_path):
    """Achado #11: texto colado do macOS ou de terminal chega decomposto."""
    verify = tmp_path / "verify"
    _rodada_a(verify)
    plano = _plano(tmp_path, [*_linhas_validas()[:2], _linha("wf_a", 7, TITULOS[6], destino="não-defeito")])
    plano.write_text(unicodedata.normalize("NFD", plano.read_text(encoding="utf-8")), encoding="utf-8")
    assert _checar(plano, verify)[0] == 0


def test_copia_com_bom_e_lida(tmp_path):
    """Achado #12: salvar pelo PowerShell ou por um editor pode acrescentar BOM."""
    verify = tmp_path / "verify"
    copia = _rodada_a(verify)
    copia.write_bytes(b"\xef\xbb\xbf" + copia.read_bytes())
    assert _checar(_plano(tmp_path, _linhas_validas()), verify)[0] == 0


def test_mesmo_arquivo_passado_duas_vezes_nao_e_duplicata(tmp_path):
    """Achado #12: a mensagem dizia "arquivos diferentes" para um arquivo so."""
    verify = tmp_path / "verify"
    copia = _rodada_a(verify)
    assert _checar(_plano(tmp_path, _linhas_validas()), verify, copia)[0] == 0


# ------------------------------------------------------- AC-2.16: skill ----

SKILL = ROOT / "skills" / "harness-workflow" / "SKILL.md"


def test_skill_manda_copiar_a_saida_e_rodar_o_checador():
    """O checador so vale se a fase de verify mandar roda-lo. E essa linha, com o
    caminho `scripts/checar_triagem.py`, que `tools/orfaos.py` le como raiz."""
    texto = SKILL.read_text(encoding="utf-8")
    assert (
        'python "<PLUGIN_ROOT>/scripts/checar_triagem.py" --plano "docs/closure-plan.md" --saidas "<STATE_DIR>/verify"'
        in texto
    )
    assert "<STATE_DIR>/verify/<run id>.json" in texto
    assert "## Destino dos achados não adjudicados" in texto
    assert "repo_root" in texto


def test_skill_nao_manda_mais_para_done_so_com_pass_de_critico_alto():
    """A linha que levou tres rodadas verdes com defeito real a DONE."""
    texto = SKILL.read_text(encoding="utf-8")
    assert "Se `pass: true` (sem findings critical/high)" not in texto


# ------------------------------------------------------------------ CLI ----


def test_cli_devolve_o_codigo_de_saida(tmp_path):
    verify = tmp_path / "verify"
    _rodada_a(verify)
    script = ROOT / "scripts" / "checar_triagem.py"
    base = [sys.executable, str(script), "--saidas", str(verify), "--plano"]
    ok = subprocess.run([*base, str(_plano(tmp_path, _linhas_validas()))], capture_output=True, text=True)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    falta = subprocess.run([*base, str(_plano(tmp_path, []))], capture_output=True, text=True, encoding="utf-8")
    assert falta.returncode == 1
    assert "| wf_a | 3 |" in falta.stdout
