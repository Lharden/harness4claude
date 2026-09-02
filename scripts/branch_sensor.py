#!/usr/bin/env python
"""branch_sensor.py — sensor passivo de ramificacao e deriva.

Numa conversa longa acontecem duas coisas que ninguem anuncia: o fio escorrega
do objetivo (**deriva**) e nascem ideias boas que ninguem desenvolve (**ramo**).
Quem deveria perceber e quem esta dentro da conversa — e e justamente quem
perde a perspectiva. Este modulo e a rede embaixo disso.

Tres camadas, independentes de proposito:

- **Camada 3 — o modelo.** A skill `branch-out` manda auto-checar a cada turno.
  E a melhor das tres quando funciona, e a que falha em silencio.
- **Camada A — regex.** Marcadores de tangente em PT e EN. Custo zero, roda
  sempre, inclusive com o Ollama fora do ar. E o "para caso eu esqueca".
- **Camada B — embedding.** Cosseno entre o turno e a ancora da sessao. So ela
  distingue "ideia nova" de "mesmo assunto dito com outras palavras", e so ela
  enxerga deriva sem palavra-chave nenhuma.

Ramo exige A **e** B (ou A sozinha, marcada como degradada, quando B esta
fora). Deriva e so B, sustentada por K turnos. A assimetria e deliberada: ramo
abre janela — errar custa foco; deriva emite uma frase — errar custa uma linha.

O orcamento tem mais logica que a deteccao, e isso nao e acidente. Um sensor
que pergunta demais reproduz o problema que veio resolver.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import branch_config
import branch_state

# ---------------------------------------------------------------------------
# Camada B: reuso do router, nao reimplementacao
# ---------------------------------------------------------------------------
# `skill_router.py` ja resolveu embed, pre-check de porta morta, dois relogios e
# disjuntor — tudo calibrado contra medicao real desta maquina (p95 1049ms).
# Uma segunda implementacao divergiria na primeira mudanca de modelo.

_ROUTER = None


def _router():
    global _ROUTER
    if _ROUTER is None:
        path = Path(__file__).resolve().parent.parent / "hooks" / "skill_router.py"
        spec = importlib.util.spec_from_file_location("skill_router", path)
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception:
            return None
        _ROUTER = mod
    return _ROUTER


_EMIT = None


def _emit_mod():
    """Emissor unico, carregado como o `_router()` — hooks/ nao entra no path.

    Devolve None se nao carregar; `main` cai no canal provado escrevendo o
    JSON a mao. Perder o sinal por causa do mensageiro seria repetir, com
    outra causa, exatamente a falha que este modulo veio consertar.
    """
    global _EMIT
    if _EMIT is None:
        path = Path(__file__).resolve().parent.parent / "hooks" / "emit.py"
        spec = importlib.util.spec_from_file_location("harness_emit", path)
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception:
            return None
        _EMIT = mod
    return _EMIT


def embed(text: str) -> list[float] | None:
    """Vetor normalizado do texto, ou None quando a camada B esta fora.

    Levanta so o que o chamador trata: `evaluate` converte qualquer falha em
    "sim=None" e segue pela camada A. Sensor que quebra e pior que sensor cego.
    """
    r = _router()
    if r is None:
        return None
    if not r.ollama_reachable():
        return None
    st = r.read_breaker()
    now = __import__("time").time()
    if r.breaker_open(st, now):
        return None
    try:
        vec = r.embed_query(text)
        r.breaker_record(st, True, now)
        r.write_breaker(st)
        return vec
    except Exception:
        r.breaker_record(st, False, now)
        r.write_breaker(st)
        return None


# ---------------------------------------------------------------------------
# Camada A
# ---------------------------------------------------------------------------

#: Marcadores de tangente. Sao formas de ABRIR assunto, nao temas — por isso
#: envelhecem devagar e valem em qualquer projeto.
LAYER_A_PATTERNS = (
    r"\be se\b",
    r"\boutra ideia\b",
    r"\bseria (interessante|legal|bom)\b",
    r"\balias\b",
    r"\bpor outro lado\b",
    r"\b(poderiamos|podiamos|dava pra|daria pra)\b.{0,24}\btambem\b",
    r"\bisso abre\b",
    r"\bfica (pra|para) depois\b",
    r"\bnum outro momento\b",
    r"\bfora do escopo\b",
    r"\bwhat if\b",
    r"\bwe could also\b",
    r"\bside note\b",
    r"\btangent\b",
    r"\bseparate (issue|topic|conversation)\b",
    r"\bout of scope\b",
)

_COMPILED_A = tuple(re.compile(p) for p in LAYER_A_PATTERNS)


def _fold(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", str(text).lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def layer_a(text: str) -> str | None:
    """Devolve o marcador encontrado, ou None."""
    if not text:
        return None
    plano = _fold(text)
    for rx in _COMPILED_A:
        m = rx.search(plano)
        if m:
            return m.group(0)
    return None


# ---------------------------------------------------------------------------
# Ancora
# ---------------------------------------------------------------------------


def anchor_path(cwd: str | os.PathLike | None = None) -> str:
    return str(branch_state.harness_paths.state_dir(cwd=cwd) / "branch-anchor.json")


def set_anchor(
    *,
    cwd: str | os.PathLike | None = None,
    text: str,
    source: str,
    session_id: str | None,
    embedding: list[float] | None = None,
) -> dict:
    """Fixa o objetivo da sessao. Uma vez por sessao — e o zero da regua."""
    data = {
        "text": str(text)[:2000],
        "source": source,
        "session_id": session_id,
        "embedding": embedding,
        "set_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    p = Path(anchor_path(cwd))
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)
    return data


def load_anchor(
    cwd: str | os.PathLike | None = None, session_id: str | None = None
) -> dict | None:
    """Ancora da sessao corrente. Ancora de outra sessao nao serve e e ignorada.

    Sessao nova costuma ter objetivo novo; medir deriva contra o objetivo de
    ontem produziria alarme constante e o sensor viraria ruido de fundo.
    """
    try:
        data = json.loads(Path(anchor_path(cwd)).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("text"):
        return None
    if session_id and data.get("session_id") and data["session_id"] != session_id:
        return None
    return data


def cosine(a, b) -> float | None:
    """Cosseno entre dois vetores. None quando um deles nao serve."""
    if not a or not b or len(a) != len(b):
        return None
    num = sum(x * y for x, y in zip(a, b, strict=False))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if not na or not nb:
        return None
    return num / (na * nb)


# ---------------------------------------------------------------------------
# Pisos e veredicto
# ---------------------------------------------------------------------------


def _f(env: str, default: float) -> float:
    """Float de um knob. O `default` do chamador vira apenas fallback.

    A fonte da verdade e `branch_config.KNOBS`: enquanto cada default vivia no
    ponto de leitura, a documentacao divergiu do codigo sem que nada acusasse
    (o CLAUDE.md listou por semanas quatro nomes que ninguem lia). O parametro
    continua na assinatura para nao quebrar chamadas com knob nao registrado.
    """
    if env in branch_config.KNOBS:
        return branch_config.get_float(env)
    try:
        return float(os.environ.get(env, default))
    except (TypeError, ValueError):
        return default


def _i(env: str, default: int) -> int:
    """Inteiro de um knob. Mesma regra do `_f`."""
    if env in branch_config.KNOBS:
        return branch_config.get_int(env)
    try:
        return int(os.environ.get(env, default))
    except (TypeError, ValueError):
        return default



def verdict(*, hit_a: str | None, sim: float | None, drift_streak: int) -> dict:
    """Decide entre ramo, deriva e silencio. Funcao pura — o resto e IO.

    AVISO DE CALIBRACAO (medido 2026-09-01, ancora real desta maquina):
    os cossenos observados vivem entre 0.28 e 0.44 — abaixo do
    HARNESS_BRANCH_FLOOR de 0.55 em 100% dos casos. Logo `sim <
    branch_floor` e sempre verdade e o segundo ramo abaixo equivale a
    `hit_a` sozinho: hoje a camada B nao veta nada, so reporta se o
    Ollama respondeu. Pior, a medicao saiu anticorrelacionada — o mesmo
    assunto pontuou 0.33 e uma tangente clara pontuou 0.44, sinal de que
    o cosseno contra o primeiro prompt esta dominado por comprimento e
    estilo, nao por tema.

    Nao troque 0.55 por outro numero a olho: seria repetir o chute com
    outro digito. O piso certo (e a metrica certa) saem de
    scripts/calibrate_branch_floor.py contra rotulos reais.
    """
    branch_floor = _f("HARNESS_BRANCH_FLOOR", 0.55)
    drift_floor = _f("HARNESS_BRANCH_DRIFT_FLOOR", 0.35)
    drift_turns = _i("HARNESS_BRANCH_DRIFT_TURNS", 3)

    if hit_a and sim is None:
        return {"kind": "ramo", "degraded": True, "marker": hit_a, "sim": None}
    if hit_a and sim is not None and sim < branch_floor:
        return {"kind": "ramo", "degraded": False, "marker": hit_a, "sim": sim}
    if sim is not None and sim < drift_floor and drift_streak >= drift_turns:
        return {"kind": "deriva", "degraded": False, "marker": None, "sim": sim}
    return {"kind": None, "degraded": sim is None, "marker": hit_a, "sim": sim}


# ---------------------------------------------------------------------------
# Orcamento de ruido
# ---------------------------------------------------------------------------


def _budget_path(cwd) -> Path:
    return Path(str(branch_state.harness_paths.state_dir(cwd=cwd) / "branch-sensor.json"))


def _budget(cwd) -> dict:
    try:
        d = json.loads(_budget_path(cwd).read_text(encoding="utf-8"))
        if isinstance(d, dict):
            return d
    except (OSError, ValueError):
        pass
    return {"offers": 0, "last_offer_turn": -999, "drift_streak": 0}


def _save_budget(cwd, data: dict) -> None:
    p = _budget_path(cwd)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _pending_path(cwd) -> Path:
    return Path(str(branch_state.harness_paths.state_dir(cwd=cwd) / "pending-signal.json"))


def stash_pending(*, cwd, session_id, text: str, turn: int) -> None:
    """Guarda um sinal nascido no Stop para o proximo UserPromptSubmit entregar.

    No Stop o turno acabou: a instrucao "invoque a skill AGORA, antes de
    responder" chega depois da resposta, o que a torna inexecutavel. Os 4
    BRANCH SIGNAL da historia inteira vieram desse evento, e nenhum virou
    oferta.

    Descartar tambem nao serve, e a medicao mostrou por que: `evaluate` chama
    `record_offer` ANTES de saber se a entrega e possivel, entao um sinal
    perdido no Stop ainda queima uma das 2 ofertas da sessao. Em 2026-09-01
    isto foi observado ao vivo — `offers: 1` gasto por um sinal que ninguem
    leu. Guardar fecha os dois buracos com o mesmo arquivo.
    """
    try:
        p = _pending_path(cwd)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({
            "session_id": session_id or "",
            "text": text,
            "turn": turn,
            "stashed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def take_pending(*, cwd, session_id) -> str:
    """Consome o sinal guardado, se for da sessao corrente. Le uma vez so.

    Sinal de outra sessao e descartado sem entregar: o objetivo mudou, e
    oferecer um ramo sobre a conversa de ontem seria ruido com cara de
    memoria.
    """
    p = _pending_path(cwd)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    try:
        p.unlink()
    except OSError:
        pass
    if not isinstance(data, dict):
        return ""
    if session_id and data.get("session_id") and data["session_id"] != session_id:
        return ""
    return str(data.get("text") or "")


def budget_allows(*, cwd, turn: int) -> bool:
    """Teto de ofertas por sessao mais cooldown entre elas.

    Cuidado com a unidade: "turno" aqui e CHAMADA DE HOOK, e uma troca completa
    gera duas (UserPromptSubmit + Stop). O default 8 vale ~4 trocas — e o numero
    a mexer se as ofertas parecerem seguidas demais.
    """
    b = _budget(cwd)
    if b.get("offers", 0) >= _i("HARNESS_BRANCH_MAX_OFFERS", 2):
        return False
    return turn - b.get("last_offer_turn", -999) >= _i("HARNESS_BRANCH_COOLDOWN_TURNS", 8)


def record_offer(*, cwd, turn: int) -> None:
    b = _budget(cwd)
    b["offers"] = b.get("offers", 0) + 1
    b["last_offer_turn"] = turn
    _save_budget(cwd, b)


def bump_drift(*, cwd, sim: float | None) -> int:
    """Conta turnos consecutivos longe da ancora. Sem medida, nao conta nada."""
    b = _budget(cwd)
    if sim is None:
        return int(b.get("drift_streak", 0))
    b["drift_streak"] = (
        int(b.get("drift_streak", 0)) + 1 if sim < _f("HARNESS_BRANCH_DRIFT_FLOOR", 0.35) else 0
    )
    _save_budget(cwd, b)
    return b["drift_streak"]


def reset_session(cwd) -> None:
    """Zera orcamento e streak. Chamado no SessionStart."""
    _save_budget(cwd, {"offers": 0, "last_offer_turn": -999, "drift_streak": 0})


# ---------------------------------------------------------------------------
# Payload dos hooks
# ---------------------------------------------------------------------------


def _assistant_text(transcript_path: str) -> str:
    """Ultima fala do assistente no transcript JSONL. String vazia se nao der."""
    try:
        linhas = Path(transcript_path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    for linha in reversed(linhas[-80:]):
        try:
            ev = json.loads(linha)
        except ValueError:
            continue
        if ev.get("type") != "assistant":
            continue
        content = (ev.get("message") or {}).get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            partes = [
                c.get("text", "")
                for c in content
                if isinstance(c, dict) and c.get("type") == "text"
            ]
            if any(partes):
                return "\n".join(partes)
    return ""


def text_from_payload(payload: dict) -> str:
    """Texto a analisar, conforme o evento que chamou o hook."""
    if payload.get("stop_hook_active"):
        # Reentrancia: o Stop dispara ao fim da resposta que o proprio Stop
        # provocou. Sem este corte, o sensor analisaria a si mesmo.
        return ""
    if (payload.get("hook_event_name") or "").lower().startswith("stop"):
        return _assistant_text(payload.get("transcript_path") or "")
    for k in ("prompt", "user_prompt", "user_message", "content"):
        v = payload.get(k)
        if isinstance(v, str) and v.strip():
            return v
    return ""


# ---------------------------------------------------------------------------
# Avaliacao
# ---------------------------------------------------------------------------

MIN_LEN = 12


def enabled() -> bool:
    return os.environ.get("HARNESS_BRANCH", "1").strip().lower() not in ("0", "false", "off")


def evaluate(
    *, cwd, text: str, session_id: str | None = None, turn: int = 0
) -> str:
    """Roda as camadas e devolve o texto do sinal, ou string vazia.

    Contrato: nunca levanta. Todo caminho de erro vira silencio, porque este
    codigo roda em UserPromptSubmit e em Stop — quebrar aqui e quebrar a sessao.
    """
    if not enabled() or not text or len(text.strip()) < MIN_LEN:
        return ""

    hit = layer_a(text)
    anchor = load_anchor(cwd, session_id)

    # Camada B custa ~1s (p95 medido: 1049ms). Pagar isso em TODO prompt seria
    # cobrar do foco para proteger o foco. Ela roda quando ha o que decidir:
    # - marcador da Camada A -> preciso de B para separar ramo de mesmo-assunto;
    # - amostragem periodica  -> deriva exige streak, entao medir turno sim,
    #   turno nao apenas atrasa o alarme em alguns turnos, nao o impede.
    sample = _i("HARNESS_BRANCH_DRIFT_SAMPLE", 2)
    precisa_b = bool(hit) or (sample > 0 and turn % sample == 0)

    sim = None
    if anchor and precisa_b:
        vec = None
        try:
            vec = embed(text)
        except Exception:
            vec = None
        sim = cosine(vec, anchor.get("embedding")) if vec else None

    streak = bump_drift(cwd=cwd, sim=sim)
    v = verdict(hit_a=hit, sim=sim, drift_streak=streak)
    if v["kind"] is None:
        return ""

    if not budget_allows(cwd=cwd, turn=turn):
        return ""

    tema = " ".join(text.split())[:160]

    if v["kind"] == "ramo":
        if branch_state.already_seen(cwd=cwd, topic=tema):
            return ""
        record_offer(cwd=cwd, turn=turn)
        branch_state.signal("offered_ramo_degradado" if v["degraded"] else "offered_ramo")
        grau = "degradado (sem camada B)" if v["degraded"] else f"sim={v['sim']:.2f}"
        return (
            f"HARNESS v3 BRANCH SIGNAL: ramo — marcador '{v['marker']}' [{grau}]. "
            f'Tema: "{tema}". '
            f"Invoque a skill 'branch-out' com acao=offer AGORA, antes de responder "
            f"ao conteudo: nomeie o ramo, justifique em ate 3 linhas e pergunte se "
            f"abre, parkeia ou descarta. Se o usuario nao decidir, parkeie."
        )

    record_offer(cwd=cwd, turn=turn)
    branch_state.signal("offered_deriva")
    alvo = (anchor or {}).get("text", "?")[:120]
    return (
        f"HARNESS v3 BRANCH SIGNAL: deriva — {streak} turnos abaixo do piso "
        f"(sim={v['sim']:.2f}). Ancora: \"{alvo}\". "
        f"Invoque a skill 'branch-out' com acao=drift: diga em uma frase de onde "
        f"a conversa saiu e pergunte se voltamos, reancoramos ou ramificamos. "
        f"Nao abra janela por deriva."
    )


# ---------------------------------------------------------------------------
# CLI — chamado pelo wrapper bash com o payload do hook em stdin
# ---------------------------------------------------------------------------


def main() -> int:
    # Le os bytes e decodifica em UTF-8 a mao. `json.load(sys.stdin)` usa o
    # encoding do processo, que no Windows nasce cp1252: sem PYTHONIOENCODING
    # o prompt "alias" com acento chega como "aliÃ¡s" e a camada A erra o
    # marcador. O wrapper .sh exporta as duas variaveis, mas depender disso
    # deixa o sensor quebrado para qualquer outro chamador.
    try:
        raw = sys.stdin.buffer.read()
    except (AttributeError, OSError, ValueError):
        raw = None
    try:
        if raw is None:
            payload = json.load(sys.stdin)
        else:
            payload = json.loads(raw.decode("utf-8", "replace"))
    except (ValueError, OSError):
        return 0
    if not isinstance(payload, dict):
        return 0

    # Heartbeat do evento que chamou. Fica antes de qualquer saida antecipada:
    # o que se mede aqui e a CHAMADA, nao o trabalho. Para o `Stop` isso pesa
    # mais que para os outros — e o evento mais novo do contrato, e se o host
    # parar de chama-lo a falha e invisivel: zero ramo detectado e igualzinho a
    # zero ramo existente.
    evento = payload.get("hook_event_name") or ""
    if evento:
        try:
            hb = Path(os.environ.get("HARNESS_DIR") or (Path.home() / ".claude" / "harness"))
            hb = hb / "heartbeats"
            hb.mkdir(parents=True, exist_ok=True)
            (hb / str(evento)).write_text(str(int(__import__("time").time())), encoding="utf-8")
        except OSError:
            pass

    cwd = payload.get("cwd") or os.getcwd()
    session_id = payload.get("session_id")
    texto = text_from_payload(payload)
    if not texto:
        return 0

    # Sessao nova zera orcamento e contador de turno. Sem isso, as 2 ofertas
    # gastas ontem calariam o sensor hoje — e o silencio seria indistinguivel
    # de "nao havia ramo nenhum".
    b0 = _budget(cwd)
    if session_id and b0.get("session_id") != session_id:
        _save_budget(
            cwd,
            {
                "session_id": session_id,
                "offers": 0,
                "last_offer_turn": -999,
                "drift_streak": 0,
                "turn": 0,
            },
        )

    # Ancora: nasce no primeiro turno substantivo da sessao. Se ja houver
    # pipeline com spec, a skill sobrescreve com o objetivo formal.
    _anchor = load_anchor(cwd, session_id)
    if _anchor is None and not (
        payload.get("hook_event_name") or ""
    ).lower().startswith("stop"):
        vec = None
        try:
            vec = embed(texto)
        except Exception:
            vec = None
        set_anchor(
            cwd=cwd,
            text=texto,
            source="first-prompt",
            session_id=session_id,
            embedding=vec,
        )
        return 0

    # Ancora nascida com o Ollama fora ficava com embedding nulo e, por nao
    # ser None, nunca era recriada: cosine(vec, None) devolvia None e a
    # camada B ficava cega pelo resto da vida daquele projeto. Medido em
    # 2026-09-01: 2 de 5 ancoras em disco estavam nesse estado.
    # Backfill so do vetor — o texto nao muda, o zero da regua nao se move.
    if _anchor is not None and not _anchor.get("embedding"):
        _vec = None
        try:
            _vec = embed(_anchor["text"])
        except Exception:
            _vec = None
        if _vec:
            set_anchor(
                cwd=cwd,
                text=_anchor["text"],
                source=_anchor.get("source") or "first-prompt",
                session_id=_anchor.get("session_id"),
                embedding=_vec,
            )

    b = _budget(cwd)
    turn = int(b.get("turn", 0)) + 1
    b["turn"] = turn
    _save_budget(cwd, b)

    msg = evaluate(cwd=cwd, text=texto, session_id=session_id, turn=turn)
    parked = branch_state.parked_block(cwd)

    # Ate 2026-09-01 o sinal saia por `systemMessage` e o parking por
    # `additionalContext`. So o segundo chegava ao modelo — e como o parking
    # depende de `branches.json`, que nunca nasceu porque nenhuma oferta foi
    # aceita, na pratica o hook falava sozinho. O emissor resolve as duas
    # metades: escolhe o canal pelo evento e junta os blocos num so, porque o
    # host aceita um `hookSpecificOutput` por saida.
    evento = payload.get("hook_event_name") or "UserPromptSubmit"

    # O Stop nao fala: guarda e sai. O UserPromptSubmit seguinte entrega, num
    # momento em que "antes de responder" ainda quer dizer alguma coisa.
    if str(evento).lower().startswith("stop"):
        if msg:
            stash_pending(cwd=cwd, session_id=session_id, text=msg, turn=turn)
        mod = _emit_mod()
        if mod is not None:
            mod.Emitter(evento, hook="branch_sensor", session_id=session_id,
                        cwd=cwd).add("branch", msg).flush()
        return 0

    # Um sinal guardado vem primeiro: ele nasceu no turno anterior e envelhece.
    pendente = take_pending(cwd=cwd, session_id=session_id)

    mod = _emit_mod()
    if mod is not None:
        em = mod.Emitter(evento, hook="branch_sensor", session_id=session_id, cwd=cwd)
        em.add("branch_pendente", pendente).add("branch", msg).add("parked", parked)
        em.flush()
        return 0

    # Fallback sem o emissor: so o canal provado, e nunca no Stop (ali o turno
    # ja acabou e a instrucao chegaria tarde por construcao).
    corpo = "\n\n".join(x for x in (pendente, msg, parked) if x)
    if corpo:
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": evento,
                "additionalContext": corpo,
            }
        }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        # Contrato dos hooks do harness: nunca derrubar a sessao.
        raise SystemExit(0) from None
