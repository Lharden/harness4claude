#!/usr/bin/env python3
"""
Harness v3 — Suite de Testes Automatizados
==========================================
Testa todos os hooks e cenários de falha do sistema Harness v3.

Uso:
    python test_harness.py              # roda todos os testes
    python test_harness.py -v           # verbose
    python test_harness.py -k classify  # só testes de classificação

Cenários de falha mapeados, organizados por hook:
  - harness-classify.sh:    17 cenários (10 base + machine-prompt guards)
  - harness-reclassify.sh:  11 cenários (contagem, promoção, travas de state)
  - harness-git-guard.sh:    7 cenários
  - harness-precompact.sh:   2 cenários
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
# Path resolution: plugin mode (HARNESS_PLUGIN_ROOT) or standalone
_PLUGIN_ROOT = os.environ.get(
    "HARNESS_PLUGIN_ROOT",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
HOME = os.path.expanduser("~")
HOOKS_DIR = os.path.join(_PLUGIN_ROOT, "hooks")
SKILLS_DIR = os.path.join(_PLUGIN_ROOT, "skills")
SCHEMAS_DIR = os.path.join(_PLUGIN_ROOT, "schemas")
HARNESS_DIR = os.path.join(HOME, ".claude", "harness")
STATE_FILE = os.path.join(HARNESS_DIR, "state.json")
COUNTER_FILE = os.path.join(HARNESS_DIR, ".session-files-count")
TRACE_FILE = os.path.join(HARNESS_DIR, "trace-current.md")

# Detect bash
_bash_path = shutil.which("bash")
if not _bash_path:
    sys.exit("ERRO: bash não encontrado no PATH")
BASH: str = _bash_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def run_hook(
    hook_name: str,
    stdin_data: dict,
    *,
    timeout: int = 15,
    env_extra: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """Executa um hook com JSON no stdin. Retorna (exit_code, stdout, stderr)."""
    hook_path = os.path.join(HOOKS_DIR, hook_name)
    proc = subprocess.run(
        [BASH, hook_path],
        input=json.dumps(stdin_data),
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, "PYTHONUTF8": "1", **(env_extra or {})},
    )
    return proc.returncode, proc.stdout, proc.stderr


def write_state(data: dict) -> None:
    """Escreve state.json para setup de testes."""
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def read_state() -> dict:
    """Lê state.json após execução de hook."""
    with open(STATE_FILE, encoding="utf-8") as f:
        return json.load(f)


def write_counter(data: dict) -> None:
    """Escreve .session-files-count para setup de testes."""
    with open(COUNTER_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def read_counter() -> dict:
    """Lê .session-files-count após execução de hook."""
    with open(COUNTER_FILE, encoding="utf-8") as f:
        return json.load(f)


def fresh_state() -> None:
    """Reseta state.json para estado limpo (sem pipeline ativo)."""
    write_state({"task_id": None, "classification": None, "status": None})


def fresh_counter() -> None:
    """Reseta counter file."""
    write_counter({"count": 0, "files": [], "task_id": None})


# ---------------------------------------------------------------------------
# Backup / Restore fixtures
# ---------------------------------------------------------------------------
class HarnessTestBase(unittest.TestCase):
    """Base class que faz backup e restore dos arquivos do harness."""

    _backup_dir: str

    @classmethod
    def setUpClass(cls) -> None:
        # Garante que o diretorio do harness existe antes do backup. Sem isto,
        # ambientes ainda nao-bootstrapped (CI limpo, Desktop App recem-instalado,
        # sandbox de health-check) falham com FileNotFoundError no setUpClass.
        os.makedirs(HARNESS_DIR, exist_ok=True)
        cls._backup_dir = tempfile.mkdtemp(prefix="harness-test-backup-")
        for fname in ("state.json", ".session-files-count", "trace-current.md"):
            src = os.path.join(HARNESS_DIR, fname)
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(cls._backup_dir, fname))

    @classmethod
    def tearDownClass(cls) -> None:
        for fname in ("state.json", ".session-files-count", "trace-current.md"):
            backup = os.path.join(cls._backup_dir, fname)
            dst = os.path.join(HARNESS_DIR, fname)
            if os.path.exists(backup):
                shutil.copy2(backup, dst)
            elif os.path.exists(dst):
                # Se não havia backup mas o teste criou, limpa
                pass
        shutil.rmtree(cls._backup_dir, ignore_errors=True)

    def setUp(self) -> None:
        fresh_state()
        fresh_counter()


# ===========================================================================
# CLASSIFY TESTS (harness-classify.sh) — 10 cenários
# ===========================================================================
class TestClassify(HarnessTestBase):
    """Testa harness-classify.sh em 10 cenários diferentes."""

    HOOK = "harness-classify.sh"

    # ------------------------------------------------------------------
    # Helpers — aceitam ambos formatos de output:
    #   L0: <harness-classification> block (XML-like)
    #   L1+: {"systemMessage": "HARNESS v3 CLASSIFIED: ..."} (JSON)
    #   Active pipeline: {"systemMessage": "HARNESS v3 CONTINUING: ..."} (JSON)
    # ------------------------------------------------------------------
    def assert_classified(self, out: str, level: str | None = None,
                          task_type: str | None = None) -> None:
        """Valida que output contém classificação em qualquer formato."""
        is_classified = ("<harness-classification>" in out
                         or "HARNESS v3 CLASSIFIED" in out)
        self.assertTrue(is_classified,
                        f"Output não contém classificação. Output: {out[:500]}")
        if level:
            # Level pode aparecer como "level: L1" (XML) ou "L1-" (systemMessage)
            has_level = (f"level: {level}" in out
                         or f"{level}-" in out
                         or f"classification: {level}" in out)
            self.assertTrue(has_level,
                            f"Level {level} não encontrado. Output: {out[:500]}")
        if task_type:
            has_type = (f"type: {task_type}" in out
                        or f"-{task_type}" in out)
            self.assertTrue(has_type,
                            f"Type {task_type} não encontrado. Output: {out[:500]}")

    def assert_continuation(self, out: str, task_id: str | None = None) -> None:
        """Valida continuação de pipeline ativo (XML ou systemMessage)."""
        is_continuation = ("<harness-continuation>" in out
                           or "HARNESS v3 CONTINUING" in out)
        self.assertTrue(is_continuation,
                        f"Output não é continuação. Output: {out[:500]}")
        if task_id:
            self.assertIn(task_id, out)

    # --- Cenário 1: Campo correto (user_prompt) ---
    def test_01_correct_field_user_prompt(self):
        """user_prompt deve ser lido corretamente (campo oficial do Claude Code)."""
        code, out, err = run_hook(self.HOOK, {"user_prompt": "o que é python?"})
        self.assertIn("<harness-classification>", out, "Hook deve emitir bloco de classificação")
        self.assertIn("L0", out, "Pergunta simples deve ser L0")

    # --- Cenário 2: Campo legado (user_message) mantém compatibilidade ---
    def test_02_legacy_field_user_message(self):
        """user_message (campo legado) deve funcionar como fallback."""
        code, out, err = run_hook(self.HOOK, {"user_message": "explique decorators"})
        self.assertIn("<harness-classification>", out)
        self.assertIn("L0", out)

    # --- Cenário 3: Classificação L0 (pergunta) ---
    def test_03_classify_l0_question(self):
        """Pergunta pura sem keywords de ação deve ser L0."""
        code, out, err = run_hook(self.HOOK, {"user_prompt": "o que é um singleton?"})
        self.assertEqual(code, 0)
        self.assertIn("level: L0", out)
        state = read_state()
        self.assertEqual(state["status"], "done", "L0 deve ter status 'done'")

    # --- Cenário 4: Classificação L1-bug ---
    def test_04_classify_l1_bug(self):
        """Relato de bug deve classificar como L1-bug."""
        code, out, err = run_hook(self.HOOK, {
            "user_prompt": "tem um bug na função de login, dá traceback"
        })
        self.assert_classified(out, level="L1", task_type="bug")
        state = read_state()
        self.assertEqual(state["status"], "active")
        self.assertIn("systematic-debugging", state["pipeline"])

    # --- Cenário 5: Classificação L1-refactor ---
    def test_05_classify_l1_refactor(self):
        """Pedido de refatoração deve classificar como L1-refactor."""
        code, out, err = run_hook(self.HOOK, {
            "user_prompt": "refatora a classe UserService para separar responsabilidades"
        })
        self.assert_classified(out, level="L1", task_type="refactor")

    # --- Cenário 6: Classificação L2-feature ---
    def test_06_classify_l2_feature(self):
        """Nova feature com escopo amplo deve classificar como L2-feature."""
        code, out, err = run_hook(self.HOOK, {
            "user_prompt": "cria um sistema completo de notificações push"
        })
        self.assert_classified(out, level="L2", task_type="feature")
        state = read_state()
        self.assertIn("brainstorming", state["pipeline"])
        # v3 pode usar write-spec ao invés de write-a-prd — aceitar ambos
        self.assertTrue(
            "write-a-prd" in state["pipeline"] or "write-spec" in state["pipeline"],
            f"Pipeline deve ter write-a-prd ou write-spec. Got: {state['pipeline']}"
        )

    # --- Cenário 7: Classificação L2-architecture ---
    def test_07_classify_l2_architecture(self):
        """Mudança arquitetural deve classificar como L2-architecture."""
        code, out, err = run_hook(self.HOOK, {
            "user_prompt": "migra a arquitetura do monolito para microserviços"
        })
        self.assertIn("L2", out)
        self.assertIn("architecture", out)

    # --- Cenário 8: Input vazio / JSON malformado ---
    def test_08_empty_and_malformed_input(self):
        """Input vazio ou malformado deve sair silenciosamente (exit 0)."""
        # Input vazio
        code, out, _ = run_hook(self.HOOK, {})
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "", "Sem user_prompt, não deve emitir nada")

        # JSON com campo irrelevante
        fresh_state()
        code, out, _ = run_hook(self.HOOK, {"random_field": "hello"})
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "")

    # --- Cenário 9: Unicode e caracteres especiais ---
    def test_09_unicode_special_chars(self):
        """Mensagens com acentos devem ser normalizadas (é→e, ã→a, ç→c)."""
        # "integração" deve normalizar para "integracao" e matchear L2
        code, out, _ = run_hook(self.HOOK, {
            "user_prompt": "cria módulo de autenticação com OAuth2 — integração completa"
        })
        self.assertEqual(code, 0)
        # "integracao" (normalizado de integração) matcha \bintegracao\b = L2
        self.assert_classified(out, level="L2")

    def test_09b_unicode_mu_no_charmap_error(self):
        """Regressão: caractere μ (U+03BC) não deve causar charmap codec error
        mesmo SEM PYTHONUTF8=1 no env (simula produção Windows/MSYS2)."""
        env = {k: v for k, v in os.environ.items()
               if k not in ("PYTHONUTF8", "PYTHONIOENCODING", "LANG", "LC_ALL")}
        proc = subprocess.run(
            [BASH, os.path.join(HOOKS_DIR, "harness-classify.sh")],
            input=json.dumps({"user_prompt": "adicionar campo de tempo em μs (microsegundos)"}),
            capture_output=True,
            text=True,
            timeout=15,
            env=env,
        )
        self.assertNotIn("charmap", proc.stderr.lower(),
                         f"Bug UTF-8 ainda presente. stderr: {proc.stderr}")
        self.assertNotIn("codec can't encode", proc.stderr,
                         f"Codec error ainda presente. stderr: {proc.stderr}")
        self.assertEqual(proc.returncode, 0,
                         f"Hook deve ter sucesso mesmo com μ. stderr: {proc.stderr}")

    # --- Cenário 10: Task switch detection ---
    def test_10_task_switch_resets_pipeline(self):
        """Palavras de troca de tarefa devem resetar pipeline ativo."""
        # Primeiro, cria pipeline ativo
        write_state({
            "task_id": "t-test-active",
            "classification": "L2-feature",
            "status": "active",
            "pipeline": ["brainstorming", "tdd"],
            "current_step": "brainstorming",
            "artifacts_so_far": [],
            "started_at": "2026-01-01T00:00:00Z"
        })

        # Sem task switch → deve emitir continuation
        code, out, _ = run_hook(self.HOOK, {
            "user_prompt": "continua com o próximo passo"
        })
        self.assert_continuation(out)

        # Com task switch → deve reclassificar
        code, out, _ = run_hook(self.HOOK, {
            "user_prompt": "nova tarefa: adiciona validação no formulário"
        })
        self.assert_classified(out, level="L1")
        self.assertNotIn("CONTINUING", out)

    # --- Cenário 11: Continuation de pipeline ativo ---
    def test_11_active_pipeline_continuation(self):
        """Pipeline ativo deve emitir continuation em vez de reclassificar."""
        write_state({
            "task_id": "t-test-cont",
            "classification": "L1-bug",
            "status": "active",
            "pipeline": ["systematic-debugging", "tdd"],
            "current_step": "systematic-debugging",
            "artifacts_so_far": [],
            "started_at": "2026-01-01T00:00:00Z"
        })

        code, out, _ = run_hook(self.HOOK, {
            "user_prompt": "achei mais informações sobre o erro"
        })
        self.assert_continuation(out, task_id="t-test-cont")
        self.assertIn("L1-bug", out)

    # --- Cenário 12: Mensagem longa (stress test) ---
    def test_12_very_long_message(self):
        """Mensagem muito longa não deve causar overflow ou timeout."""
        long_msg = "implementa " + "funcionalidade de teste " * 500  # ~11KB
        code, out, err = run_hook(self.HOOK, {"user_prompt": long_msg}, timeout=15)
        self.assertEqual(code, 0)
        # Deve classificar normalmente (pode ser L1 ou L2 dependendo dos keywords)
        self.assert_classified(out)

    # --- Cenário 13: L2 deve vencer L1 em ambiguidade ---
    def test_13_l2_wins_over_l1(self):
        """Quando mensagem tem keywords L1 e L2, L2 deve prevalecer."""
        code, out, _ = run_hook(self.HOOK, {
            "user_prompt": "implementa um novo sistema de cache distribuído"
        })
        # "implementa" = L1, "sistema" = L2. L2 deve vencer.
        self.assertIn("L2", out, "L2 deve prevalecer sobre L1 em caso de empate")

    # --- Cenário 14: state.json escrito com schema correto ---
    def test_14_state_schema_correct(self):
        """state.json deve ter todos os campos obrigatórios após classificação."""
        run_hook(self.HOOK, {"user_prompt": "adiciona testes unitários"})
        state = read_state()

        required_fields = ["task_id", "classification", "status", "pipeline",
                           "current_step", "artifacts_so_far", "started_at"]
        for field in required_fields:
            self.assertIn(field, state, f"Campo '{field}' faltando no state.json")

        self.assertTrue(state["task_id"].startswith("t-"), "task_id deve começar com 't-'")
        self.assertIsInstance(state["pipeline"], list)
        self.assertIsInstance(state["artifacts_so_far"], list)

    # ------------------------------------------------------------------
    # Cenários 31-35: guards contra prompts de máquina
    # Incidente 2026-06-12 (t-20260612-034438): a sessão headless do
    # remember (sumarizador de daily log) envia um prompt gigante embutindo
    # o extrato da conversa. Frases do extrato ("outra coisa") casavam
    # SWITCH_PATTERNS por substring e keywords ("bug", "erro", "falha",
    # "pipeline") classificavam L2-bug — sobrescrevendo a task ativa
    # t-20260612-033900 de outra sessão no state.json global.
    # ------------------------------------------------------------------

    def _active_state(self, task_id: str) -> None:
        """Configura um pipeline L1-feature ativo (como no incidente)."""
        write_state({
            "task_id": task_id,
            "schema_version": 3,
            "classification": "L1-feature",
            "classification_meta": {
                "suggested": "L1-feature",
                "final": "L1-feature",
                "source": "semantic",
                "confidence": 0.9,
                "agreed": True,
            },
            "status": "active",
            "pipeline": ["write-spec-light", "tdd", "verify-against-spec"],
            "current_step": "tdd",
            "artifacts_so_far": [],
            "started_at": "2026-06-12T03:39:00+00:00",
        })
        write_counter({"count": 2, "files": ["C:/a.py", "C:/b.py"], "task_id": task_id})

    # --- Cenário 31: prompt gigante de automação não toca o state ---
    def test_31_machine_prompt_never_touches_state(self):
        """Reprodução fiel do incidente: prompt ~160KB com switch words e
        keywords de bug, pipeline ativo → state intocado, sem classificação."""
        self._active_state("t-test-incident")
        filler = (
            "discutimos um bug com erro de falha no pipeline do sistema. "
            "ou seguimos para outra coisa?\n"
        )
        huge = (
            "You are summarizing a Claude Code session for a daily memory log.\n"
            + filler * 1800  # ~160KB, como o prompt real do remember
        )
        code, out, _ = run_hook(self.HOOK, {"prompt": huge})
        self.assertEqual(code, 0)
        state = read_state()
        self.assertEqual(
            state["task_id"], "t-test-incident",
            "prompt de máquina sobrescreveu task ativa (incidente t-20260612-034438)"
        )
        self.assertEqual(state["classification"], "L1-feature")
        self.assertEqual(state["current_step"], "tdd")
        self.assertNotIn("CLASSIFIED", out, "prompt de máquina não pode gerar classificação")
        # counter da task ativa também não pode ser resetado
        self.assertEqual(read_counter()["task_id"], "t-test-incident")

    # --- Cenário 31b: prompt médio com switch substring não mata pipeline ---
    def test_31b_medium_prompt_with_switch_words_continues(self):
        """Prompt de alguns KB contendo 'outra coisa' não é comando humano de
        troca de tarefa — pipeline ativo deve emitir CONTINUING."""
        self._active_state("t-test-medium")
        msg = (
            "segue o log da conversa para resumo: o usuário perguntou sobre o "
            "deploy e disse: vamos ver outra coisa depois. houve um erro 40012 "
            "e uma FALHA no teste do pipeline.\n"
        ) * 30  # ~5KB — acima do limite de switch, abaixo do limite de classify
        code, out, _ = run_hook(self.HOOK, {"prompt": msg})
        self.assertEqual(code, 0)
        self.assert_continuation(out)
        self.assertEqual(read_state()["task_id"], "t-test-medium")

    # --- Cenário 32: switch patterns exigem word boundary ---
    def test_32_switch_requires_word_boundary(self):
        """'cancelamento' contém 'cancela' mas não é troca de tarefa."""
        self._active_state("t-test-wb")
        code, out, _ = run_hook(self.HOOK, {
            "user_prompt": "o cancelamento do pedido esta retornando 500"
        })
        self.assertEqual(code, 0)
        self.assert_continuation(out)
        self.assertEqual(
            read_state()["task_id"], "t-test-wb",
            "substring de switch word não pode derrubar pipeline ativo"
        )

    # --- Cenário 33: switch explícito curto continua funcionando ---
    def test_33_short_explicit_switch_still_works(self):
        """Comando humano curto de troca de tarefa deve criar task nova."""
        self._active_state("t-test-switch")
        code, out, _ = run_hook(self.HOOK, {
            "user_prompt": "esquece isso, muda de assunto: otimiza o cache do parser"
        })
        self.assertEqual(code, 0)
        self.assert_classified(out)
        self.assertNotEqual(read_state()["task_id"], "t-test-switch")

    # --- Cenário 34: prompt gigante não cria task nem quando idle ---
    def test_34_huge_prompt_never_creates_task_when_idle(self):
        """Sem pipeline ativo, prompt acima do limite também não vira task."""
        # setUp deixou fresh_state (task_id None, status None)
        huge = "corrige o bug do modulo de pagamentos agora mesmo. " * 800  # ~40KB
        code, out, _ = run_hook(self.HOOK, {"prompt": huge})
        self.assertEqual(code, 0)
        self.assertIsNone(read_state()["task_id"], "prompt gigante não deve criar task")
        self.assertNotIn("CLASSIFIED", out)

    # --- Cenário 35: state cacheia o prompt que originou a task ---
    def test_35_state_caches_prompt_excerpt(self):
        """O prompt original fica auditável no state (prompt_excerpt/prompt_len).
        Qualquer reclassificação futura usa este cache — nunca tool output."""
        prompt = "adiciona suporte a webhooks no servico de notificacoes"
        run_hook(self.HOOK, {"user_prompt": prompt})
        state = read_state()
        self.assertIn("prompt_excerpt", state)
        self.assertIn("adiciona suporte a webhooks", state["prompt_excerpt"])
        self.assertEqual(state["prompt_len"], len(prompt))

    # --- Cenário 36: sumarizador na dead zone (~16KB) não cria task idle ---
    def test_36_summarizer_preamble_never_creates_task_idle(self):
        """Regressão t-20260612-155238: o prompt do sumarizador do remember
        (~16KB) cai na dead zone entre MAX_SWITCH_LEN (1500) e MAX_CLASSIFY_LEN
        (30000). Sem pipeline ativo ele passava o guard de comprimento e criava
        task fantasma no state.json GLOBAL. A assinatura de automação deve barrá-lo
        em qualquer tamanho."""
        # setUp deixou fresh_state (task_id None, status None)
        body = (
            "read the conversation extract below and write one memory entry. "
            "discutimos um bug com erro de falha no pipeline do sistema.\n"
        )
        summarizer = (
            "You are summarizing a Claude Code session for a daily memory log.\n"
            + body * 180
        )
        self.assertTrue(
            1500 < len(summarizer) < 30000,
            f"prompt deve cair na dead zone, tem {len(summarizer)} chars"
        )
        code, out, _ = run_hook(self.HOOK, {"prompt": summarizer})
        self.assertEqual(code, 0)
        self.assertIsNone(
            read_state()["task_id"],
            "sumarizador não pode criar task (state global sobrescrito)"
        )
        self.assertNotIn("CLASSIFIED", out)

    # --- Cenário 36b: sumarizador não toca pipeline ativo (independe do status) ---
    def test_36b_summarizer_preamble_never_touches_active(self):
        """O mesmo sumarizador (~16KB) com pipeline ATIVO: a assinatura sai antes
        de qualquer leitura do state — task ativa intacta, sem CLASSIFIED e sem
        CONTINUING (prompt de máquina não gera nenhuma saída do harness)."""
        self._active_state("t-test-summ-active")
        summarizer = (
            "You are summarizing a Claude Code session for a daily memory log.\n"
            + ("linha de log qualquer do extrato da conversa.\n" * 250)
        )
        code, out, _ = run_hook(self.HOOK, {"prompt": summarizer})
        self.assertEqual(code, 0)
        self.assertEqual(
            read_state()["task_id"], "t-test-summ-active",
            "sumarizador não pode tocar pipeline ativo"
        )
        self.assertNotIn("CLASSIFIED", out)
        self.assertNotIn("CONTINUING", out)


# ===========================================================================
# RECLASSIFY TESTS (harness-reclassify.sh) — 4 cenários
# ===========================================================================
class TestSDDInfrastructure(HarnessTestBase):
    """Testa infraestrutura SDD v3: schemas, templates, skill files."""

    def test_spec_schema_is_valid_json_schema(self):
        """Schema de spec deve ser JSON Schema Draft 2020-12 válido."""
        schema_path = os.path.join(SCHEMAS_DIR, "spec.schema.json")
        self.assertTrue(os.path.exists(schema_path), f"Schema não existe: {schema_path}")
        with open(schema_path, "r", encoding="utf-8") as f:
            schema = json.load(f)
        self.assertTrue(
            schema.get("$schema", "").endswith("2020-12/schema"),
            "Deve ser Draft 2020-12"
        )
        self.assertIn("properties", schema)
        self.assertIn("user_stories", schema["properties"])
        self.assertIn("boundaries", schema["properties"])
        self.assertIn("needs_clarification", schema["properties"])

    def test_spec_template_has_required_sections(self):
        """Template spec-template.md deve ter todas as seções obrigatórias."""
        path = os.path.join(
            SKILLS_DIR,
            "write-spec", "templates", "spec-template.md"
        )
        self.assertTrue(os.path.exists(path), f"Template não existe: {path}")
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        required = [
            "# Spec:", "**Status**:", "## Executive Summary", "## User Stories",
            "### US-", "Given", "When", "Then",
            "## Requirements", "### Functional", "### Non-Functional",
            "## Boundaries", "ALWAYS", "NEVER", "ASK",
            "## [NEEDS CLARIFICATION]", "## Success Criteria"
        ]
        for section in required:
            self.assertIn(section, content, f"Template faltando seção: {section}")

    def test_spec_light_template_is_concise(self):
        """Template spec-light deve ser enxuto (< 80 linhas) mas ter seções mínimas."""
        path = os.path.join(
            SKILLS_DIR,
            "write-spec-light", "templates", "spec-light-template.md"
        )
        self.assertTrue(os.path.exists(path), f"Template não existe: {path}")
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        required = [
            "# Spec (Light):", "## Objetivo", "## Requisitos",
            "## Acceptance Criteria", "Given", "When", "Then",
            "## Boundaries", "## [NEEDS CLARIFICATION]"
        ]
        for section in required:
            self.assertIn(section, content, f"Spec light faltando: {section}")
        # Não deve ter estrutura de spec completa
        self.assertNotIn("## User Stories", content, "Light não deve ter User Stories")
        self.assertNotIn("Priority: P1", content, "Light não tem priorização")
        lines = content.split("\n")
        self.assertLess(len(lines), 80, f"Template muito grande: {len(lines)} linhas")

    def test_design_template_has_technical_sections(self):
        """Template design deve ter seções técnicas claras."""
        path = os.path.join(
            SKILLS_DIR,
            "design-doc", "templates", "design-template.md"
        )
        self.assertTrue(os.path.exists(path), f"Template não existe: {path}")
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        required = [
            "# Design:", "**Spec Link**", "## Technical Context",
            "## Architecture", "## Data Model", "## API Contracts",
            "## Implementation Phases", "## Test Strategy"
        ]
        for section in required:
            self.assertIn(content, content, f"Design template faltando: {section}")

    # ---------------------------------------------------------------
    # SKILL.md validation tests — cada skill v3 deve ter frontmatter
    # YAML válido + seções obrigatórias no corpo.
    # ---------------------------------------------------------------
    def _validate_skill_file(self, skill_name: str, required_sections: list[str],
                             required_keywords: list[str] | None = None) -> None:
        """Helper: valida estrutura de um SKILL.md."""
        import re
        path = os.path.join(
            SKILLS_DIR,
            skill_name, "SKILL.md"
        )
        self.assertTrue(os.path.exists(path), f"SKILL.md não existe: {path}")
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        # 1. Frontmatter YAML válido
        self.assertTrue(content.startswith("---\n"),
                        f"{skill_name}: deve começar com frontmatter ---")
        fm_end = content.find("\n---\n", 4)
        self.assertGreater(fm_end, 0, f"{skill_name}: frontmatter não fechado")

        frontmatter_text = content[4:fm_end]
        # name field correto
        name_match = re.search(r"^name:\s*(\S+)\s*$", frontmatter_text, re.M)
        self.assertIsNotNone(name_match, f"{skill_name}: campo 'name' ausente")
        assert name_match is not None  # for pyright
        self.assertEqual(
            name_match.group(1), skill_name,
            f"{skill_name}: name field deve ser '{skill_name}'"
        )
        # description field presente e não trivial
        desc_match = re.search(r"^description:\s*(.+)$", frontmatter_text, re.M)
        self.assertIsNotNone(desc_match, f"{skill_name}: campo 'description' ausente")
        assert desc_match is not None  # for pyright
        self.assertGreater(
            len(desc_match.group(1)), 30,
            f"{skill_name}: description muito curta para discoverability"
        )

        # 2. Parse YAML se possível (graceful fallback se PyYAML ausente)
        try:
            import yaml
            data = yaml.safe_load(frontmatter_text)
            self.assertEqual(data["name"], skill_name)
        except ImportError:
            pass  # PyYAML opcional

        # 3. Seções obrigatórias no corpo
        body = content[fm_end:]
        for section in required_sections:
            self.assertIn(section, body, f"{skill_name}: seção faltando: {section}")

        # 4. Keywords obrigatórios (opcional)
        if required_keywords:
            for kw in required_keywords:
                self.assertIn(kw, body, f"{skill_name}: keyword faltando: {kw}")

    def test_write_spec_skill_is_valid(self):
        """write-spec SKILL.md deve ser descoberto pelo Claude Code."""
        self._validate_skill_file(
            "write-spec",
            required_sections=["## Quando ativar", "## Workflow", "## Princípios"],
            required_keywords=["[NEEDS CLARIFICATION]", "user stor", "acceptance criteria"]
        )

    def test_write_spec_light_skill_is_valid(self):
        """write-spec-light SKILL.md deve ser descoberto pelo Claude Code."""
        self._validate_skill_file(
            "write-spec-light",
            required_sections=["## Quando ativar", "## Workflow"],
            required_keywords=["L1", "[NEEDS CLARIFICATION]"]
        )

    def test_design_doc_skill_is_valid(self):
        """design-doc SKILL.md deve ser descoberto pelo Claude Code."""
        self._validate_skill_file(
            "design-doc",
            required_sections=["## Quando ativar", "## Workflow"],
            required_keywords=["spec", "design", "Technical Context"]
        )

    def test_verify_against_spec_skill_is_valid(self):
        """verify-against-spec SKILL.md deve ser descoberto pelo Claude Code."""
        self._validate_skill_file(
            "verify-against-spec",
            required_sections=["## Quando ativar", "## Workflow"],
            required_keywords=["REQ", "AC", "coverage"]
        )

    # ---------------------------------------------------------------
    # Pipeline integration tests — Harness v3 usa as 4 skills SDD em
    # pipelines L1 e L2. Valida que harness-workflow SKILL.md e o hook
    # harness-classify.sh estão em sincronia.
    # ---------------------------------------------------------------
    def test_harness_workflow_has_v3_pipelines(self):
        """harness-workflow SKILL.md deve referenciar skills SDD v3."""
        path = os.path.join(
            SKILLS_DIR,
            "harness-workflow", "SKILL.md"
        )
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        # Skills v3 devem aparecer
        self.assertIn("write-spec-light", content,
                      "L1 pipelines devem usar write-spec-light")
        self.assertIn("write-spec", content,
                      "L2 pipelines devem usar write-spec")
        self.assertIn("design-doc", content,
                      "L2 pipelines devem ter design-doc")
        self.assertIn("verify-against-spec", content,
                      "Pipelines devem ter verify-against-spec")

        # Linha de pipeline L2-feature (tabela de fases) deve listar as skills v3
        l2_feat_idx = content.find("**L2-feature**")
        self.assertGreater(l2_feat_idx, 0, "Linha de pipeline L2-feature nao encontrada")
        l2_feat_line = content[l2_feat_idx:l2_feat_idx + 200]
        self.assertIn("write-spec", l2_feat_line,
                      "L2-feature deve usar write-spec")
        self.assertIn("design-doc", l2_feat_line,
                      "L2-feature deve ter design-doc")
        self.assertIn("verify-against-spec", l2_feat_line,
                      "L2-feature deve terminar em verify-against-spec")

    def test_classify_emits_v3_pipelines_for_l2(self):
        """harness-classify.sh deve emitir pipelines v3 para L2-feature."""
        fresh_state()
        code, out, err = run_hook("harness-classify.sh", {
            "user_prompt": "criar sistema completo de notificacoes push email e in-app"
        })
        self.assertEqual(code, 0, f"Hook failed: {err}")

        # state.json deve ter write-spec no pipeline
        state = read_state()
        self.assertEqual(state["status"], "active")
        self.assertIn("write-spec", state["pipeline"],
                      f"L2 pipeline deve ter write-spec. Got: {state['pipeline']}")
        self.assertIn("design-doc", state["pipeline"],
                      f"L2 pipeline deve ter design-doc. Got: {state['pipeline']}")
        self.assertIn("verify-against-spec", state["pipeline"],
                      f"L2 pipeline deve ter verify-against-spec. Got: {state['pipeline']}")

    def test_classify_emits_v3_pipelines_for_l1(self):
        """harness-classify.sh deve emitir pipelines v3 para L1-feature."""
        fresh_state()
        code, out, err = run_hook("harness-classify.sh", {
            "user_prompt": "adiciona funcao de logging simples ao main.py"
        })
        self.assertEqual(code, 0, f"Hook failed: {err}")

        state = read_state()
        self.assertEqual(state["classification"], "L1-feature")
        self.assertIn("write-spec-light", state["pipeline"],
                      f"L1 pipeline deve ter write-spec-light. Got: {state['pipeline']}")
        self.assertIn("verify-against-spec", state["pipeline"],
                      f"L1 pipeline deve ter verify-against-spec. Got: {state['pipeline']}")


class TestReclassify(HarnessTestBase):
    """Testa harness-reclassify.sh em cenários de contagem e promoção."""

    HOOK = "harness-reclassify.sh"

    def _setup_l0_state(self) -> None:
        """Configura um estado L0 ativo para testes de promoção."""
        write_state({
            "task_id": "t-test-reclass",
            "classification": "L0-feature",
            "status": "done",
            "pipeline": [],
            "current_step": None,
            "artifacts_so_far": [],
            "started_at": "2026-01-01T00:00:00Z"
        })
        write_counter({"count": 0, "files": [], "task_id": "t-test-reclass"})

    # --- Cenário 15: Contador incrementa por arquivo novo ---
    def test_15_counter_increments(self):
        """Cada arquivo novo editado deve incrementar o contador."""
        self._setup_l0_state()

        # Usa paths Windows-style para evitar MSYS path mangling
        test_path = "C:/project/src/main.py"
        run_hook(self.HOOK, {
            "tool_name": "Edit",
            "tool_input": {"file_path": test_path},
        })
        counter = read_counter()
        self.assertEqual(counter["count"], 1)
        self.assertIn(test_path, counter["files"])

    # --- Cenário 16: Sem contagem dupla do mesmo arquivo ---
    def test_16_no_duplicate_counting(self):
        """Editar o mesmo arquivo 2x não deve contar duas vezes."""
        self._setup_l0_state()

        test_path = "C:/project/src/main.py"
        for _ in range(3):
            run_hook(self.HOOK, {
                "tool_name": "Edit",
                "tool_input": {"file_path": test_path},
            })

        counter = read_counter()
        self.assertEqual(counter["count"], 1, "Mesmo arquivo editado 3x deve contar como 1")

    # --- Cenário 17: Promoção L0→L1 com 3+ arquivos ---
    def test_17_l0_to_l1_promotion(self):
        """3+ arquivos distintos devem promover L0 para L1."""
        self._setup_l0_state()

        files = ["C:/project/src/a.py", "C:/project/src/b.py", "C:/project/src/c.py"]
        for f in files:
            run_hook(self.HOOK, {
                "tool_name": "Edit",
                "tool_input": {"file_path": f},
            })

        state = read_state()
        self.assertTrue(
            state["classification"].startswith("L1"),
            f"3+ arquivos deve promover para L1, got: {state['classification']}"
        )

    # --- Cenário 17b: classification_meta sincroniza na promoção ---
    def test_17b_promotion_syncs_classification_meta(self):
        """Promover L0→L1 deve atualizar classification_meta, não deixar L0 stale.

        Sem isto, record_signal.py fotografa final='L0-...' e o loop de
        confirmação semântica trata a task promovida como já finalizada.
        """
        write_state({
            "task_id": "t-test-meta",
            "schema_version": 3,
            "classification": "L0-feature",
            "classification_meta": {
                "suggested": "L0-feature",
                "final": "L0-feature",
                "source": "regex",
                "confidence": None,
                "agreed": None,
            },
            "status": "done",
            "pipeline": [],
            "current_step": None,
            "artifacts_so_far": [],
            "started_at": "2026-01-01T00:00:00Z",
        })
        write_counter({"count": 0, "files": [], "task_id": "t-test-meta"})

        files = ["C:/project/src/x.py", "C:/project/src/y.py", "C:/project/src/z.py"]
        for f in files:
            run_hook(self.HOOK, {
                "tool_name": "Edit",
                "tool_input": {"file_path": f},
            })

        meta = read_state()["classification_meta"]
        self.assertTrue(
            meta["suggested"].startswith("L1"),
            f"suggested deve refletir L1, got: {meta['suggested']}"
        )
        self.assertIsNone(
            meta["final"], "final deve voltar a None aguardando confirmação semântica"
        )
        self.assertIsNone(meta["agreed"], "agreed deve ser None (semântica ainda não avaliou)")
        self.assertEqual(meta["source"], "regex")

    # --- Cenário 18: Classification None não deve crashar ---
    def test_18_null_classification_no_crash(self):
        """state.json com classification: null não deve causar crash."""
        write_state({
            "task_id": "t-test-null",
            "classification": None,
            "status": None,
            "pipeline": [],
            "current_step": None,
            "artifacts_so_far": [],
            "started_at": "2026-01-01T00:00:00Z"
        })
        write_counter({"count": 0, "files": [], "task_id": "t-test-null"})

        # Editar 3+ arquivos com classification=None
        files = ["C:/project/a.py", "C:/project/b.py", "C:/project/c.py"]
        for f in files:
            code, out, err = run_hook(self.HOOK, {
                "tool_name": "Edit",
                "tool_input": {"file_path": f},
            })
            self.assertEqual(
                code, 0,
                f"Hook não deve crashar com classification=None. stderr: {err}"
            )

    # --- Cenário 19: file_path vazio deve sair silenciosamente ---
    def test_19_empty_file_path(self):
        """tool_input sem file_path deve sair sem erro."""
        code, out, err = run_hook(self.HOOK, {
            "tool_name": "Edit",
            "tool_input": {},
        })
        self.assertEqual(code, 0)

    # ------------------------------------------------------------------
    # Cenários 19b-19f: travas de state em PostToolUse
    # Incidente 2026-06-12: garantir que NENHUM caminho de PostToolUse
    # cria task nova, classifica tool output ou mexe em pipeline ativo.
    # ------------------------------------------------------------------

    # --- Cenário 19b: promoção preserva task_id ---
    def test_19b_reclassify_preserves_task_id(self):
        """PostToolUse nunca cria task nova — task_id é imutável aqui."""
        self._setup_l0_state()
        files = ["C:/p/a.py", "C:/p/b.py", "C:/p/c.py", "C:/p/d.py"]
        for f in files:
            run_hook(self.HOOK, {"tool_name": "Edit", "tool_input": {"file_path": f}})
        state = read_state()
        self.assertEqual(state["task_id"], "t-test-reclass",
                         "promoção L0→L1 deve manter a MESMA task")
        self.assertTrue(state["classification"].startswith("L1"))

    # --- Cenário 19c: tool output jamais é classificado ---
    def test_19c_reclassify_ignores_tool_output_text(self):
        """Conteúdo de Write/Edit (ex.: script com 'FALHA', 'erro 40012') não
        pode ser tratado como prompt: o type da task vem do prompt original."""
        self._setup_l0_state()
        payload = "raise RuntimeError('FALHA: erro 40012 - bug critico no sistema')"
        files = ["C:/p/x.py", "C:/p/y.py", "C:/p/z.py"]
        for f in files:
            run_hook(self.HOOK, {
                "tool_name": "Write",
                "tool_input": {"file_path": f, "content": payload},
                "tool_response": {"type": "text", "text": payload},
            })
        state = read_state()
        self.assertEqual(
            state["classification"], "L1-feature",
            "type não pode ser re-derivado de tool output (seria 'bug')"
        )
        self.assertEqual(state["task_id"], "t-test-reclass")

    # --- Cenário 19d: agreed=True trava a classificação ---
    def test_19d_reclassify_respects_agreed_lock(self):
        """Classificação confirmada semanticamente (agreed=true) é imutável."""
        write_state({
            "task_id": "t-test-locked",
            "schema_version": 3,
            "classification": "L0-feature",
            "classification_meta": {
                "suggested": "L0-feature",
                "final": "L0-feature",
                "source": "semantic",
                "confidence": 0.95,
                "agreed": True,
            },
            "status": "done",
            "pipeline": [],
            "current_step": None,
            "artifacts_so_far": [],
            "started_at": "2026-01-01T00:00:00Z",
        })
        write_counter({"count": 0, "files": [], "task_id": "t-test-locked"})
        for f in ["C:/p/m.py", "C:/p/n.py", "C:/p/o.py"]:
            run_hook(self.HOOK, {"tool_name": "Edit", "tool_input": {"file_path": f}})
        state = read_state()
        self.assertEqual(state["classification"], "L0-feature",
                         "agreed=true deve travar reclassificação (não reabrir)")
        self.assertEqual(state["classification_meta"]["final"], "L0-feature")

    def _assert_untouched_when_active(self, classification: str) -> None:
        """Helper: com status=active, reclassify não altera NADA do state."""
        original = {
            "task_id": "t-test-act",
            "schema_version": 3,
            "classification": classification,
            "classification_meta": {
                "suggested": classification,
                "final": None,
                "source": "regex",
                "confidence": None,
                "agreed": None,
            },
            "status": "active",
            "pipeline": ["write-spec-light", "tdd", "verify-against-spec"],
            "current_step": "tdd",
            "artifacts_so_far": ["docs/specs/x-spec-light.md"],
            "started_at": "2026-06-12T03:39:00+00:00",
        }
        write_state(original)
        write_counter({"count": 0, "files": [], "task_id": "t-test-act"})
        for f in ["C:/p/q.py", "C:/p/r.py", "C:/p/s.py", "C:/p/t.py"]:
            run_hook(self.HOOK, {"tool_name": "Edit", "tool_input": {"file_path": f}})
        state = read_state()
        self.assertEqual(state, original,
                         f"status=active deve ser intocável (classification={classification})")

    # --- Cenário 19e: pipeline L1 ativo é intocável ---
    def test_19e_reclassify_never_touches_active_pipeline(self):
        self._assert_untouched_when_active("L1-feature")

    # --- Cenário 19f: até L0 anômalo com status=active é intocável ---
    def test_19f_reclassify_never_promotes_active_l0(self):
        """Estado anômalo (L0 + active) não pode ser promovido em PostToolUse."""
        self._assert_untouched_when_active("L0-feature")


# ===========================================================================
# GIT GUARD TESTS (harness-git-guard.sh) — 6 cenários
# ===========================================================================
class TestGitGuard(HarnessTestBase):
    """Testa harness-git-guard.sh bloqueios e permissões."""

    HOOK = "harness-git-guard.sh"

    # --- Cenário 20: Bloqueia git push --force ---
    def test_20_block_force_push(self):
        """git push --force deve ser bloqueado (exit 2)."""
        code, out, err = run_hook(self.HOOK, {
            "tool_name": "Bash",
            "tool_input": {"command": "git push --force origin main"},
        })
        self.assertEqual(code, 2, "git push --force deve retornar exit 2 (block)")

    # --- Cenário 21: Bloqueia git reset --hard ---
    def test_21_block_reset_hard(self):
        """git reset --hard deve ser bloqueado."""
        code, out, err = run_hook(self.HOOK, {
            "tool_name": "Bash",
            "tool_input": {"command": "git reset --hard HEAD~1"},
        })
        self.assertEqual(code, 2)

    # --- Cenário 22: Bloqueia git clean -f ---
    def test_22_block_clean_f(self):
        """git clean -f deve ser bloqueado."""
        code, out, err = run_hook(self.HOOK, {
            "tool_name": "Bash",
            "tool_input": {"command": "git clean -fd"},
        })
        self.assertEqual(code, 2)

    # --- Cenário 23: Bloqueia git branch -D ---
    def test_23_block_branch_delete(self):
        """git branch -D deve ser bloqueado (mas -d permitido)."""
        code, _, _ = run_hook(self.HOOK, {
            "tool_name": "Bash",
            "tool_input": {"command": "git branch -D feature-old"},
        })
        self.assertEqual(code, 2, "git branch -D deve ser bloqueado")

        code, _, _ = run_hook(self.HOOK, {
            "tool_name": "Bash",
            "tool_input": {"command": "git branch -d feature-merged"},
        })
        self.assertEqual(code, 0, "git branch -d (minúsculo) deve ser permitido")

    # --- Cenário 24: Warning em git push normal ---
    def test_24_warn_normal_push(self):
        """git push sem --force deve gerar warning mas não bloquear."""
        code, out, err = run_hook(self.HOOK, {
            "tool_name": "Bash",
            "tool_input": {"command": "git push origin main"},
        })
        self.assertEqual(code, 0, "git push normal não deve bloquear")
        self.assertIn("Warning", out, "Deve emitir warning")

    # --- Cenário 25: Comandos seguros passam limpos ---
    def test_25_safe_commands_pass(self):
        """Comandos git seguros não devem gerar bloqueio nem warning."""
        safe_commands = [
            "git status",
            "git log --oneline -5",
            "git diff HEAD",
            "git add src/main.py",
            "git commit -m 'fix: resolve issue'",
            "git branch feature-new",
            "git checkout -b new-branch",
            "git stash",
            "ls -la",
            "python -m pytest",
        ]
        for cmd in safe_commands:
            code, out, err = run_hook(self.HOOK, {
                "tool_name": "Bash",
                "tool_input": {"command": cmd},
            })
            self.assertEqual(code, 0, f"Comando seguro deve passar: {cmd}")

    # --- Cenário 26: Input vazio passa limpo ---
    def test_26_empty_command_passes(self):
        """Comando vazio deve passar sem erro."""
        code, out, err = run_hook(self.HOOK, {
            "tool_name": "Bash",
            "tool_input": {"command": ""},
        })
        self.assertEqual(code, 0)

        code, out, err = run_hook(self.HOOK, {
            "tool_name": "Bash",
            "tool_input": {},
        })
        self.assertEqual(code, 0)


# ===========================================================================
# PRECOMPACT TESTS (harness-precompact.sh) — 2 cenários
# ===========================================================================
class TestPrecompact(HarnessTestBase):
    """Testa harness-precompact.sh snapshot e rotação."""

    HOOK = "harness-precompact.sh"

    # --- Cenário 27: Snapshot escrito corretamente ---
    def test_27_snapshot_written(self):
        """Precompact deve adicionar snapshot ao trace-current.md."""
        write_state({
            "task_id": "t-test-snap",
            "classification": "L1-feature",
            "status": "active",
            "pipeline": ["prd-to-plan", "tdd"],
            "current_step": "prd-to-plan",
            "artifacts_so_far": ["docs/prd.md"],
            "started_at": "2026-01-01T00:00:00Z"
        })

        # Limpa trace antes do teste
        if os.path.exists(TRACE_FILE):
            os.remove(TRACE_FILE)

        code, out, err = run_hook(self.HOOK, {})
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(TRACE_FILE), "trace-current.md deve existir")

        with open(TRACE_FILE, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("[SNAPSHOT]", content)
        self.assertIn("t-test-snap", content)
        self.assertIn("L1-feature", content)

    # --- Cenário 28: Rotação quando trace > 50KB ---
    def test_28_trace_rotation(self):
        """Trace > 50KB deve ser rotacionado para traces/ — em HARNESS_DIR isolado.

        Regressão 2026-06-12: a versão antiga escrevia no harness REAL e o
        precompact espelhava o fixture para o vault via vault_sync — 41
        fixtures "Big trace" acumulados em traces/ e AI-Brain/wiki/sessions/.
        """
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_harness = os.path.join(tmp, "harness")
            tmp_vault = os.path.join(tmp, "vault")
            os.makedirs(tmp_harness)
            os.makedirs(tmp_vault)
            with open(os.path.join(tmp_harness, "state.json"), "w", encoding="utf-8") as f:
                json.dump({"task_id": "t-test-28", "status": "done"}, f)
            trace_file = os.path.join(tmp_harness, "trace-current.md")
            with open(trace_file, "w", encoding="utf-8") as f:
                f.write("# Big trace\n" + "x" * 52000)

            env = {
                # bash (MSYS) aceita paths Windows com forward slashes
                "HARNESS_DIR": tmp_harness.replace(os.sep, "/"),
                # vault_sync filho herda e NAO toca o vault real
                "AI_BRAIN_PATH": tmp_vault,
            }
            code, out, err = run_hook(self.HOOK, {}, env_extra=env)
            self.assertEqual(code, 0)

            # dir tmp nasce vazio: qualquer arquivo em traces/ e desta rotacao
            traces_dir = os.path.join(tmp_harness, "traces")
            rotated = os.listdir(traces_dir) if os.path.isdir(traces_dir) else []
            self.assertTrue(len(rotated) > 0, f"Deve haver rotacao em traces/. Listing: {rotated}")

            # trace-current.md deve ter sido recriado (menor)
            size = os.path.getsize(trace_file)
            self.assertLess(size, 51200, "trace-current.md deve ser menor após rotação")


# ===========================================================================
# INTEGRATION TESTS — Fluxo completo
# ===========================================================================
class TestIntegration(HarnessTestBase):
    """Testa fluxos end-to-end combinando múltiplos hooks."""

    # --- Cenário 29: Classify → Reclassify upgrade flow ---
    def test_29_classify_then_reclassify_upgrade(self):
        """L0 classificado → 3+ arquivos editados → upgrade para L1."""
        # Step 1: Classificar como L0
        code, out, _ = run_hook("harness-classify.sh", {
            "user_prompt": "o que é dependency injection?"
        })
        self.assertIn("L0", out)
        state = read_state()
        task_id = state["task_id"]

        # Step 2: Editar 3 arquivos (trigger reclassify)
        for f in ["C:/project/a.py", "C:/project/b.py", "C:/project/c.py"]:
            run_hook("harness-reclassify.sh", {
                "tool_name": "Edit",
                "tool_input": {"file_path": f},
            })

        # Step 3: Verificar promoção
        state = read_state()
        self.assertTrue(
            state["classification"].startswith("L1"),
            f"Deve ser promovido para L1 após 3 edições: {state['classification']}"
        )
        self.assertEqual(state["task_id"], task_id, "task_id deve ser preservado")

    # --- Cenário 30: Classify → Precompact snapshot ---
    def test_30_classify_then_precompact(self):
        """Classificação seguida de precompact deve gerar snapshot correto."""
        if os.path.exists(TRACE_FILE):
            os.remove(TRACE_FILE)

        # Step 1: Classificar
        run_hook("harness-classify.sh", {
            "user_prompt": "cria um novo módulo de autenticação"
        })
        state = read_state()

        # Step 2: Precompact
        run_hook("harness-precompact.sh", {})

        # Step 3: Verificar trace
        with open(TRACE_FILE, encoding="utf-8") as f:
            content = f.read()
        self.assertIn(state["task_id"], content)
        self.assertIn(state["classification"], content)


# ===========================================================================
# Runner
# ===========================================================================
if __name__ == "__main__":
    print("=" * 70)
    print("  Harness v3 — Suite de Testes Automatizados")
    print("  30 cenários | 4 hooks | classify + reclassify + git-guard + precompact")
    print("=" * 70)
    print()
    unittest.main(verbosity=2)
