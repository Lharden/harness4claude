#!/usr/bin/env python3
"""Constroi o indice de busca da wiki AI-Brain.

Mesma espinha do build_skills_index.py — varredura, embed via Ollama, escrita atomica,
--check-stale — apontada para outro corpus. Indice **separado** do de skills
(~/.claude/harness/wiki-index/), para nao contaminar o skill-router em producao.

As primitivas de embedding (l2norm/pack_f16/ollama_embed/atomic_write) sao importadas
do build_skills_index, nao copiadas.

Diferenca de corpus que motiva o chunking: uma skill e "nome. descricao" — cabe num
vetor. Uma pagina de wiki e prosa longa e multi-assunto; embedada inteira vira um
centroide, e pergunta sobre uma secao especifica perde para o assunto medio da pagina.
Por isso cada secao (cabecalho de nivel 2-4) vira seu proprio vetor, e a consulta
deduplica por pagina depois.

Uso: python build_wiki_index.py --root DIR [--no-embed] [--check-stale] [--out DIR]
--check-stale: exit 0 = fresco, exit 1 = stale/ausente.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_skills_index import (
    EMBED_MODEL,
    atomic_write,
    l2norm,
    ollama_embed,
    pack_f16,
)

HOME = os.path.expanduser("~")
DEFAULT_OUT = os.path.join(HOME, ".claude", "harness", "wiki-index")
ALIASES_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wiki-aliases.json")

# Espelha wiki_lint: paginas meta sao navegacao, nao conhecimento; a subarvore do
# graphify e gerada por maquina e inundaria o indice com 949 notas de no.
META_PAGES = {"index.md", "log.md"}
GENERATED_SUBTREE = "graphs"

CHUNK_CHARS = 1200
MIN_CHUNK_CHARS = 80

_FM_BLOCK_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)
_TAGS_RE = re.compile(r"^tags:\s*\[(.*?)\]\s*$", re.M)
_TYPE_RE = re.compile(r"^type:\s*(\S+)\s*$", re.M)
_H1_RE = re.compile(r"^#\s+(.+)$", re.M)
_HEADING_RE = re.compile(r"^(#{2,4})\s+(.+?)\s*$")
_SKIP_PREFIXES = ("```", ">", "---", "!")
_TABLE_SEP_RE = re.compile(r"^\|[\s:|-]+\|$")


def _default_root():
    """Resolve o sub-vault AI-Brain na precedencia do vault_sync.py."""
    ai_brain = os.environ.get("AI_BRAIN_PATH")
    if ai_brain:
        return ai_brain
    vault_root = os.environ.get("VAULT_PATH")
    if vault_root:
        return os.path.join(vault_root, "AI-Brain")
    return os.path.join(HOME, "Documents", "Obsidian Vault", "AI-Brain")


def page_files(root):
    """Paginas de wiki/ que entram no indice, ordenadas por caminho."""
    wiki = os.path.join(root, "wiki")
    found = []
    for dirpath, dirnames, filenames in os.walk(wiki):
        top = os.path.relpath(dirpath, wiki).split(os.sep)[0]
        if top == GENERATED_SUBTREE:
            dirnames[:] = []
            continue
        for name in filenames:
            if name.endswith(".md") and name not in META_PAGES:
                found.append(os.path.join(dirpath, name))
    return sorted(found)


def flatten_row(line):
    """Converte linha de tabela em prosa: '| a | b |' -> 'a b'."""
    return " ".join(cell.strip() for cell in line.strip("|").split("|") if cell.strip())


def clean_lines(block):
    """Prosa util de um bloco: sem ruido de markup, com tabelas achatadas.

    Tabelas entram porque neste vault e nelas que moram as decisoes (assimilacoes,
    recusas, knobs); pula-las esvaziava justamente as paginas mais consultaveis.
    """
    out = []
    for raw in block:
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(_SKIP_PREFIXES):
            continue
        if _TABLE_SEP_RE.match(line):
            continue
        if line.startswith("|"):
            line = flatten_row(line)
            if not line:
                continue
        out.append(line)
    return out


def split_sections(body):
    """Divide o corpo em (cabecalho, linhas) por heading de nivel 2-4."""
    sections, heading, block = [], "", []
    for raw in body.splitlines():
        match = _HEADING_RE.match(raw)
        if match:
            sections.append((heading, block))
            heading, block = match.group(2), []
        else:
            block.append(raw)
    sections.append((heading, block))
    return sections


def load_aliases(path=ALIASES_JSON):
    """Aliases curados por page_id. Chaves com '_' inicial sao comentarios."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return {k: list(v) for k, v in data.items()
            if not k.startswith("_") and isinstance(v, list)}


def page_meta(path, wiki_dir, text, aliases_map=None):
    """Metadados comuns a todos os chunks de uma pagina."""
    fm_match = _FM_BLOCK_RE.match(text)
    fm_raw = fm_match.group(1) if fm_match else ""
    body = text[fm_match.end():] if fm_match else text

    page_id = os.path.relpath(path, wiki_dir).replace(os.sep, "/")[:-3]
    stem = os.path.basename(path)[:-3]
    h1 = _H1_RE.search(body)
    title = h1.group(1).strip() if h1 else stem

    tags_match = _TAGS_RE.search(fm_raw)
    tags = [t.strip().strip("'\"") for t in tags_match.group(1).split(",")] if tags_match else []
    type_match = _TYPE_RE.search(fm_raw)

    return {
        "page_id": page_id,
        "name": stem,
        "title": title,
        "type": type_match.group(1).strip() if type_match else "page",
        "tags": [t for t in tags if t],
        "path": path,
        # aliases alimentam a Camada A (match exato): derivados do nome/titulo mais os
        # curados em wiki-aliases.json. Tags ficam de fora de proposito — "meta" e
        # "harness" aparecem em dezenas de paginas e disparariam em tudo.
        "aliases": sorted(
            ({stem, stem.replace("-", " "), title} | set((aliases_map or {}).get(page_id, [])))
            - {""}
        ),
    }, body


def _chunk(meta, heading, text, first):
    """Monta um registro de chunk no formato que skill_router.layer_b/pick consome."""
    return {
        "id": f"{meta['page_id']}#{heading}" if heading else meta["page_id"],
        "page_id": meta["page_id"],
        "name": meta["name"],
        "title": meta["title"],
        "heading": heading,
        "type": meta["type"],
        "tags": meta["tags"],
        "path": meta["path"],
        "description": text[:CHUNK_CHARS],
        # Aliases so no primeiro chunk: senao a Camada A devolveria a mesma pagina
        # uma vez por secao.
        "aliases": meta["aliases"] if first else [],
        # A wiki nao tem entrada desabilitada nem contador de uso — neutros por
        # construcao, para caber no contrato de layer_b/pick sem fork.
        "enabled": True,
        "usage_count": 0,
        "vec_row": -1,
    }


def page_chunks(path, wiki_dir, aliases_map=None):
    """Registros indexaveis de uma pagina — um por secao, agrupando secoes curtas."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return []

    meta, body = page_meta(path, wiki_dir, text, aliases_map)
    records, pending = [], []
    for heading, block in split_sections(body):
        lines = clean_lines(block)
        if not lines:
            continue
        pending.append((heading, lines))
        joined = " ".join(line for _, group in pending for line in group)
        if len(joined) < MIN_CHUNK_CHARS:
            continue  # secao curta demais para virar vetor propria: acumula
        headings = [h for h, _ in pending if h]
        records.append(_chunk(meta, headings[0] if headings else "", joined, not records))
        pending = []

    if pending:
        extra = " ".join(line for _, group in pending for line in group)
        if records:
            records[-1]["description"] = (records[-1]["description"] + " " + extra)[:CHUNK_CHARS]
        else:
            records.append(_chunk(meta, "", extra, True))
    return records


def scan_pages(root, aliases_map=None):
    """Lista os registros indexaveis da wiki (um ou mais chunks por pagina)."""
    wiki = os.path.join(root, "wiki")
    aliases_map = load_aliases() if aliases_map is None else aliases_map
    records = []
    for path in page_files(root):
        records.extend(page_chunks(path, wiki, aliases_map))
    return records


def fingerprint(root):
    """Hash de caminho+mtime+tamanho de cada pagina — base do --check-stale.

    Usa caminho **relativo** a wiki/: hashear o absoluto fazia o mesmo vault parecer
    stale so porque a raiz chegou como 'C:\\...' num lugar e 'C:/...' noutro (o
    os.path.join sobre VAULT_PATH mistura as barras no Windows).
    """
    wiki = os.path.join(root, "wiki")
    h = hashlib.sha1()
    for path in page_files(root):
        rel = os.path.relpath(path, wiki).replace(os.sep, "/")
        try:
            st = os.stat(path)
            h.update(f"{rel}|{int(st.st_mtime)}|{st.st_size}\n".encode())
        except OSError:
            h.update(f"{rel}|gone\n".encode())
    return {"page_files_hash": h.hexdigest()}


def embed_text(chunk):
    """Documento embedado: titulo da pagina, secao, tipo, tags e o texto do chunk."""
    tags = " ".join(chunk["tags"])
    heading = chunk["heading"] or chunk["title"]
    return (f"search_document: {chunk['title']} — {heading}. "
            f"{chunk['type']}. {tags}. {chunk['description']}")


def build(root, out_dir=DEFAULT_OUT, no_embed=False, pages=None):
    """Constroi e grava o indice. Retorna o nº de chunks indexados."""
    if pages is None:
        pages = scan_pages(root)
    dim, blob = 0, b""
    if not no_embed and pages:
        vecs = [l2norm(v) for v in ollama_embed([embed_text(p) for p in pages])]
        dim = len(vecs[0])
        for row, chunk in enumerate(pages):
            chunk["vec_row"] = row
        blob = pack_f16(vecs)
    index = {
        "schema_version": 2,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "model": None if no_embed else EMBED_MODEL,
        "dim": dim,
        "root": root,
        "fingerprint": fingerprint(root),
        "pages": pages,
    }
    atomic_write(os.path.join(out_dir, "embeddings.f16.bin"), blob)
    atomic_write(os.path.join(out_dir, "wiki-index.json"),
                 json.dumps(index, ensure_ascii=False, indent=1).encode("utf-8"))
    meta = {k: index[k] for k in ("schema_version", "built_at", "model", "dim", "fingerprint")}
    meta["chunks"] = len(pages)
    meta["pages"] = len({p["page_id"] for p in pages})
    meta["root"] = root
    atomic_write(os.path.join(out_dir, "meta.json"), json.dumps(meta).encode("utf-8"))
    return len(pages)


def check_stale(root, out_dir=DEFAULT_OUT):
    """True se o indice esta ausente ou desatualizado em relacao ao disco."""
    try:
        with open(os.path.join(out_dir, "meta.json"), encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, ValueError):
        return True
    return fingerprint(root) != meta.get("fingerprint")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=None, help="Raiz do sub-vault AI-Brain.")
    ap.add_argument("--no-embed", action="store_true")
    ap.add_argument("--check-stale", action="store_true")
    ap.add_argument("--out", default=DEFAULT_OUT)
    a = ap.parse_args(argv)
    root = a.root or _default_root()
    if a.check_stale:
        return 1 if check_stale(root, a.out) else 0
    n = build(root, a.out, a.no_embed)
    pages = len({p["page_id"] for p in scan_pages(root)})
    print(f"wiki-index: {n} chunks de {pages} paginas -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
