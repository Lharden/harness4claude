#!/usr/bin/env python3
"""emit.py — o unico lugar que decide COMO um hook fala com o modelo.

## Por que existe

Ate 2026-09-01 todo sinal do harness saia por `systemMessage`. Esse campo e
canal de UI: ele aparece na tela do usuario e **nao entra no contexto do
modelo**. Medido nos 343 transcripts desta maquina: 100% das linhas cujo
`stdout` traz systemMessage tem `"content": ""`.

O custo disso nao foi teorico. `HARNESS v3 CLASSIFIED` foi emitido 81 vezes
em 47 sessoes; `Skill(harness-workflow)` foi invocada em **zero**. O sensor de
ramo emitiu 4 sinais e nenhum chegou. O modo de falha foi o pior possivel:
roda, nao quebra, nao registra erro, e nao faz nada — indistinguivel de "nao
havia o que sinalizar".

## A matriz de canais

Estabelecida por observacao direta do que chegou ao modelo, nao por leitura
de documentacao:

| evento           | canal              | prova                                   |
|------------------|--------------------|-----------------------------------------|
| UserPromptSubmit | additionalContext  | `[skill-hint]` chega rotulado           |
| UserPromptSubmit | stdout cru         | hook de timestamp chega como `content`  |
| SessionStart     | additionalContext  | bloco do superpowers chega rotulado     |
| SessionStart     | stdout cru         | bloco do `remember` chega como `content`|
| PostToolUse      | stdout cru         | `<harness-reclassification>` chega      |
| PostToolUse      | additionalContext  | NUNCA observado — nao usar              |
| Stop             | decision/block     | interrompe de verdade; so para gate     |
| qualquer         | systemMessage      | MORTO                                   |

Duas regras seguem dai, e as duas sao de projeto, nao de gosto:

1. **Instrucao imperativa vai por `additionalContext`, nunca por stdout cru.**
   O stdout vira `content` sem marca de proveniencia: um "You MUST invoke..."
   por esse caminho chega ao modelo indistinguivel de fala do usuario. Pelo
   canal rotulado o modelo sabe que e maquina falando e pode confrontar com o
   CLAUDE.md. Dado (uma classificacao, um bloco de estado) pode ir por stdout.

2. **O Stop nao fala.** Quando o Stop dispara, o turno acabou: uma instrucao
   do tipo "faca X antes de responder" chega tarde por construcao — foi
   exatamente assim que os 4 sinais de ramo morreram. Aqui o Stop e `silent`,
   e quem precisa dele guarda o sinal para o proximo UserPromptSubmit
   entregar. `decision: block` fica reservado a gate de verdade: com o
   pipeline auto-disparando, bloquear no Stop convida a laco.

## Um bloco por turno

O host aceita um `hookSpecificOutput` por saida de hook. Dois emissores no
mesmo processo escrevendo a chave direto se sobrescrevem em silencio — e esse
era o risco real de migrar o BRANCH SIGNAL para a mesma chave que ja carrega
o `<harness-parked>`. Por isso `Emitter` acumula e escreve uma vez.

## O extrato

Todo `flush` deixa uma linha em `<HARNESS_DIR>/emissions.jsonl`. O sistema
passa a agir sozinho; sem registro nao havia como auditar o que ele injetou,
nem medir o custo em tokens do que antes custava zero. `--aggregate` conta por
tipo lendo o proprio log: uma fonte de verdade, sem contador paralelo para
sair de sincronia.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
try:  # o emissor nunca pode derrubar um hook por causa de import
    import harness_paths  # type: ignore
except Exception:  # pragma: no cover - fallback defensivo
    harness_paths = None  # type: ignore

STDOUT = "stdout"
ADDCTX = "additionalContext"
SILENT = "silent"

# Canal por evento. Ausente do mapa => stdout, o canal conservador que
# comprovadamente chega. `stop` e silencioso de proposito; ver docstring.
#
# NAO VERIFICADO: `postcompact` e `subagentstart` nunca foram observados
# entregando por canal nenhum — nao ha um so exemplo nos 343 transcripts.
# Ficam em additionalContext pelo criterio semantico (o que sai deles e
# instrucao de retomada, nao dado) e porque nao ha regressao a temer: hoje
# esses sinais saem por systemMessage e ja se perdem. Se um dia aparecer
# evidencia de entrega, e aqui que ela vira certeza.
CHANNEL_BY_EVENT = {
    "userpromptsubmit": ADDCTX,
    "sessionstart": ADDCTX,
    "posttooluse": STDOUT,
    "precompact": STDOUT,
    "postcompact": ADDCTX,  # nao verificado
    "subagentstart": ADDCTX,  # nao verificado
    "stop": SILENT,
    "subagentstop": SILENT,
    "sessionend": SILENT,
}

# Nome canonico do evento para o `hookEventName` do payload.
CANONICAL_EVENT = {
    "userpromptsubmit": "UserPromptSubmit",
    "sessionstart": "SessionStart",
    "posttooluse": "PostToolUse",
    "pretooluse": "PreToolUse",
    "precompact": "PreCompact",
    "postcompact": "PostCompact",
    "stop": "Stop",
    "subagentstop": "SubagentStop",
    "sessionend": "SessionEnd",
}


def default_root(root=None) -> Path:
    if root is not None:
        return Path(root)
    if harness_paths is not None:
        try:
            return Path(harness_paths.default_root())
        except Exception:
            pass
    env = os.environ.get("HARNESS_DIR")
    return Path(env).expanduser() if env else Path.home() / ".claude" / "harness"


def resolve_channel(event) -> str:
    return CHANNEL_BY_EVENT.get(str(event or "").strip().lower(), STDOUT)


def canonical_event(event) -> str:
    key = str(event or "").strip().lower()
    return CANONICAL_EVENT.get(key, str(event or "UserPromptSubmit"))


def log_path(root=None) -> Path:
    return default_root(root) / "emissions.jsonl"


def _force_utf8(stream) -> None:
    """Garante UTF-8 na saida, independente de quem chamou o hook.

    No Windows o stdout de um processo Python nasce em cp1252 e um travessao
    vira `?`. O wrapper do sensor exporta PYTHONUTF8=1, mas os outros hooks
    nao — e o texto injetado e todo em portugues. Depender do chamador aqui
    seria trocar um canal invisivel por um canal ilegivel.
    """
    try:
        enc = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
        if enc not in ("utf8", "utf8mb4") and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    except Exception:
        pass


def _slug(cwd) -> str:
    if harness_paths is not None and cwd:
        try:
            return str(harness_paths.project_slug(cwd))
        except Exception:
            pass
    return ""


class Emitter:
    """Acumula blocos e emite UMA vez, pelo canal correto do evento.

    Uso tipico num hook:

        em = Emitter("UserPromptSubmit", hook="branch_sensor", cwd=cwd,
                     session_id=sid)
        em.add("branch", sinal)
        em.add("parked", bloco)
        em.flush()

    `flush` e idempotente e nunca levanta: um hook que quebra por causa da
    telemetria seria regressao pior do que o silencio que ele conserta.
    """

    def __init__(self, event, *, hook, session_id=None, cwd=None, root=None):
        self.event = event
        self.hook = hook
        self.session_id = session_id or ""
        self.cwd = cwd or ""
        self.root = root
        self.blocks = []
        self._flushed = False

    def add(self, kind, text):
        if text and str(text).strip():
            self.blocks.append((str(kind), str(text).rstrip()))
        return self

    def text(self) -> str:
        return "\n\n".join(t for _, t in self.blocks)

    def payload(self) -> dict:
        """O JSON a escrever no stdout, ou {} se nao ha nada a dizer."""
        body = self.text()
        if not body:
            return {}
        channel = resolve_channel(self.event)
        if channel == SILENT:
            return {}
        if channel == ADDCTX:
            return {
                "hookSpecificOutput": {
                    "hookEventName": canonical_event(self.event),
                    "additionalContext": body,
                }
            }
        return {"__stdout__": body}

    def flush(self, stream=None) -> dict:
        if self._flushed:
            return {}
        self._flushed = True
        out = self.payload()
        channel = resolve_channel(self.event)
        stream = stream if stream is not None else sys.stdout
        _force_utf8(stream)
        try:
            if out.get("__stdout__") is not None:
                stream.write(out["__stdout__"])
            elif out:
                stream.write(json.dumps(out, ensure_ascii=False))
        except Exception:
            pass
        for kind, text in self.blocks:
            self._record(kind, text, channel if out else SILENT)
        self.heartbeat()
        return out

    def heartbeat(self, title: str = "") -> None:
        """Deixa um batimento por sessao em `<HARNESS_DIR>/live/`.

        "Sessoes vivas" nao precisa de protocolo. Medido em 2026-09-01: das 61
        sessoes peer que o `ListAgents` enumera, TODAS estavam offline ou idle —
        construir troca de mensagens entre elas seria construir para um caso que
        nao acontece. Um arquivo por sessao com `last_seen` responde a pergunta
        real ("o que mais esta aberto agora?") com um `ls`, sem depender de host,
        de porta, nem de a outra ponta estar escutando.

        Nunca levanta: telemetria que derruba o hook virou o problema.
        """
        if not self.session_id:
            return
        try:
            d = default_root(self.root) / "live"
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{self.session_id}.json").write_text(json.dumps({
                "session_id": self.session_id,
                "cwd": str(self.cwd),
                "cwd_slug": _slug(self.cwd),
                "title": title,
                "last_seen": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def _record(self, kind, text, channel) -> None:
        """Uma linha por bloco no extrato. Nunca levanta."""
        try:
            row = {
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "session_id": self.session_id,
                "cwd_slug": _slug(self.cwd),
                "event": canonical_event(self.event),
                "hook": self.hook,
                "kind": kind,
                "channel": channel,
                "chars": len(text),
                "sha8": hashlib.sha256(text.encode("utf-8")).hexdigest()[:8],
            }
            p = log_path(self.root)
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception:
            pass


def aggregate(root=None) -> dict:
    """Conta o extrato por kind e canal. Fonte unica: o proprio jsonl."""
    agg = {}
    try:
        raw = log_path(root).read_text(encoding="utf-8")
    except OSError:
        return agg
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        k = str(row.get("kind") or "?")
        b = agg.setdefault(k, {"n": 0, "chars": 0, "channels": {}})
        b["n"] += 1
        b["chars"] += int(row.get("chars") or 0)
        ch = str(row.get("channel") or "?")
        b["channels"][ch] = b["channels"].get(ch, 0) + 1
    return agg


def live_sessions(root=None, max_age_s: int = 600) -> list:
    """Sessoes com batimento recente. Ordenadas da mais recente para a mais velha.

    `max_age_s` de 600 e o corte entre "aberta" e "esquecida": uma janela sem
    prompt ha dez minutos pode estar viva, mas ja nao e onde o trabalho esta.
    """
    from datetime import datetime as _dt

    saida = []
    agora = datetime.now(timezone.utc)
    try:
        d = default_root(root) / "live"
        arquivos = list(d.glob("*.json"))
    except Exception:
        return saida
    for f in arquivos:
        try:
            row = json.loads(f.read_text(encoding="utf-8"))
            visto = _dt.fromisoformat(str(row.get("last_seen")))
            idade = (agora - visto).total_seconds()
        except Exception:
            continue
        if idade <= max_age_s:
            row["age_s"] = int(idade)
            saida.append(row)
    saida.sort(key=lambda r: r.get("age_s", 1 << 30))
    return saida


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Emissor unico dos hooks do harness.")
    ap.add_argument("--event", default="UserPromptSubmit")
    ap.add_argument("--kind", default="misc")
    ap.add_argument("--hook", default="cli")
    ap.add_argument("--session-id", default="")
    ap.add_argument("--cwd", default="")
    ap.add_argument("--text", default=None)
    ap.add_argument("--text-file", default=None, help="arquivo, ou - para stdin")
    ap.add_argument("--aggregate", action="store_true")
    ap.add_argument("--channel-of", default=None, help="imprime o canal do evento e sai")
    ap.add_argument("--live", action="store_true", help="sessoes com batimento recente")
    ap.add_argument("--max-age", type=int, default=600)
    a = ap.parse_args(argv)

    if a.channel_of is not None:
        print(resolve_channel(a.channel_of))
        return 0
    if a.live:
        print(json.dumps(live_sessions(max_age_s=a.max_age), ensure_ascii=False, indent=1))
        return 0
    if a.aggregate:
        print(json.dumps(aggregate(), ensure_ascii=False, indent=2))
        return 0

    text = a.text
    if text is None and a.text_file:
        if a.text_file == "-":
            text = sys.stdin.read()
        else:
            text = Path(a.text_file).read_text(encoding="utf-8")
    if not text:
        return 0

    Emitter(
        a.event, hook=a.hook, session_id=a.session_id, cwd=a.cwd
    ).add(a.kind, text).flush()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # contrato: nunca falhar um hook
        sys.exit(0)
