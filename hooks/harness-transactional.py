#!/usr/bin/env python
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

#: O CLI de estado que acompanha ESTE hook, em forma copiavel para o shell.
#: Ver `comando_de_evidencia` para por que o caminho e literal e por que a barra
#: e a normal.
CLI_DE_ESTADO = (SCRIPTS / "state_cli.py").as_posix()

from evidencia_em_segundo_plano import (  # type: ignore[import-not-found]
    capturar_lancamentos,
    contar_testes,
    resumo_dos_lancamentos,
)
from harness_paths import ensure_state_dir, find_repo_root  # type: ignore[import-not-found]
from post_tool_policy import inside_root  # type: ignore[import-not-found]
from projecao import gravar_json_atomico  # type: ignore[import-not-found]
from trabalho_em_voo import jobs_em_voo  # type: ignore[import-not-found]
from transactional_state import (  # type: ignore[import-not-found]
    HarnessDatabase,
    StateTransitionError,
    cobra_evidencia_nesta_fase,
    tipo_de_evidencia,
)

VERIFICATION_PATTERNS = (
    r"^\s*(?:py|python(?:\.exe)?)\s+-m\s+(?:pytest|unittest)\b",
    r"^\s*pytest(?:\.exe)?\b",
    r"^\s*(?:npm|pnpm)\s+(?:run\s+)?test\b",
    r"^\s*yarn\s+test\b",
    r"^\s*cargo\s+test\b",
    r"^\s*go\s+test\b",
)
SHELL_TOOLS = {"bash", "powershell", "shell", "shell_command"}


def _event_name(payload: dict[str, Any], explicit: str | None = None) -> str:
    return str(explicit or payload.get("hook_event_name") or payload.get("hookEventName") or "")


def _tool_input(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("tool_input") or payload.get("toolInput") or {}
    return value if isinstance(value, dict) else {}


#: Surrogate sem par. `json.load` junta os pares validos num caractere so,
#: entao todo D800-DFFF que sobra num `str` do payload e solitario: JSON valido
#: (`"\ud800"`), e sem codificacao UTF-8 nenhuma.
_SURROGATE_SOLITARIO = re.compile(r"[\ud800-\udfff]")


def _texto_codificavel(texto: str) -> str:
    """O texto do payload com cada surrogate solitario trocado por U+FFFD.

    Todo sumidouro deste hook e UTF-8 — hash, `recusas.jsonl`, sqlite — e um
    surrogate solitario derrubava o primeiro que alcancasse, com exit 1 e a
    task sem o toque. Eram quatro medidos (revisao adversarial de 2026-09-28):
    os dois hashes, `touch_files` e `record_evidence`. Normalizar aqui, na
    entrada, cobre os quatro e o proximo que alguem escrever.

    U+FFFD e nao a forma `\\ud800` em texto: esta troca roda ANTES do parser de
    shell, e uma barra invertida nova mudaria o que `shell_write_targets` le.
    U+FFFD nao e aspa, operador nem espaco.
    """
    return _SURROGATE_SOLITARIO.sub("\ufffd", texto)


def _command(payload: dict[str, Any]) -> str:
    value = _tool_input(payload)
    return _texto_codificavel(
        str(value.get("command") or value.get("cmd") or value.get("script") or "")
    )


#: Operadores que compoem comandos. Montados com `chr()` porque este arquivo
#: ja foi corrompido uma vez por escaping de heredoc: `\r` chegou ao disco
#: como byte de retorno de carro e quebrou a sintaxe.
_OPERADORES = frozenset({';', '|', '&', '`', chr(13), chr(10)})

#: O que pode SEGUIR `>&` numa duplicacao de descritor: o numero do descritor
#: de destino, ou `-` para fecha-lo.
_ALVOS_DE_FD = frozenset('0123456789-')


def _duplicacao_de_fd(command: str, index: int) -> bool:
    """O `&` em `index` e parte de `2>&1`, e nao um operador.

    `2>&1` nao introduz comando nenhum: aponta um descritor para outro, dentro
    do MESMO processo. Trata-lo como composicao era o defeito mais caro deste
    arquivo, porque a varredura e uma so e as quatro isencoes dependem dela —
    `is_state_management`, `is_trusted_verification`, `is_read_only` e
    `nao_muda_a_arvore` caiam juntas.

    Medido em 2026-09-21 nos baldes desta maquina: 1 062 recusas de candidato
    `&` em 649 comandos distintos, 46,3% de todas as 2 292 recusas — a classe
    mais frequente, e maior que todas as outras somadas menos uma. Na task
    `t-20260921-090314300233`, 33 de 33 toques sairam com origem
    `shell-placeholder`, nenhum caminho atribuido, e as 13 linhas de evidence
    foram invalidadas por um toque <=2s depois de nascerem. Entre os comandos
    recusados estava, 17 vezes, a receita que a mensagem do portao manda
    copiar.

    A porta e estreita de proposito, e a estreiteza e o que separa isto de
    afrouxar o guarda:

    - exige `>` ou `<` COLADO antes, entao `a && b` e `sleep 1 &` continuam
      compostos: em `&&` o caractere anterior e espaco ou o proprio `&`;
    - exige digito ou `-` colado depois, entao `a &> saida.txt` continua
      composto. Aquilo e redirecionamento para ARQUIVO, escrita de verdade, e
      `shell_write_targets` tem de seguir vendo o alvo.
    """
    if index == 0 or command[index - 1] not in {'>', '<'}:
        return False
    seguinte = index + 1
    return seguinte < len(command) and command[seguinte] in _ALVOS_DE_FD


def _scan_composition(command: str) -> tuple[int, bool]:
    """Indice do primeiro operador fora de aspas (-1 se nao houver), e se
    a linha terminou com aspa aberta.

    Duas perguntas diferentes saem da mesma varredura, e e de proposito:
    `is_trusted_verification` recusa nos DOIS casos, enquanto `atomic_prefix`
    so pode cortar no primeiro. Manter duas varreduras separadas foi o que
    fez o corte cair dentro de um argumento entre aspas.

    A segunda pergunta e "nao confiavel, mas sem ponto de corte bom": aspa
    aberta, ou `$( )`/crase DENTRO de aspas duplas — o bash executa as duas ali.
    Ate 2026-09-30 o miolo das aspas duplas era inerte para esta varredura, e
    `python state_cli.py ... "$(python gera.py)"` saia isento rodando um
    programa. Nao vira indice porque cortar no meio da aspa daria a
    `atomic_prefix` uma sugestao que nem fecha as aspas.
    """
    quote = None
    escaped = False
    substitui_entre_aspas = False
    for index, character in enumerate(command):
        if escaped:
            escaped = False
            continue
        if quote:
            if character == chr(92) and quote == '"':
                escaped = True
            elif character == quote:
                quote = None
            elif quote == '"' and (character == '`' or command.startswith('$(', index)):
                substitui_entre_aspas = True
            continue
        if character in {chr(39), '"'}:
            quote = character
            continue
        if character in _OPERADORES:
            if character == '&' and _duplicacao_de_fd(command, index):
                continue
            return index, False
        if character == '$' and index + 1 < len(command) and command[index + 1] == '(':
            return index, False
    return -1, quote is not None or substitui_entre_aspas


def _has_unquoted_shell_composition(command: str) -> bool:
    """Composicao que as aspas NAO neutralizam — `$( )` entre aspas duplas conta."""
    indice, sem_corte = _scan_composition(command)
    return indice >= 0 or sem_corte


def is_trusted_verification(command: str) -> bool:
    if not command or _has_unquoted_shell_composition(command):
        return False
    return any(re.search(pattern, command, re.IGNORECASE) for pattern in VERIFICATION_PATTERNS)


def looks_like_verification(command: str) -> bool:
    """Parece tentativa de verificar, mesmo que nao sirva como evidencia.

    Difere de `is_trusted_verification` em um ponto so: ignora composicao de
    shell. Serve para separar "voce tentou verificar e eu descartei" de "isto
    nao tinha nada a ver com teste" — sem essa distincao o aviso apareceria em
    `git log | head` e viraria ruido por turno.

    As ancoras `^` de VERIFICATION_PATTERNS continuam valendo: `echo "rode
    python -m pytest"` menciona pytest e nao roda teste nenhum.
    """
    if not command:
        return False
    return any(re.search(p, command, re.IGNORECASE) for p in VERIFICATION_PATTERNS)


def atomic_prefix(command: str) -> str:
    """O comando ate o primeiro operador de shell nao citado.

    E o que o aviso devolve para o leitor rodar. Reconstruir a intencao a
    partir do comando dele vale mais que uma receita generica:
    `python -m pytest -q | tail -20` vira `python -m pytest -q`, que e
    exatamente o que ele queria.

    Aspa aberta NAO e ponto de corte: ela torna o comando nao confiavel, mas
    nao ha prefixo bom a sugerir, entao devolve a linha inteira.
    """
    indice, _ = _scan_composition(command)
    return (command[:indice] if indice >= 0 else command).strip()


#: O aviso do descarte. Curto de proposito: ele aparece no meio do trabalho, e
#: um paragrafo aqui custa mais atencao do que o erro que ele evita.
#: A ressalva que acompanha a receita do portao.
#:
#: Ate 2026-09-21 a mensagem documentava um caminho e nao dizia a condicao dele.
#: A receita e isenta do contador SO enquanto a linha nao tem composicao, e um
#: `cd ... &&` na frente ou um `| tail -1` atras a tiram da isencao em silencio:
#: o CLI responde `verified: true`, o PostToolUse sobe a revisao logo atras, e o
#: portao bloqueia de novo lendo `verified=False`. Quem passou por isso nao tinha
#: como saber por que — o `2>&1` que causava a maior parte dos casos foi
#: consertado em `_duplicacao_de_fd`, mas as outras formas de composicao seguem
#: valendo, e agora estao escritas.
AVISO_LINHA_SOZINHA = (
    "Copie a linha inteira e rode SOZINHA: sem `cd` antes, sem `&&`, sem `;` e "
    "sem pipe. Composicao de shell tira o comando da isencao e ele passa a "
    "invalidar a evidencia que acabou de gravar. Redirecionar descritor "
    "(`2>&1`) pode."
)


AVISO_COMPOSICAO = (
    "[harness] evidencia de teste NAO gravada: o comando tem composicao de "
    "shell (pipe, `&&`, `;`, nova linha ou substituicao), e um comando composto "
    "pode fabricar saida de teste. Para que conte, rode sozinho: {sugestao}"
)

#: Evidencia sem caso coletado nao verifica (contrato Harness4Contract v1). O
#: silencio aqui custou dois runs de ~7 min repetidos em 2026-09-02. A causa
#: daquele dia — suite em background, PostToolUse antes de existir saida — tem
#: aviso proprio desde 2026-09-30 (`AVISO_SEGUNDO_PLANO`); o que sobra aqui e
#: saida em primeiro plano sem contagem que o hook reconheca.
AVISO_SEM_CASOS = (
    "[harness] evidencia de teste gravada SEM casos coletados, entao nao "
    "verifica: a saida nao trazia contagem de testes que o hook reconheca."
)

#: Lancar a suite em segundo plano nao e resultado. Ate 2026-09-30 o PostToolUse
#: do lancamento passava por `is_trusted_verification` e gravava uma linha com
#: `tests_collected=NULL` — que nunca verificou nada (a regua exige
#: `tests_collected > 0`) e so enchia `evidence`. Caso real: balde `037312e7`,
#: suite de ~21 min empurrada para o fundo pelo timeout de 10 min da ferramenta.
#: Ver `docs/specs/portao-stop-em-voo-diagnostico.md`.
#:
#: Desde 2026-09-30 o lancamento fica registrado e a evidencia e capturada
#: sozinha no termino, na revisao do lancamento
#: (`docs/specs/evidencia-em-segundo-plano-spec.md`). A receita manual fica como
#: saida quando a captura nao acontece — e so vale se nada mudou desde entao.
AVISO_SEGUNDO_PLANO = (
    "[harness] a suite foi para segundo plano (background, job {job}): o "
    "lancamento NAO e resultado, e nenhuma evidencia foi gravada agora. Enquanto "
    "o job roda, o Stop nao cobra continuacao. Quando ele terminar, a evidencia "
    "sera capturada sozinha, na code_revision {revisao} — qualquer escrita antes "
    "disso a torna historico, e historico nao verifica. Se a captura nao "
    "acontecer (a mensagem do portao lista o lancamento e o motivo), leia a "
    "saida e, so se nenhum arquivo mudou desde o lancamento, registre a "
    "evidencia:\n{comando}"
)

#: As duas formas de texto com que o host anuncia um job em segundo plano: o
#: pedido (`run_in_background`) e o empurrado pelo timeout da ferramenta.
_ANUNCIO_DE_SEGUNDO_PLANO = re.compile(
    r"running in background with ID:\s*([A-Za-z0-9_-]+)"
    r"|moved to the background \(ID:\s*([A-Za-z0-9_-]+)\)"
)


#: O proprio CLI de estado do harness. Ver `is_state_management`.
STATE_CLI_PATTERNS = (
    r"\bstate_cli\.py\b",
    r"\bbranch_state\.py\b",
    r"\bconfirm_classification\.py\b",
)


def is_state_management(command: str) -> bool:
    """O comando so mexe no estado do harness, nao no codigo do projeto.

    `_handle_post_tool` trata todo comando de shell como possivel alteracao de
    codigo e chama `touch_file`, que zera `verified` e sobe `code_revision`. A
    heuristica e conservadora e correta para `sed -i`, `npm install`, `git
    checkout`. Mas ela criava um deadlock estrutural: `state_cli.py complete`
    so pode ser invocado por shell, e a propria invocacao invalidava, no mesmo
    PostToolUse, a evidencia que o `complete` exige. **Nenhuma task podia ser
    concluida pelo caminho previsto.**

    Medido em 2026-09-02: `code_revision` foi 501 -> 507 -> 511 entre gravar a
    evidencia e tentar fechar, sem uma linha de codigo mudar. `verified` foi
    para True tres vezes e voltou para False no comando seguinte, todas.

    A isencao e estreita de proposito: so o CLI do proprio harness, e so
    quando o comando nao tem composicao de shell — `state_cli.py ... && sed -i
    ...` continua contando como alteracao, porque a segunda metade e.
    """
    if not command or _has_unquoted_shell_composition(command):
        return False
    return any(re.search(p, command, re.IGNORECASE) for p in STATE_CLI_PATTERNS)



#: Destinos de redirecionamento que nao sao arquivo do projeto.
_DESTINOS_NULOS = frozenset({'/dev/null', 'nul', 'NUL', 'con', 'CON'})


#: Caracteres que NENHUM nome de arquivo pode conter neste sistema de arquivos.
#: Controle inclui nova linha, retorno de carro e tabulacao.
_CARACTERES_ILEGAIS = frozenset('<>"|?*') | frozenset(chr(c) for c in range(32))

_LETRA_DE_DRIVE = re.compile(r'^[A-Za-z]:$')

#: O motivo da recusa da etapa 2 de R3. Tem nome proprio porque `is_read_only`
#: e `nao_muda_a_arvore` precisam distingui-lo dos outros: recusar um destino
#: significa "houve escrita e eu nao sei para onde", nunca "nao houve escrita".
MOTIVO_IMPOSSIVEL = "nao-pode-ser-caminho"


def nao_pode_ser_caminho(candidato: str) -> str | None:
    """O motivo, se este candidato NAO PODE ser um arquivo. `None` se pode.

    A regua e deliberadamente essa, e nao "nao PARECE um caminho". A diferenca
    e a direcao do erro: um candidato rejeitado por engano e uma escrita real
    que some da atribuicao, o oposto do fail-closed de `inside_root`. Entao so
    entra aqui o que e impossivel, nao o que e improvavel.

    Vem da §2 do mapa `revisao-que-invalida`, onde 113 entradas da tabela real
    passavam pela regua mais frouxa do autor e nao podiam ser arquivo nenhum:
    `$TEMP\\claude\\orfaos.py`, `$LOCK\\dono`, `$P\\$f`, corpo de documento
    inteiro, e `0.75:` — que e literalmente uma linha da tabela `files`.

    O mapa registrou um erro de instrumento aqui em vez de apaga-lo: a primeira
    versao classificava `0.75`, `0.99999` e `F4.0` como caminhos VALIDOS fora da
    raiz, porque a regex de extensao era `\\.[A-Za-z0-9]{1,6}$` e `.75` casa. O
    numero saiu plausivel — inflava uma classe em 3 — e quase passou. Esta regua
    nao usa extensao nenhuma, justamente por isso.

    A clausula "sem separador, forma que nao e nome de arquivo" da §2 NAO foi
    trazida: `MSGEOF` e `Passar` sao nomes de arquivo perfeitamente validos, e
    quem os elimina e a etapa 1 (corpo de heredoc), pela causa e nao pela forma.
    Adivinhar aqui erraria na direcao perigosa.
    """
    texto = candidato.strip()
    if not texto:
        return "vazio"
    if any(caractere in _CARACTERES_ILEGAIS for caractere in texto):
        return "caractere-ilegal"
    for indice, caractere in enumerate(texto):
        if caractere != '$':
            continue
        seguinte = texto[indice + 1] if indice + 1 < len(texto) else ''
        if seguinte in {'(', '{'} or seguinte.isalpha() or seguinte == '_':
            return "variavel-nao-expandida"
    if not any(caractere.isalnum() for caractere in texto):
        return "sem-alfanumerico"
    if _LETRA_DE_DRIVE.match(texto):
        # `b:` e uma referencia de drive, nao um arquivo. Passava pela regua ate
        # a regressao dos 468 caminhos apontar: o teste da letra de drive existe
        # para aceitar `C:\...`, e sozinho ele aceitava tambem o `C:` pelado.
        return "so-letra-de-drive"
    if texto[-1] in {'/', chr(92)}:
        return "termina-em-separador"      # nomeia diretorio, nao arquivo
    componentes = re.split(r'[/\\]', texto)
    for posicao, componente in enumerate(componentes):
        if not componente:
            continue                       # raiz, UNC ou barra dupla: legitimo
        if componente in {'.', '..'}:
            continue                       # `../saida.txt` e caminho de verdade
        if componente[-1] in {'.', ' '}:
            return "componente-termina-em-ponto-ou-espaco"
        if ':' in componente and not (posicao == 0 and _LETRA_DE_DRIVE.match(componente)):
            return "dois-pontos-fora-de-letra-de-drive"
    return None


#: Dentro de aspas DUPLAS, a barra invertida so escapa estes. Antes de qualquer
#: outro caractere ela e literal — regra do POSIX, e a que o bash aplica.
#:
#: `_tokenize` escapava INCONDICIONALMENTE, e o efeito era comer os separadores
#: de todo caminho absoluto do Windows entre aspas: `"C:\Users\me\x.py"` chegava
#: em `files` como `C:UsersmeX.py`. Medido em 2026-09-17 ao ligar R2 — o filtro
#: de raiz nao tinha como funcionar porque `os.path.isabs` respondia False sobre
#: o proprio caminho que ele deveria comparar.
#:
#: E o mesmo defeito, numa direcao a mais, que o mapa `revisao-que-invalida`
#: mediu na classe A: escrita REAL que chega ao banco parecendo lixo. A regua do
#: autor ("sem separador e sem extensao") classificava essas entradas como
#: "nao parece caminho" — e elas eram caminho, mutilado na tokenizacao.
_ESCAPAVEIS_EM_ASPAS_DUPLAS = frozenset({chr(34), chr(92), '$', '`', chr(10)})


def _tokenize(command: str) -> list[str]:
    """Tokens do comando, com os operadores fora de aspas separados.

    Conteudo entre aspas nunca vira operador: `echo 'a > b'` nao escreve
    em `b`. E por isso que uma regex sobre a linha crua nao serve aqui.
    """
    tokens: list[str] = []
    atual: list[str] = []
    quote = None
    escaped = False

    def fechar():
        if atual:
            tokens.append(''.join(atual))
            atual.clear()

    for indice, character in enumerate(command):
        if escaped:
            atual.append(character)
            escaped = False
            continue
        if quote:
            if character == chr(92) and quote == chr(34):
                seguinte = command[indice + 1] if indice + 1 < len(command) else ''
                if seguinte in _ESCAPAVEIS_EM_ASPAS_DUPLAS:
                    escaped = True
                    continue
            if character == quote:
                quote = None
                continue
            atual.append(character)
            continue
        if character in {chr(39), chr(34)}:
            quote = character
            continue
        if character in {chr(10), chr(13)}:
            # Quebra de linha fora de aspas separa comandos. Ate 2026-09-30 ela
            # caia no `isspace` abaixo e virava espaco: `echo hi` + nova linha +
            # `python gera.py` era UM segmento de `echo`, e `is_read_only`
            # dizia True. `\r` entra junto porque o PowerShell tambem o le como
            # fim de linha, e `_scan_composition` ja o tratava como operador.
            fechar()
            tokens.append(chr(10))
            continue
        if character.isspace():
            fechar()
            continue
        if character == '>':
            fechar()
            if tokens and tokens[-1] == '>':
                tokens[-1] = '>>'
            else:
                tokens.append('>')
            continue
        if character in {';', '|', '&'}:
            fechar()
            tokens.append(character)
            continue
        atual.append(character)
    fechar()
    return tokens


_OPERADORES_TOKEN = frozenset({'>', '>>', ';', '|', '&', chr(10)})


#: Binarios cujo unico efeito e ler. Cada nome aqui e a afirmacao "este comando
#: nao escreve", e uma afirmacao errada apaga alteracao de codigo do contador —
#: por isso a lista e curta e nao inclui interpretador (`python`, `awk`) nem
#: comando que muda de efeito pelo argumento (`find -delete`, `git config`).
#:
#: `cd` entrou em 2026-09-25: muda o diretorio do shell e nao escreve arquivo
#: nenhum, e `cd X && grep ...` e a forma mais comum de leitura. Na task do
#: incidente dos docs, 20 dos 27 comandos de leitura pura que subiram
#: `code_revision` comecavam por `cd`. `find` tambem le, mas escreve e executa
#: pelo argumento — por isso fica fora daqui e tem regra propria em
#: `_FIND_QUE_ESCREVE`.
#:
#: Estar aqui NAO dispensa a opcao. `sort -o`, `uniq IN OUT` e `sed -i`/`w`
#: escrevem, e ate 2026-09-30 passavam como leitura; a regra de cada um mora em
#: `_ESCRITA_POR_OPCAO`. A auditoria das demais entradas esta escrita em
#: `tests/test_transactional_hook.py::SEM_ESCRITA_POR_OPCAO`, e o teste reprova
#: binario novo que entre aqui sem passar por uma das duas.
_SOMENTE_LEITURA = frozenset({
    'cat', 'head', 'tail', 'wc', 'grep', 'rg', 'ls', 'pwd', 'echo', 'printf',
    'sort', 'uniq', 'cut', 'nl', 'basename', 'dirname', 'stat', 'diff', 'cmp',
    'date', 'sed', 'true', 'false', 'cd',
})

#: As acoes de `find` que escrevem ou executam, comparadas por token exato —
#: `-name '*-delete*'` e um padrao de busca, nao uma acao. Sem nenhuma delas,
#: `find` so lista. Medido no mesmo incidente: 7 comandos `find` de leitura,
#: nenhum com acao.
_FIND_QUE_ESCREVE = frozenset({
    '-delete', '-exec', '-execdir', '-ok', '-okdir',
    '-fprint', '-fprint0', '-fprintf', '-fls',
})

#: Subcomandos de git que so leem. `branch`, `remote` e `config` ficam de fora:
#: os tres escrevem dependendo da flag.
#:
#: `ls-tree`, `merge-base` e `rev-list` entraram em 2026-09-30: apareciam entre
#: os toques de leitura pura da sessao `44b0dfb5`. Nenhum tem flag que escreva.
#: `merge` e `merge-file` continuam fora — a lista compara o nome inteiro.
_GIT_SOMENTE_LEITURA = frozenset({
    'status', 'log', 'diff', 'show', 'ls-files', 'rev-parse', 'blame',
    'shortlog', 'describe', 'cat-file', 'grep',
    'ls-tree', 'merge-base', 'rev-list',
})


#: Git que escreve em `.git/` e deixa a ARVORE DE TRABALHO byte-identica.
#:
#: Nao sao read-only — `add` mexe no indice e `commit` cria objeto e move HEAD —
#: mas a evidencia de teste e sobre o codigo que a suite mediu, e esse codigo e
#: a arvore. Sem esta lista, a sequencia obrigatoria do proprio workflow era
#: impossivel de completar: rodar a suite -> gravar evidencia -> commitar ->
#: responder. O commit invalidava a evidencia que o justificou. Medido em
#: 2026-09-16: `code_revision` foi de 218 para 220 por um `git add` e um
#: `git commit`, com a arvore limpa.
#:
#: EXCECAO CONHECIDA, declarada em vez de escondida: commit feito DIRETAMENTE em
#: `main` move a ref publicada, e `test_deploy_drift` compara o cache contra ela
#: — entao um commit ali pode virar o veredito daquele teste sem expirar a
#: evidencia. O workflow deste repositorio manda ramificar antes, e a proxima
#: suite pega. Nao esta coberto, e esta escrito.
_GIT_NAO_MUDA_ARVORE = frozenset({'add', 'commit'})


#: Opcoes globais do git que consomem o token SEGUINTE como valor. Medido no git
#: 2.55.0: `git <opcao> <valor> version` e aceito para cada uma destas. As que
#: nao levam valor (`--no-pager`, `--no-optional-locks`, `-p`, `--bare`...) e a
#: forma `--opcao=valor` ja sao um token so com `-` e nao precisam de lista.
#:
#: Ate 2026-09-30 o subcomando era "o primeiro token sem `-`", e em
#: `git -C <dir> log` esse token era `<dir>`. Nos transcripts da sessao
#: `44b0dfb5` (principal + subagentes, medidos em 2026-09-30), as 181 linhas
#: `git <opcao com valor> ...` eram classificadas como toque, leitura inclusive.
#: Na direcao oposta, `git -C log checkout main` passava por leitura.
#:
#: Opcao com valor que nao esteja aqui continua do lado seguro: o valor vira o
#: subcomando, nao esta em lista nenhuma, e o comando conta.
#:
#: EXCECAO CONHECIDA, declarada em vez de escondida: `-c` e `--config-env`
#: trocam configuracao, e ha chave que faz um subcomando de leitura executar
#: programa (`core.fsmonitor` em `status`, `diff.external` em `diff`). A mesma
#: chave no `.git/config` ja tem esse poder sobre `git status` puro; em linha
#: ela chega sem escrita contada antes. Nas 181 linhas medidas, as unicas
#: chaves em `-c` sao `core.autocrlf` e `http.sslCAInfo`, e nenhuma executa
#: programa. Nao esta coberto, e esta escrito.
_GIT_OPCAO_COM_VALOR = frozenset({
    '-C', '-c', '--git-dir', '--work-tree', '--namespace', '--attr-source',
    '--config-env',
})


def _subcomando_git(partes: list[str]) -> str | None:
    """O subcomando de `git ...`, depois das opcoes globais e dos valores delas."""
    indice = _posicao_do_subcomando_git(partes)
    return partes[indice] if indice is not None else None


def _posicao_do_subcomando_git(partes: list[str]) -> int | None:
    """Indice do subcomando em `partes`. `_escrita_do_git` le as opcoes depois dele."""
    indice = 1
    while indice < len(partes):
        parte = partes[indice]
        if parte in _GIT_OPCAO_COM_VALOR:
            indice += 2
        elif parte.startswith('-'):
            indice += 1
        else:
            return indice
    return None


def nao_muda_a_arvore(command: str) -> bool:
    """O comando escreve, mas nao no codigo que a suite mede.

    O commit do Claude Code, `git commit -m "$(cat <<'EOF' ... EOF)"`, passa
    aqui porque a substituicao dele roda so `cat`: cada corpo de `_decompor`
    tem de ser leitura, e `git commit -m "$(python gera.py)"` nao passa.
    """
    decomposto = _decompor(command) if command else None
    if decomposto is None:
        return False
    externa, corpos = decomposto
    if not all(_corpo_so_le(corpo) for corpo in corpos):
        return False
    if _alvos_da_linha(externa) or _redireciona_para_arquivo(externa):
        return False
    segmentos = _segmentos(externa)
    if not segmentos:
        return False
    for partes in segmentos:
        if _binario(partes[0]) != 'git' or _escrita_por_opcao(partes)[1]:
            return False
        if _subcomando_git(partes) not in (_GIT_NAO_MUDA_ARVORE | _GIT_SOMENTE_LEITURA):
            return False
    return True


def _binario(token: str) -> str:
    nome = token.replace(chr(92), '/').rsplit('/', 1)[-1]
    return nome[:-4] if nome.endswith('.exe') else nome


def _redireciona_para_arquivo(command: str) -> bool:
    """Algum `>`/`>>` da linha aponta para destino que nao e nulo.

    `shell_write_targets` nao basta para esta pergunta: ele recusa como caminho
    o que nao PODE ser arquivo (`> $OUT`, variavel nao expandida), e a lista sai
    vazia para uma escrita real. Ate 2026-09-25 `is_read_only("cat x > $OUT")`
    dava False por acidente — o `$OUT` virava cabeca de segmento em
    `_segmentos`. Com `_segmentos` consumindo o alvo, a recusa passa a ser esta.

    `>` sem alvo e erro de sintaxe no shell, nao escrita. A duplicacao de
    descritor (`2>&1`, `>&2`) nao aponta para arquivo e passa.

    O corpo de heredoc sai antes, como em `_segmentos`: ate 2026-09-30 o `>` do
    `<noreply@anthropic.com>` na mensagem de `git commit -F - <<'EOF'` era lido
    como redirecionamento, e todo commit por heredoc contava como mudanca.
    """
    tokens = _tokenize(sem_corpo_de_heredoc(command))
    for indice, token in enumerate(tokens):
        if token not in {'>', '>>'} or indice + 1 >= len(tokens):
            continue
        alvo = tokens[indice + 1]
        if alvo == '&' or alvo in _DESTINOS_NULOS:
            continue
        return True
    return False


def _segmentos(command: str) -> list[list[str]]:
    """Os comandos da linha, um por segmento.

    A duplicacao de descritor e descartada em vez de partir o segmento. Sem
    isto, `git status 2>&1` virava `[['git','status','2'], ['1']]`, o segundo
    segmento comecava por `1`, e `is_read_only` respondia False para um
    comando que so le — a mesma causa de `_duplicacao_de_fd`, por outra porta.

    O alvo de `>`/`>>` tambem nao abre segmento: ele e argumento do
    redirecionamento, nao comando. Ate 2026-09-25 `ls x 2>/dev/null | grep y`
    virava `[['ls','x','2'], ['/dev/null'], ['grep','y']]`, e `null` nao e
    binario de leitura — 10 dos 27 comandos de leitura pura do incidente dos
    docs subiram `code_revision` so por isso. Quem decide se o alvo e escrita
    e `_redireciona_para_arquivo`, chamado antes por quem usa os segmentos.

    Nova linha abre segmento (ver `_tokenize`), e por isso o corpo de heredoc
    sai ANTES, como em `shell_write_targets`: o corpo e dado para o stdin, nao
    comando, e cada linha dele viraria um segmento de "binario" desconhecido.
    """
    return _segmentar(_tokenize(sem_corpo_de_heredoc(command)))


def _segmentar(tokens: list[str]) -> list[list[str]]:
    """`_segmentos` sobre tokens ja prontos.

    Separado porque `sem_corpo_de_heredoc` nao e idempotente: aplicado ao texto
    ja limpo, o `cat <<EOF` que sobrou abriria outro corpo e engoliria as linhas
    seguintes. `shell_write_targets` limpa uma vez e segmenta os proprios tokens.
    """
    segmento: list[str] = []
    segmentos: list[list[str]] = []
    indice = 0
    while indice < len(tokens):
        token = tokens[indice]
        if (
            token in {'>', '<'}
            and indice + 2 < len(tokens)
            and tokens[indice + 1] == '&'
            and tokens[indice + 2]
            and all(c in _ALVOS_DE_FD for c in tokens[indice + 2])
        ):
            indice += 3
            continue
        if token in {'>', '>>'}:
            # O alvo e argumento do redirecionamento — salvo quando o que vem e
            # operador. `echo x >` no fim da linha e erro de sintaxe, e engolir
            # a quebra fundiria a linha seguinte neste segmento.
            seguinte = tokens[indice + 1] if indice + 1 < len(tokens) else None
            indice += 1 if seguinte in _OPERADORES_TOKEN else 2
            continue
        if token in _OPERADORES_TOKEN:
            if segmento:
                segmentos.append(segmento)
            segmento = []
            indice += 1
            continue
        segmento.append(token)
        indice += 1
    if segmento:
        segmentos.append(segmento)
    return segmentos


#: O que entra na linha externa no lugar de cada substituicao. Comeca por `$_`
#: de proposito: `nao_pode_ser_caminho` o recusa como variavel, entao `> $(x)`
#: continua sendo redirecionamento sem alvo atribuivel; e, como cabeca de
#: segmento, nao casa com binario de leitura nenhum — `$(x) arg` roda o que a
#: substituicao imprimir.
_MARCA_DE_SUBSTITUICAO = '$__substituicao__'


class _SubstituicaoAberta(ValueError):
    """Uma substituicao abriu e a linha acabou antes de ela fechar."""


def _decompor(command: str) -> tuple[str, list[str]] | None:
    """`(linha externa, corpos)`: cada substituicao sai da linha e vira um corpo.

    Substituicao e o trecho que o shell EXECUTA para usar a saida: `$( )` e
    crase (fora de aspas e dentro de aspas duplas), `<( )` e `>( )`, o `( )` de
    subshell — que tambem e o agrupamento do PowerShell, `echo (python x)` —
    e a aritmetica `$(( ))`, que nao executa mas pode conter uma substituicao.
    Corpo de heredoc sem aspas no delimitador passa pela mesma expansao.

    Ate 2026-09-30 `_tokenize` tratava todos como caractere comum:
    `echo $(python gera.py)` era um segmento de `echo`, e `is_read_only` dizia
    True para uma linha que roda um programa. Recusar todo `$(` tambem nao
    serve: o commit do Claude Code, `git commit -m "$(cat <<'EOF' ... EOF)"`,
    roda so `cat`, e conta-lo de volta faria todo commit invalidar a evidencia
    — o problema que `_GIT_NAO_MUDA_ARVORE` resolve. Por isso cada corpo e
    devolvido para ser julgado como comando, recursivamente.

    Corpo de heredoc com delimitador entre aspas e literal e e pulado: la,
    crase e parentese de mensagem de commit sao texto.

    `None` quando uma substituicao nao fecha: nao da para saber o que roda.
    Aspa sem fechamento nao e isso — o shell recusa a linha inteira, nada roda.

    EXCECOES CONHECIDAS, todas na direcao segura (contam a mais, nunca a menos):
    a crase e escape no PowerShell, entao `echo "a`nb"` la vira substituicao
    sem fechamento e conta; `$( )` dentro de comentario `#` conta; e `case`
    dentro de `$( )` fecha no primeiro `x)` e deixa um `)` solto, que conta.
    """
    try:
        externa, corpos, _fim = _varrer(command, 0, 'shell', fecha=False)
    except _SubstituicaoAberta:
        return None
    return externa, corpos


def _varrer(texto: str, inicio: int, modo: str, fecha: bool) -> tuple[str, list[str], int]:
    """Varre `texto` desde `inicio`. Devolve `(saida, corpos, fim)`.

    `modo`: `shell` (linha de comando), `expande` (corpo de heredoc sem aspas:
    aspas e parenteses sao literais, `$( )` e crase executam) ou `aritmetica`
    (parentese agrupa conta, nao abre subshell). Com `fecha`, a varredura para
    no `)` que fecha a substituicao aberta por quem chamou, e `fim` aponta logo
    depois dele.
    """
    saida: list[str] = []
    corpos: list[str] = []
    aspas = None
    profundidade = 0
    heredocs: list[tuple[str, bool, bool]] = []
    indice = inicio
    tamanho = len(texto)

    def substituir(corpo: str, depois: int) -> int:
        corpos.append(corpo)
        saida.append(_MARCA_DE_SUBSTITUICAO)
        return depois

    def ate_fechar(abertura: int) -> int:
        """Indice logo depois do `)` que fecha o corpo aberto em `abertura`."""
        return _varrer(texto, abertura, 'shell', fecha=True)[2]

    def entre_parenteses(abertura: int) -> int:
        fim = ate_fechar(abertura)
        return substituir(texto[abertura:fim - 1], fim)

    def aritmetica(abertura: int) -> int:
        """A conta nao executa; o que ela contem, sim. Pede o segundo `)`."""
        _conta, internos, fim = _varrer(texto, abertura, 'aritmetica', fecha=True)
        if fim >= tamanho or texto[fim] != ')':
            raise _SubstituicaoAberta(texto[abertura:])
        corpos.extend(internos)
        saida.append(_MARCA_DE_SUBSTITUICAO)
        return fim + 1

    while indice < tamanho:
        caractere = texto[indice]
        if aspas == chr(39):
            if caractere == chr(39):
                aspas = None
            saida.append(caractere)
            indice += 1
            continue
        if caractere == chr(92):
            saida.append(texto[indice:indice + 2])
            indice += 2
            continue
        if texto.startswith('$((', indice):
            indice = aritmetica(indice + 3)
            continue
        if texto.startswith('$(', indice):
            indice = entre_parenteses(indice + 2)
            continue
        if caractere == '`':
            fim = indice + 1
            while fim < tamanho and texto[fim] != '`':
                fim += 2 if texto[fim] == chr(92) else 1
            if fim >= tamanho:
                raise _SubstituicaoAberta(texto[indice:])
            # Dentro da crase a barra ainda escapa crase, cifrao e barra.
            corpo = re.sub(r'\\([`$\\])', r'\1', texto[indice + 1:fim])
            indice = substituir(corpo, fim + 1)
            continue
        if modo == 'expande':
            saida.append(caractere)
            indice += 1
            continue
        if aspas == chr(34):
            if caractere == chr(34):
                aspas = None
            saida.append(caractere)
            indice += 1
            continue
        if caractere in {chr(39), chr(34)}:
            aspas = caractere
            saida.append(caractere)
            indice += 1
            continue
        if modo == 'aritmetica':
            if caractere == '(':
                profundidade += 1
            elif caractere == ')':
                if not profundidade:
                    return ''.join(saida), corpos, indice + 1
                profundidade -= 1
            saida.append(caractere)
            indice += 1
            continue
        if texto.startswith('((', indice):
            indice = aritmetica(indice + 2)
            continue
        if caractere in {'<', '>'} and texto.startswith('(', indice + 1):
            indice = entre_parenteses(indice + 2)
            continue
        if caractere == '(':
            indice = entre_parenteses(indice + 1)
            continue
        if caractere == ')':
            if fecha:
                return ''.join(saida), corpos, indice + 1
            raise _SubstituicaoAberta(texto[:indice + 1])
        if texto.startswith('<<', indice) and not texto.startswith('<<<', indice):
            achado, depois = _delimitador_de_heredoc(texto, indice)
            if depois is None:
                # Aspa do delimitador sem fechamento: a varredura segue pela
                # aspa, e o que houver depois dela continua sendo olhado.
                depois = indice + 2
            elif achado:
                heredocs.append(achado)
            saida.append(texto[indice:depois])
            indice = depois
            continue
        if caractere == chr(10) and heredocs:
            saida.append(caractere)
            indice = _pular_corpos_de_heredoc(texto, indice + 1, heredocs, corpos, saida)
            heredocs = []
            continue
        saida.append(caractere)
        indice += 1
    if fecha:
        raise _SubstituicaoAberta(texto[inicio:])
    return ''.join(saida), corpos, indice


def _pular_corpos_de_heredoc(
    texto: str,
    indice: int,
    heredocs: list[tuple[str, bool, bool]],
    corpos: list[str],
    saida: list[str],
) -> int:
    """Consome os corpos pendentes a partir de `indice`; devolve onde a linha segue.

    O corpo fica na saida como esta: quem tokeniza a linha externa ja o tira
    por `sem_corpo_de_heredoc`. Corpo sem aspas no delimitador expande `$( )` e
    crase, e o que ele executa vai para `corpos`. Sem delimitador de fechamento,
    o corpo vai ate o fim, como no bash e em `sem_corpo_de_heredoc`.
    """
    for delimitador, ignora_tab, expande in heredocs:
        comeco = indice
        while indice < len(texto):
            quebra = texto.find(chr(10), indice)
            fim_da_linha = len(texto) if quebra == -1 else quebra
            linha = texto[indice:fim_da_linha]
            indice = fim_da_linha + 1 if quebra != -1 else len(texto)
            alvo = linha.lstrip('\t') if ignora_tab else linha
            if alvo.rstrip('\r') == delimitador:
                break
        corpo = texto[comeco:indice]
        if expande:
            corpos.extend(_varrer(corpo, 0, 'expande', fecha=False)[1])
        saida.append(corpo)
    return indice


def is_read_only(command: str) -> bool:
    """Sei que este comando nao escreve — nao apenas "nao consegui ver escrita".

    `shell_write_targets` distingue "escreve em X" de "nao da para saber", e o
    chamador guarda o placeholder no segundo caso. Isso e o certo para um
    programa arbitrario, mas `grep`, `cat` e `git log` nao sao caso duvidoso:
    inspecionar o repositorio subia `code_revision` e invalidava a evidencia da
    suite, o que obrigava a rodar a suite de novo para conseguir fechar.

    A porta e estreita: todo segmento da linha tem de comecar por um binario da
    lista, e qualquer redirecionamento ja tira o comando daqui pelo chamador.
    Cada substituicao (`_decompor`) tem de ser leitura por si.
    """
    decomposto = _decompor(command) if command else None
    if decomposto is None:
        return False
    externa, corpos = decomposto
    return _externa_so_le(externa) and all(_corpo_so_le(corpo) for corpo in corpos)


def _corpo_so_le(corpo: str) -> bool:
    """`$()` vazio nao roda nada; qualquer outro corpo e julgado como comando."""
    return not corpo.strip() or is_read_only(corpo)


def _externa_so_le(command: str) -> bool:
    """`is_read_only` da linha externa, com as substituicoes ja trocadas pela marca."""
    if _alvos_da_linha(command) or _redireciona_para_arquivo(command):
        return False
    segmentos = _segmentos(command)
    if not segmentos:
        return False
    for partes in segmentos:
        if _escrita_por_opcao(partes)[1]:
            return False
        binario = _binario(partes[0])
        if binario == 'git':
            if _subcomando_git(partes) not in _GIT_SOMENTE_LEITURA:
                return False
            continue
        if binario == 'find':
            if any(p in _FIND_QUE_ESCREVE for p in partes[1:]):
                return False
            continue
        if binario not in _SOMENTE_LEITURA:
            return False
    return True


def _fim_da_aritmetica(linha: str, inicio: int) -> int:
    """Indice logo depois da expressao aritmetica que abre em `inicio`.

    `inicio` aponta para `((` (o de `$((` ou o comando `((`) ou para `$[`. Ali
    dentro `<<` e deslocamento de bits, nao heredoc. Lido como heredoc, o `<<2`
    de `echo $((1<<2))` engolia as linhas seguintes como corpo — e, com o corpo
    saindo antes da segmentacao, elas sumiam da leitura. Sem fechamento na
    linha, a expressao vai ate o fim dela.
    """
    abre, fecha = ('[', ']') if linha[inicio] == '$' else ('(', ')')
    cursor = inicio + 1 if abre == '[' else inicio
    profundidade = 0
    while cursor < len(linha):
        if linha[cursor] == abre:
            profundidade += 1
        elif linha[cursor] == fecha:
            profundidade -= 1
            if profundidade == 0:
                return cursor + 1
        cursor += 1
    return len(linha)


def _aberturas_de_heredoc(linha: str) -> list[tuple[str, bool]]:
    """`(delimitador, ignora_tabulacao)` de cada `<<DELIM` FORA de aspas.

    Aspas importam: `echo "a <<EOF b"` nao abre heredoc nenhum. `<<<` e
    here-string — o corpo esta na propria linha, entao nao ha nada a excluir.
    """
    achados: list[tuple[str, bool]] = []
    quote = None
    escaped = False
    indice = 0
    tamanho = len(linha)
    while indice < tamanho:
        caractere = linha[indice]
        if escaped:
            escaped = False
            indice += 1
            continue
        if quote:
            if caractere == chr(92) and quote == chr(34):
                escaped = True
            elif caractere == quote:
                quote = None
            indice += 1
            continue
        if caractere in {chr(39), chr(34)}:
            quote = caractere
            indice += 1
            continue
        if caractere == chr(92):
            escaped = True
            indice += 1
            continue
        if linha.startswith('((', indice) or linha.startswith('$[', indice):
            indice = _fim_da_aritmetica(linha, indice)
            continue
        if caractere == '<' and linha.startswith('<<', indice):
            if linha.startswith('<<<', indice):
                indice += 3
                continue
            achado, depois = _delimitador_de_heredoc(linha, indice)
            if depois is None:
                break
            if achado:
                achados.append(achado[:2])
            indice = depois
            continue
        indice += 1
    return achados


def _delimitador_de_heredoc(
    texto: str, indice: int
) -> tuple[tuple[str, bool, bool] | None, int | None]:
    """O `<<DELIM` que comeca em `indice`: `((delim, ignora_tab, expande), depois)`.

    `expande` e False quando o delimitador vem entre aspas: ai o corpo e
    literal. Sem aspas, o shell expande `$( )` e crase no corpo — e o que
    `_decompor` precisa saber. `depois` e None quando a aspa do delimitador
    nao fecha; o achado e None quando nao ha palavra depois do `<<`.
    """
    tamanho = len(texto)
    cursor = indice + 2
    ignora_tab = False
    if cursor < tamanho and texto[cursor] == '-':
        ignora_tab = True
        cursor += 1
    while cursor < tamanho and texto[cursor] in ' \t':
        cursor += 1
    if cursor < tamanho and texto[cursor] in {chr(39), chr(34)}:
        fim = texto.find(texto[cursor], cursor + 1)
        if fim == -1:
            return None, None
        return (texto[cursor + 1:fim], ignora_tab, False), fim + 1
    fim = cursor
    while fim < tamanho and (texto[fim].isalnum() or texto[fim] in '_-.'):
        fim += 1
    achado = (texto[cursor:fim], ignora_tab, True) if fim > cursor else None
    return achado, max(fim, cursor + 1)


def sem_corpo_de_heredoc(command: str, recusas: list[dict[str, Any]] | None = None) -> str:
    """O comando sem os corpos de heredoc — a correcao de causa raiz do lixo.

    `_tokenize` e um tokenizador de shell POSIX, e ele estava sendo aplicado a
    uma string que muitas vezes NAO e um comando POSIX: ela carrega corpo de
    heredoc, codigo de programa e sintaxe de PowerShell. Dentro dessas regioes o
    rastreio de aspas nao significa nada e todo `>` vira operador.

    Medido no mapa `revisao-que-invalida` §6.2, com as funcoes de producao:

        python - <<'PY' / if riqueza>0.75:        ->  ['0.75:']
        cat > nota.md <<'EOF' / > Passar ...      ->  ['nota.md', 'Passar']
        python - <<'PY' / if a>b: pass            ->  ['b:']

    E `'0.75:'` e literalmente uma linha real da tabela `files`. O caso que
    fecha o achado sobre si: `git commit -F - <<'MSGEOF'` gravou `'MSGEOF'` como
    arquivo, porque a ultima linha da mensagem e a de atribuicao obrigatoria,
    `Co-Authored-By: ... <noreply@anthropic.com>`, e o `>` que fecha o e-mail e
    operador para `_tokenize`. TODA mensagem de commit por heredoc dispara isso.

    O corpo excluido e REGISTRADO em `recusas`, nao descartado em silencio:
    trocar ruido por cegueira seria o mesmo erro numa direcao nova.
    """
    linhas = command.splitlines()
    mantidas: list[str] = []
    pendentes: list[tuple[str, bool]] = []
    consumidas = 0
    for linha in linhas:
        if pendentes:
            consumidas += 1
            delimitador, ignora_tab = pendentes[0]
            alvo = linha.lstrip('\t') if ignora_tab else linha
            if alvo.rstrip('\r') == delimitador:
                pendentes.pop(0)
                if recusas is not None:
                    recusas.append({"candidato": delimitador, "motivo": "delimitador-de-heredoc"})
            continue
        mantidas.append(linha)
        pendentes.extend(_aberturas_de_heredoc(linha))
    if consumidas and recusas is not None:
        recusas.append({"candidato": f"<{consumidas} linha(s)>", "motivo": "corpo-de-heredoc"})
    return "\n".join(mantidas)


def _nome_longo(nome: str, longas: dict[str, str], abrevia: bool) -> str | None:
    """O nome canonico de `--nome`, ou None se ele nao identifica opcao nenhuma.

    O `getopt_long` do GNU aceita qualquer prefixo sem ambiguidade — `sort
    --out=x` escreve em `x`. Prefixo ambiguo ou desconhecido faz o programa
    sair com erro antes de rodar, entao nao ha escrita a atribuir.
    """
    if nome in longas:
        return nome
    if not abrevia or not nome:
        return None
    candidatos = [longa for longa in longas if longa.startswith(nome)]
    return candidatos[0] if len(candidatos) == 1 else None


def _opcoes_gnu(
    argumentos: list[str],
    *,
    com_valor: str = '',
    valor_colado: str = '',
    longas: dict[str, str] | None = None,
    abrevia: bool = True,
) -> tuple[list[tuple[str, str | None]], list[str]]:
    """(opcoes, operandos) de uma linha no estilo `getopt_long`.

    As opcoes saem como `('-o', valor)` e `('--output', valor)`, ja com o nome
    longo resolvido. `com_valor` sao as curtas que exigem valor (colado ou no
    token seguinte), `valor_colado` as que so aceitam valor colado (`sed -i.bak`),
    e `longas` mapeia cada nome longo para `sim`, `opcional` ou `nao`.

    Existe porque "a opcao aparece na linha" nao basta nas duas direcoes: `-uo x`
    agrupa o `-o`, `sort f -o x` o poe depois do operando (o GNU permuta), e em
    `git grep -e -O` o `-O` e o padrao buscado, nao a opcao. Depois de `--` tudo
    e operando.
    """
    longas = longas or {}
    opcoes: list[tuple[str, str | None]] = []
    operandos: list[str] = []
    indice = 0
    while indice < len(argumentos):
        argumento = argumentos[indice]
        indice += 1
        if argumento == '--':
            operandos.extend(argumentos[indice:])
            break
        if argumento.startswith('--'):
            nome, igual, valor = argumento[2:].partition('=')
            resolvido = _nome_longo(nome, longas, abrevia)
            if resolvido and not igual and longas[resolvido] == 'sim' and indice < len(argumentos):
                valor, igual = argumentos[indice], '='
                indice += 1
            opcoes.append(('--' + (resolvido or nome), valor if igual else None))
            continue
        if argumento.startswith('-') and argumento != '-':
            for posicao in range(1, len(argumento)):
                letra, resto = argumento[posicao], argumento[posicao + 1:]
                if letra in com_valor:
                    if not resto and indice < len(argumentos):
                        resto = argumentos[indice]
                        indice += 1
                    opcoes.append(('-' + letra, resto))
                    break
                if letra in valor_colado:
                    opcoes.append(('-' + letra, resto or None))
                    break
                opcoes.append(('-' + letra, None))
            continue
        operandos.append(argumento)
    return opcoes, operandos


_LONGAS_DO_SORT = {
    'output': 'sim', 'compress-program': 'sim', 'key': 'sim',
    'field-separator': 'sim', 'buffer-size': 'sim', 'temporary-directory': 'sim',
    'batch-size': 'sim', 'files0-from': 'sim', 'random-source': 'sim',
    'parallel': 'sim', 'sort': 'sim', 'check': 'opcional',
    'ignore-leading-blanks': 'nao', 'debug': 'nao', 'dictionary-order': 'nao',
    'general-numeric-sort': 'nao', 'human-numeric-sort': 'nao', 'ignore-case': 'nao',
    'ignore-nonprinting': 'nao', 'merge': 'nao', 'month-sort': 'nao',
    'numeric-sort': 'nao', 'random-sort': 'nao', 'reverse': 'nao', 'stable': 'nao',
    'unique': 'nao', 'version-sort': 'nao', 'zero-terminated': 'nao',
    'help': 'nao', 'version': 'nao',
}


def _escrita_do_sort(argumentos: list[str]) -> tuple[list[str], bool]:
    """`-o`/`--output` escreve o resultado; `--compress-program` executa um programa."""
    opcoes, _ = _opcoes_gnu(argumentos, com_valor='kotST', longas=_LONGAS_DO_SORT)
    alvos = [valor for nome, valor in opcoes if nome in {'-o', '--output'} and valor]
    escreve = any(nome in {'-o', '--output', '--compress-program'} for nome, _ in opcoes)
    return alvos, escreve


_LONGAS_DO_UNIQ = {
    'skip-fields': 'sim', 'skip-chars': 'sim', 'check-chars': 'sim',
    'all-repeated': 'opcional', 'group': 'opcional', 'count': 'nao',
    'repeated': 'nao', 'ignore-case': 'nao', 'unique': 'nao',
    'zero-terminated': 'nao', 'help': 'nao', 'version': 'nao',
}


def _escrita_do_uniq(argumentos: list[str]) -> tuple[list[str], bool]:
    """`uniq [ENTRADA [SAIDA]]`: o segundo operando e arquivo escrito, salvo `-`."""
    _, operandos = _opcoes_gnu(argumentos, com_valor='fsw', longas=_LONGAS_DO_UNIQ)
    if len(operandos) >= 2 and operandos[1] != '-':
        return [operandos[1]], True
    return [], False


_LONGAS_DO_SED = {
    'expression': 'sim', 'file': 'sim', 'line-length': 'sim', 'in-place': 'opcional',
    'null-data': 'nao', 'zero-terminated': 'nao', 'separate': 'nao', 'sandbox': 'nao',
    'debug': 'nao', 'posix': 'nao', 'quiet': 'nao', 'silent': 'nao',
    'regexp-extended': 'nao', 'unbuffered': 'nao', 'follow-symlinks': 'nao',
    'binary': 'nao', 'help': 'nao', 'version': 'nao',
}


def _escrita_do_sed(argumentos: list[str]) -> tuple[list[str], bool]:
    """Edicao no lugar, e os comandos do roteiro que escrevem ou executam.

    `-i` edita TODOS os arquivos de entrada, nao so o ultimo, e aparece como
    `--in-place`, agrupado (`-Ei`) e com sufixo (`-i.bak`). O roteiro escreve
    por `w`/`W` e pela flag `w` do `s`, e executa por `e` e pela flag `e`.
    `--sandbox` recusa `e`/`r`/`w`. Roteiro em arquivo (`-f`) nao da para ler
    daqui: conta como escrita.
    """
    opcoes, operandos = _opcoes_gnu(
        argumentos, com_valor='efl', valor_colado='i', longas=_LONGAS_DO_SED
    )
    nomes = {nome for nome, _ in opcoes}
    roteiros = [valor for nome, valor in opcoes if nome in {'-e', '--expression'} and valor is not None]
    em_arquivo = bool(nomes & {'-f', '--file'})
    arquivos = operandos
    if not roteiros and not em_arquivo and operandos:
        roteiros, arquivos = [operandos[0]], operandos[1:]
    alvos: list[str] = []
    escreve = False
    if nomes & {'-i', '--in-place'}:
        alvos.extend(arquivo for arquivo in arquivos if arquivo != '-')
        escreve = True
    if '--sandbox' not in nomes:
        escreve = escreve or em_arquivo
        for roteiro in roteiros:
            do_roteiro, escreve_roteiro = _roteiro_do_sed(roteiro)
            alvos.extend(do_roteiro)
            escreve = escreve or escreve_roteiro
    return alvos, escreve


def _roteiro_do_sed(roteiro: str) -> tuple[list[str], bool]:
    """(arquivos que o roteiro escreve, se escreve ou executa), pela gramatica do GNU sed.

    Procurar `w` no texto nao serve: `sed -n '/def test/p'` e `s/hello/world/`
    tem `e` e `w` dentro da expressao e so leem. Entao o roteiro e lido como o
    sed le — endereco, `!`, comando, e os argumentos de cada comando — e o que
    nao for reconhecido conta como escrita.
    """
    alvos: list[str] = []
    escreve = False
    tamanho = len(roteiro)

    def ate_o_fim_da_linha(cursor: int) -> tuple[str, int]:
        fim = roteiro.find(chr(10), cursor)
        if fim < 0:
            return roteiro[cursor:], tamanho
        return roteiro[cursor:fim], fim + 1

    def delimitado(cursor: int, delimitador: str) -> int:
        """Indice depois do delimitador que fecha, ou -1 se nao fecha."""
        while cursor < tamanho:
            caractere = roteiro[cursor]
            if caractere == chr(92):
                cursor += 2
                continue
            if caractere == delimitador:
                return cursor + 1
            if caractere == chr(10):
                return -1
            cursor += 1
        return -1

    def endereco(cursor: int) -> int:
        if cursor < tamanho and roteiro[cursor].isdigit():
            while cursor < tamanho and (roteiro[cursor].isdigit() or roteiro[cursor] == '~'):
                cursor += 1
            return cursor
        if cursor < tamanho and roteiro[cursor] == '$':
            return cursor + 1
        if cursor < tamanho and roteiro[cursor] in {'/', chr(92)}:
            if roteiro[cursor] == chr(92):
                if cursor + 1 >= tamanho:
                    return -1
                delimitador, cursor = roteiro[cursor + 1], cursor + 2
            else:
                delimitador, cursor = '/', cursor + 1
            cursor = delimitado(cursor, delimitador)
            while 0 <= cursor < tamanho and roteiro[cursor] in 'IM':
                cursor += 1
        return cursor

    def pula_brancos(cursor: int, tambem: str = '') -> int:
        while cursor < tamanho and roteiro[cursor] in ' \t' + tambem:
            cursor += 1
        return cursor

    def arquivo(nome: str) -> None:
        if nome.strip():
            alvos.append(nome.strip())

    cursor = 0
    while cursor < tamanho:
        if roteiro[cursor] in ' \t;' + chr(10):
            cursor += 1
            continue
        if roteiro[cursor] == '#':
            _, cursor = ate_o_fim_da_linha(cursor)
            continue
        cursor = endereco(cursor)
        if cursor < 0:
            return alvos, True
        cursor = pula_brancos(cursor)
        if cursor < tamanho and roteiro[cursor] == ',':
            cursor = pula_brancos(cursor + 1)
            if cursor < tamanho and roteiro[cursor] in '+~':
                cursor += 1
                while cursor < tamanho and roteiro[cursor].isdigit():
                    cursor += 1
            else:
                cursor = endereco(cursor)
                if cursor < 0:
                    return alvos, True
        cursor = pula_brancos(cursor, '!')
        if cursor >= tamanho:
            return alvos, True
        comando = roteiro[cursor]
        cursor += 1
        if comando in '{}=dDgGhHnNpPxzF':
            continue
        if comando in 'lLqQ':
            cursor = pula_brancos(cursor)
            while cursor < tamanho and roteiro[cursor].isdigit():
                cursor += 1
            continue
        if comando in ':btTv':
            while cursor < tamanho and roteiro[cursor] not in ';' + chr(10):
                cursor += 1
            continue
        if comando in 'aic':
            linha, cursor = ate_o_fim_da_linha(cursor)
            while linha.endswith(chr(92)) and cursor < tamanho:
                linha, cursor = ate_o_fim_da_linha(cursor)
            continue
        if comando in 'rRwW':
            nome, cursor = ate_o_fim_da_linha(cursor)
            if comando in 'wW':
                escreve = True
                arquivo(nome)
            continue
        if comando == 'e':
            _, cursor = ate_o_fim_da_linha(cursor)
            escreve = True
            continue
        if comando in 'sy':
            if cursor >= tamanho or roteiro[cursor] in {chr(10), chr(92)}:
                return alvos, True
            delimitador = roteiro[cursor]
            cursor = delimitado(cursor + 1, delimitador)
            if cursor >= 0:
                cursor = delimitado(cursor, delimitador)
            if cursor < 0:
                return alvos, True
            while comando == 's' and cursor < tamanho:
                flag = roteiro[cursor]
                if flag in 'gpiImM' or flag.isdigit():
                    cursor += 1
                elif flag == 'e':
                    escreve = True
                    cursor += 1
                elif flag == 'w':
                    nome, cursor = ate_o_fim_da_linha(cursor + 1)
                    escreve = True
                    arquivo(nome)
                else:
                    break
            continue
        return alvos, True
    return alvos, escreve


#: Subcomandos de leitura do git que aceitam `--output=<arquivo>` (opcao de
#: diff). Conferido em git 2.55: cada um criou o arquivo; `status`, `grep`,
#: `ls-files`, `rev-parse`, `describe` e `cat-file` nao.
_GIT_COM_OUTPUT = frozenset({'diff', 'log', 'show', 'shortlog', 'blame'})

_LONGAS_DO_GIT_GREP = {
    'open-files-in-pager': 'opcional', 'only-matching': 'nao', 'or': 'nao',
    'and': 'nao', 'not': 'nao', 'max-depth': 'sim', 'max-count': 'sim',
    'threads': 'sim', 'after-context': 'sim', 'before-context': 'sim',
    'context': 'sim',
}


def _escrita_do_git(argumentos: list[str]) -> tuple[list[str], bool]:
    """`--output` nos subcomandos de diff, e o pager arbitrario de `git grep -O`.

    `--output` nao aceita abreviacao (git 2.55 recusa `--outp`), e
    `--output-indicator-new` e outra opcao. Depois de `--` e pathspec.
    """
    # A mesma leitura de `_subcomando_git`: `git -C sub diff` tem o `sub` como
    # valor de `-C`, e confundi-lo com o subcomando esconderia o `--output`.
    posicao = _posicao_do_subcomando_git(['git', *argumentos])
    if posicao is None:
        return [], False
    subcomando, resto = argumentos[posicao - 1], argumentos[posicao:]
    if subcomando == 'grep':
        opcoes, _ = _opcoes_gnu(
            resto, com_valor='ABCefm', valor_colado='O', longas=_LONGAS_DO_GIT_GREP
        )
        return [], any(nome in {'-O', '--open-files-in-pager'} for nome, _ in opcoes)
    if subcomando not in _GIT_COM_OUTPUT:
        return [], False
    alvos: list[str] = []
    escreve = False
    for indice, argumento in enumerate(resto):
        if argumento == '--':
            break
        if argumento.startswith('--output='):
            escreve = True
            alvos.append(argumento[len('--output='):])
        elif argumento == '--output':
            escreve = True
            if indice + 1 < len(resto):
                alvos.append(resto[indice + 1])
    return [alvo for alvo in alvos if alvo], escreve


_LONGAS_DO_RG = {
    'pre': 'sim', 'pre-glob': 'sim', 'no-pre': 'nao', 'regexp': 'sim', 'file': 'sim',
    'glob': 'sim', 'iglob': 'sim', 'replace': 'sim', 'type': 'sim', 'type-not': 'sim',
}


def _escrita_do_rg(argumentos: list[str]) -> tuple[list[str], bool]:
    """`--pre COMANDO` roda um programa por arquivo. rg 15.1 nao abrevia opcao."""
    opcoes, _ = _opcoes_gnu(
        argumentos, com_valor='efgtTmABCMjdEr', longas=_LONGAS_DO_RG, abrevia=False
    )
    return [], any(nome == '--pre' and valor for nome, valor in opcoes)


#: Binarios que escrevem ou executam dependendo da opcao. Cada regra devolve
#: `(alvos, escreve)`: os arquivos atribuiveis, e se o segmento escreve ou
#: executa. As duas metades sao independentes de proposito — `sort -o $OUT`
#: escreve sem alvo que a regua aceite, e `rg --pre` executa sem alvo nenhum.
_ESCRITA_POR_OPCAO = {
    'sort': _escrita_do_sort,
    'uniq': _escrita_do_uniq,
    'sed': _escrita_do_sed,
    'git': _escrita_do_git,
    'rg': _escrita_do_rg,
}


def _escrita_por_opcao(partes: list[str]) -> tuple[list[str], bool]:
    """A regra de `_ESCRITA_POR_OPCAO` para o binario que abre o segmento."""
    regra = _ESCRITA_POR_OPCAO.get(_binario(partes[0])) if partes else None
    return regra(partes[1:]) if regra else ([], False)


def shell_write_targets(command: str, recusas: list[dict[str, Any]] | None = None) -> list[str]:
    """Arquivos que este comando de shell escreve, ate onde da para atribuir.

    Existe porque `_handle_post_tool` registrava todo comando como o caminho
    sintetico 'shell-command'. Com PRIMARY KEY(task_id, path) e INSERT OR
    IGNORE, mil comandos viravam UMA linha, e nenhuma nomeava um arquivo — o
    contador de arquivos so crescia por Edit/Write. Em 2026-09-03 uma task
    que alterou 2 arquivos por heredoc registrou `files=0` e virou L0, e
    `proxy_regex_vs_observado` e calculado sobre esse rotulo.

    Cobre redirecionamento, `tee` e a escrita por opcao de `_ESCRITA_POR_OPCAO`
    (`sed -i`, `sort -o`, `uniq IN OUT`, `git diff --output`...), esta so no
    binario que abre o segmento: em `grep -rn uniq a.py b.py` a palavra `uniq`
    e padrao de busca, e ler `b.py` como saida invalidaria evidencia por uma
    leitura. `tee` segue sendo procurado em qualquer posicao. NAO cobre programa que escreve
    por dentro (`python - <<PY` com `write_text`), e nao ha como cobrir: e um
    programa. Por isso o chamador mantem o placeholder quando esta lista sai
    vazia — 'nao da para saber' e diferente de 'nao escreveu'.

    `recusas`, quando passada, recebe um dicionario por candidato REJEITADO.
    Um candidato rejeitado por engano e uma escrita real que some do contador —
    o erro na direcao perigosa. Sem o registro, "o ruido caiu" e "o guarda
    cegou" produzem exatamente o mesmo numero.

    A escrita de dentro de uma substituicao conta: `echo $(sed -i ... x.py)`
    escreve `x.py`. Substituicao sem fechamento (`_decompor` devolve None) e lida
    como a linha crua — `is_read_only` ja a recusa, e o placeholder fica.
    """
    if not command:
        return []
    decomposto = _decompor(command)
    if decomposto is None:
        return _alvos_da_linha(command, recusas)
    externa, corpos = decomposto
    alvos = _alvos_da_linha(externa, recusas)
    for corpo in corpos:
        for alvo in shell_write_targets(corpo, recusas):
            if alvo not in alvos:
                alvos.append(alvo)
    return alvos


def _alvos_da_linha(command: str, recusas: list[dict[str, Any]] | None = None) -> list[str]:
    """`shell_write_targets` de uma linha so, sem descer nas substituicoes."""
    tokens = _tokenize(sem_corpo_de_heredoc(command, recusas))
    alvos: list[str] = []

    def recusar(candidato: str, motivo: str, detalhe: str | None = None) -> None:
        if recusas is not None:
            registro = {"candidato": candidato, "motivo": motivo}
            if detalhe:
                registro["detalhe"] = detalhe
            recusas.append(registro)

    def considerar(candidato: str) -> None:
        if not candidato or candidato in _OPERADORES_TOKEN:
            return recusar(candidato, "vazio-ou-operador")
        if candidato.startswith('-'):
            return recusar(candidato, "flag")
        if candidato in _DESTINOS_NULOS:
            return recusar(candidato, "destino-nulo")
        impossivel = nao_pode_ser_caminho(candidato)
        if impossivel:
            return recusar(candidato, MOTIVO_IMPOSSIVEL, detalhe=impossivel)
        if candidato not in alvos:
            alvos.append(candidato)

    for indice, token in enumerate(tokens):
        if token in {'>', '>>'} and indice + 1 < len(tokens):
            considerar(tokens[indice + 1])
        elif token == 'tee':
            for seguinte in tokens[indice + 1:]:
                if seguinte in _OPERADORES_TOKEN:
                    break
                if seguinte.startswith('-'):
                    continue
                considerar(seguinte)
                break
    for partes in _segmentar(tokens):
        for alvo in _escrita_por_opcao(partes)[0]:
            considerar(alvo)
    return alvos

def _response(payload: dict[str, Any]) -> Any:
    return payload.get("tool_response") or payload.get("toolResponse") or payload.get("output") or ""


def _walk(value: Any):
    if isinstance(value, dict):
        for nested in value.values():
            yield from _walk(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            yield from _walk(nested)
    else:
        yield value


def _explicit_exit_code(value: Any) -> int | None:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = re.sub(r"[^a-z]", "", str(key).casefold())
            if normalized in {"exitcode", "returncode"}:
                if isinstance(nested, int) and not isinstance(nested, bool):
                    return nested
                if isinstance(nested, str) and re.fullmatch(r"-?\d+", nested.strip()):
                    return int(nested)
        for nested in value.values():
            result = _explicit_exit_code(nested)
            if result is not None:
                return result
    elif isinstance(value, (list, tuple)):
        for nested in value:
            result = _explicit_exit_code(nested)
            if result is not None:
                return result
    return None


def _exit_code(payload: dict[str, Any]) -> int | None:
    explicit = _explicit_exit_code(_response(payload))
    if explicit is not None:
        return explicit
    event = _event_name(payload)
    if event == "PostToolUse":
        return 0
    if event == "PostToolUseFailure":
        text = str(payload.get("error") or "")
        for pattern in (
            r"(?:status|exit)\s+code\s*[:=]?\s*(-?\d+)",
            r"exit(?:ed)?\s+with\s+(?:non-zero\s+)?(?:status\s+)?(?:code\s+)?(-?\d+)",
            r"exit\s+status\s+(-?\d+)",
        ):
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return int(match.group(1))
        return 1
    return None


def _response_text(payload: dict[str, Any]) -> str:
    values = [value for value in _walk(_response(payload)) if isinstance(value, str)]
    error = payload.get("error")
    if isinstance(error, str) and error:
        values.append(error)
    return _texto_codificavel("\n".join(values))


def _write_heartbeat(
    payload: dict[str, Any], event: str, harness_root: str | Path | None
) -> None:
    if not event:
        return
    root = Path(harness_root or os.environ.get("HARNESS_DIR") or Path.home() / ".claude" / "harness")
    try:
        heartbeats = root / "heartbeats"
        heartbeats.mkdir(parents=True, exist_ok=True)
        temporary = heartbeats / f".{event}.tmp"
        temporary.write_text(str(time.time()), encoding="utf-8")
        temporary.replace(heartbeats / event)
    except OSError:
        pass


def _test_counts(payload: dict[str, Any]) -> tuple[int | None, int | None, int | None, str | None]:
    """(coletados, passando, pulados, digest) da resposta em primeiro plano.

    A contagem mora em `evidencia_em_segundo_plano.contar_testes`, a mesma que
    conta o arquivo de saida da suite em segundo plano (REQ-F5).
    """
    return contar_testes(_response_text(payload))


def _projection(bucket: Path) -> dict[str, Any]:
    try:
        value = json.loads((bucket / "state.json").read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _sync_projection(bucket: Path, projection: dict[str, Any], task: dict[str, Any]) -> None:
    projection.update(
        {
            "task_id": task["task_id"],
            "status": task["status"],
            "pipeline": task["pipeline"],
            "current_step": task["phase"],
            "revision": task["revision"],
            "code_revision": task["code_revision"],
            "verified": task["verified"],
            "stop_continuations": task["stop_continuations"],
            "pending_gate": task["pending_gate"],
            "scope_id": task["scope_id"],
            # A projecao do hook e a que sobrescreve o state com mais
            # frequencia. Sem esta linha ela reintroduzia a lista vazia a cada
            # PostToolUse, desfazendo o que o `state_cli` tivesse acabado de
            # projetar — as duas metades precisam existir juntas.
            "artifacts_so_far": [a["path"] for a in task.get("artifacts", [])],
        }
    )
    # A projecao lida no inicio do hook pode ja ter sido trocada: um prompt de
    # troca explicita abriu outra task enquanto este PostToolUse rodava.
    # Regravar o snapshot antigo apontaria a projecao para a task superada, e
    # todo PostToolUse seguinte (toques, evidencia) iria para ela. Relido aqui,
    # no ultimo momento; a janela que sobra e a do proprio replace.
    atual = _projection(bucket).get("task_id")
    if atual and atual != task["task_id"]:
        return
    # Escrita pelo helper unico (`scripts/projecao.py`): tmp de nome unico,
    # retentativa curta, nunca levanta. Com o tmp fixo e `replace` direto, 56%
    # das escritas levantavam com 1 escritor concorrente e 86% com 4 (ramo
    # ciclo-de-vida-da-task). Esgotou, registra — falha engolida sem registro e
    # o que deixou o incidente 2 sem prova.
    ultimo = gravar_json_atomico(bucket / "state.json", projection)
    if ultimo is None:
        return
    try:
        with (bucket / "projection-errors.log").open("a", encoding="utf-8") as log:
            log.write(
                f"{time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime())} "
                f"task={task['task_id']} {type(ultimo).__name__}: {ultimo}\n"
            )
    except OSError:
        pass


def _database_for_payload(
    payload: dict[str, Any], harness_root: str | Path | None
) -> tuple[Path, HarnessDatabase, dict[str, Any], dict[str, Any]] | None:
    root = Path(harness_root or os.environ.get("HARNESS_DIR") or Path.home() / ".claude" / "harness")
    bucket = ensure_state_dir(
        root,
        payload.get("cwd") or None,
        session_id=payload.get("session_id") or payload.get("sessionId") or None,
    )
    projection = _projection(bucket)
    task_id = projection.get("task_id")
    if not task_id or not (bucket / "harness.db").is_file():
        return None
    database = HarnessDatabase(bucket)
    try:
        task = database.task(str(task_id))
    except StateTransitionError:
        return None
    return bucket, database, projection, task


#: Onde cada recusa do extrator de caminhos fica registrada, dentro do balde da
#: sessao. Append-only, uma linha JSON por recusa. Sem rotacao de proposito: o
#: balde e por sessao e a linha tem ~150 bytes, entao o arquivo morre com a
#: sessao. Se um dia crescer demais, a correcao e rotacionar — nunca parar de
#: escrever, que e o defeito que este arquivo existe para nao repetir.
ARQUIVO_DE_RECUSAS = "recusas.jsonl"


def _emissor(payload: dict[str, Any]) -> dict[str, str]:
    """Quem emitiu o comando, nos campos que o Claude Code poe no payload.

    `agent_id` e `agent_type` so existem dentro de subagente; no principal o
    dicionario sai vazio. O `session_id` nao serve para esta pergunta: o
    subagente chega com o do pai (sonda de 2026-09-28, Claude Code 2.1.283).
    """
    return {
        chave: str(payload[chave])
        for chave in ("agent_id", "agent_type")
        if payload.get(chave)
    }


def _registrar_recusas(
    bucket: Path,
    task_id: str,
    command: str,
    alvos: list[str],
    recusas: list[dict[str, Any]],
    emissor: dict[str, str] | None = None,
) -> None:
    """Toda recusa do extrator fica escrita, com comando, candidato e motivo.

    O risco do conserto de R3 e na direcao perigosa: um candidato rejeitado por
    engano e uma escrita real que some do contador, e o contador nao denuncia a
    propria cegueira — "o ruido caiu" e "o guarda parou de ver" dao o mesmo
    numero. Este arquivo e o que separa os dois.

    O mapa `revisao-que-invalida` so conseguiu medir alguma coisa porque as
    entradas ACEITAS ficavam em `files`. As recusadas nunca ficaram em lugar
    nenhum, e a proxima pergunta seria irrespondivel pelo mesmo motivo.

    `emissor` (ver `_emissor`) entra em cada linha. Na sessao `44b0dfb5` a
    pergunta "quem emitiu o comando que invalidou?" so teve resposta por
    correlacao de carimbo de tempo com os transcripts dos subagentes.

    Degrada em silencio: falha de escrita aqui nunca pode derrubar o hook.
    """
    if not recusas:
        return
    # O hook ja entrega texto codificavel (`_texto_codificavel`), mas o contrato
    # acima e desta funcao, nao de quem a chama. `surrogatepass` no hash e
    # `backslashreplace` no arquivo tornam a escrita total para qualquer `str`
    # sem perder a linha: um surrogate solitario sai como `\ud800`, que e o
    # escape JSON dele, e `json.loads` devolve o texto identico.
    digest = hashlib.sha256(command.encode("utf-8", "surrogatepass")).hexdigest()[:12]
    agora = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    try:
        with (bucket / ARQUIVO_DE_RECUSAS).open(
            "a", encoding="utf-8", errors="backslashreplace"
        ) as arquivo:
            for recusa in recusas:
                arquivo.write(
                    json.dumps(
                        {
                            "task_id": task_id,
                            "comando_hash": digest,
                            "comando": command[:200],
                            "aceitos": alvos,
                            **(emissor or {}),
                            **recusa,
                            "created_at": agora,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
    except OSError:
        pass


#: Comandos que mudam o diretorio do shell. No PowerShell, `cd`, `chdir` e `sl`
#: sao aliases de `Set-Location`, `pushd` de `Push-Location` e `popd` de
#: `Pop-Location`. Comparados sem caixa, porque o PowerShell nao distingue.
_MUDA_DIRETORIO = frozenset({
    "cd", "chdir", "pushd", "popd", "sl", "set-location", "push-location", "pop-location",
})

#: As mesmas palavras, procuradas no TEXTO inteiro da linha, aspas e corpo de
#: heredoc incluidos. Token nao basta: `bash -c "cd ../repo && x"` e um token
#: so, e `os.chdir("../repo")` so existe no corpo do programa. Hifen conta como
#: parte da palavra para que `wt-cd-x` num caminho nao dispare.
_PALAVRA_DE_DIRETORIO = re.compile(
    r"(?<![\w-])(?:" + "|".join(sorted(_MUDA_DIRETORIO, key=len, reverse=True)) + r")(?![\w-])"
)

#: `/c/...` e como o Git Bash escreve `C:\...`.
_DRIVE_MSYS = re.compile(r"^/([A-Za-z])(?=/|$)")

#: Variavel do cmd (`%TEMP%`). A do bash e a do PowerShell comecam por `$`.
_VARIAVEL_CMD = re.compile(r"%[A-Za-z_][A-Za-z0-9_]*%")

#: O motivo registrado quando um comando nao conta por rodar fora do checkout.
MOTIVO_FORA = "roda-fora-da-raiz"


def _resolver_caminho(token: str, base: str) -> str | None:
    """O caminho absoluto que `token` nomeia a partir de `base`, ou None.

    None e a resposta para o que so o shell saberia resolver: variavel, `~`,
    `-` (diretorio anterior), e, no Windows, caminho enraizado sem drive que
    nao seja a forma MSYS. `/tmp` no Git Bash e um ponto de montagem, e junta-lo
    ao drive do `cwd` inventaria um lugar.

    A forma MSYS e convertida, e nao recusada, porque recusar a deixaria cair em
    `C:\\c\\...`: um lugar que nao existe e que e disjunto de tudo — o `cd` para
    DENTRO do checkout passaria por fora. Os transcripts da `44b0dfb5` tem 41
    comandos nessa forma.
    """
    texto = token.strip()
    if not texto or texto[0] in "~-" or "$" in texto or "`" in texto:
        return None
    if _VARIAVEL_CMD.search(texto):
        return None
    if os.name == "nt":
        msys = _DRIVE_MSYS.match(texto)
        if msys:
            texto = f"{msys.group(1)}:{texto[msys.end():] or '/'}"
        elif texto[0] in "/\\" and not texto.startswith(("//", "\\\\")):
            return None
    try:
        return os.path.normpath(texto if os.path.isabs(texto) else os.path.join(base, texto))
    except (TypeError, ValueError):
        return None


def _prefixo_de_diretorio(command: str, cwd: str) -> tuple[str | None, list[str]]:
    """(onde o resto da linha roda, tokens do resto). Ver `diretorio_declarado`."""
    tokens = _tokenize(sem_corpo_de_heredoc(command))
    if not cwd:
        return None, tokens
    atual: str | None = cwd
    indice = 0
    saltos = 0
    while indice < len(tokens) and tokens[indice].casefold() in _MUDA_DIRETORIO:
        separador = tokens[indice + 2:indice + 4]
        if separador[:1] == [";"]:
            proximo = indice + 3
        elif separador == ["&", "&"]:
            proximo = indice + 4
        else:
            return None, tokens
        atual = _resolver_caminho(tokens[indice + 1], atual)
        if atual is None:
            return None, tokens
        indice = proximo
        saltos += 1
    resto = tokens[indice:]
    # Qualquer troca de diretorio alem das iniciais — `cd -`, `popd`, um shell
    # aninhado, um `os.chdir` no programa — pode levar o resto de volta ao
    # checkout. Nao da para seguir, entao nao se sabe.
    if len(_PALAVRA_DE_DIRETORIO.findall(command.casefold())) != saltos:
        return None, resto
    return atual, resto


def diretorio_declarado(command: str, cwd: str) -> str | None:
    """Onde o resto da linha roda: o `cwd`, ou o destino dos `cd` com que ela comeca.

    `payload.cwd` e o diretorio PERSISTENTE da sessao. O Bash desta maquina
    volta a ele depois de toda chamada, e o subagente herda o do pai — entao o
    agente declara onde roda na propria linha, `cd "<dir>" && ...`, e quem
    quiser saber onde o comando rodou tem de ler o comando.

    So a forma que da para ler sem executar: `cd <um argumento>` seguido de `&&`
    ou `;`, repetivel. Qualquer outra coisa e None — "nao se sabe" —, e o
    chamador cai no comportamento de antes:

    - `cd` no meio da linha, porque o que vem antes dele rodou em outro lugar;
    - `||`, `|` ou `&` depois do `cd`, porque o resto pode rodar sem ele;
    - flag (`cd -P`), variavel, `~`, `-`, nova linha colada no argumento.
    """
    return _prefixo_de_diretorio(command, cwd)[0]


def _arvore_de_trabalho(cwd: str) -> str | None:
    """O `.git` mais proximo acima do `cwd`, SEM colapsar worktree no dono.

    `find_repo_root` colapsa, e para a atribuicao de escrita isso esta certo.
    Mas numa sessao aberta num worktree FORA do diretorio do dono, o colapso
    diria que o proprio `cwd` da sessao esta fora da raiz — e todo comando
    dela deixaria de contar. `roda_fora_da_raiz` exige distancia das duas.
    """
    try:
        atual = os.path.abspath(cwd)
    except (OSError, ValueError):
        return None
    while True:
        if os.path.exists(os.path.join(atual, ".git")):
            return atual
        pai = os.path.dirname(atual)
        if pai == atual:
            return None
        atual = pai


def _normalizar_texto(texto: str) -> str:
    """Caixa, barra e forma MSYS iguais, para procurar um caminho num texto."""
    texto = re.sub(r"\\+", "/", texto.casefold())
    return re.sub(r"(^|[\s\"'=(])/([a-z])(?=/)", r"\1\2:", texto)


def _linha_alcanca(command: str, resto: list[str], diretorio: str, raizes: list[str]) -> bool:
    """A linha pode tocar alguma das raizes? Na duvida, sim.

    Tres portas, e qualquer uma basta:

    - o texto inteiro, corpo de heredoc incluido, cita o caminho de uma raiz —
      o corpo e o programa, e `open(r"C:\\repo\\x.py", "w")` so aparece ali;
    - um token, resolvido contra `diretorio`, cai dentro de uma raiz — pega
      `cp a ../../repo/b`, e o valor depois do `=` tanto de flag
      (`--saida=../repo/x`) quanto de atribuicao (`SAIDA=../repo/x python y`);
    - um token tem variavel, `~` ou outra coisa que so o shell resolveria.

    Token que nao pode ser caminho nenhum (`no:cacheprovider`, `HEAD:docs/x`)
    nao nomeia lugar e e pulado. Destino nulo (`/dev/null`) tambem.
    """
    texto = _normalizar_texto(command)
    formas = {
        _normalizar_texto(forma)
        for raiz in raizes
        for forma in (raiz, os.path.realpath(raiz))
    }
    if any(forma in texto for forma in formas):
        return True
    for token in resto:
        if token in _OPERADORES_TOKEN or token in _DESTINOS_NULOS:
            continue
        # O token inteiro nao e caminho quando e flag; o valor depois do `=`
        # pode ser, seja de flag seja de atribuicao — e o `..` de
        # `SAIDA=../repo/x` so e `..` depois de separado do nome.
        valores = [] if token.startswith("-") else [token]
        if "=" in token:
            valores.append(token.split("=", 1)[1])
        for valor in filter(None, valores):
            motivo = nao_pode_ser_caminho(valor)
            if motivo == "variavel-nao-expandida":
                return True
            if motivo:
                continue
            destino = _resolver_caminho(valor, diretorio)
            if destino is None or any(inside_root(destino, raiz) for raiz in raizes):
                return True
    return False


def roda_fora_da_raiz(payload: dict[str, Any], command: str) -> str | None:
    """O diretorio onde o comando roda, SE ele comprovadamente roda fora.

    E a pergunta que o placeholder fazia implicitamente e respondia sempre
    igual: "o programa opaco rodou dentro do checkout?". Ele supunha que sim,
    porque lia o local no `cwd` do payload. Na sessao `44b0dfb5` isso custou
    394 toques na task do pai — 330 emitidos por subagente —, e 10 das 14
    invalidacoes atribuiveis vieram de linhas `cd "<fora do checkout>" && ...`,
    9 delas para `%TEMP%\\...\\wt-*`.

    Devolve o diretorio so quando as tres coisas valem; em qualquer duvida,
    None, e o chamador conta como antes:

    1. o local e conhecido (`diretorio_declarado`);
    2. o local e disjunto do checkout: nem dentro nem ANCESTRAL da raiz do
       projeto nem da arvore da sessao. Ancestral nao e fora — de `projects/`
       qualquer `master-harness/x` relativo chega dentro;
    3. nada na linha alcanca o checkout (`_linha_alcanca`).

    **Identidade nao entra.** Subagente e principal passam pela mesma regua: o
    subagente trabalha na mesma sessao e no mesmo diretorio, e ignora-lo por
    `agent_id` deixaria a edicao delegada depois da evidencia passar por fresca.

    **O que continua descoberto, declarado:** programa lancado de fora que
    escreve no checkout por um caminho que nao esta na linha — lido de
    arquivo-ponteiro, de variavel de ambiente, montado por dentro. So medir a
    arvore fecha isso (`portao-mede-atividade-verification.md` §3).

    Fail-closed como `_apenas_dentro_da_raiz`: sem `cwd`, ou fora de repositorio,
    nao ha checkout e portanto nao ha fora.
    """
    cwd = str(payload.get("cwd") or "")
    if not cwd:
        return None
    raizes = [raiz for raiz in dict.fromkeys((find_repo_root(cwd), _arvore_de_trabalho(cwd))) if raiz]
    if not raizes:
        return None
    diretorio, resto = _prefixo_de_diretorio(command, cwd)
    if diretorio is None:
        return None
    for raiz in raizes:
        if inside_root(diretorio, raiz) or inside_root(raiz, diretorio):
            return None
    if _linha_alcanca(command, resto, diretorio, raizes):
        return None
    return diretorio


def _apenas_dentro_da_raiz(
    payload: dict[str, Any],
    alvos: list[str],
    recusas: list[dict[str, Any]],
    base: str | None = None,
) -> list[str]:
    """A MESMA pergunta que o caminho do `Edit`/`Write` ja fazia.

    `b771b6b` ensinou `counts_as_modified_file` a distinguir *escreveu algo* de
    *mudou o codigo sob teste*, e ligou isso em `harness-reclassify.sh:135`. O
    caminho do shell chamava `touch_files` sem passar por ele. Mesmo arquivo,
    mesmo lugar, dois veredictos — medido ao vivo no mapa §5:

        Write -> counts_as_modified_file(tool, path, raiz)  ->  False
        echo ... > <mesmo caminho>                          ->  True

    O conserto de `b771b6b` chegou a um caminho e nao ao outro.

    **Fail-closed, e a decisao NAO esta sendo reaberta:** sem `cwd` no payload
    nao ha projeto declarado, e sem projeto nao ha dentro nem fora. Cair em
    `os.getcwd()` inventaria a fronteira a partir de onde o hook por acaso roda,
    e foi o que derrubou 7 testes de `TestReclassify`. Sem `cwd`, conta tudo.

    O alvo relativo e resolvido contra o `cwd` do payload, nao contra o do
    processo do hook: `cat > scripts/x.py` e relativo a sessao, e `abspath`
    sozinho o ancoraria no lugar errado. O caminho GRAVADO continua sendo o
    original — quem le `files` continua vendo `scripts/x.py`.

    `base`, quando conhecida, e o diretorio que a propria linha declara
    (`diretorio_declarado`) e vence o `cwd`. Sem ela, `cd "%TEMP%\\...\\l4" &&
    cat > sim_334.py` gravava `sim_334.py` como arquivo do checkout — 20 toques
    assim na sessao `44b0dfb5`, todos de arquivo escrito em `%TEMP%`.
    """
    cwd = str(payload.get("cwd") or "")
    raiz = find_repo_root(cwd) if cwd else None
    if not raiz:
        return alvos
    ancora = base or cwd
    dentro: list[str] = []
    for alvo in alvos:
        absoluto = alvo if os.path.isabs(alvo) else os.path.join(ancora, alvo)
        if inside_root(absoluto, raiz):
            dentro.append(alvo)
        else:
            recusas.append({"candidato": alvo, "motivo": "fora-da-raiz"})
    return dentro


def _handle_post_tool(payload: dict[str, Any], context) -> str:
    bucket, database, projection, task = context
    tool_name = str(payload.get("tool_name") or payload.get("toolName") or "").casefold()
    command = _command(payload)
    if tool_name in SHELL_TOOLS and not is_state_management(command):
        # Atribuir o caminho real quando da; manter o placeholder quando nao da.
        # As duas metades importam: sem a primeira o contador de arquivos e cego
        # a escrita por shell; sem a segunda, um programa que escreve por dentro
        # passaria por "nao alterou nada".
        #
        # A terceira metade: o placeholder supoe que o programa opaco rodou no
        # checkout. Quando a linha declara que roda FORA e nada nela alcanca o
        # checkout, a suposicao e falsa e nao ha o que invalidar — e a recusa
        # fica escrita, com local e emissor (ver `roda_fora_da_raiz`).
        recusas: list[dict[str, Any]] = []
        cwd = str(payload.get("cwd") or "")
        base = diretorio_declarado(command, cwd) if cwd else None
        alvos = _apenas_dentro_da_raiz(
            payload, shell_write_targets(command, recusas), recusas, base=base
        )
        conta = bool(alvos) or not (is_read_only(command) or nao_muda_a_arvore(command))
        # So pergunta onde o comando rodou quando a resposta muda o veredito:
        # leitura pura ja nao conta, e registrar recusa para ela seria ruido.
        fora = roda_fora_da_raiz(payload, command) if conta and not alvos else None
        if fora:
            recusas.append({"candidato": "shell-command", "motivo": MOTIVO_FORA, "detalhe": fora})
            conta = False
        _registrar_recusas(bucket, task["task_id"], command, alvos, recusas, _emissor(payload))
        if conta:
            task = database.touch_files(
                task["task_id"],
                alvos or ["shell-command"],
                origem="shell" if alvos else "shell-placeholder",
            )
    aviso = ""
    job = _job_em_segundo_plano(payload) if is_trusted_verification(command) else None
    if job:
        # Depois do toque acima: a revisao gravada e a que a suite vai testar.
        # O termino e capturado no Stop ou no `complete`, lendo o transcript.
        database.registrar_lancamento(
            task["task_id"],
            job_id=job,
            tool_use_id=payload.get("tool_use_id") or payload.get("toolUseId"),
            command=command,
            transcript_path=payload.get("transcript_path") or payload.get("transcriptPath"),
        )
        aviso = AVISO_SEGUNDO_PLANO.format(
            job=job,
            revisao=task["code_revision"],
            comando=comando_de_evidencia(bucket, task["task_id"], task.get("kind")),
        )
    elif is_trusted_verification(command):
        collected, passed, skipped, output_hash = _test_counts(payload)
        task = database.record_evidence(
            task["task_id"],
            evidence_type="test",
            command=command,
            exit_code=_exit_code(payload),
            tests_collected=collected,
            tests_passed=passed,
            tests_skipped=skipped,
            output_hash=output_hash,
        )
        if collected is None:
            aviso = AVISO_SEM_CASOS
    elif looks_like_verification(command):
        aviso = AVISO_COMPOSICAO.format(sugestao=atomic_prefix(command) or command)
    _sync_projection(bucket, projection, task)
    return aviso


def _job_em_segundo_plano(payload: dict[str, Any]) -> str | None:
    """O id do job, se esta resposta e o LANCAMENTO de um comando em segundo plano.

    O `tool_response` do hook e o mesmo objeto que o transcript grava em
    `toolUseResult` (sha256 igual, medido no diagnostico), com
    `backgroundTaskId` tanto no pedido quanto no empurrado por timeout. O texto
    fica como segunda via para resposta que chegue so como string.
    """
    resposta = _response(payload)
    if isinstance(resposta, dict) and isinstance(resposta.get("backgroundTaskId"), str):
        return resposta["backgroundTaskId"]
    anuncio = _ANUNCIO_DE_SEGUNDO_PLANO.search(_response_text(payload))
    if anuncio:
        return anuncio.group(1) or anuncio.group(2)
    return None


def _jobs_em_voo(payload: dict[str, Any], task: dict[str, Any]) -> list[dict[str, str]]:
    """Jobs desta task ainda rodando, lidos do transcript do Stop.

    Qualquer falha devolve lista vazia, que e cobrar como antes. Deixar a
    excecao subir derrubaria o hook, e o host trata hook que morre como erro
    nao bloqueante: o Stop passaria sem portao nenhum.
    """
    try:
        return jobs_em_voo(
            payload.get("transcript_path") or payload.get("transcriptPath"),
            desde=task.get("started_at"),
        )
    except Exception:
        return []


def _handle_stop(payload: dict[str, Any], context) -> str:
    if payload.get("stop_hook_active") or payload.get("stopHookActive"):
        return ""
    bucket, database, projection, task = context
    # Suite em segundo plano que ja terminou vira evidencia ANTES do teste de
    # `verified`: capturar depois dele deixaria o Stop que disparou a captura
    # bloquear uma task que acabou de ser verificada. Nunca levanta.
    if capturar_lancamentos(database, task["task_id"]):
        task = database.task(task["task_id"])
        _sync_projection(bucket, projection, task)
    if task["status"] != "active" or not task["pipeline"] or task["verified"]:
        return ""
    # Docs so e cobrado na fase que produz a verificacao (D1); teste, da primeira
    # fase de implementacao em diante, ou se a task ja passou por uma. A regra mora em
    # `transactional_state`, e `register_stop_continuation` le a mesma: hook e
    # banco nao podem discordar sobre quando ha continuacao a contar.
    if not cobra_evidencia_nesta_fase(
        task.get("kind"), task["pipeline"], task["phase"],
        passou_pela_implementacao=bool(task.get("passou_pela_implementacao")),
    ):
        return ""
    # Com job desta task em voo o fim de turno e espera, nao tentativa de
    # encerrar: o Stop bloqueia igual, mas nao conta para a escalada. Quem
    # decide contar e o banco; o hook so entrega a lista.
    em_voo = _jobs_em_voo(payload, task)
    task = database.register_stop_continuation(
        task["task_id"], limit=2, em_voo=[job["id"] for job in em_voo]
    )
    _sync_projection(bucket, projection, task)
    reason = _motivo_do_gate(bucket, database, task, em_voo)
    return json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False)


def _conta_evidencia(database, task_id: str, code_revision: int, tipo: str = "test") -> str:
    """Quantas linhas do tipo exigido esta task tem, e quantas na revisao atual.

    Conta so o tipo que o portao julga. Numa task de docs, "3 linhas de
    evidence" que fossem tres pytest diriam que ha prova quando nao ha.
    """
    try:
        with sqlite3.connect(database.path) as raw:
            qualquer = raw.execute(
                "SELECT COUNT(*) FROM evidence WHERE task_id = ?", (task_id,)
            ).fetchone()[0]
            total = raw.execute(
                "SELECT COUNT(*) FROM evidence WHERE task_id = ? AND evidence_type = ?",
                (task_id, tipo),
            ).fetchone()[0]
            desta = raw.execute(
                "SELECT COUNT(*) FROM evidence WHERE task_id = ? AND evidence_type = ? "
                "AND code_revision = ?",
                (task_id, tipo, code_revision),
            ).fetchone()[0]
    except sqlite3.Error:
        return "nao foi possivel ler a tabela `evidence`"
    if not qualquer:
        return "a tabela `evidence` desta task esta VAZIA (0 linhas)"
    if not total:
        return f"nenhuma linha de evidence do tipo `{tipo}` ({qualquer} de outro tipo, que nao conta)"
    return f"{total} linha(s) de evidence nesta task, {desta} na code_revision atual"


def _ultimos_toques(database, task_id: str, quantos: int = 3) -> str:
    """As ultimas invalidacoes, com caminho e origem.

    Sem isto a mensagem dizia `code_revision=24` e parava ali. Quem a lia sabia
    que a evidencia tinha expirado e nao sabia POR QUE — e a saida mais barata
    era inventar um diagnostico. A tabela `touches` existe justamente para
    responder isso (ver `transactional_state.touch_files`); esta funcao e o
    consumidor dela na unica tela onde a resposta e util.
    """
    try:
        linhas = database.touches(task_id, limite=quantos)
    except Exception:
        return ""
    if not linhas:
        return ""
    itens = " ; ".join(
        f"rev={linha['code_revision']} {linha['path']} ({linha['origem']})" for linha in linhas
    )
    return f"Ultima(s) invalidacao(oes): {itens}"


def _lancamentos(database, task_id: str) -> str:
    """As suites em segundo plano desta task e o destino de cada uma.

    Sem isto, `rejeitado` e `historico` seriam silencio: o modelo nao saberia
    por que a suite que ele esperou nao verificou, e inventaria um diagnostico.
    """
    try:
        return resumo_dos_lancamentos(database.lancamentos(task_id))
    except Exception:
        return ""


def comando_de_evidencia(bucket: Path, task_id: str, kind: str | None = None) -> str:
    """A linha que o portao manda copiar para registrar evidencia a mao.

    Ela precisa satisfazer `is_state_management`, senao o PostToolUse que vem
    logo atras dela sobe `code_revision` e a evidencia que o CLI acabou de
    gravar nasce obsoleta. Nao e corrida: `state_cli evidence` grava em
    `row["code_revision"]` (`transactional_state.py:1035`) e o hook so roda
    DEPOIS do comando, entao a ordem e sempre grava-em-N, sobe-para-N+1.

    Ate 2026-09-18 esta mensagem imprimia

        PR="$(cat "${HARNESS_DIR:-...}/plugin-root")"; python "$PR/..." ...

    e o `;` fora de aspas a tirava da isencao. `223c53f` consertou exatamente
    esta forma nos `SKILL.md` e nao chegou aqui — a receita do hook e uma
    receita tambem, e ninguem a media. Medido na sessao-mae em 2026-09-17:
    evidencia gravada na `code_revision` 375, portao lendo 376 no turno
    seguinte, com a 376 listada como `shell-placeholder`.

    O caminho do CLI sai de `SCRIPTS`, que e derivado do `__file__` deste
    arquivo: e o CLI que acompanha o hook que esta rodando. O marcador
    `plugin-root` responde outra pergunta e ja apontou para versao antiga
    (`test_arsenal.py:853`).

    `as_posix()` de proposito: barra invertida dentro de aspas duplas e escape
    para `_scan_composition` (`:69`), e um caminho do Windows cru faria a
    varredura comer separador. Barra normal funciona nos dois shells.

    `kind` escolhe o tipo de evidencia (`transactional_state.tipo_de_evidencia`).
    Em docs, `--command-text` e o caminho do relatorio de verificacao: o CLI
    recusa se ele nao existir e grava o hash dele (D3). A receita sai sempre com
    marcadores — `<relatorio>`, `<N>` —, nunca com valor: se ela ligasse
    `verified` rodada como sai, copiar a mensagem destravaria o portao sem
    verificar nada.
    """
    tipo = tipo_de_evidencia(kind)
    texto = "<relatorio>" if tipo == "docs" else "python -m pytest -q"
    return (
        f'python "{CLI_DE_ESTADO}" --home "{bucket}" evidence '
        f'--task {task_id} --type {tipo} --command-text "{texto}" '
        "--exit-code 0 --tests-collected <N> --tests-passed <P> --tests-skipped <S>"
    )


def _motivo_do_gate(
    bucket: Path, database, task: dict[str, Any], em_voo: list[dict[str, str]] | tuple = ()
) -> str:
    """A mensagem do bloqueio, dizendo o que o portao LEU.

    Ate 2026-09-16 ela nao citava `task_id`, nem o balde, nem o comando que
    registraria evidencia. Quem a recebia nao tinha como confirmar nada, e a
    saida mais barata era inventar um diagnostico — duas sessoes diferentes
    concluiram "a task e fantasma" sobre uma task que existia e que de fato
    nunca tinha recebido evidencia. Um portao que nao mostra a leitura obriga
    quem le a adivinhar a leitura.

    O texto segue o tipo de evidencia que a task exige. Ate 2026-09-25 ele so
    sabia pedir teste, e mandou uma task de docs, numa pasta sem suite, anexar
    "evidencia de teste fresca" tres vezes — a sessao acabou escrevendo 127
    testes sobre o proprio texto.

    Com job em voo (`em_voo`), a mensagem abre dizendo que este Stop NAO foi
    cobrado e por qual job. E toda leitura, escalada incluida, traz
    `stops_nao_cobrados=N` quando houver algum: a suspensao nao tem teto
    numerico (D3), entao tem de estar a vista de quem le o bloqueio.
    """
    tipo = tipo_de_evidencia(task.get("kind"))
    contagem = _conta_evidencia(database, task["task_id"], task["code_revision"], tipo)
    pipeline = task["pipeline"] or []
    fase = task["phase"]
    posicao = (
        f"{pipeline.index(fase) + 1} de {len(pipeline)}"
        if fase in pipeline else f"? de {len(pipeline)}"
    )
    leitura = (
        f"task_id={task['task_id']} | balde={bucket} | fase={fase} ({posicao}) | "
        f"code_revision={task['code_revision']} | verified={task['verified']} | {contagem}"
    )
    nao_cobrados = database.stops_nao_cobrados(task["task_id"])
    if nao_cobrados:
        leitura = f"{leitura} | stops_nao_cobrados={nao_cobrados}"
    toques = _ultimos_toques(database, task["task_id"])
    if toques:
        leitura = f"{leitura}\n{toques}"
    lancamentos = _lancamentos(database, task["task_id"])
    if lancamentos:
        leitura = f"{leitura}\n{lancamentos}"
    # `--home` e do parser RAIZ: vai antes do subcomando, nao depois. Escrever
    # na ordem errada aqui entregaria um comando que nao roda, que e a mesma
    # falha que esta mensagem existe para corrigir — instrucao que nao se
    # consegue seguir vale tanto quanto instrucao nenhuma. Pela mesma razao ele
    # tem de ser ATOMICO: ver `comando_de_evidencia`.
    comando = comando_de_evidencia(bucket, task["task_id"], task.get("kind"))
    regua = (
        "A regua: exit 0, tests_passed > 0, e tests_passed + tests_skipped == "
        "tests_collected. Teste pulado NAO reprova; teste que falhou, sim."
    )
    if task["pending_gate"] == "escalation":
        return (
            "HARNESS v3 escalation gate: a verificacao segue incompleta depois de duas "
            f"continuacoes.\nO que o portao leu: {leitura}\n"
            "Leve ao usuario o bloqueio concreto e esta leitura — nao reformule o "
            "diagnostico sem antes conferir os numeros acima."
        )
    cabecalho = ""
    if em_voo:
        jobs = ", ".join(f"{job['id']} ({job['tipo']})" for job in em_voo)
        cabecalho = (
            f"HARNESS v3: este Stop NAO foi cobrado — job(s) desta task em voo: {jobs}. "
            "O host reinvoca o modelo quando terminarem; se voce so esta esperando, "
            "encerre o turno. A evidencia continua exigida antes da resposta final.\n"
        )
    if tipo == "docs":
        return cabecalho + (
            "HARNESS v3 verification gate (docs): esta e a fase final do pipeline de "
            "docs; registre a verificacao da doc antes da resposta final.\n"
            f"O que o portao leu: {leitura}\n"
            "A regua de docs (skills/documentation, secao Verificacao): "
            "N = afirmacoes da tabela de fontes conferidas, P = confirmadas na doc, "
            "S = descartadas com motivo escrito no relatorio; P > 0 e P + S == N. "
            "Exit 0 so se todo exemplo roda, nenhum [NEEDS CLARIFICATION] ficou e "
            "nenhum caminho citado sumiu. <relatorio> e o arquivo da verificacao: "
            "o CLI recusa se ele nao existir e grava o hash dele. Escreva o "
            "relatorio ANTES; qualquer escrita depois expira a verificacao.\n"
            f"{comando}\n"
            f"{AVISO_LINHA_SOZINHA}"
        )
    return cabecalho + (
        "HARNESS v3 verification gate: continue o pipeline do harness-workflow e anexe "
        f"evidencia de teste fresca antes da resposta final.\n"
        f"O que o portao leu: {leitura}\n{regua}\n"
        f"Rodar a suite em primeiro plano ja grava sozinho. Para registrar a mao:\n{comando}\n"
        f"{AVISO_LINHA_SOZINHA}"
    )


def handle_payload(
    payload: dict[str, Any], *, harness_root: str | Path | None = None, event: str | None = None
) -> str:
    name = _event_name(payload, event)
    _write_heartbeat(payload, name, harness_root)
    if name == "Stop" and (payload.get("stop_hook_active") or payload.get("stopHookActive")):
        return ""
    context = _database_for_payload(payload, harness_root)
    if context is None:
        return ""
    if name in {"PostToolUse", "PostToolUseFailure"}:
        return _handle_post_tool(payload, context)
    if name == "Stop":
        return _handle_stop(payload, context)
    return ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event")
    args = parser.parse_args()
    try:
        payload = json.load(sys.stdin)
    except (OSError, ValueError):
        payload = {}
    output = handle_payload(payload, event=args.event)
    if not output:
        return 0
    if _event_name(payload, args.event) == "Stop":
        # O gate do Stop e `decision: block`, o unico canal que interrompe de
        # verdade. Ele sai cru: passar por `emit.py` o silenciaria, porque la o
        # Stop e `silent` por projeto.
        print(output)
        return 0
    _emitir(payload, args.event, output)
    return 0


def _emitir(payload: dict[str, Any], event: str | None, texto: str) -> None:
    """Avisos do PostToolUse pelo emissor unico, e nao por `print` solto.

    O aviso de composicao de shell existe para dizer "a evidencia deste teste
    NAO foi gravada". Ele saia por `print`, entao nao passava pelo extrato de
    `emissions.jsonl` e `check_hook_liveness.py --delivery` nao tinha como
    medir se chegava. Em 2026-09-03 uma suite verde de 551s foi descartada por
    um `| tail -12` e o aviso nao apareceu — invisivel para quem devia ler e
    invisivel para a auditoria, que e a combinacao que originou esta auditoria
    toda.
    """
    nome = _event_name(payload, event)
    try:
        import importlib.util

        caminho = os.path.join(os.path.dirname(os.path.abspath(__file__)), "emit.py")
        spec = importlib.util.spec_from_file_location("harness_emit", caminho)
        if spec is None or spec.loader is None:
            raise ImportError
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.Emitter(
            nome,
            hook="transactional",
            session_id=payload.get("session_id"),
            cwd=payload.get("cwd"),
        ).add("evidence_warning", texto).flush()
    except Exception:
        print(texto)


if __name__ == "__main__":
    raise SystemExit(main())
