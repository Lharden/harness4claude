#!/usr/bin/env python3
"""build_sessions_index.py — indexa os transcripts de sessao para busca cross-sessao.

## Por que existe

Ha 343 transcripts em `~/.claude/projects/`, 490 MB, e **nada os indexava**.
Existe indice para as skills (236) e para o vault (123 paginas); para as
proprias conversas, nenhum. Consequencia pratica: reencontrar o que foi
decidido em outra sessao exigia lembrar o uuid, e `/resume` carrega a sessao
INTEIRA — um jsonl de 5,7 MB nao cabe em contexto.

Nao ha atalho pronto: os transcripts nao tem registro `type: "summary"`
(verificado, zero ocorrencias em 343 arquivos), e so 26 tem `ai-title`.

## O recorte, e por que ele e agressivo

O bruto engana. Medido em 2026-09-02:

| | |
|---|---|
| transcripts raiz | 343 arquivos, 490 MB |
| texto de user+assistant, sem sidechain/tool/attachment | **20,3 MB** (4%) |
| sessoes com >= 3 turnos humanos | **63** -> 5,73 MB -> ~4.800 chunks |

96% do byte e `attachment` (68.491 registros), `tool_use`, `tool_result` e
transcripts de subagente. Nada disso e memoria da conversa: e o rastro da
ferramenta. Indexar tudo custaria 40x mais e afogaria a busca no proprio
boilerplate do harness.

O filtro de ruido nao e cosmetico. Sem ele o cosseno mede o texto que o
harness injeta em todo turno — `<harness-classification>`, `[skill-hint]`,
`hook additional context` — e nao o assunto. E exatamente o modo de falha que
deixou a camada B do branch-sensor anticorrelacionada.

## A unidade e o par de turno

Nao a sessao inteira: `build_wiki_index` ja documenta por que embedar prosa
longa vira centroide inutil. Nao a mensagem solta: granular demais, e a
pergunta sozinha perde a resposta. O par (prompt + resposta final) e a menor
unidade que responde "o que foi decidido aqui".

## Reuso

`l2norm`, `pack_f16`, `ollama_embed` e `atomic_write` vem de
`build_skills_index`, como o wiki ja faz. O registro respeita o contrato de
`skill_router.layer_a/layer_b/pick` (`id`, `name`, `aliases`, `vec_row`,
`usage_count`) para que `tools/session_query.py` nao precise forkar o
ranqueador.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_skills_index import (  # noqa: E402
    EMBED_MODEL,
    atomic_write,
    l2norm,
    ollama_embed,
    pack_f16,
)

HOME = os.path.expanduser("~")
DEFAULT_ROOT = os.path.join(HOME, ".claude", "projects")
DEFAULT_OUT = os.path.join(HOME, ".claude", "harness", "sessions-index")
DEFAULT_CATALOG = os.path.join(HOME, ".claude", "harness", "sessions-catalog.json")
HISTORY_JSONL = os.path.join(HOME, ".claude", "history.jsonl")

CHUNK_CHARS = 1200
MIN_TURN_CHARS = 25
MIN_HUMAN_TURNS = 3
DEFAULT_DAYS = 90

#: Texto que o harness injeta em todo turno. Indexa-lo faria o cosseno medir o
#: proprio boilerplate — a falha que deixou a camada B do branch-sensor
#: anticorrelacionada (mesmo assunto 0.33, tangente 0.44).
RUIDO = (
    "<harness-classification>",
    "<harness-reclassification>",
    "<harness-parked>",
    "[skill-hint]",
    "hook additional context",
    "hook success",
    "Caveat: The messages below",
    "<command-name>",
    "<system-reminder>",
    "<local-command-stdout>",
    "HARNESS v3 ",
    "=== REMEMBER ===",
    "VAULT AI-Brain disponivel",
)

# NAO acrescente "Base directory for this skill:" aqui. Foi tentado em
# 2026-09-02 para tirar corpo de SKILL.md do indice — 0,8% dos turnos, mas
# desproporcional em chunks. O resultado foi medido e foi pior: a busca "por
# que o branch keeper nunca disparava" perdeu o acerto certo (a sessao de
# design do proprio branch-keeper, cos 0.4848) e 3 sessoes cairam abaixo de
# MIN_HUMAN_TURNS e sumiram inteiras.
#
# A causa e a granularidade: este filtro descarta o TURNO inteiro, e nos turnos
# onde uma skill foi invocada tambem esta a conversa que importa. Tirar corpo de
# skill exigiria remover o trecho e manter o resto — mais caro, e o ganho era
# cosmetico. Se alguem for tentar de novo, mude para remocao de trecho e meca
# a busca acima antes e depois.

_RUIDO_RE = re.compile("|".join(re.escape(x) for x in RUIDO))


# ---------------------------------------------------------------------------
# Leitura dos transcripts
# ---------------------------------------------------------------------------


def session_files(root=DEFAULT_ROOT):
    """Transcripts de sessao: jsonl na RAIZ de cada projeto.

    Subdiretorios (`<sessionId>/subagents/`, `tool-results/`) ficam de fora:
    sao 761 arquivos de contexto de subagente, que nao sao memoria da conversa.
    """
    achados = []
    try:
        projetos = sorted(os.scandir(root), key=lambda e: e.name)
    except OSError:
        return achados
    for projeto in projetos:
        if not projeto.is_dir():
            continue
        try:
            for entrada in os.scandir(projeto.path):
                if entrada.is_file() and entrada.name.endswith(".jsonl"):
                    achados.append(entrada.path)
        except OSError:
            continue
    return achados


def _texto_do_conteudo(content):
    """Blocos `text` de uma mensagem. Ignora tool_use e tool_result."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    partes = [
        b.get("text", "")
        for b in content
        if isinstance(b, dict) and b.get("type") == "text"
    ]
    return "\n".join(p for p in partes if p)


def _limpo(texto: str) -> str:
    """Descarta turno de boilerplate; devolve o resto normalizado."""
    if not texto:
        return ""
    texto = " ".join(texto.split())
    if len(texto) < MIN_TURN_CHARS:
        return ""
    if _RUIDO_RE.search(texto):
        return ""
    return texto


def parse_session(path, *, corte_epoch=0.0):
    """Extrai `{session_id, project, title, started_at, turns[]}` de um jsonl.

    Devolve None quando a sessao nao qualifica: poucos turnos humanos, fora da
    janela, ou ilegivel. Nunca levanta — um transcript torto nao pode derrubar
    a indexacao inteira.
    """
    session_id = os.path.splitext(os.path.basename(path))[0]
    projeto = os.path.basename(os.path.dirname(path))
    titulo = ""
    cwd = ""
    branch = ""
    primeiro_ts = ""
    ultimo_ts = ""
    turns = []
    pendente = None

    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for linha in fh:
                linha = linha.strip()
                if not linha:
                    continue
                try:
                    ev = json.loads(linha)
                except ValueError:
                    continue
                if not isinstance(ev, dict):
                    continue
                if ev.get("isSidechain"):
                    continue

                tipo = ev.get("type")
                if tipo == "ai-title" and ev.get("aiTitle"):
                    titulo = str(ev["aiTitle"])
                    continue
                if tipo not in ("user", "assistant"):
                    continue

                ts = ev.get("timestamp") or ""
                if ts:
                    primeiro_ts = primeiro_ts or ts
                    ultimo_ts = ts
                cwd = cwd or (ev.get("cwd") or "")
                branch = branch or (ev.get("gitBranch") or "")

                texto = _limpo(_texto_do_conteudo((ev.get("message") or {}).get("content")))
                if not texto:
                    continue
                if tipo == "user":
                    if pendente is not None:
                        turns.append(pendente)
                    pendente = {"prompt": texto, "resposta": ""}
                elif pendente is not None and not pendente["resposta"]:
                    pendente["resposta"] = texto
        if pendente is not None:
            turns.append(pendente)
    except OSError:
        return None

    if len(turns) < MIN_HUMAN_TURNS:
        return None
    if corte_epoch and primeiro_ts:
        try:
            quando = time.mktime(time.strptime(primeiro_ts[:19], "%Y-%m-%dT%H:%M:%S"))
            if quando < corte_epoch:
                return None
        except (ValueError, OverflowError):
            pass

    if not titulo:
        titulo = turns[0]["prompt"][:70]

    return {
        "session_id": session_id,
        "short_ref": session_id.replace("-", "")[:6],
        "project": projeto,
        "cwd": cwd,
        "git_branch": branch,
        "title": titulo,
        "started_at": primeiro_ts,
        "ended_at": ultimo_ts,
        "n_turns": len(turns),
        "turns": turns,
        "path": path,
    }


# ---------------------------------------------------------------------------
# Chunks
# ---------------------------------------------------------------------------


def _fatias(texto: str, tamanho: int = CHUNK_CHARS):
    """Corta em pedacos de ate `tamanho`, preferindo fronteira de palavra.

    Truncar em vez de fatiar seria descartar em silencio o que mais importa: os
    turnos longos sao as specs, os planos e os relatorios. Medido na primeira
    versao deste arquivo, que truncava — 888 chunks e 615 KB indexados de 5,7 MB
    disponiveis, ou seja 89% do conteudo jogado fora sem aviso.
    """
    texto = texto.strip()
    if not texto:
        return []
    if len(texto) <= tamanho:
        return [texto]
    pedacos, i = [], 0
    while i < len(texto):
        fim = min(i + tamanho, len(texto))
        if fim < len(texto):
            espaco = texto.rfind(" ", i + tamanho // 2, fim)
            if espaco > i:
                fim = espaco
        pedaco = texto[i:fim].strip()
        if pedaco:
            pedacos.append(pedaco)
        i = fim
    return pedacos


def session_chunks(sessao):
    """Chunks do par de turno (prompt + resposta), fatiados em CHUNK_CHARS.

    O par e a unidade porque a pergunta sozinha perde a decisao e a resposta
    sozinha perde o pedido. Turno longo vira varios chunks, todos apontando
    para o mesmo turno — a dedupe por sessao no `session_query` cuida de nao
    devolver a mesma conversa cinco vezes.
    """
    saida = []
    for n, turno in enumerate(sessao["turns"]):
        corpo = turno["prompt"]
        if turno["resposta"]:
            corpo = f"{corpo}\n{turno['resposta']}"
        for parte, texto in enumerate(_fatias(corpo)):
            saida.append({
                "id": f"{sessao['session_id']}#{n}.{parte}",
                "name": sessao["title"][:80],
                "description": texto,
                "aliases": [sessao["short_ref"], sessao["title"][:60]],
                "session_id": sessao["session_id"],
                "short_ref": sessao["short_ref"],
                "project": sessao["project"],
                "cwd": sessao["cwd"],
                "git_branch": sessao["git_branch"],
                "title": sessao["title"],
                "started_at": sessao["started_at"],
                "turn": n,
                "part": parte,
                "path": sessao["path"],
                "enabled": True,
                "usage_count": 0,
            })
    return saida


def scan_sessions(root=DEFAULT_ROOT, *, days=DEFAULT_DAYS):
    """Sessoes qualificadas e seus chunks."""
    corte = time.time() - days * 86400 if days else 0.0
    sessoes, chunks = [], []
    for caminho in session_files(root):
        s = parse_session(caminho, corte_epoch=corte)
        if s is None:
            continue
        sessoes.append(s)
        chunks.extend(session_chunks(s))
    return sessoes, chunks


def fingerprint(root=DEFAULT_ROOT):
    """Hash de (caminho, mtime, tamanho) dos transcripts. Barato e suficiente."""
    h = hashlib.sha256()
    for caminho in session_files(root):
        try:
            st = os.stat(caminho)
        except OSError:
            continue
        h.update(caminho.encode("utf-8", "replace"))
        h.update(f"{int(st.st_mtime)}:{st.st_size}".encode())
    return h.hexdigest()[:16]


# ---------------------------------------------------------------------------
# Catalogo — barato, sem embeddings
# ---------------------------------------------------------------------------


def _history_map(path=HISTORY_JSONL):
    """`session_id -> ultimo prompt digitado`, de ~/.claude/history.jsonl.

    Unico mapa plano prompt->sessao->projeto da maquina, com 350 linhas, e
    **nenhum codigo o lia**. Serve de titulo quando nao ha `ai-title` — que e o
    caso de 317 das 343 sessoes.
    """
    mapa = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for linha in fh:
                try:
                    row = json.loads(linha)
                except ValueError:
                    continue
                sid = row.get("sessionId")
                if sid and row.get("display"):
                    mapa[sid] = str(row["display"])[:200]
    except OSError:
        pass
    return mapa


def build_catalog(sessoes, out=DEFAULT_CATALOG):
    """Uma linha por sessao, sem vetor. Responde 'o que fiz ontem no projeto X'."""
    hist = _history_map()
    linhas = []
    for s in sessoes:
        linhas.append({
            "session_id": s["session_id"],
            "short_ref": s["short_ref"],
            "title": s["title"],
            "project": s["project"],
            "cwd": s["cwd"],
            "git_branch": s["git_branch"],
            "first_prompt": s["turns"][0]["prompt"][:300],
            "last_prompt": hist.get(s["session_id"], s["turns"][-1]["prompt"][:300]),
            "n_turns": s["n_turns"],
            "started_at": s["started_at"],
            "ended_at": s["ended_at"],
        })
    linhas.sort(key=lambda r: r["started_at"] or "", reverse=True)
    atomic_write(out, json.dumps(
        {"schema_version": 1,
         "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
         "sessions": linhas},
        ensure_ascii=False, indent=1).encode("utf-8"))
    return len(linhas)


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def embed_text(chunk):
    return f"{chunk['title']}\n{chunk['description']}"[:2000]


def build(root=DEFAULT_ROOT, out_dir=DEFAULT_OUT, *, no_embed=False,
          days=DEFAULT_DAYS, catalog=DEFAULT_CATALOG):
    sessoes, chunks = scan_sessions(root, days=days)
    dim, blob = 0, b""
    if not no_embed and chunks:
        vecs = [l2norm(v) for v in ollama_embed([embed_text(c) for c in chunks])]
        dim = len(vecs[0])
        for row, chunk in enumerate(chunks):
            chunk["vec_row"] = row
        blob = pack_f16(vecs)
    index = {
        "schema_version": 1,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "model": None if no_embed else EMBED_MODEL,
        "dim": dim,
        "root": root,
        "days": days,
        "fingerprint": fingerprint(root),
        "pages": chunks,
    }
    atomic_write(os.path.join(out_dir, "embeddings.f16.bin"), blob)
    atomic_write(os.path.join(out_dir, "sessions-index.json"),
                 json.dumps(index, ensure_ascii=False, indent=1).encode("utf-8"))
    meta = {k: index[k] for k in ("schema_version", "built_at", "model", "dim", "fingerprint", "days")}
    meta["chunks"] = len(chunks)
    meta["sessions"] = len(sessoes)
    meta["root"] = root
    atomic_write(os.path.join(out_dir, "meta.json"), json.dumps(meta).encode("utf-8"))
    if catalog:
        build_catalog(sessoes, catalog)
    return len(chunks), len(sessoes)


def check_stale(root=DEFAULT_ROOT, out_dir=DEFAULT_OUT):
    """True se o indice esta ausente ou atrasado em relacao ao disco."""
    try:
        with open(os.path.join(out_dir, "meta.json"), encoding="utf-8") as fh:
            meta = json.load(fh)
    except (OSError, ValueError):
        return True
    return fingerprint(root) != meta.get("fingerprint")


def mark_stale(out_dir=DEFAULT_OUT):
    """Marca o indice como sujo sem reconstruir. Chamado no SessionEnd."""
    try:
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, ".stale"), "w", encoding="utf-8") as fh:
            fh.write(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        return True
    except OSError:
        return False


def main(argv=None):
    ap = argparse.ArgumentParser(description="Indexa transcripts de sessao.")
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--catalog", default=DEFAULT_CATALOG)
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS,
                    help="janela em dias; 0 desliga o corte")
    ap.add_argument("--no-embed", action="store_true")
    ap.add_argument("--check-stale", action="store_true")
    ap.add_argument("--stats", action="store_true",
                    help="so conta o que entraria, sem gravar nada")
    a = ap.parse_args(argv)

    if a.check_stale:
        sujo = check_stale(a.root, a.out)
        print("stale" if sujo else "fresh")
        return 1 if sujo else 0
    if a.stats:
        sessoes, chunks = scan_sessions(a.root, days=a.days)
        print(json.dumps({
            "transcripts_na_raiz": len(session_files(a.root)),
            "sessoes_qualificadas": len(sessoes),
            "chunks": len(chunks),
            "chars": sum(len(c["description"]) for c in chunks),
        }, indent=1))
        return 0

    n_chunks, n_sessoes = build(a.root, a.out, no_embed=a.no_embed,
                                days=a.days, catalog=a.catalog)
    print(f"{n_chunks} chunks de {n_sessoes} sessoes -> {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
