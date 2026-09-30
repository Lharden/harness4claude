"""A suite que foi para segundo plano vira evidencia quando termina.

O hook so via teste em PRIMEIRO plano: le a saida na resposta da ferramenta.
Suite que vai para o fundo — pedida (`run_in_background`) ou empurrada pelo host
depois de 10 min de Bash — terminava sem evidencia, e a do harness4claude leva
~21 min. Ver `docs/specs/evidencia-em-segundo-plano-spec.md` e o design ao lado.

Este modulo e leitura e julgamento; nao escreve em lugar nenhum. Quem grava e
`HarnessDatabase`, numa transacao por lancamento.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from trabalho_em_voo import _BLOCO, _TASK_ID, _instante, _textos  # type: ignore[import-not-found]

# Categorias que o pytest imprime na linha de sumario, separadas pelo que elas
# significam para o portao.
#
# Ate 2026-09-16 este parser so via `passed`, `failed` e `errors`, e derivava
# `tests_collected` como a soma dos tres. O numero gravado no banco nunca foi o
# `collected N items` do pytest — e a mesma palavra queria dizer duas coisas
# conforme quem escrevia, o hook ou uma pessoa rodando o `state_cli` a mao. A
# pessoa que reportava o collected verdadeiro era a unica recusada.
#
# `skipped`, `xfailed`, `xpassed` e `deselected` nao produzem veredito que
# gateie: nenhum deles e falha, e nenhum deles e prova de que algo passou.
#
# Veio de `hooks/harness-transactional.py` em 2026-09-30: a suite em segundo
# plano conta o arquivo de saida, a de primeiro plano conta a resposta, e as
# duas tem de dar o mesmo numero para o mesmo texto (REQ-F5). Uma copia aqui
# e outra la divergiriam no primeiro conserto de uma delas.
VEREDITO_PASSA = (r"\b(\d+)\s+passed\b",)
VEREDITO_FALHA = (r"\b(\d+)\s+failed\b", r"\b(\d+)\s+errors?\b")
SEM_VEREDITO = (
    r"\b(\d+)\s+skipped\b",
    r"\b(\d+)\s+xfailed\b",
    r"\b(\d+)\s+xpassed\b",
    r"\b(\d+)\s+deselected\b",
)


def _soma_categorias(text: str, padroes: tuple[str, ...]) -> int:
    # `max` por padrao, e nao soma: o pytest repete a linha de sumario (uma vez
    # em "short test summary info", outra no rodape) e somar contaria duas vezes.
    return sum(
        max((int(v) for v in re.findall(padrao, text, re.IGNORECASE)), default=0)
        for padrao in padroes
    )


def contar_testes(text: str) -> tuple[int | None, int | None, int | None, str | None]:
    """(coletados, passando, pulados, digest) — coletados = tudo que o pytest contou."""
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest() if text else None
    if re.search(r"\b(no tests ran|collected 0 items|0 tests? (?:run|passed|total))\b", text, re.IGNORECASE):
        return 0, 0, 0, digest
    passou = _soma_categorias(text, VEREDITO_PASSA)
    falhou = _soma_categorias(text, VEREDITO_FALHA)
    sem_veredito = _soma_categorias(text, SEM_VEREDITO)
    if passou or falhou or sem_veredito:
        return passou + falhou + sem_veredito, passou, sem_veredito, digest
    if re.search(r"\btest result:\s*ok\b", text, re.IGNORECASE) or re.search(r"(?m)^ok\s+\S+", text):
        return 1, 1, 0, digest
    return None, None, None, digest


# --- Termino no transcript --------------------------------------------------------
#
# A direcao de erro aqui e a OPOSTA da de `trabalho_em_voo`. La, dar um job por
# terminado sem estar faz o Stop cobrar — o comportamento de antes —, entao
# qualquer `<status>`, ate citado, conta. Aqui, aceitar um termino que o host nao
# escreveu fabricaria evidencia. So valem as duas formas que o host usa para
# entregar a notificacao, medidas em 2026-09-30 sobre os transcripts da maquina:
#
# - `attachment` `queued_command` com `commandMode == "task-notification"` —
#   715 de 715 notificacoes terminais enfileiradas; prompt de humano ou de outra
#   sessao enfileirado vem com `commandMode == "prompt"`;
# - `user` com `origin.kind == "task-notification"` e sem `toolUseResult`.
#
# `queue-operation` fica de fora de proposito: e onde um texto colado entraria
# sem marcador nenhum, e as duas formas acima ja cobrem os 619 terminos medidos.

_TAG = {
    nome: re.compile(rf"<{nome}>(.*?)</{nome}>", re.DOTALL)
    for nome in ("tool-use-id", "output-file", "status", "summary")
}
_CODIGO = re.compile(r"\(exit code (-?\d+)|with exit code (-?\d+)")
_TRAILER = re.compile(r"\[exited with code (-?\d+)\]\s*$")
_STATUS_COM_CODIGO = frozenset({"completed", "failed"})
_FIM_DOS_TEMPOS = "9999-12-31T00:00:00+00:00"

#: Quanto do fim do arquivo de saida se le. Medido: `.output` de ate 323 MB, e o
#: sumario do pytest comeca no maximo 511 bytes antes do fim (140 arquivos).
LIMITE_DA_CAUDA = 64 * 1024


@dataclass(frozen=True)
class Notificacao:
    job: str
    tool_use_id: str | None
    status: str
    resumo: str
    arquivo: str | None
    terminou_em: str | None


@dataclass(frozen=True)
class Veredito:
    estado: str  # 'pendente' | 'aceito' | 'rejeitado'
    motivo: str | None = None
    exit_code: int | None = None
    tests_collected: int | None = None
    tests_passed: int | None = None
    tests_skipped: int | None = None
    output_hash: str | None = None
    terminou_em: str | None = None


def _forma_do_host(entrada: dict[str, Any]) -> bool:
    if entrada.get("isSidechain"):
        return False
    tipo = entrada.get("type")
    if tipo == "attachment":
        anexo = entrada.get("attachment")
        return (
            isinstance(anexo, dict)
            and anexo.get("type") == "queued_command"
            and anexo.get("commandMode") == "task-notification"
        )
    if tipo == "user":
        if "toolUseResult" in entrada:
            return False
        origem = entrada.get("origin")
        return isinstance(origem, dict) and origem.get("kind") == "task-notification"
    return False


def _tag(nome: str, bloco: str) -> str | None:
    achado = _TAG[nome].search(bloco)
    return achado.group(1).strip() if achado else None


def _ordem(texto: str | None):
    return _instante(texto) or _instante(_FIM_DOS_TEMPOS)


def notificacoes_de(
    transcript_path: str | Path, esperados: dict[str, str | None]
) -> dict[str, list[Notificacao]]:
    """Notificacoes terminais aceitas de cada job esperado, lendo o transcript uma vez.

    `esperados` mapeia job -> `tool_use_id` do lancamento. `None` nunca casa:
    sem o id do lancamento nao ha como saber que a notificacao e dele.
    Levanta `OSError` se o transcript nao abre — quem chama deixa pendente.
    """
    achadas: dict[str, dict[tuple, Notificacao]] = {job: {} for job in esperados}
    with open(transcript_path, encoding="utf-8", errors="replace") as arquivo:
        for linha in arquivo:
            if "<task-notification>" not in linha:
                continue
            try:
                entrada = json.loads(linha)
            except ValueError:
                continue
            if not isinstance(entrada, dict) or not _forma_do_host(entrada):
                continue
            quando = entrada.get("timestamp") if isinstance(entrada.get("timestamp"), str) else None
            for texto in _textos(entrada):
                if "<task-notification>" not in texto:
                    continue
                for bloco in _BLOCO.findall(texto):
                    status = _tag("status", bloco)
                    ids = _TASK_ID.findall(bloco)
                    if not status or len(ids) != 1 or ids[0] not in esperados:
                        continue
                    job = ids[0]
                    tool_use_id = _tag("tool-use-id", bloco)
                    if esperados[job] is None or tool_use_id != esperados[job]:
                        continue
                    nova = Notificacao(
                        job=job,
                        tool_use_id=tool_use_id,
                        status=status,
                        resumo=_tag("summary", bloco) or "",
                        arquivo=_tag("output-file", bloco),
                        terminou_em=quando,
                    )
                    # A mesma notificacao chega em copias (prompt e texto
                    # renderizado do anexo; anexo e `user`). Uma por conteudo, a
                    # de carimbo mais cedo.
                    chave = (nova.status, nova.resumo, nova.arquivo)
                    anterior = achadas[job].get(chave)
                    if anterior is None or _ordem(nova.terminou_em) < _ordem(anterior.terminou_em):
                        achadas[job][chave] = nova
    return {job: list(por_conteudo.values()) for job, por_conteudo in achadas.items()}


def _codigo(resumo: str) -> int | None:
    achado = _CODIGO.search(resumo)
    if not achado:
        return None
    return int(achado.group(1) if achado.group(1) is not None else achado.group(2))


def _arquivo_no_lugar(arquivo: str, job: str, transcript_path: str) -> bool:
    """`<projeto>/<sessao>/tasks/<job>.output`, com projeto e sessao os do transcript.

    Medido no caso real: `projects\\<projeto>\\<sessao>.jsonl` <->
    `%TEMP%\\claude\\<projeto>\\<sessao>\\tasks\\<job>.output`.
    """
    saida, transcript = Path(arquivo), Path(transcript_path)
    partes = [saida.name, saida.parent.name, saida.parent.parent.name, saida.parent.parent.parent.name]
    esperado = [f"{job}.output", "tasks", transcript.stem, transcript.parent.name]
    return [os.path.normcase(p) for p in partes] == [os.path.normcase(p) for p in esperado]


def ler_cauda(caminho: str | Path, limite: int = LIMITE_DA_CAUDA) -> str:
    """Os ultimos `limite` bytes, decodificados sem falhar.

    O arquivo real do consumidor nao e UTF-8 valido (byte 0x97 de uma mensagem
    de teste) e usa CRLF: decodificar estrito perderia a leitura inteira por um
    caractere que nao entra na contagem.
    """
    with open(caminho, "rb") as arquivo:
        arquivo.seek(0, os.SEEK_END)
        tamanho = arquivo.tell()
        arquivo.seek(max(0, tamanho - limite))
        return arquivo.read().decode("utf-8", errors="replace")


def julgar(
    lancamento: dict[str, Any],
    notificacoes: list[Notificacao],
    ler: Callable[[str], str] = ler_cauda,
) -> Veredito:
    """Veredito de um lancamento, na precedencia do design; a primeira regra decide.

    Pura quanto ao banco. `pendente` = nada a fazer agora (tenta no proximo);
    `rejeitado` = terminou sem prova (terminal, sem evidencia); `aceito` = tem
    evidencia — `capturado`, `historico` ou `superado` so se decide sob o lock,
    em `HarnessDatabase.resolver_lancamento`.
    """
    if not notificacoes:
        return Veredito("pendente")
    termino = min((n.terminou_em for n in notificacoes), key=_ordem)
    distintos = {(n.status, _codigo(n.resumo)) for n in notificacoes}
    if len(distintos) > 1:
        return Veredito("rejeitado", "notificacao-diverge", terminou_em=termino)
    notificacao = notificacoes[0]
    codigo = _codigo(notificacao.resumo)
    if notificacao.status not in _STATUS_COM_CODIGO or codigo is None:
        return Veredito("rejeitado", "sem-codigo", terminou_em=termino)
    if notificacao.status == "failed" and codigo == 0:
        return Veredito("rejeitado", "status-inconsistente", terminou_em=termino)
    transcript = lancamento.get("transcript_path") or ""
    if not notificacao.arquivo or not _arquivo_no_lugar(notificacao.arquivo, lancamento["job_id"], transcript):
        return Veredito("rejeitado", "arquivo-estranho", terminou_em=termino)
    try:
        cauda = ler(notificacao.arquivo)
    except FileNotFoundError:
        return Veredito("rejeitado", "sem-arquivo", terminou_em=termino)
    except OSError:
        return Veredito("pendente")
    trailer = _TRAILER.search(cauda)
    if trailer and int(trailer.group(1)) != codigo:
        return Veredito("rejeitado", "codigo-diverge", terminou_em=termino)
    coletados, passaram, pulados, digest = contar_testes(cauda)
    return Veredito(
        "aceito",
        exit_code=codigo,
        tests_collected=coletados,
        tests_passed=passaram,
        tests_skipped=pulados,
        output_hash=digest,
        terminou_em=termino,
    )


def resumo_dos_lancamentos(lancamentos: list[dict[str, Any]]) -> str:
    """`Lancamentos desta task: J rev=N estado[:motivo] ; ...`, ou vazio.

    Consumidores: a mensagem do Stop que bloqueia e a recusa do `complete`.
    Pendente que se acumula aqui e o alarme de que o host mudou o formato da
    notificacao — sem esta linha, captura que parou de funcionar seria silencio.
    """
    if not lancamentos:
        return ""
    itens = " ; ".join(
        f"{l['job_id']} rev={l['code_revision']} {l['estado']}" + (f":{l['motivo']}" if l.get("motivo") else "")
        for l in lancamentos
    )
    return f"Lancamentos desta task: {itens}"


def capturar_lancamentos(database: Any, task_id: str) -> int:
    """Resolve os lancamentos pendentes de UMA task. Devolve quantas evidencias gravou.

    Nunca levanta: o Stop e o `complete` que a chamam decidem como antes se
    qualquer coisa aqui falhar. Sem pendente, custa uma consulta.
    """
    try:
        pendentes = database.lancamentos(task_id, estado="pendente")
    except Exception:
        return 0
    if not pendentes:
        return 0
    por_transcript: dict[str, list[dict[str, Any]]] = {}
    for lancamento in pendentes:
        if lancamento.get("transcript_path"):
            por_transcript.setdefault(lancamento["transcript_path"], []).append(lancamento)
    julgados: list[tuple[dict[str, Any], Veredito]] = []
    for caminho, grupo in por_transcript.items():
        try:
            notificacoes = notificacoes_de(caminho, {l["job_id"]: l.get("tool_use_id") for l in grupo})
        except Exception:
            continue
        for lancamento in grupo:
            try:
                veredito = julgar(lancamento, notificacoes.get(lancamento["job_id"], []))
            except Exception:
                continue
            if veredito.estado != "pendente":
                julgados.append((lancamento, veredito))
    # Ordem de termino (REQ-F10): quem terminou por ultimo e gravado por ultimo.
    julgados.sort(key=lambda par: _ordem(par[1].terminou_em))
    gravadas = 0
    for lancamento, veredito in julgados:
        try:
            estado = database.resolver_lancamento(task_id, lancamento["job_id"], veredito)
        except Exception:
            continue
        if estado in ("capturado", "historico"):
            gravadas += 1
    return gravadas
