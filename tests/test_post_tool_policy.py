from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
SPEC = importlib.util.spec_from_file_location(
    "post_tool_policy",
    ROOT / "scripts" / "post_tool_policy.py",
)
policy = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(policy)


def test_edit_uses_real_path_and_shell_uses_non_counting_revision_marker():
    assert policy.touch_target("Edit", "src/app.py") == "src/app.py"
    assert policy.touch_target("Bash", "") == "<shell-command>"
    assert policy.counts_as_modified_file("Edit", "src/app.py") is True
    assert policy.counts_as_modified_file("Bash", "") is False


class TestFrescuraExpiraPorCODIGO:
    """Escrita fora do repositorio nao pode invalidar evidencia de teste.

    Medido em 2026-09-16: a evidencia de `1311 passed, 0 failed` foi invalidada
    por tres escritas em `<scratchpad>/msg2.txt` — a mensagem de commit — com a
    arvore de trabalho limpa e `git status` vazio. O criterio antigo perguntava
    "algum arquivo foi escrito?" quando queria saber "o codigo sob teste mudou?".

    Portao que so se satisfaz rodando 13 minutos de suite E respondendo sem
    escrever nem um rascunho torna o caminho honesto mais caro que o desonesto.
    """

    # ---- metade 1: o portao AINDA expira quando o codigo muda de verdade ----

    def test_edicao_dentro_do_repo_AINDA_conta(self, tmp_path):
        repo = tmp_path / "repo"
        (repo / "scripts").mkdir(parents=True)
        alvo = repo / "scripts" / "app.py"
        alvo.write_text("x = 1\n", encoding="utf-8")
        assert policy.counts_as_modified_file("Edit", str(alvo), str(repo)) is True

    def test_markdown_versionado_dentro_do_repo_conta(self, tmp_path):
        """De proposito: `test_orfaos.py` le `tools/README.md`, e ha testes que
        leem `SKILL.md` — um `.md` do repo muda o resultado da suite."""
        repo = tmp_path / "repo"
        (repo / "tools").mkdir(parents=True)
        alvo = repo / "tools" / "README.md"
        alvo.write_text("# t\n", encoding="utf-8")
        assert policy.counts_as_modified_file("Edit", str(alvo), str(repo)) is True

    def test_subdiretorio_profundo_conta(self, tmp_path):
        repo = tmp_path / "repo"
        fundo = repo / "a" / "b" / "c"
        fundo.mkdir(parents=True)
        assert policy.counts_as_modified_file("Write", str(fundo / "x.py"), str(repo)) is True

    # ---- metade 2: o falso positivo que quebrou a evidencia de hoje ----

    def test_escrita_no_scratchpad_NAO_conta(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        scratch = tmp_path / "scratchpad"
        scratch.mkdir()
        assert policy.counts_as_modified_file("Write", str(scratch / "msg2.txt"), str(repo)) is False

    def test_irmao_com_prefixo_parecido_NAO_conta(self, tmp_path):
        """`/x/repo-notas` nao esta dentro de `/x/repo`. Comparacao de prefixo
        de STRING diria que sim — e seria a armadilha de substring de novo,
        pela terceira vez neste ramo."""
        repo = tmp_path / "repo"
        vizinho = tmp_path / "repo-notas"
        repo.mkdir()
        vizinho.mkdir()
        assert policy.counts_as_modified_file("Edit", str(vizinho / "x.py"), str(repo)) is False

    def test_home_do_usuario_NAO_conta(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        fora = tmp_path / "home" / ".claude"
        fora.mkdir(parents=True)
        assert policy.counts_as_modified_file("Edit", str(fora / "CLAUDE.md"), str(repo)) is False

    # ---- o contrato de degradacao ----

    def test_sem_raiz_o_comportamento_e_o_de_antes(self):
        """Chamador antigo nao quebra: sem `root`, tudo conta, como sempre."""
        assert policy.counts_as_modified_file("Edit", "qualquer/coisa.py") is True

    def test_o_hook_nao_inventa_raiz_quando_o_payload_nao_traz_cwd(self):
        """Sem `cwd` no payload nao ha projeto declarado, e sem projeto nao ha
        dentro nem fora.

        A primeira versao caia em `os.getcwd()`, que e o repo onde o hook por
        acaso roda — uma fronteira inventada. Derrubou 7 testes de
        `TestReclassify`, que mandam `file_path` ficticio e nenhum `cwd`: os
        arquivos caiam "fora" de um projeto que ninguem declarou e a promocao
        L0 -> L1 parou de acontecer.
        """
        texto = (ROOT / "hooks" / "harness-reclassify.sh").read_text(encoding="utf-8")
        assert "if cwd_sessao:" in texto
        assert "os.getcwd()" not in texto.split("projeto_raiz = ''")[1].split("# Read state")[0]

    def test_caminho_irresolvivel_conta_FAIL_CLOSED(self, tmp_path):
        """Na duvida, expira. Deixar de expirar apos edicao real deixaria passar
        numero velho como fresco, que e a falha que o portao existe para impedir."""
        repo = tmp_path / "repo"
        repo.mkdir()
        assert policy.counts_as_modified_file("Edit", str(repo / "x.py"), "") is True

    def test_shell_continua_fora(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        assert policy.counts_as_modified_file("Bash", "", str(repo)) is False

    def test_o_hook_passa_a_raiz_do_projeto(self):
        """A regra nao pode ficar so na funcao que ninguem chama com `root`."""
        texto = (ROOT / "hooks" / "harness-reclassify.sh").read_text(encoding="utf-8")
        assert "find_repo_root" in texto
        assert "counts_as_modified_file(tool_name, file_path, projeto_raiz)" in texto


def test_post_tool_hooks_route_shell_state_through_one_transactional_handler():
    hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    commands = hooks["hooks"]["PostToolUse"]
    reclassify = next(
        entry for entry in commands
        if any("harness-reclassify.sh" in hook["command"] for hook in entry["hooks"])
    )
    transactional = next(
        entry for entry in commands
        if any("harness-transactional.py" in hook["command"] for hook in entry["hooks"])
    )

    assert reclassify["matcher"] == "Edit|Write"
    assert transactional["matcher"] == "Bash|PowerShell"
