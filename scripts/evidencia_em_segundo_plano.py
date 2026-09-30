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

# Referencia tardia (`_voo.X` na hora da chamada), e nao `from ... import X`: o
# hook importa este modulo no topo, e um nome privado renomeado em
# `trabalho_em_voo` derrubava o hook inteiro — contagem em primeiro plano
# inclusive — em vez de so a captura, que ja degrada sozinha (rodada 3 #15).
try:
    import trabalho_em_voo as _voo  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - sem o modulo, a captura nao roda
    _voo = None

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
#: O codigo e o do FIM do resumo. O resumo e `Background command "<description>"
#: completed (exit code N...)`, e a `description` e texto do modelo: casar a
#: primeira ocorrencia deixava "suite (exit code 0 esperado)" decidir o codigo
#: (verify #10, #12). O sufixo do host e sempre o ultimo trecho.
_CODIGO = re.compile(r"(?:\(exit code (-?\d+)[^()]*\)|with exit code (-?\d+))\s*$")
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
    return _voo._instante(texto) or _voo._instante(_FIM_DOS_TEMPOS)


def _textos_da_notificacao(entrada: dict[str, Any]) -> list[str]:
    """So o campo que carrega o texto da notificacao, nunca os metadados da entrada.

    A primeira versao varria toda string da entrada (`_textos`), e com ela o
    `cwd` e o `gitBranch`: em POSIX, um `cd` para um diretorio cujo nome
    carrega um bloco fazia a notificacao legitima de outro job levar o bloco
    forjado — medido pelo revisor com a funcao de producao (rodada 3 #7).
    """
    if entrada.get("type") == "attachment":
        prompt = (entrada.get("attachment") or {}).get("prompt")
        return [prompt] if isinstance(prompt, str) else []
    conteudo = (entrada.get("message") or {}).get("content")
    if isinstance(conteudo, str):
        return [conteudo]
    if isinstance(conteudo, list):
        return [p["text"] for p in conteudo if isinstance(p, dict) and isinstance(p.get("text"), str)]
    return []


def _bloco_do_host(texto: str) -> tuple[str, str] | None:
    """(cabeca, resumo) da notificacao do host neste texto, ou None.

    O bloco vai do PRIMEIRO `<task-notification>` ao ULTIMO
    `</task-notification>`; a cabeca (task-id, tool-use-id, output-file,
    status) e o que vem antes do primeiro `<summary>`, e o resumo vai ate o
    ULTIMO `</summary>`. A `description` do modelo mora dentro do resumo: um
    bloco forjado para outro job (verify #11) ou uma tag literal de bloco na
    descricao (rodada 3 #9, provavel neste repositorio) ficam dentro do resumo
    do job verdadeiro e nao valem para ninguem. A primeira versao exigia um
    bloco so por texto e descartava a notificacao legitima nesse segundo caso.
    """
    inicio = texto.find("<task-notification>")
    if inicio < 0:
        return None
    fim = texto.rfind("</task-notification>")
    bloco = texto[inicio + len("<task-notification>"): fim if fim > inicio else len(texto)]
    abre = bloco.find("<summary>")
    if abre < 0:
        return bloco, ""
    fecha = bloco.rfind("</summary>")
    resumo = bloco[abre + len("<summary>"): fecha if fecha > abre else len(bloco)]
    return bloco[:abre], resumo.strip()


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
            for texto in _textos_da_notificacao(entrada):
                partes = _bloco_do_host(texto)
                if partes is None:
                    continue
                # Os campos do host vem ANTES do `<summary>`; dali em diante e a
                # `description` do modelo (re-verify #4).
                cabeca, resumo = partes
                status = _tag("status", cabeca)
                ids = _voo._TASK_ID.findall(cabeca)
                if not status or len(ids) != 1 or ids[0] not in esperados:
                    continue
                job = ids[0]
                tool_use_id = _tag("tool-use-id", cabeca)
                if esperados[job] is None or tool_use_id != esperados[job]:
                    continue
                nova = Notificacao(
                    job=job,
                    tool_use_id=tool_use_id,
                    status=status,
                    resumo=resumo,
                    arquivo=_tag("output-file", cabeca),
                    terminou_em=quando,
                )
                # A mesma notificacao chega em copias (anexo e `user`). Uma por
                # conteudo, a de carimbo mais cedo.
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


def _arquivo_no_lugar(arquivo: str, job: str) -> bool:
    """`.../tasks/<job>.output`: o nome e o diretorio que o host usa.

    A primeira versao exigia tambem que as pastas de sessao e de projeto fossem
    as do transcript, e rejeitava em definitivo 65 de 654 terminos reais: sessao
    retomada grava a saida sob o id novo, e em 12 o projeto diverge (verify #19,
    medido com esta funcao). Nem o `sessionId` da entrada acerta sempre (597).
    O caminho vem de um campo que o host escreve antes do `<summary>`, e com um
    bloco so por texto o modelo nao o alcanca; o nome fica como conferencia.
    """
    saida = Path(arquivo)
    return (
        os.path.normcase(saida.name) == os.path.normcase(f"{job}.output")
        and os.path.normcase(saida.parent.name) == "tasks"
    )


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
    # O arquivo entra na comparacao: copias que so divergem no caminho faziam o
    # desfecho depender da ordem em que apareciam no transcript (verify #13).
    distintos = {(n.status, _codigo(n.resumo), n.arquivo) for n in notificacoes}
    if len(distintos) > 1:
        return Veredito("rejeitado", "notificacao-diverge", terminou_em=termino)
    notificacao = notificacoes[0]
    codigo = _codigo(notificacao.resumo)
    if notificacao.status not in _STATUS_COM_CODIGO or codigo is None:
        return Veredito("rejeitado", "sem-codigo", terminou_em=termino)
    if notificacao.status == "failed" and codigo == 0:
        return Veredito("rejeitado", "status-inconsistente", terminou_em=termino)
    if not notificacao.arquivo or not _arquivo_no_lugar(notificacao.arquivo, lancamento["job_id"]):
        return Veredito("rejeitado", "arquivo-estranho", terminou_em=termino)
    try:
        cauda = ler(notificacao.arquivo)
    except FileNotFoundError:
        return Veredito("rejeitado", "sem-arquivo", terminou_em=termino)
    except OSError:
        return Veredito("pendente")
    # Trailer obrigatorio (verify #15): arquivo vazio ou truncado virava `aceito`
    # com exit 0 e contagem nula, e desverificava um verde. Medido: 228 de 231
    # `.output` existentes tem trailer. Com ele, codigo e contagens vem do
    # arquivo que o host gravou; o resumo so confirma.
    trailer = _TRAILER.search(cauda)
    if not trailer:
        return Veredito("rejeitado", "sem-trailer", terminou_em=termino)
    if int(trailer.group(1)) != codigo:
        return Veredito("rejeitado", "codigo-diverge", terminou_em=termino)
    coletados, passaram, pulados, digest = contar_testes(cauda)
    if coletados is None:
        # Nenhuma contagem reconhecivel: `pytest -q > log.txt` em segundo plano
        # (`>` nao e composicao) deixa o `.output` so com o trailer. Sem
        # informacao nao ha evidencia para nenhum lado — gravar a linha nula
        # desverificaria um verde (re-verify #6). `no tests ran` conta 0, nao
        # None, e continua gravando (AC-3.9).
        return Veredito("rejeitado", "sem-contagem", terminou_em=termino)
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
