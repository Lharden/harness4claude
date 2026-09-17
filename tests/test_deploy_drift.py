"""Drift entre o repo e o plugin instalado (incidente 2026-09-02).

## O que aconteceu

Duas sessoes de Claude trabalhavam no mesmo plugin por checkouts diferentes —
uma em `Documents/projects/harness4claude`, outra em
`.claude/plugins/marketplaces/harness4claude` — e as duas faziam deploy com
`cp` ad-hoc para o MESMO cache. O cache virou uma mistura: parte de uma
sessao, parte da outra, e nenhum dos dois repos igual ao que rodava.

O sintoma chegou como um teste vermelho aparentemente trivial (`branch_state.py
add` recusando `--parent-session`). A causa era que o codigo em execucao vinha
de uma branch que nao existia em nenhum repo local.

## Por que este teste e separado do teste da CLI

`TestComandosDaSkillRodamMesmo` roda o CLI contra `HARNESS_PLUGIN_ROOT`, que o
`conftest` aponta para o repo. Isso esta certo: um teste de unidade deve medir
o codigo do repo, nao o que por acaso esta instalado. Mas com isso ele deixou
de detectar deploy velho — e detectar deploy velho era metade do valor dele.

Sao duas perguntas diferentes e cada uma merece o seu teste:

  - "o codigo do repo esta correto?"        -> testes de unidade, contra o repo
  - "o que roda e o codigo do repo?"        -> este arquivo, contra o cache

## Por que ele pula em vez de falhar quando nao ha cache

Em CI nao existe plugin instalado, e nao deve existir: a maquina de CI nao e
uma maquina de trabalho. Um teste que exige o cache falharia em CI por um
motivo que nao e defeito. `skip` diz a verdade — nao ha o que comparar.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
sys.path.insert(0, str(ROOT / "scripts"))

import deploy_to_cache as dtc  # noqa: E402


def _instalado():
    alvo = dtc.installed_root()
    if alvo is None:
        pytest.skip("nenhum plugin instalado nesta maquina — nada a comparar")
    return alvo


class TestInventario:
    """A lista de arquivos que viajam tem que ser derivada, nunca escrita a mao."""

    def test_usa_os_arquivos_versionados(self):
        arquivos = dtc.shipped_files(ROOT)
        assert arquivos, "inventario vazio — o git ls-files nao rodou"
        assert Path("scripts/branch_state.py") in arquivos
        assert Path("hooks/hooks.json") in arquivos

    def test_nao_inclui_o_que_nao_viaja(self):
        arquivos = dtc.shipped_files(ROOT)
        assert not [p for p in arquivos if p.parts[0] == ".github"]
        assert not [p for p in arquivos if "__pycache__" in p.parts]
        assert not [p for p in arquivos if p.parts[0] == "worktrees"]


class TestComparacao:
    """Fim de linha nao e divergencia; conteudo e."""

    def test_crlf_nao_conta_como_drift(self, tmp_path):
        a, b = tmp_path / "a", tmp_path / "b"
        a.write_bytes(b"linha um\nlinha dois\n")
        b.write_bytes(b"linha um\r\nlinha dois\r\n")
        assert dtc.same_content(a, b)

    def test_conteudo_diferente_conta(self, tmp_path):
        a, b = tmp_path / "a", tmp_path / "b"
        a.write_bytes(b"linha um\n")
        b.write_bytes(b"linha dois\n")
        assert not dtc.same_content(a, b)

    def test_ausente_no_destino_conta_como_drift(self, tmp_path):
        origem, destino = tmp_path / "src", tmp_path / "dst"
        (origem / "scripts").mkdir(parents=True)
        destino.mkdir()
        (origem / "scripts" / "x.py").write_text("oi\n", encoding="utf-8")
        divergentes = dtc.drift(origem, destino, [Path("scripts/x.py")])
        assert divergentes == [Path("scripts/x.py")]


def _ref_publicada():
    ref = dtc.published_ref(ROOT)
    if ref is None:
        pytest.skip("sem ref publicada (main/master) — nada a comparar")
    return ref


def _cache_falso(tmp_path: Path, ref: str) -> Path:
    """Um cache fabricado a partir de `ref`, para falsificar o portao."""
    destino = tmp_path / "cache"
    destino.mkdir()
    return dtc.extract_ref(ROOT, ref, destino)


def _mensagem(divergentes, alvo, ref) -> str:
    return (
        f"o que roda diverge de `{ref}` em {len(divergentes)} arquivo(s).\n"
        f"plugin: {alvo}\n  " + "\n  ".join(str(p) for p in divergentes[:20])
        + "\n\ncausa: deploy velho, ou cache com codigo de um ramo nao mesclado."
        "\nde um checkout de `" + ref + "` (NAO deste worktree), rode:"
        "\n    python scripts/deploy_to_cache.py --apply"
    )


class TestOQueRodaEOQueFoiPublicado:
    """O portao, e ele pergunta se o que roda e PUBLICADO — nao se e o worktree.

    Ate 2026-09-16 ele comparava `ROOT` (o worktree, via `HARNESS_PLUGIN_ROOT`)
    com o cache. Num worktree de ramo isso reprova por construcao: ramo tem
    trabalho nao implantado por definicao, e deveria ter. O efeito era pior que
    um falso positivo — era vermelho permanente, e vermelho que esta sempre la
    treina a ignorar vermelho. O portao morreria por uso sem nunca ser removido,
    continuando a custar manutencao sem entregar sinal, e o proximo drift de
    verdade passaria. Pior ainda, o conserto obvio para quem encontra o vermelho
    e `--apply`, que publica codigo de ramo nao revisado no plugin que toda
    sessao carrega: o teste empurrava para inverter a ordem deploy/merge.
    """

    def test_o_cache_reflete_o_publicado(self):
        alvo, ref = _instalado(), _ref_publicada()
        divergentes = dtc.drift_publicado(ROOT, alvo, ref)
        assert not divergentes, _mensagem(divergentes, alvo, ref)

    # ---- falsificacao: as duas metades, medidas ----

    def test_cache_igual_ao_publicado_passa(self, tmp_path):
        ref = _ref_publicada()
        assert dtc.drift_publicado(ROOT, _cache_falso(tmp_path, ref), ref) == []

    def test_cache_com_arquivo_alterado_REPROVA(self, tmp_path):
        """Metade 1: o portao ainda pega o defeito que existe para pegar."""
        ref = _ref_publicada()
        cache = _cache_falso(tmp_path, ref)
        alvo = cache / "scripts" / "branch_state.py"
        alvo.write_text(alvo.read_text(encoding="utf-8") + "\n# sabotagem\n", encoding="utf-8")
        assert Path("scripts/branch_state.py") in dtc.drift_publicado(ROOT, cache, ref)

    def test_cache_com_arquivo_faltando_REPROVA(self, tmp_path):
        ref = _ref_publicada()
        cache = _cache_falso(tmp_path, ref)
        (cache / "hooks" / "hooks.json").unlink()
        assert Path("hooks/hooks.json") in dtc.drift_publicado(ROOT, cache, ref)

    def test_deploy_velho_REPROVA(self, tmp_path):
        """O incidente de 2026-09-02 em forma de teste: cache de um commit anterior."""
        ref = _ref_publicada()
        anterior = tmp_path / "velho"
        anterior.mkdir()
        try:
            dtc.extract_ref(ROOT, ref + "~1", anterior)
        except Exception:
            pytest.skip(f"`{ref}~1` nao existe — historico raso")
        assert dtc.drift_publicado(ROOT, anterior, ref), (
            "cache de um commit anterior passou como se estivesse atualizado"
        )

    def test_arquivo_que_so_existe_no_RAMO_nao_e_exigido(self, tmp_path):
        """Metade 2: o falso positivo estrutural morreu.

        Um arquivo que existe no worktree e nao em `ref` nao pode ser cobrado do
        cache — ele nao foi publicado, e exigi-lo e o que reprovava todo ramo.
        """
        ref = _ref_publicada()
        cache = _cache_falso(tmp_path, ref)
        so_no_ramo = [
            p for p in dtc.shipped_files(ROOT)
            if not (cache / p).exists()
        ]
        if not so_no_ramo:
            pytest.skip("este worktree nao tem arquivo fora de `" + ref + "`")
        divergentes = dtc.drift_publicado(ROOT, cache, ref)
        assert not [p for p in so_no_ramo if p in divergentes], (
            f"arquivo que so existe no ramo foi cobrado do cache: {so_no_ramo[:5]}"
        )

    def test_o_comando_que_a_mensagem_imprime_e_aceito_pelo_parser_real(self):
        """`--apply` escreve no plugin, entao a prova possivel e parsear.

        Executar esta fora de questao; parsear com o parser REAL pega a ordem
        errada de flag, que foi o defeito consertado em `5ca9d4e`.
        """
        msg = _mensagem([Path("scripts/x.py")], Path("/cache"), "main")
        linha = next(x for x in msg.splitlines() if "deploy_to_cache.py" in x)
        argv = shlex.split(linha.strip(), posix=False)
        corte = next(i for i, a in enumerate(argv) if a.endswith("deploy_to_cache.py")) + 1
        args = dtc._parser().parse_args(argv[corte:])
        assert args.apply is True

    def test_o_check_pela_linha_de_comando_concorda_com_a_funcao(self):
        _instalado()
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "deploy_to_cache.py"), "--check"],
            capture_output=True, text=True, encoding="utf-8", timeout=120,
        )
        divergentes = dtc.drift(ROOT, _instalado(), dtc.shipped_files(ROOT))
        assert proc.returncode == (1 if divergentes else 0), proc.stdout + proc.stderr
