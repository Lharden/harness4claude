# DERIVADO - nao edite este arquivo.
#
# A fonte e `master-harness/mh/sonda_mcp.py`, e ela e a unica
# dona do handshake da sonda MCP (initialize e tools/list, prazo, morte da
# arvore, tres estados). O repositorio do host `claude` nao importa o
# master-harness: o leitor do registro do host mora na sonda dele, nunca aqui.
#
# Para mudar o que esta aqui: edite a fonte e rode `mh identidade semear`.
# Para saber se derivou: `mh identidade check` (sai 1 se divergir).
#
# ---- fim do cabecalho vendorizado; daqui para baixo e copia exata ----
"""Núcleo da sonda de um servidor MCP por stdio: sobe o servidor registrado, faz `initialize` e `tools/list` com prazo, e
devolve um relato em três estados (D-136; desenho `docs/specs/d136-sonda-science-design.md` §3 e §7).

## Por que existe

A L-63 pede que a sonda de `integration.*` alcance o serviço real e reporte três estados sem `skip`. Até o D-136 a sonda
do Science provava só o roteamento: com o `shs.exe` quebrado, ela continuava verde. Este núcleo é o handshake que a
sonda de cada host passa a fazer contra o servidor que o host registrou.

## Uma fonte, duas cópias

O protocolo é o mesmo nos dois hosts; só o leitor do registro muda (`.claude.json` no harness4claude, `config.toml` no
harness4codex). Por isso o núcleo mora aqui e chega aos hosts como cópia derivada (`mh identidade semear --artefato
sonda_mcp`, registro em `mh/identidade.py`). A cópia não pode importar o `mh`: este arquivo é stdlib pura e roda no
Python 3.10 do harness4codex. Cada host lê o próprio registro e chama `sondar` (ou `relato_sem_servidor`, quando o
registro não declara um servidor alcançável) e `emitir`.

## Os três estados

| estado | saída | motivo (vocabulário fechado, `MOTIVOS`) |
|---|---|---|
| `passa` | 0 | `ok` |
| `reprova` | 1 | `executavel_ausente`, `nao_subiu`, `prazo`, `saiu_antes`, `resposta_invalida`, `ferramenta_ausente` |
| `nao_verificado` | 2 | `config_ausente`, `config_ilegivel`, `nao_registrado`, `desligado`, `transporte_url` |

Registrado e quebrado reprova (é o caso do D-136). Não registrado ou desligado é não verificado: a máquina não declarou o
serviço. O relato leva `duracao_s`, o `command` e os `args` do registro; o valor de `env` nunca sai, nem em erro (a
cauda do `stderr` vai com cada valor de `env` trocado por `***`).

## O processo

- Os três fluxos são sempre explícitos (WinError 6 sob a captura de fd do pytest). O `stdout` é lido linha a linha por
  uma thread; o `stderr` é drenado por outra, guardando só os 2 KB finais, para o filho nunca travar com o pipe cheio.
- Um relógio monotônico só para a conversa inteira (`PRAZO_S`, 20 s).
- **Morte da árvore em toda saída**, inclusive exceção e `KeyboardInterrupt`. A primitiva do MH para isso é
  `mh.janelas.encerrar_e_esperar` (LL-17), mas a cópia dos hosts não alcança o `mh`. Então a posse da árvore é
  provada pelo sistema, sem pid solto: no Windows o servidor nasce suspenso, entra num Job Object com
  `KILL_ON_JOB_CLOSE` e só então roda, e o encerramento é `TerminateJobObject` seguido da espera até o job não ter
  processo ativo (um processo fora do job nunca é tocado; se a sonda morrer, o sistema fecha o job e mata a árvore).
  Fora do Windows, o servidor nasce em sessão própria e o encerramento é o grupo dela. Os testes do MH conferem a
  morte de um neto com `mh.janelas.processo_vivo`.
- Nenhum `tools/call`: a sonda não executa ferramenta.
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from typing import Any, cast

__all__ = [
    "CAPACIDADE_SCIENCE",
    "CODIGO",
    "ESPERA_SAIDA_S",
    "EXIGIDAS_SCIENCE",
    "MOTIVOS",
    "NAO_VERIFICADO",
    "PASSA",
    "PRAZO_S",
    "PROTOCOLO",
    "REPROVA",
    "codigo_de",
    "emitir",
    "relato_sem_servidor",
    "sondar",
]

PASSA = "passa"
REPROVA = "reprova"
NAO_VERIFICADO = "nao_verificado"

#: A convenção 0/1/2 do `mh paridade` e do `mh pronto`.
CODIGO = {PASSA: 0, REPROVA: 1, NAO_VERIFICADO: 2}

#: O vocabulário fechado de motivos, por estado. Motivo fora daqui é relato inválido para quem consome.
MOTIVOS: dict[str, tuple[str, ...]] = {
    PASSA: ("ok",),
    REPROVA: ("executavel_ausente", "nao_subiu", "prazo", "saiu_antes", "resposta_invalida", "ferramenta_ausente"),
    NAO_VERIFICADO: ("config_ausente", "config_ilegivel", "nao_registrado", "desligado", "transporte_url"),
}

#: A capacidade do contrato que esta sonda serve, e as ferramentas que a skill do Science e o roteiro do Codex mandam
#: usar, cruzadas com as que o Codex aprova (DU-6, 21/10). `list_corpora` fica fora: o Codex não a aprova.
CAPACIDADE_SCIENCE = "integration.science-harness"
EXIGIDAS_SCIENCE = ("server_info", "search_claims", "get_claim")

#: Prazo da conversa inteira. Medido no notebook em 21/10: `initialize` em 1,61 s, `tools/list` em 0,05 s.
PRAZO_S = 20.0
#: Depois de fechar o `stdin`, quanto o servidor tem para sair sozinho antes de a árvore ser encerrada.
ESPERA_SAIDA_S = 5.0
#: Quanto esperar as threads leitoras depois do encerramento (com a arvore morta, o fim dos pipes e imediato).
PRAZO_LEITORES_S = 5.0
#: Prazo do encerramento da árvore e da espera pela morte de cada processo dela.
PRAZO_ENCERRAR_S = 10.0
PROTOCOLO = "2025-06-18"
NOME_DO_CLIENTE = "mh-sonda-mcp"
MAX_PAGINAS = 10
CAUDA_STDERR = 2048

_EH_WINDOWS = sys.platform == "win32"
_FIM = object()


class _Falha(Exception):
    """Reprovação com motivo do vocabulário e um detalhe legível."""

    def __init__(self, motivo: str, detalhe: str = "") -> None:
        super().__init__(motivo, detalhe)
        self.motivo = motivo
        self.detalhe = detalhe


# --------------------------------------------------------------------------------------------------------- relato


def codigo_de(relato: dict[str, Any]) -> int:
    """O código de saída do relato: 0, 1 ou 2. Estado desconhecido é 2 (não deu para dizer)."""
    return CODIGO.get(str(relato.get("estado")), 2)


def _relato(host: str, estado: str, motivo: str, *, spec: dict[str, Any] | None, exigidas: tuple[str, ...],
            vistas: list[str] | None, inicio: float, detalhe: str = "", stderr_final: str = "",
            capacidade: str = CAPACIDADE_SCIENCE) -> dict[str, Any]:
    if motivo not in MOTIVOS.get(estado, ()):
        raise ValueError(f"motivo {motivo!r} fora do vocabulario de {estado!r}")
    spec = spec if isinstance(spec, dict) else {}
    args = spec.get("args")
    relato: dict[str, Any] = {
        "capacidade": capacidade,
        "host": host,
        "estado": estado,
        "motivo": motivo,
        "ferramentas_exigidas": list(exigidas),
        "ferramentas_vistas": sorted(vistas) if vistas is not None else None,
        "duracao_s": round(time.monotonic() - inicio, 3),
        "command": spec.get("command") if isinstance(spec.get("command"), str) else None,
        "args": [a for a in args if isinstance(a, str)] if isinstance(args, list) else [],
    }
    if detalhe:
        relato["detalhe"] = detalhe
    if stderr_final:
        relato["stderr_final"] = stderr_final
    return relato


def relato_sem_servidor(host: str, motivo: str, *, spec: dict[str, Any] | None = None, detalhe: str = "",
                        exigidas: tuple[str, ...] = EXIGIDAS_SCIENCE,
                        capacidade: str = CAPACIDADE_SCIENCE) -> dict[str, Any]:
    """O relato `nao_verificado` de quando o registro não declara um servidor que esta sonda alcance.

    `motivo` é um de `MOTIVOS[NAO_VERIFICADO]`: o leitor do registro de cada host decide qual.
    """
    return _relato(host, NAO_VERIFICADO, motivo, spec=spec, exigidas=exigidas, vistas=None,
                   inicio=time.monotonic(), detalhe=detalhe, capacidade=capacidade)


def emitir(relato: dict[str, Any], fluxo: Any = None) -> int:
    """Escreve o relato como uma linha JSON e devolve o código de saída."""
    fluxo = sys.stdout if fluxo is None else fluxo
    fluxo.write(json.dumps(relato, ensure_ascii=False, sort_keys=True) + "\n")
    fluxo.flush()
    return codigo_de(relato)


def _ocultar(texto: str, env: dict[str, str]) -> str:
    for valor in sorted({v for v in env.values() if v}, key=len, reverse=True):
        texto = texto.replace(valor, "***")
    return texto


# ---------------------------------------------------------------------------------------------- registro e comando


def _validar_spec(spec: Any) -> tuple[str, list[str], dict[str, str]] | str:
    """`(command, args, env)` do registro, ou o motivo `nao_verificado` de não haver servidor por stdio a subir."""
    if not isinstance(spec, dict):
        return "config_ilegivel"
    command = spec.get("command")
    if command is None and isinstance(spec.get("url"), str):
        return "transporte_url"
    args = spec.get("args", [])
    env = spec.get("env", {})
    if args is None:
        args = []
    if env is None:
        env = {}
    if not isinstance(command, str) or not command.strip():
        return "config_ilegivel"
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        return "config_ilegivel"
    if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
        return "config_ilegivel"
    return command, list(args), dict(env)


def _resolver(command: str, cwd: str | None, env: dict[str, str]) -> str | None:
    """O executável no disco: caminho (relativo ao `cwd`) que existe, ou o nome achado no PATH do servidor."""
    tem_pasta = os.sep in command or (os.altsep is not None and os.altsep in command)
    if tem_pasta or os.path.isabs(command):
        caminho = command if os.path.isabs(command) else os.path.join(cwd or os.getcwd(), command)
        return caminho if os.path.isfile(caminho) else None
    return shutil.which(command, path=env.get("PATH"))


# -------------------------------------------------------------------------------------------- a árvore do servidor


class _Arvore:
    """O servidor e tudo o que ele criar, com posse provada pelo sistema (ver o docstring do módulo)."""

    def __init__(self, argv: list[str], cwd: str | None, env: dict[str, str]) -> None:
        self._job: int | None = None
        self._k32: Any = None
        self.encerrada = False
        opcoes: dict[str, Any] = {
            "stdin": subprocess.PIPE, "stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
            "cwd": cwd, "env": env, "bufsize": 0,
        }
        if _EH_WINDOWS:
            opcoes["creationflags"] = _CREATE_SUSPENDED | _CREATE_NO_WINDOW
        else:
            opcoes["start_new_session"] = True
        self.proc = cast("subprocess.Popen[bytes]", subprocess.Popen(argv, **opcoes))  # bytes: sem text nem encoding
        if _EH_WINDOWS:
            self._prender_no_job()

    def _prender_no_job(self) -> None:
        """Cria o job, põe o servidor suspenso nele e só então o solta. Falha aqui encerra o servidor e sobe."""
        try:
            self._k32 = _win_api()
            self._job = _win_job(self._k32)
            if not self._k32.AssignProcessToJobObject(self._job, _handle(self.proc)):
                raise OSError(_win_erro(), "AssignProcessToJobObject falhou")
            if _win_ntdll().NtResumeProcess(_handle(self.proc)) != 0:
                raise OSError(_win_erro(), "NtResumeProcess falhou")
        except BaseException:
            self.encerrar()
            raise

    def encerrar(self, prazo_s: float = PRAZO_ENCERRAR_S) -> bool:
        """Encerra a árvore e espera cada processo dela morrer; `False` se algum sobreviveu ao prazo."""
        prazo = time.monotonic() + prazo_s
        self.encerrada = True
        if _EH_WINDOWS:
            ok = self._encerrar_job(prazo)
        else:
            ok = self._encerrar_grupo(prazo)
        try:
            self.proc.wait(timeout=max(0.0, prazo - time.monotonic()))
        except subprocess.TimeoutExpired:
            ok = False
        return ok

    def _encerrar_job(self, prazo: float) -> bool:
        if self._job is None:
            # O servidor nao chegou ao job (a montagem falhou): ele ainda esta suspenso e nao criou ninguem; o handle
            # do `Popen` prende o pid, entao encerrar por ele toca so este processo.
            k32 = self._k32 or _win_api()
            k32.TerminateProcess(_handle(self.proc), 1)
            return True
        self._k32.TerminateJobObject(self._job, 1)
        while _win_ativos(self._k32, self._job) > 0:
            if time.monotonic() >= prazo:
                return False
            time.sleep(0.02)
        return True

    def _encerrar_grupo(self, prazo: float) -> bool:
        assert sys.platform != "win32"  # o ramo do Windows e o job; aqui so POSIX
        import signal

        grupo = self.proc.pid  # `start_new_session`: o servidor lidera o grupo; o pgid nao e reusado enquanto existir
        try:
            os.killpg(grupo, signal.SIGKILL)  # mh: kill-sem-arvore grupo-de-sessao-propria: o grupo da sessao do servidor
        except ProcessLookupError:
            return True
        while True:
            self.proc.poll()  # colhe o servidor, senao o zumbi dele mantem o grupo de pe
            try:
                os.killpg(grupo, 0)  # mh: kill-sem-arvore sonda-posix: sinal 0 so testa se o grupo ainda existe
            except ProcessLookupError:
                return True
            except PermissionError:
                return False
            if time.monotonic() >= prazo:
                return False
            time.sleep(0.02)

    def fechar(self) -> None:
        """Fecha o job (com `KILL_ON_JOB_CLOSE`, quem ainda estiver nele morre aqui)."""
        if self._job is not None:
            self._k32.CloseHandle(self._job)
            self._job = None


_CREATE_SUSPENDED = 0x00000004
_CREATE_NO_WINDOW = 0x08000000
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_EXTENDED_LIMIT_INFORMATION = 9
_JOB_BASIC_ACCOUNTING_INFORMATION = 1


def _handle(proc: subprocess.Popen[bytes]) -> int:
    """O handle do processo que o `Popen` guarda no Windows (`_handle`, o mesmo que `mh.janelas` usa): ele prende o
    pid enquanto vive, entao nada do que se faz por ele toca outro processo."""
    return int(proc._handle)  # type: ignore[attr-defined]


def _win_erro() -> int:
    import ctypes

    return ctypes.get_last_error()  # type: ignore[attr-defined]


def _win_api() -> Any:
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    k32.SetInformationJobObject.restype = wintypes.BOOL
    k32.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                              ctypes.c_void_p]
    k32.QueryInformationJobObject.restype = wintypes.BOOL
    k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    k32.AssignProcessToJobObject.restype = wintypes.BOOL
    k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    k32.TerminateJobObject.restype = wintypes.BOOL
    k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    k32.TerminateProcess.restype = wintypes.BOOL
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.CloseHandle.restype = wintypes.BOOL
    return k32


def _win_ntdll() -> Any:
    import ctypes
    from ctypes import wintypes

    nt = ctypes.WinDLL("ntdll")  # type: ignore[attr-defined]
    nt.NtResumeProcess.argtypes = [wintypes.HANDLE]
    nt.NtResumeProcess.restype = ctypes.c_long
    return nt


def _win_job(k32: Any) -> int:
    """Um Job Object com `KILL_ON_JOB_CLOSE`."""
    import ctypes
    from ctypes import wintypes

    class _Basico(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class _Estendido(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", _Basico), ("IoInfo", ctypes.c_uint64 * 6),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    job = k32.CreateJobObjectW(None, None)
    if not job:
        raise OSError(_win_erro(), "CreateJobObjectW falhou")
    info = _Estendido()
    info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not k32.SetInformationJobObject(job, _JOB_EXTENDED_LIMIT_INFORMATION, ctypes.byref(info), ctypes.sizeof(info)):
        erro = _win_erro()
        k32.CloseHandle(job)
        raise OSError(erro, "SetInformationJobObject falhou")
    return int(job)


def _win_ativos(k32: Any, job: int) -> int:
    """Quantos processos do job ainda estão ativos."""
    import ctypes
    from ctypes import wintypes

    class _Contabil(ctypes.Structure):
        _fields_ = [("TotalUserTime", ctypes.c_int64), ("TotalKernelTime", ctypes.c_int64),
                    ("ThisPeriodTotalUserTime", ctypes.c_int64), ("ThisPeriodTotalKernelTime", ctypes.c_int64),
                    ("TotalPageFaultCount", wintypes.DWORD), ("TotalProcesses", wintypes.DWORD),
                    ("ActiveProcesses", wintypes.DWORD), ("TotalTerminatedProcesses", wintypes.DWORD)]

    info = _Contabil()
    if not k32.QueryInformationJobObject(job, _JOB_BASIC_ACCOUNTING_INFORMATION, ctypes.byref(info),
                                         ctypes.sizeof(info), None):
        raise OSError(_win_erro(), "QueryInformationJobObject falhou")
    return int(info.ActiveProcesses)


# ------------------------------------------------------------------------------------------------------ a conversa


class _Conversa:
    """Leitura do `stdout` e dreno do `stderr` por threads, escrita no `stdin`, tudo sob um prazo único."""

    def __init__(self, proc: subprocess.Popen[bytes], prazo: float) -> None:
        self._proc = proc
        self._prazo = prazo
        self._linhas: queue.Queue[Any] = queue.Queue()
        self._cauda = bytearray()
        self._trava = threading.Lock()
        self._leitores = [
            threading.Thread(target=self._ler_stdout, name="sonda-mcp-stdout", daemon=True),
            threading.Thread(target=self._drenar_stderr, name="sonda-mcp-stderr", daemon=True),
        ]
        for t in self._leitores:
            t.start()

    def _ler_stdout(self) -> None:
        try:
            assert self._proc.stdout is not None
            for linha in iter(self._proc.stdout.readline, b""):
                self._linhas.put(linha)
        except (OSError, ValueError):
            pass
        finally:
            self._linhas.put(_FIM)

    def _drenar_stderr(self) -> None:
        try:
            assert self._proc.stderr is not None
            for pedaco in iter(lambda: self._proc.stderr.read(4096), b""):  # type: ignore[union-attr]
                with self._trava:
                    self._cauda += pedaco
                    del self._cauda[:-CAUDA_STDERR]
        except (OSError, ValueError):
            pass

    def cauda_stderr(self) -> str:
        with self._trava:
            return bytes(self._cauda).decode("utf-8", errors="replace")

    def enviar(self, mensagem: dict[str, Any]) -> None:
        dados = (json.dumps(mensagem, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            assert self._proc.stdin is not None
            self._proc.stdin.write(dados)
            self._proc.stdin.flush()
        except (BrokenPipeError, ConnectionResetError, OSError, ValueError) as erro:
            raise _Falha("saiu_antes", f"o servidor fechou o stdin ({erro.__class__.__name__})") from None

    def responder(self, ident: int, metodo: str) -> dict[str, Any]:
        """O `result` da resposta a `ident`. Notificação do servidor (sem `id`) é pulada."""
        while True:
            resta = self._prazo - time.monotonic()
            if resta <= 0:
                raise _Falha("prazo", f"sem resposta a {metodo} dentro do prazo")
            try:
                linha = self._linhas.get(timeout=resta)
            except queue.Empty:
                raise _Falha("prazo", f"sem resposta a {metodo} dentro do prazo") from None
            if linha is _FIM:
                raise _Falha("saiu_antes", f"o stdout fechou antes da resposta a {metodo}")
            texto = linha.decode("utf-8", errors="replace").strip()
            if not texto:
                continue
            try:
                msg = json.loads(texto)
            except ValueError:
                raise _Falha("resposta_invalida", f"linha que nao e JSON em resposta a {metodo}") from None
            if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
                raise _Falha("resposta_invalida", f"mensagem fora de JSON-RPC 2.0 em resposta a {metodo}")
            if "id" not in msg and "method" in msg:
                continue  # notificacao do servidor (log, progresso): nao e a resposta
            if msg.get("id") != ident:
                raise _Falha("resposta_invalida", f"id {msg.get('id')!r} onde se esperava {ident} ({metodo})")
            if "error" in msg:
                erro = msg.get("error")
                codigo = erro.get("code") if isinstance(erro, dict) else None
                raise _Falha("resposta_invalida", f"{metodo} respondeu erro {codigo!r}")
            resultado = msg.get("result")
            if not isinstance(resultado, dict):
                raise _Falha("resposta_invalida", f"{metodo} sem `result` objeto")
            return resultado

    def esperar_leitores(self, prazo_s: float) -> None:
        fim = time.monotonic() + prazo_s
        for t in self._leitores:
            t.join(timeout=max(0.0, fim - time.monotonic()))

    def leitores_terminaram(self) -> bool:
        return not any(t.is_alive() for t in self._leitores)


def _handshake(conversa: _Conversa) -> list[str]:
    """`initialize`, `notifications/initialized` e `tools/list` (com `nextCursor`); devolve os nomes das ferramentas."""
    conversa.enviar({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": PROTOCOLO, "capabilities": {},
        "clientInfo": {"name": NOME_DO_CLIENTE, "version": "1"}}})
    conversa.responder(1, "initialize")
    conversa.enviar({"jsonrpc": "2.0", "method": "notifications/initialized"})
    nomes: list[str] = []
    cursor: str | None = None
    for pagina in range(MAX_PAGINAS):
        ident = 2 + pagina
        params: dict[str, Any] = {} if cursor is None else {"cursor": cursor}
        conversa.enviar({"jsonrpc": "2.0", "id": ident, "method": "tools/list", "params": params})
        resultado = conversa.responder(ident, "tools/list")
        ferramentas = resultado.get("tools")
        if not isinstance(ferramentas, list):
            raise _Falha("resposta_invalida", "tools/list sem a lista `tools`")
        for f in ferramentas:
            nome = f.get("name") if isinstance(f, dict) else None
            if not isinstance(nome, str) or not nome:
                raise _Falha("resposta_invalida", "ferramenta sem `name` legivel em tools/list")
            nomes.append(nome)
        proximo = resultado.get("nextCursor")
        if proximo is None:
            return nomes
        if not isinstance(proximo, str) or not proximo:
            raise _Falha("resposta_invalida", "`nextCursor` invalido em tools/list")
        cursor = proximo
    raise _Falha("resposta_invalida", f"tools/list passou de {MAX_PAGINAS} paginas")


def sondar(spec: Any, *, host: str, cwd: str | None = None, prazo_s: float = PRAZO_S,
           exigidas: tuple[str, ...] = EXIGIDAS_SCIENCE, capacidade: str = CAPACIDADE_SCIENCE,
           espera_saida_s: float = ESPERA_SAIDA_S,
           ao_subir: Callable[[subprocess.Popen[bytes]], None] | None = None) -> dict[str, Any]:
    """Sobe o servidor de `spec` (`{"command", "args", "env"}` do registro), conversa e devolve o relato.

    `spec` sem `command` e com `url` é `transporte_url`; fora do formato, `config_ilegivel` (os dois `nao_verificado`).
    `ao_subir` recebe o `Popen` logo depois de o servidor subir (para teste: registrar o pid). A árvore morre em toda
    saída desta função.
    """
    inicio = time.monotonic()
    prazo = inicio + max(0.0, prazo_s)
    validado = _validar_spec(spec)
    if isinstance(validado, str):
        return _relato(host, NAO_VERIFICADO, validado, spec=spec, exigidas=exigidas, vistas=None, inicio=inicio,
                       capacidade=capacidade)
    command, args, env_registro = validado
    env = {**os.environ, **env_registro}

    def falha(motivo: str, detalhe: str, vistas: list[str] | None = None, cauda: str = "") -> dict[str, Any]:
        return _relato(host, REPROVA, motivo, spec=spec, exigidas=exigidas, vistas=vistas, inicio=inicio,
                       detalhe=_ocultar(detalhe, env_registro), stderr_final=_ocultar(cauda, env_registro),
                       capacidade=capacidade)

    executavel = _resolver(command, cwd, env)
    if executavel is None:
        return falha("executavel_ausente", f"{command!r} nao esta no disco nem no PATH")
    try:
        arvore = _Arvore([executavel, *args], cwd, env)
    except OSError as erro:
        return falha("nao_subiu", f"{erro.__class__.__name__}: {erro}")
    conversa: _Conversa | None = None
    try:
        if ao_subir is not None:
            ao_subir(arvore.proc)
        conversa = _Conversa(arvore.proc, prazo)
        try:
            vistas = _handshake(conversa)
        except _Falha as f:
            arvore.encerrar()
            conversa.esperar_leitores(PRAZO_LEITORES_S)
            return falha(f.motivo, f.detalhe, cauda=conversa.cauda_stderr())
        faltam = [n for n in exigidas if n not in vistas]
        _sair_por_bem(arvore, espera_saida_s)
        conversa.esperar_leitores(PRAZO_LEITORES_S)
        if faltam:
            return falha("ferramenta_ausente", "faltam: " + ", ".join(faltam), vistas=vistas)
        return _relato(host, PASSA, "ok", spec=spec, exigidas=exigidas, vistas=vistas, inicio=inicio,
                       capacidade=capacidade)
    finally:
        if not arvore.encerrada:
            arvore.encerrar()
        arvore.fechar()
        _fechar_pipes(arvore.proc, conversa)


def _sair_por_bem(arvore: _Arvore, espera_s: float) -> None:
    """Fecha o `stdin` e dá `espera_s` ao servidor para sair; senão, encerra a árvore."""
    try:
        assert arvore.proc.stdin is not None
        arvore.proc.stdin.close()
    except OSError:
        pass
    try:
        arvore.proc.wait(timeout=espera_s)
    except subprocess.TimeoutExpired:
        pass
    arvore.encerrar()  # tambem quando o servidor saiu: um neto que ele deixou ainda esta no job (ou no grupo)


def _fechar_pipes(proc: subprocess.Popen[bytes], conversa: _Conversa | None) -> None:
    """Fecha os pipes só quando os leitores terminaram: no Windows, fechar um pipe que uma thread está lendo bloqueia
    até quem segura a outra ponta soltar. Leitor vivo aqui é um sobrevivente (o processo é daemon e não segura a
    saída da sonda)."""
    if conversa is not None and not conversa.leitores_terminaram():
        return
    for fluxo in (proc.stdin, proc.stdout, proc.stderr):
        try:
            if fluxo is not None:
                fluxo.close()
        except OSError:
            pass
