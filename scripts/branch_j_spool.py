"""branch_j_spool.py — o spool da camada J em modo sombra (spec `docs/specs/branch-camada-j-ligacao-spec.md`).

O hook guarda, localmente, a âncora e os turnos humanos das sessões que começam com a janela aberta, para que um lote
no `mh` pontue tudo com o Jev **depois** que a janela fecha. Aqui não há rede, processo, chave nem criação de
diretório: quem cria a pasta (com ACL só do usuário) e o `janela.json` é o `mh avaliar camada-j sombra abrir`. Sem os
dois, ou com a flag desligada, nada é escrito e a saída do hook não muda.

Só biblioteca padrão: o wrapper pode rodar este código no Python do sistema, e o `mh` não é importável lá.

Contrato: `registrar` nunca levanta. Toda falha vira, no máximo, a contagem `falha_spool` da sessão.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import branch_config  # noqa: E402

#: Começos de turno que não são prompt do usuário (REQ-003). Comparados por `startswith` literal depois de `lstrip()`.
PREFIXOS = (
    "<task-notification>",
    "<command-",
    "<cross-session-message",
    "<local-command",
    "Base directory for this skill:",
    "Caveat: The messages below",
    "This session is being continued",
    "[Request interrupted",
    "Continue from where you left off.",
)
#: Turno com menos code points que isto, depois de `strip()`, não carrega tema (o mesmo piso do sensor).
MIN_LEN = 12
#: O que o `abrir` grava no `janela.json`: se o filtro mudar no meio da janela (outro deploy), o hook para de gravar.
SHA_FILTRO = hashlib.sha256(json.dumps([list(PREFIXOS), MIN_LEN]).encode("utf-8")).hexdigest()
#: Teto da soma dos `*.jsonl` da pasta (AC-1.7).
LIMITE_BYTES = 200 * 2 ** 20

_SESSAO = re.compile(r"[A-Za-z0-9-]{8,64}")


def casa() -> Path:
    """A casa do master-harness, pela mesma regra do `mh` (`MASTER_HARNESS_HOME`, senão `~/.master-harness`)."""
    env = os.environ.get("MASTER_HARNESS_HOME")
    return Path(env) if env else Path.home() / ".master-harness"


def pasta() -> Path:
    return casa() / "avaliacao" / "camada-j" / "sombra"


def motivo_de_filtro(texto: str) -> str | None:
    """None se o turno é humano; senão o prefixo que o denunciou, ou `curto`."""
    inicio = texto.lstrip()
    for p in PREFIXOS:
        if inicio.startswith(p):
            return p
    if len(texto.strip()) < MIN_LEN:
        return "curto"
    return None


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def _instante(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _janela_aberta(p: Path, agora: datetime) -> bool:
    try:
        j = json.loads((p / "janela.json").read_text(encoding="utf-8"))
        fim = datetime.strptime(j["fim"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return j.get("estado") == "aberta" and agora < fim and j.get("sha_filtro") == SHA_FILTRO




def _int(x: object) -> int:
    try:
        return int(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _contar(p: Path, sid: str, *chaves: str) -> None:
    arq = p / f"{sid}.contagens.json"
    try:
        dados = json.loads(arq.read_text(encoding="utf-8"))
        if not isinstance(dados, dict):
            dados = {}
    except (OSError, ValueError):
        dados = {}
    for c in chaves:
        dados[c] = _int(dados.get(c, 0)) + 1
    tmp = arq.with_name(f"{arq.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(dados, ensure_ascii=True, sort_keys=True), encoding="utf-8")
    os.replace(tmp, arq)


def _tamanho(p: Path) -> int:
    with os.scandir(p) as it:
        return sum(e.stat().st_size for e in it if e.name.endswith(".jsonl") and e.is_file())


def _projeto(cwd: object) -> str:
    try:
        import harness_paths

        return harness_paths.project_slug(cwd if isinstance(cwd, str) and cwd else None)
    except Exception:
        return "?"


def _linha(linha: dict) -> bytes:
    # ASCII com escapes: um surrogate solto no prompt não derruba a escrita, e o JSON continua lendo igual
    return (json.dumps(linha, ensure_ascii=True, sort_keys=True) + "\n").encode("ascii")


def _escrever(arq: Path, dados: bytes, *, criar: bool) -> None:
    """`criar`: O_CREAT|O_EXCL (a âncora, uma vez). Senão O_APPEND **sem** O_CREAT: um spool apagado por
    `excluir-sessao` ou `purgar` nunca é recriado por um turno que chegou depois (AC-5.1, AC-5.2)."""
    flags = os.O_WRONLY | getattr(os, "O_BINARY", 0) | (os.O_CREAT | os.O_EXCL if criar else os.O_APPEND)
    fd = os.open(arq, flags, 0o600)
    try:
        os.write(fd, dados)
    finally:
        os.close(fd)


def _gate(payload: object, evento: str, agora: datetime) -> tuple[Path, str] | None:
    """(pasta, sid) se a flag está ligada, o evento confere, a janela está aberta e o `session_id` é válido."""
    if not branch_config.get_bool("HARNESS_BRANCH_LAYER_J"):
        return None
    if not isinstance(payload, dict) or payload.get("hook_event_name") != evento:
        return None
    sid = payload.get("session_id")
    if not isinstance(sid, str) or not _SESSAO.fullmatch(sid):
        return None
    p = pasta()
    if not p.is_dir() or not _janela_aberta(p, agora):
        return None
    return p, sid


def iniciar_sessao(payload: object, *, agora: datetime | None = None) -> None:
    """AC-1.0: no `SessionStart` de uma sessão nova (`startup` ou `clear`) com a janela aberta, o marcador `<sid>.nova`.
    É ele que põe a sessão na amostra; retomada e compactação não entram. Nunca levanta."""
    try:
        g = _gate(payload, "SessionStart", agora or _agora())
        if g is None or payload.get("source") not in ("startup", "clear"):  # type: ignore[union-attr]
            return
        p, sid = g
        if not (p / f"{sid}.jsonl").exists() and not (p / f"{sid}.excluida").exists():
            (p / f"{sid}.nova").touch()
    except Exception:
        pass


def registrar(payload: object, *, agora: datetime | None = None) -> None:
    """Grava o turno do `UserPromptSubmit` no spool da sessão, se ela está na amostra. Nunca levanta (AC-1.6)."""
    try:
        _registrar(payload, agora=agora or _agora())
    except Exception:
        try:
            sid = payload.get("session_id") if isinstance(payload, dict) else None
            p = pasta()
            if isinstance(sid, str) and _SESSAO.fullmatch(sid) and (
                    (p / f"{sid}.nova").exists() or (p / f"{sid}.jsonl").exists()):
                _contar(p, sid, "falha_spool")
        except Exception:
            pass


def _registrar(payload: object, *, agora: datetime) -> None:
    g = _gate(payload, "UserPromptSubmit", agora)
    if g is None:
        return
    p, sid = g
    texto = payload.get("prompt")  # type: ignore[union-attr]
    if not isinstance(texto, str):
        return
    arq, marcador = p / f"{sid}.jsonl", p / f"{sid}.nova"
    if (p / f"{sid}.excluida").exists():
        return                                   # excluída pelo usuário: nunca volta, nem por um .nova que sobrou
    tem_spool = arq.exists()
    if not tem_spool and not marcador.exists():
        return                                   # fora da amostra: sem gravar e sem contar
    motivo = motivo_de_filtro(texto)
    if motivo is not None:
        _contar(p, sid, motivo)                  # não consome o marcador: a âncora é o primeiro prompt humano
        return
    if _tamanho(p) >= LIMITE_BYTES:
        _contar(p, sid, "spool_cheio")
        return
    projeto = _projeto(payload.get("cwd"))  # type: ignore[union-attr]
    if tem_spool:
        idx = arq.read_bytes().count(b"\n")
        _escrever(arq, _linha({"tipo": "turno", "turno_idx": idx, "instante": _instante(agora), "sessao": sid,
                               "projeto": projeto, "texto": texto}), criar=False)
        return
    _escrever(arq, _linha({"tipo": "ancora", "turno_idx": 0, "instante": _instante(agora), "sessao": sid,
                           "projeto": projeto, "texto": texto}), criar=True)
    marcador.unlink(missing_ok=True)
