"""Que trabalho em segundo plano esta sessao lancou e ainda espera.

O Stop cobrava continuacao a cada fim de turno, e nem todo fim de turno e
tentativa de encerrar: quando o modelo lanca a suite, uma medicao ou um agente
em segundo plano, o turno acaba e o host o reinvoca quando o job terminar.
Incidente 2026-09-29 (`t-20260929-130710180778`): tres Stops com job da propria
sessao rodando, tres cobrancas, `escalation` aberto em doze minutos sem falha
de verificacao nenhuma. Ver `docs/specs/portao-stop-em-voo-diagnostico.md`.

A fonte e o transcript (`transcript_path`, documentado no payload do Stop), e
nao um evento de hook: nao existe evento de job em segundo plano, o
`UserPromptSubmit` perde 7,2% das notificacoes terminais (as absorvidas no meio
do turno) e o hook transacional so recebe `PostToolUse` de `Bash|PowerShell`.
No transcript estao o lancamento e o termino, na ordem em que o host gravou.

Quem erra para um lado ou para o outro, e por que a escolha e esta:

- id marcado como terminado sem estar: o Stop cobra, que e o comportamento de
  antes deste modulo. Por isso qualquer `<status>` conta como termino, e
  notificacao citada em texto (ler esta spec, `grep` num `.jsonl`) tambem.
- job dado como em voo sem estar: o Stop deixa de cobrar, mas continua
  bloqueando (decisao D1). Por isso lancamento so vem de chave de primeiro nivel
  de `toolUseResult`, nunca de texto.
- transcript ausente, ilegivel ou com formato novo: lista vazia, ou seja, cobra.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

#: Substrings que uma linha precisa ter para valer o `json.loads`. O maior
#: transcript da maquina tem 129 MB e 67 131 linhas; so 632 passam por aqui, e a
#: leitura inteira custa 1,3 s contra 15 s de timeout do Stop. Sem o filtro o
#: custo cresce com a sessao, e estourar o timeout faz o host SOLTAR o Stop.
_MARCAS = (
    "backgroundTaskId",
    "async_launched",
    "timeoutMs",
    "<task-notification>",
    "Successfully stopped task",
)

_BLOCO = re.compile(r"<task-notification>(.*?)(?:</task-notification>|$)", re.DOTALL)
_TASK_ID = re.compile(r"<task-id>([^<\s]+)</task-id>")

#: O Monitor avisa que expirou por um evento sem `<status>`; e a unica forma
#: de termino dele que nao e `<status>` nem prazo.
_MONITOR_EXPIROU = "[Monitor expired"

_PARADO = "Successfully stopped task"


def _instante(texto: Any) -> datetime | None:
    if not isinstance(texto, str) or not texto:
        return None
    try:
        valor = datetime.fromisoformat(texto.replace("Z", "+00:00"))
    except ValueError:
        return None
    return valor if valor.tzinfo else valor.replace(tzinfo=timezone.utc)


def _textos(valor: Any):
    if isinstance(valor, str):
        yield valor
    elif isinstance(valor, dict):
        for aninhado in valor.values():
            yield from _textos(aninhado)
    elif isinstance(valor, list):
        for aninhado in valor:
            yield from _textos(aninhado)


def _terminados_no_texto(texto: str) -> set[str]:
    """Ids encerrados pelas notificacoes contidas num texto.

    Um bloco com `<status>` encerra TODOS os `<task-id>` dele: na retomada o
    host lista num bloco so os jobs que morreram com a sessao anterior.
    """
    terminados: set[str] = set()
    for bloco in _BLOCO.findall(texto):
        if "<status>" in bloco or _MONITOR_EXPIROU in bloco:
            terminados.update(_TASK_ID.findall(bloco))
    return terminados


def _lancamento(entrada: dict[str, Any]) -> tuple[str, str, timedelta | None] | None:
    """(id, tipo, prazo) se a entrada lanca trabalho que acorda o modelo."""
    if entrada.get("type") != "user" or entrada.get("isSidechain"):
        return None
    resultado = entrada.get("toolUseResult")
    if not isinstance(resultado, dict):
        return None
    if isinstance(resultado.get("backgroundTaskId"), str):
        return resultado["backgroundTaskId"], "shell", None
    task_id = resultado.get("taskId")
    if isinstance(task_id, str) and "timeoutMs" in resultado:
        if resultado.get("persistent"):
            # Vigia que so acaba com a sessao nao e resultado pelo qual se espera.
            return None
        prazo = resultado.get("timeoutMs")
        return task_id, "monitor", timedelta(milliseconds=prazo) if isinstance(prazo, (int, float)) else None
    if resultado.get("status") == "async_launched":
        if isinstance(task_id, str):
            return task_id, "workflow", None
        if isinstance(resultado.get("agentId"), str):
            return resultado["agentId"], "agent", None
    return None


def _parado(entrada: dict[str, Any]) -> str | None:
    """Id de um `TaskStop` bem-sucedido: 39 de 55 jobs parados nao sao notificados."""
    resultado = entrada.get("toolUseResult")
    if not isinstance(resultado, dict):
        return None
    if str(resultado.get("message") or "").startswith(_PARADO) and isinstance(resultado.get("task_id"), str):
        return resultado["task_id"]
    return None


def jobs_em_voo(
    transcript_path: str | Path | None,
    *,
    desde: str | datetime | None = None,
    agora: datetime | None = None,
) -> list[dict[str, str]]:
    """Jobs lancados por esta sessao que ainda nao terminaram.

    `desde` recorta pelo inicio da task (decisao D2): o contador de continuacoes
    e por task, e um servidor subido numa task anterior nao isenta a seguinte.
    Lancamento sem carimbo de tempo legivel fica de fora quando ha recorte.

    Devolve `[{"id", "tipo", "lancado_em"}]` na ordem de lancamento.
    """
    if not transcript_path:
        return []
    agora = agora or datetime.now(timezone.utc)
    corte = desde if isinstance(desde, datetime) else _instante(desde)
    lancados: dict[str, tuple[str, str, datetime | None, timedelta | None]] = {}
    terminados: set[str] = set()
    try:
        with open(transcript_path, encoding="utf-8", errors="replace") as arquivo:
            for linha in arquivo:
                if not any(marca in linha for marca in _MARCAS):
                    continue
                try:
                    entrada = json.loads(linha)
                except ValueError:
                    # O host pode estar gravando a ultima linha agora.
                    continue
                if not isinstance(entrada, dict):
                    continue
                lancamento = _lancamento(entrada)
                if lancamento is not None:
                    job, tipo, prazo = lancamento
                    lancados[job] = (job, tipo, _instante(entrada.get("timestamp")), prazo)
                parado = _parado(entrada)
                if parado:
                    terminados.add(parado)
                if "<task-notification>" in linha:
                    for texto in _textos(entrada):
                        if "<task-notification>" in texto:
                            terminados |= _terminados_no_texto(texto)
    except OSError:
        return []
    em_voo = []
    for job, tipo, lancado_em, prazo in lancados.values():
        if job in terminados:
            continue
        if corte is not None and (lancado_em is None or lancado_em < corte):
            continue
        if prazo is not None and lancado_em is not None and lancado_em + prazo <= agora:
            # O Monitor tem prazo declarado pelo host no lancamento; passado
            # ele, o host ja o encerrou, avisando ou nao.
            continue
        em_voo.append(
            {"id": job, "tipo": tipo, "lancado_em": lancado_em.isoformat() if lancado_em else ""}
        )
    return em_voo
