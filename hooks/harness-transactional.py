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

from harness_paths import ensure_state_dir, find_repo_root  # type: ignore[import-not-found]
from post_tool_policy import inside_root  # type: ignore[import-not-found]
from projecao import gravar_json_atomico  # type: ignore[import-not-found]
from transactional_state import HarnessDatabase, StateTransitionError  # type: ignore[import-not-found]

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


def _command(payload: dict[str, Any]) -> str:
    value = _tool_input(payload)
    return str(value.get("command") or value.get("cmd") or value.get("script") or "")


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
    """
    quote = None
    escaped = False
    for index, character in enumerate(command):
        if escaped:
            escaped = False
            continue
        if quote:
            if character == chr(92) and quote == '"':
                escaped = True
            elif character == quote:
                quote = None
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
    return -1, quote is not None


def _has_unquoted_shell_composition(command: str) -> bool:
    indice, aspa_aberta = _scan_composition(command)
    return indice >= 0 or aspa_aberta


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

#: Background nao tem composicao nenhuma, entao passa por `is_trusted_verification`
#: — mas o PostToolUse chega antes de existir saida, e evidencia sem caso
#: coletado nao verifica (contrato Harness4Contract v1). O silencio aqui custou
#: dois runs de ~7 min repetidos em 2026-09-02.
AVISO_SEM_CASOS = (
    "[harness] evidencia de teste gravada SEM casos coletados, entao nao "
    "verifica. Causa provavel: o comando rodou em background e a saida ainda "
    "nao existia. Rode em primeiro plano para que conte."
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


_OPERADORES_TOKEN = frozenset({'>', '>>', ';', '|', '&'})


#: Binarios cujo unico efeito e ler. Cada nome aqui e a afirmacao "este comando
#: nao escreve", e uma afirmacao errada apaga alteracao de codigo do contador —
#: por isso a lista e curta e nao inclui interpretador (`python`, `awk`) nem
#: comando que muda de efeito pelo argumento (`find -delete`, `git config`).
_SOMENTE_LEITURA = frozenset({
    'cat', 'head', 'tail', 'wc', 'grep', 'rg', 'ls', 'pwd', 'echo', 'printf',
    'sort', 'uniq', 'cut', 'nl', 'basename', 'dirname', 'stat', 'diff', 'cmp',
    'date', 'sed', 'true', 'false',
})

#: Subcomandos de git que so leem. `branch`, `remote` e `config` ficam de fora:
#: os tres escrevem dependendo da flag.
_GIT_SOMENTE_LEITURA = frozenset({
    'status', 'log', 'diff', 'show', 'ls-files', 'rev-parse', 'blame',
    'shortlog', 'describe', 'cat-file', 'grep',
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


def nao_muda_a_arvore(command: str) -> bool:
    """O comando escreve, mas nao no codigo que a suite mede."""
    if not command or shell_write_targets(command):
        return False
    segmentos = _segmentos(command)
    if not segmentos:
        return False
    for partes in segmentos:
        if _binario(partes[0]) != 'git':
            return False
        resto = [p for p in partes[1:] if not p.startswith('-')]
        if not resto or resto[0] not in (_GIT_NAO_MUDA_ARVORE | _GIT_SOMENTE_LEITURA):
            return False
    return True


def _binario(token: str) -> str:
    nome = token.replace(chr(92), '/').rsplit('/', 1)[-1]
    return nome[:-4] if nome.endswith('.exe') else nome


def _segmentos(command: str) -> list[list[str]]:
    """Os comandos da linha, um por segmento.

    A duplicacao de descritor e descartada em vez de partir o segmento. Sem
    isto, `git status 2>&1` virava `[['git','status','2'], ['1']]`, o segundo
    segmento comecava por `1`, e `is_read_only` respondia False para um
    comando que so le — a mesma causa de `_duplicacao_de_fd`, por outra porta.
    """
    tokens = _tokenize(command)
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


def is_read_only(command: str) -> bool:
    """Sei que este comando nao escreve — nao apenas "nao consegui ver escrita".

    `shell_write_targets` distingue "escreve em X" de "nao da para saber", e o
    chamador guarda o placeholder no segundo caso. Isso e o certo para um
    programa arbitrario, mas `grep`, `cat` e `git log` nao sao caso duvidoso:
    inspecionar o repositorio subia `code_revision` e invalidava a evidencia da
    suite, o que obrigava a rodar a suite de novo para conseguir fechar.

    A porta e estreita: todo segmento da linha tem de comecar por um binario da
    lista, e qualquer redirecionamento ja tira o comando daqui pelo chamador.
    """
    if not command:
        return False
    if shell_write_targets(command):
        return False
    segmentos = _segmentos(command)
    if not segmentos:
        return False
    for partes in segmentos:
        binario = _binario(partes[0])
        if binario == 'git':
            resto = [p for p in partes[1:] if not p.startswith('-')]
            if not resto or resto[0] not in _GIT_SOMENTE_LEITURA:
                return False
            continue
        if binario not in _SOMENTE_LEITURA:
            return False
        if binario == 'sed' and any(p.startswith('-i') for p in partes[1:]):
            return False
    return True


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
        if caractere == '<' and linha.startswith('<<', indice):
            if linha.startswith('<<<', indice):
                indice += 3
                continue
            cursor = indice + 2
            ignora_tab = False
            if cursor < tamanho and linha[cursor] == '-':
                ignora_tab = True
                cursor += 1
            while cursor < tamanho and linha[cursor] in ' \t':
                cursor += 1
            if cursor < tamanho and linha[cursor] in {chr(39), chr(34)}:
                fecha = linha[cursor]
                fim = linha.find(fecha, cursor + 1)
                if fim == -1:
                    break
                achados.append((linha[cursor + 1:fim], ignora_tab))
                indice = fim + 1
                continue
            fim = cursor
            while fim < tamanho and (linha[fim].isalnum() or linha[fim] in '_-.'):
                fim += 1
            if fim > cursor:
                achados.append((linha[cursor:fim], ignora_tab))
            indice = max(fim, cursor + 1)
            continue
        indice += 1
    return achados


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


def shell_write_targets(command: str, recusas: list[dict[str, Any]] | None = None) -> list[str]:
    """Arquivos que este comando de shell escreve, ate onde da para atribuir.

    Existe porque `_handle_post_tool` registrava todo comando como o caminho
    sintetico 'shell-command'. Com PRIMARY KEY(task_id, path) e INSERT OR
    IGNORE, mil comandos viravam UMA linha, e nenhuma nomeava um arquivo — o
    contador de arquivos so crescia por Edit/Write. Em 2026-09-03 uma task
    que alterou 2 arquivos por heredoc registrou `files=0` e virou L0, e
    `proxy_regex_vs_observado` e calculado sobre esse rotulo.

    Cobre redirecionamento, `tee` e `sed -i`. NAO cobre programa que escreve
    por dentro (`python - <<PY` com `write_text`), e nao ha como cobrir: e um
    programa. Por isso o chamador mantem o placeholder quando esta lista sai
    vazia — 'nao da para saber' e diferente de 'nao escreveu'.

    `recusas`, quando passada, recebe um dicionario por candidato REJEITADO.
    Um candidato rejeitado por engano e uma escrita real que some do contador —
    o erro na direcao perigosa. Sem o registro, "o ruido caiu" e "o guarda
    cegou" produzem exatamente o mesmo numero.
    """
    if not command:
        return []
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
        elif token == 'sed':
            fatia = []
            for seguinte in tokens[indice + 1:]:
                if seguinte in _OPERADORES_TOKEN:
                    break
                fatia.append(seguinte)
            if any(f.startswith('-i') for f in fatia) and fatia:
                considerar(fatia[-1])
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
    return "\n".join(values)


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


def _test_counts(payload: dict[str, Any]) -> tuple[int | None, int | None, int | None, str | None]:
    """(coletados, passando, pulados, digest) — coletados = tudo que o pytest contou."""
    text = _response_text(payload)
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


def _registrar_recusas(
    bucket: Path,
    task_id: str,
    command: str,
    alvos: list[str],
    recusas: list[dict[str, Any]],
) -> None:
    """Toda recusa do extrator fica escrita, com comando, candidato e motivo.

    O risco do conserto de R3 e na direcao perigosa: um candidato rejeitado por
    engano e uma escrita real que some do contador, e o contador nao denuncia a
    propria cegueira — "o ruido caiu" e "o guarda parou de ver" dao o mesmo
    numero. Este arquivo e o que separa os dois.

    O mapa `revisao-que-invalida` so conseguiu medir alguma coisa porque as
    entradas ACEITAS ficavam em `files`. As recusadas nunca ficaram em lugar
    nenhum, e a proxima pergunta seria irrespondivel pelo mesmo motivo.

    Degrada em silencio: falha de escrita aqui nunca pode derrubar o hook.
    """
    if not recusas:
        return
    digest = hashlib.sha256(command.encode("utf-8")).hexdigest()[:12]
    agora = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    try:
        with (bucket / ARQUIVO_DE_RECUSAS).open("a", encoding="utf-8") as arquivo:
            for recusa in recusas:
                arquivo.write(
                    json.dumps(
                        {
                            "task_id": task_id,
                            "comando_hash": digest,
                            "comando": command[:200],
                            "aceitos": alvos,
                            **recusa,
                            "created_at": agora,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
    except OSError:
        pass


def _apenas_dentro_da_raiz(
    payload: dict[str, Any], alvos: list[str], recusas: list[dict[str, Any]]
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
    """
    cwd = str(payload.get("cwd") or "")
    raiz = find_repo_root(cwd) if cwd else None
    if not raiz:
        return alvos
    dentro: list[str] = []
    for alvo in alvos:
        absoluto = alvo if os.path.isabs(alvo) else os.path.join(cwd, alvo)
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
        recusas: list[dict[str, Any]] = []
        alvos = _apenas_dentro_da_raiz(payload, shell_write_targets(command, recusas), recusas)
        _registrar_recusas(bucket, task["task_id"], command, alvos, recusas)
        if alvos or not (is_read_only(command) or nao_muda_a_arvore(command)):
            task = database.touch_files(
                task["task_id"],
                alvos or ["shell-command"],
                origem="shell" if alvos else "shell-placeholder",
            )
    aviso = ""
    if is_trusted_verification(command):
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


def _handle_stop(payload: dict[str, Any], context) -> str:
    if payload.get("stop_hook_active") or payload.get("stopHookActive"):
        return ""
    bucket, database, projection, task = context
    if task["status"] != "active" or not task["pipeline"] or task["verified"]:
        return ""
    task = database.register_stop_continuation(task["task_id"], limit=2)
    _sync_projection(bucket, projection, task)
    reason = _motivo_do_gate(bucket, database, task)
    return json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False)


def _conta_evidencia(database, task_id: str, code_revision: int) -> str:
    """Quantas linhas de evidencia esta task tem, e quantas na revisao atual."""
    try:
        with sqlite3.connect(database.path) as raw:
            total = raw.execute(
                "SELECT COUNT(*) FROM evidence WHERE task_id = ?", (task_id,)
            ).fetchone()[0]
            desta = raw.execute(
                "SELECT COUNT(*) FROM evidence WHERE task_id = ? AND code_revision = ?",
                (task_id, code_revision),
            ).fetchone()[0]
    except sqlite3.Error:
        return "nao foi possivel ler a tabela `evidence`"
    if not total:
        return "a tabela `evidence` desta task esta VAZIA (0 linhas)"
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


def comando_de_evidencia(bucket: Path, task_id: str) -> str:
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
    """
    return (
        f'python "{CLI_DE_ESTADO}" --home "{bucket}" evidence '
        f'--task {task_id} --type test --command-text "python -m pytest -q" '
        "--exit-code 0 --tests-collected <N> --tests-passed <P> --tests-skipped <S>"
    )


def _motivo_do_gate(bucket: Path, database, task: dict[str, Any]) -> str:
    """A mensagem do bloqueio, dizendo o que o portao LEU.

    Ate 2026-09-16 ela nao citava `task_id`, nem o balde, nem o comando que
    registraria evidencia. Quem a recebia nao tinha como confirmar nada, e a
    saida mais barata era inventar um diagnostico — duas sessoes diferentes
    concluiram "a task e fantasma" sobre uma task que existia e que de fato
    nunca tinha recebido evidencia. Um portao que nao mostra a leitura obriga
    quem le a adivinhar a leitura.
    """
    contagem = _conta_evidencia(database, task["task_id"], task["code_revision"])
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
    toques = _ultimos_toques(database, task["task_id"])
    if toques:
        leitura = f"{leitura}\n{toques}"
    # `--home` e do parser RAIZ: vai antes do subcomando, nao depois. Escrever
    # na ordem errada aqui entregaria um comando que nao roda, que e a mesma
    # falha que esta mensagem existe para corrigir — instrucao que nao se
    # consegue seguir vale tanto quanto instrucao nenhuma. Pela mesma razao ele
    # tem de ser ATOMICO: ver `comando_de_evidencia`.
    comando = comando_de_evidencia(bucket, task["task_id"])
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
    return (
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
