"""Escopo de estado por projeto (auditoria 2026-07-28).

Evidencia do bug: `~/.claude/harness/.session-files-count` continha 130 arquivos
sob UM `task_id` — 41 de `master_project`, 39 de `harness4claude`, o resto de
temporarios. `harness-reclassify.sh` usa esse contador para promover L0 -> L1,
entao editar arquivos num repositorio escalava a classificacao de outro. E
`harness-session-start.sh` oferecia retomar a mesma task em toda sessao de todo
projeto, porque a checagem era apenas `status == "active"`.

O teste central e `TestIsolamento`: dois projetos, dois estados, zero contato.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
PATHS_PY = ROOT / "scripts" / "harness_paths.py"


@pytest.fixture(scope="module")
def hp():
    spec = importlib.util.spec_from_file_location("harness_paths", PATHS_PY)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["harness_paths"] = mod
    spec.loader.exec_module(mod)
    return mod


def _repo(base: Path, name: str) -> Path:
    """Cria um diretorio que parece um repo git (basta existir `.git`)."""
    d = base / name
    (d / ".git").mkdir(parents=True)
    return d


class TestFindRepoRoot:
    def test_encontra_na_raiz(self, hp, tmp_path):
        r = _repo(tmp_path, "proj")
        assert hp.find_repo_root(str(r)) == str(r)

    def test_encontra_subindo_de_subdiretorio(self, hp, tmp_path):
        r = _repo(tmp_path, "proj")
        sub = r / "src" / "deep"
        sub.mkdir(parents=True)
        assert hp.find_repo_root(str(sub)) == str(r)

    def test_worktree_com_git_arquivo(self, hp, tmp_path):
        """Em worktree `.git` e um ARQUIVO — checar so por diretorio erraria."""
        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /outro/lugar", encoding="utf-8")
        assert hp.find_repo_root(str(wt)) == str(wt)

    def test_sem_repo_retorna_none(self, hp, tmp_path):
        d = tmp_path / "solto"
        d.mkdir()
        assert hp.find_repo_root(str(d)) is None

    def test_cwd_vazio_retorna_none(self, hp):
        assert hp.find_repo_root(None) is None
        assert hp.find_repo_root("") is None


class TestProjectSlug:
    def test_estavel_entre_chamadas(self, hp, tmp_path):
        r = _repo(tmp_path, "alpha")
        assert hp.project_slug(str(r)) == hp.project_slug(str(r))

    def test_subdiretorio_da_o_mesmo_slug_da_raiz(self, hp, tmp_path):
        r = _repo(tmp_path, "alpha")
        sub = r / "a" / "b"
        sub.mkdir(parents=True)
        assert hp.project_slug(str(sub)) == hp.project_slug(str(r))

    def test_projetos_distintos_dao_slugs_distintos(self, hp, tmp_path):
        a = _repo(tmp_path, "alpha")
        b = _repo(tmp_path, "beta")
        assert hp.project_slug(str(a)) != hp.project_slug(str(b))

    def test_mesmo_basename_em_caminhos_diferentes_nao_colide(self, hp, tmp_path):
        """Dois checkouts de mesmo nome sao projetos diferentes."""
        a = _repo(tmp_path / "x", "proj")
        b = _repo(tmp_path / "y", "proj")
        assert hp.project_slug(str(a)) != hp.project_slug(str(b))

    def test_slug_comeca_pelo_basename_legivel(self, hp, tmp_path):
        r = _repo(tmp_path, "harness4claude")
        assert hp.project_slug(str(r)).startswith("harness4claude-")

    def test_caracteres_invalidos_sanitizados(self, hp, tmp_path):
        r = _repo(tmp_path, "meu proj@2026")
        slug = hp.project_slug(str(r))
        assert not set(slug) & set(" @/\\:")


class TestScope:
    def test_default_e_por_projeto(self, hp, tmp_path, monkeypatch):
        monkeypatch.delenv("HARNESS_SCOPE", raising=False)
        r = _repo(tmp_path, "alpha")
        d = hp.state_dir(tmp_path / "root", str(r))
        assert d.parent.name == hp.PROJECTS_SUBDIR

    def test_global_usa_a_raiz(self, hp, tmp_path):
        r = _repo(tmp_path, "alpha")
        root = tmp_path / "root"
        assert hp.state_dir(root, str(r), scope="global") == root

    def test_global_via_env(self, hp, tmp_path, monkeypatch):
        monkeypatch.setenv("HARNESS_SCOPE", "GLOBAL")
        r = _repo(tmp_path, "alpha")
        root = tmp_path / "root"
        assert hp.state_dir(root, str(r)) == root

    def test_signals_sempre_na_raiz(self, hp, tmp_path):
        """Telemetria e agregada de proposito: registros sao chaveados por task_id."""
        root = tmp_path / "root"
        assert hp.signals_dir(root) == root


class TestIsolamento:
    """O bug original: contador de um projeto mexendo na classificacao de outro."""

    def test_dois_projetos_dois_diretorios(self, hp, tmp_path):
        root = tmp_path / "root"
        a = hp.ensure_state_dir(root, str(_repo(tmp_path, "alpha")))
        b = hp.ensure_state_dir(root, str(_repo(tmp_path, "beta")))
        assert a != b
        assert a.exists() and b.exists()

    def test_contador_de_um_nao_afeta_o_outro(self, hp, tmp_path):
        root = tmp_path / "root"
        a = hp.ensure_state_dir(root, str(_repo(tmp_path, "alpha")))
        b = hp.ensure_state_dir(root, str(_repo(tmp_path, "beta")))

        (a / ".session-files-count").write_text(
            json.dumps({"count": 130, "files": [], "task_id": "t-a"}), encoding="utf-8")
        (b / ".session-files-count").write_text(
            json.dumps({"count": 0, "files": [], "task_id": "t-b"}), encoding="utf-8")

        lido = json.loads((b / ".session-files-count").read_text(encoding="utf-8"))
        assert lido["count"] == 0
        assert lido["task_id"] == "t-b"

    def test_pipeline_ativo_de_um_nao_aparece_no_outro(self, hp, tmp_path):
        root = tmp_path / "root"
        a = hp.ensure_state_dir(root, str(_repo(tmp_path, "alpha")))
        b = hp.ensure_state_dir(root, str(_repo(tmp_path, "beta")))

        (a / "state.json").write_text(
            json.dumps({"task_id": "t-a", "status": "active", "pipeline": ["tdd"]}),
            encoding="utf-8")

        assert not (b / "state.json").exists(), \
            "state de um projeto nao pode ser visivel do bucket de outro"

    def test_duas_sessoes_no_mesmo_worktree_tem_estado_independente(self, hp, tmp_path):
        root = tmp_path / "root"
        repo = _repo(tmp_path, "alpha")
        a = hp.ensure_state_dir(root, str(repo), session_id="session-a")
        b = hp.ensure_state_dir(root, str(repo), session_id="session-b")

        assert a != b
        assert a.parent == b.parent
        assert a.name.startswith("session-a-")


class TestCli:
    """Contrato com os hooks em bash."""

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env.pop("HARNESS_SCOPE", None)
        return subprocess.run([sys.executable, str(PATHS_PY), *args],
                              capture_output=True, text=True, check=False, env=env)

    def test_imprime_diretorio_e_cria(self, tmp_path):
        r = _repo(tmp_path, "alpha")
        res = self._run("--root", str(tmp_path / "root"), "--cwd", str(r))
        assert res.returncode == 0, res.stderr
        assert Path(res.stdout.strip()).is_dir()

    def test_slug_isolado(self, tmp_path):
        r = _repo(tmp_path, "alpha")
        res = self._run("--root", str(tmp_path / "root"), "--cwd", str(r), "--slug")
        assert res.returncode == 0
        assert res.stdout.strip().startswith("alpha-")

    def test_signals_aponta_para_raiz(self, tmp_path):
        root = tmp_path / "root"
        res = self._run("--root", str(root), "--cwd", str(_repo(tmp_path, "alpha")), "--signals")
        assert Path(res.stdout.strip()) == root


class TestSkillResolveBucketDaSessao:
    """O comando DOCUMENTADO tem de cair no mesmo bucket em que o hook escreve.

    Incidente 2026-09-09: entre a mudanca para bucket por sessao e esta data, os
    tres blocos bash de `skills/harness-workflow/SKILL.md` chamavam
    `harness_paths.py` SEM `--session-id`. O CLI, sem ele, devolve o bucket do
    PROJETO — enquanto `harness-classify.sh` escreve no da SESSAO. Resultado:
    quem seguia o protocolo apontava `confirm_classification.py` e
    `record_signal.py` para um `state.json` orfao de `task_id: null` e recebia
    exit 2. `agreed` ficou null em 100% das tasks, e o proprio SKILL.md atribuia
    isso a desobediencia — obedecer falhava do mesmo jeito.

    O teste e sobre a DOCUMENTACAO porque o defeito estava nela: o codigo sempre
    aceitou `--session-id`.
    """

    SKILL = ROOT / "skills" / "harness-workflow" / "SKILL.md"

    def test_toda_invocacao_documentada_passa_session_id(self):
        import re

        texto = self.SKILL.read_text(encoding="utf-8")
        # So INVOCACAO conta: a linha tem de chamar o interpretador. Sem isso o
        # teste falha na prosa que apenas cita o script — inclusive a nota
        # historica que este proprio incidente deixou no SKILL.md.
        # `--slug`/`--signals` nao dependem de sessao e ficam de fora.
        chamadas = [
            linha
            for linha in re.findall(r"^.*python[^\n]*harness_paths\.py.*$", texto, flags=re.M)
            if "--slug" not in linha and "--signals" not in linha
        ]
        assert chamadas, "SKILL.md deixou de documentar a resolucao do bucket"
        sem_sessao = [c.strip() for c in chamadas if "--session-id" not in c]
        assert not sem_sessao, (
            "invocacao documentada resolve o bucket do projeto, nao o da sessao: "
            + " | ".join(sem_sessao)
        )

    def test_bucket_do_projeto_e_da_sessao_sao_diferentes(self, hp, tmp_path):
        """A premissa do teste acima: omitir a sessao muda mesmo o destino."""
        repo = _repo(tmp_path, "alpha")
        root = tmp_path / "root"
        projeto = hp.state_dir(root=root, cwd=repo)
        sessao = hp.state_dir(root=root, cwd=repo, session_id="s-1")
        assert projeto != sessao
        assert sessao.parent.parent == projeto


class TestPinDeSessao:
    """Incidente 2026-09-12: uma sessao, dois buckets, gate escalando sozinho.

    A sessao `86459dbf` trabalhou em `master_project/slb-mestrado-projeto` e em
    `science-harness`. O bucket e `projects/<slug do cwd>/sessions/<slug da
    sessao>` — projeto ANTES de sessao — e o `cwd` muda no meio da sessao. Medido
    nos dois `harness.db`:

        02:00:07  classify cria t-20260912-020007618177  -> bucket science
        02:07-02:40  agente grava 3x evidence            -> bucket slb
        02:41:18  Stop le o bucket science: 0 evidence   -> gate escalation

    A task tida por "fantasma" estava inteira no outro banco. O gate estava
    certo: `select count(*) from evidence where task_id='t-20260912-020007618177'`
    devolve 0 ate hoje.

    O conserto fixa o bucket na SESSAO. O projeto passa a ser rotulo dela.
    """

    def test_cwd_muda_no_meio_da_sessao_e_o_bucket_nao(self, hp, tmp_path):
        """O teste central. Sem ele o estado de uma sessao se parte em dois."""
        root = tmp_path / "root"
        a = _repo(tmp_path, "science-harness")
        b = _repo(tmp_path, "slb-mestrado-projeto")

        primeiro = hp.ensure_state_dir(root, str(a), session_id="s-86459dbf")
        segundo = hp.ensure_state_dir(root, str(b), session_id="s-86459dbf")

        assert primeiro == segundo, (
            "mudar de diretorio no meio da sessao partiu o estado em dois buckets"
        )

    def test_o_pin_sobrevive_a_um_processo_novo(self, hp, tmp_path):
        """Hook e tool call sao processos distintos: cache em memoria nao basta."""
        root = tmp_path / "root"
        a = _repo(tmp_path, "alpha")
        b = _repo(tmp_path, "beta")

        esperado = hp.ensure_state_dir(root, str(a), session_id="s-1")
        code = (
            "import importlib.util,sys;"
            f"spec=importlib.util.spec_from_file_location('hp',r'{PATHS_PY}');"
            "m=importlib.util.module_from_spec(spec);sys.modules['hp']=m;"
            "spec.loader.exec_module(m);"
            f"print(m.state_dir(r'{root}',r'{b}',session_id='s-1'))"
        )
        env = dict(os.environ)
        env.pop("HARNESS_SCOPE", None)
        res = subprocess.run([sys.executable, "-c", code], capture_output=True,
                             text=True, env=env)
        assert res.returncode == 0, res.stderr
        assert Path(res.stdout.strip()) == esperado

    def test_sessoes_diferentes_no_mesmo_cwd_seguem_isoladas(self, hp, tmp_path):
        """O pin nao pode desfazer o isolamento por thread."""
        root = tmp_path / "root"
        repo = _repo(tmp_path, "alpha")
        a = hp.ensure_state_dir(root, str(repo), session_id="s-a")
        b = hp.ensure_state_dir(root, str(repo), session_id="s-b")
        assert a != b

    def test_projetos_distintos_sem_sessao_seguem_isolados(self, hp, tmp_path):
        """A invariante de 2026-07-28 continua de pe: sem sessao, sem pin."""
        root = tmp_path / "root"
        a = hp.ensure_state_dir(root, str(_repo(tmp_path, "alpha")))
        b = hp.ensure_state_dir(root, str(_repo(tmp_path, "beta")))
        assert a != b

    def test_sem_session_id_o_pin_nao_existe(self, hp, tmp_path):
        """9,4% das emissoes desta maquina nao tem session_id. Nao ha chave."""
        root = tmp_path / "root"
        hp.ensure_state_dir(root, str(_repo(tmp_path, "alpha")))
        assert not (root / hp.PINS_SUBDIR).exists()

    def test_global_ignora_o_pin(self, hp, tmp_path):
        root = tmp_path / "root"
        d = hp.state_dir(root, str(_repo(tmp_path, "alpha")),
                         scope="global", session_id="s-1")
        assert d == root
        assert not (root / hp.PINS_SUBDIR).exists()

    def test_pin_corrompido_degrada_para_o_cwd(self, hp, tmp_path):
        """Invariante de `ensure_state_dir`: hook nao pode falhar."""
        root = tmp_path / "root"
        repo = _repo(tmp_path, "alpha")
        pins = root / hp.PINS_SUBDIR
        pins.mkdir(parents=True)
        (pins / f"{hp.session_slug('s-1')}.json").write_text("{ nao e json",
                                                             encoding="utf-8")

        d = hp.state_dir(root, str(repo), session_id="s-1")
        assert d == (root / hp.PROJECTS_SUBDIR / hp.project_slug(str(repo))
                     / hp.SESSIONS_SUBDIR / hp.session_slug("s-1"))

    def test_pins_bloqueado_nao_levanta(self, hp, tmp_path):
        """`pins` ocupado por um ARQUIVO: escrever e impossivel, resolver nao."""
        root = tmp_path / "root"
        root.mkdir()
        (root / hp.PINS_SUBDIR).write_text("ocupado", encoding="utf-8")
        d = hp.ensure_state_dir(root, str(_repo(tmp_path, "alpha")), session_id="s-1")
        assert d.exists()

    def test_adota_o_bucket_existente_mais_recente(self, hp, tmp_path):
        """Sessao que JA rodou antes da mudanca: o pin nasce onde o trabalho esta.

        Para a sessao `86459dbf` isso escolhe `science-harness` (mtime 02:49)
        sobre `slb` (02:40) — que e onde a task viva estava.
        """
        root = tmp_path / "root"
        velho = _repo(tmp_path, "slb-mestrado-projeto")
        novo = _repo(tmp_path, "science-harness")
        outro = _repo(tmp_path, "harness4claude")
        sess = hp.session_slug("s-86459dbf")

        for repo, quando in ((velho, 1_700_000_000), (novo, 1_700_000_600)):
            b = root / hp.PROJECTS_SUBDIR / hp.project_slug(str(repo)) / hp.SESSIONS_SUBDIR / sess
            b.mkdir(parents=True)
            os.utime(b, (quando, quando))

        d = hp.state_dir(root, str(outro), session_id="s-86459dbf")
        assert d.parent.parent.name == hp.project_slug(str(novo)), (
            "adocao escolheu o bucket abandonado em vez do que tem trabalho vivo"
        )

    def test_a_deriva_fica_registrada(self, hp, tmp_path):
        """Discordancia visivel e o que separa este conserto de um pin mudo."""
        root = tmp_path / "root"
        a = _repo(tmp_path, "alpha")
        b = _repo(tmp_path, "beta")
        hp.ensure_state_dir(root, str(a), session_id="s-1")
        hp.ensure_state_dir(root, str(b), session_id="s-1")

        pin = json.loads((root / hp.PINS_SUBDIR / f"{hp.session_slug('s-1')}.json")
                         .read_text(encoding="utf-8"))
        assert pin["project_slug"] == hp.project_slug(str(a))
        assert hp.project_slug(str(b)) in [d["project_slug"] for d in pin["drifts"]]

    def test_deriva_repetida_nao_incha_o_pin(self, hp, tmp_path):
        """O mesmo `cd` repetido em 200 prompts nao pode virar 200 linhas."""
        root = tmp_path / "root"
        a = _repo(tmp_path, "alpha")
        b = _repo(tmp_path, "beta")
        hp.ensure_state_dir(root, str(a), session_id="s-1")
        for _ in range(5):
            hp.ensure_state_dir(root, str(b), session_id="s-1")

        pin = json.loads((root / hp.PINS_SUBDIR / f"{hp.session_slug('s-1')}.json")
                         .read_text(encoding="utf-8"))
        assert len(pin["drifts"]) == 1

    def test_cli_com_a_mesma_sessao_cai_no_mesmo_lugar(self, tmp_path):
        """O contrato que o agente e os hooks usam de fato."""
        env = dict(os.environ)
        env.pop("HARNESS_SCOPE", None)
        root = tmp_path / "root"
        saidas = []
        for nome in ("science-harness", "slb-mestrado-projeto"):
            r = tmp_path / nome
            (r / ".git").mkdir(parents=True)
            res = subprocess.run(
                [sys.executable, str(PATHS_PY), "--root", str(root),
                 "--cwd", str(r), "--session-id", "86459dbf"],
                capture_output=True, text=True, env=env)
            assert res.returncode == 0, res.stderr
            saidas.append(res.stdout.strip())
        assert saidas[0] == saidas[1]


class TestPinVence:
    """O pin e do TRECHO DE TRABALHO, nao do id da sessao para sempre.

    Medido em 2026-09-21 no pin real de `86459dbf`:

        pinned_at    2026-09-12T08:12:52   project_slug science-harness-f34c6792
        deriva       2026-09-12T16:03:22   slb-mestrado-projeto
        deriva       2026-09-15T18:59:18   mainframe
        deriva       2026-09-17T17:35:56   harness4claude
        atividade    2026-09-21T09:03-15:40, toda ela em slb-mestrado-projeto

    Nove dias e quatro projetos depois, o estado ainda ia para o balde cunhado
    no primeiro dia: 110 tasks em `science-harness` contra 27 em `slb`, e o
    portao que bloqueou a sessao lia a task no banco de um repositorio que ela
    nao tocou. O pin resolveu o problema de 2026-09-12 (o cd no meio do
    trabalho) e criou o de 2026-09-21 (a sessao que sobrevive ao proprio pin).

    A regua tem de separar os dois casos, e por isso os testes vem em par: o
    pin so cede quando ficou parado mais que o TTL E o projeto corrente e
    outro. Qualquer regra que ceda so por uma das metades reabre o incidente
    de 2026-09-12.
    """

    def test_pin_parado_alem_do_ttl_em_outro_projeto_repina(self, hp, tmp_path, monkeypatch):
        """Primeira metade: a sessao retomada dias depois nao leva o balde velho."""
        monkeypatch.setenv("HARNESS_PIN_TTL_H", "24")
        root = tmp_path / "root"
        velho = _repo(tmp_path, "science-harness")
        novo = _repo(tmp_path, "slb-mestrado-projeto")

        hp.ensure_state_dir(root, str(velho), session_id="s-86459dbf")
        arquivo = root / hp.PINS_SUBDIR / f"{hp.session_slug('s-86459dbf')}.json"
        pin = json.loads(arquivo.read_text(encoding="utf-8"))
        pin["pinned_at"] = "2026-09-12T08:12:52+00:00"
        pin["last_seen_at"] = "2026-09-12T08:12:52+00:00"
        arquivo.write_text(json.dumps(pin), encoding="utf-8")

        destino = hp.state_dir(root, str(novo), session_id="s-86459dbf")
        assert destino.parent.parent.name == hp.project_slug(str(novo)), (
            "pin parado ha nove dias continuou mandando o estado para o balde "
            "do projeto anterior"
        )

    def test_pin_vigente_em_outro_projeto_nao_cede(self, hp, tmp_path, monkeypatch):
        """Segunda metade: o incidente de 2026-09-12 continua consertado.

        Sem esta, "pin com validade" e "pin removido" dao o mesmo verde.
        """
        monkeypatch.setenv("HARNESS_PIN_TTL_H", "24")
        root = tmp_path / "root"
        a = _repo(tmp_path, "science-harness")
        b = _repo(tmp_path, "slb-mestrado-projeto")

        primeiro = hp.ensure_state_dir(root, str(a), session_id="s-viva")
        segundo = hp.ensure_state_dir(root, str(b), session_id="s-viva")
        assert primeiro == segundo, "o pin cedeu dentro do proprio trecho de trabalho"

    def test_pin_parado_no_mesmo_projeto_nao_muda_nada(self, hp, tmp_path, monkeypatch):
        """Retomar a sessao no MESMO projeto nao e motivo para trocar de balde."""
        monkeypatch.setenv("HARNESS_PIN_TTL_H", "24")
        root = tmp_path / "root"
        repo = _repo(tmp_path, "alpha")

        primeiro = hp.ensure_state_dir(root, str(repo), session_id="s-1")
        arquivo = root / hp.PINS_SUBDIR / f"{hp.session_slug('s-1')}.json"
        pin = json.loads(arquivo.read_text(encoding="utf-8"))
        pin["last_seen_at"] = "2020-01-01T00:00:00+00:00"
        arquivo.write_text(json.dumps(pin), encoding="utf-8")

        assert hp.state_dir(root, str(repo), session_id="s-1") == primeiro

    def test_o_pin_anterior_fica_escrito(self, hp, tmp_path, monkeypatch):
        """Trocar de balde em silencio e como perder o historico do trabalho."""
        monkeypatch.setenv("HARNESS_PIN_TTL_H", "24")
        root = tmp_path / "root"
        velho = _repo(tmp_path, "science-harness")
        novo = _repo(tmp_path, "slb-mestrado-projeto")

        hp.ensure_state_dir(root, str(velho), session_id="s-1")
        arquivo = root / hp.PINS_SUBDIR / f"{hp.session_slug('s-1')}.json"
        pin = json.loads(arquivo.read_text(encoding="utf-8"))
        pin["last_seen_at"] = "2020-01-01T00:00:00+00:00"
        arquivo.write_text(json.dumps(pin), encoding="utf-8")

        hp.state_dir(root, str(novo), session_id="s-1")
        pin = json.loads(arquivo.read_text(encoding="utf-8"))
        assert pin["project_slug"] == hp.project_slug(str(novo))
        anteriores = [r.get("project_slug") for r in pin.get("repins", [])]
        assert hp.project_slug(str(velho)) in anteriores, (
            "o balde anterior sumiu do pin; quem procurar o estado antigo nao "
            "tem por onde comecar"
        )

    def test_atividade_seguida_no_mesmo_projeto_renova_o_pin(self, hp, tmp_path, monkeypatch):
        """last_seen_at avanca, senao um trecho longo venceria sozinho."""
        monkeypatch.setenv("HARNESS_PIN_TTL_H", "24")
        root = tmp_path / "root"
        repo = _repo(tmp_path, "alpha")
        hp.ensure_state_dir(root, str(repo), session_id="s-1")
        arquivo = root / hp.PINS_SUBDIR / f"{hp.session_slug('s-1')}.json"
        pin = json.loads(arquivo.read_text(encoding="utf-8"))
        pin["last_seen_at"] = "2020-01-01T00:00:00+00:00"
        arquivo.write_text(json.dumps(pin), encoding="utf-8")

        hp.state_dir(root, str(repo), session_id="s-1")
        pin = json.loads(arquivo.read_text(encoding="utf-8"))
        assert pin["last_seen_at"] > "2020-01-02", "o pin nao renovou com atividade"

    def test_ttl_desligado_mantem_o_comportamento_anterior(self, hp, tmp_path, monkeypatch):
        """HARNESS_PIN_TTL_H=0 volta ao pin permanente, por opt-in."""
        monkeypatch.setenv("HARNESS_PIN_TTL_H", "0")
        root = tmp_path / "root"
        velho = _repo(tmp_path, "alpha")
        novo = _repo(tmp_path, "beta")
        primeiro = hp.ensure_state_dir(root, str(velho), session_id="s-1")
        arquivo = root / hp.PINS_SUBDIR / f"{hp.session_slug('s-1')}.json"
        pin = json.loads(arquivo.read_text(encoding="utf-8"))
        pin["last_seen_at"] = "2020-01-01T00:00:00+00:00"
        arquivo.write_text(json.dumps(pin), encoding="utf-8")

        assert hp.state_dir(root, str(novo), session_id="s-1") == primeiro

    def test_pin_sem_last_seen_usa_pinned_at(self, hp, tmp_path, monkeypatch):
        """Pin gravado antes deste campo nao pode virar erro nem repin falso."""
        monkeypatch.setenv("HARNESS_PIN_TTL_H", "24")
        root = tmp_path / "root"
        a = _repo(tmp_path, "alpha")
        b = _repo(tmp_path, "beta")
        primeiro = hp.ensure_state_dir(root, str(a), session_id="s-1")
        arquivo = root / hp.PINS_SUBDIR / f"{hp.session_slug('s-1')}.json"
        pin = json.loads(arquivo.read_text(encoding="utf-8"))
        pin.pop("last_seen_at", None)
        arquivo.write_text(json.dumps(pin), encoding="utf-8")

        # `pinned_at` e de agora: o pin esta vigente e nao cede.
        assert hp.state_dir(root, str(b), session_id="s-1") == primeiro

    def test_data_ilegivel_no_pin_nao_levanta(self, hp, tmp_path, monkeypatch):
        """ensure_state_dir promete nao levantar. A validade nao quebra isso."""
        monkeypatch.setenv("HARNESS_PIN_TTL_H", "24")
        root = tmp_path / "root"
        a = _repo(tmp_path, "alpha")
        b = _repo(tmp_path, "beta")
        hp.ensure_state_dir(root, str(a), session_id="s-1")
        arquivo = root / hp.PINS_SUBDIR / f"{hp.session_slug('s-1')}.json"
        pin = json.loads(arquivo.read_text(encoding="utf-8"))
        pin["last_seen_at"] = "nao e data"
        pin["pinned_at"] = "tambem nao"
        arquivo.write_text(json.dumps(pin), encoding="utf-8")

        destino = hp.ensure_state_dir(root, str(b), session_id="s-1")
        assert destino.exists()

    def test_repin_preserva_as_derivas_do_pin_anterior(self, hp, tmp_path, monkeypatch):
        """As derivas sao o unico registro de por onde a sessao passou."""
        monkeypatch.setenv("HARNESS_PIN_TTL_H", "24")
        root = tmp_path / "root"
        velho = _repo(tmp_path, "science-harness")
        meio = _repo(tmp_path, "mainframe")
        novo = _repo(tmp_path, "slb-mestrado-projeto")

        hp.ensure_state_dir(root, str(velho), session_id="s-1")
        hp.ensure_state_dir(root, str(meio), session_id="s-1")
        arquivo = root / hp.PINS_SUBDIR / f"{hp.session_slug('s-1')}.json"
        pin = json.loads(arquivo.read_text(encoding="utf-8"))
        assert [d["project_slug"] for d in pin["drifts"]] == [hp.project_slug(str(meio))]
        pin["last_seen_at"] = "2020-01-01T00:00:00+00:00"
        arquivo.write_text(json.dumps(pin), encoding="utf-8")

        hp.state_dir(root, str(novo), session_id="s-1")
        pin = json.loads(arquivo.read_text(encoding="utf-8"))
        assert pin["drifts"] == [], "a deriva do pin anterior seguiu viva no novo"
        guardadas = [
            d["project_slug"] for r in pin["repins"] for d in r.get("drifts", [])
        ]
        assert hp.project_slug(str(meio)) in guardadas, (
            "o repin apagou o registro dos projetos por onde a sessao passou"
        )
