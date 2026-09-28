"""Semantic vector index for the data bank (knowledge/ folder).

This is the embeddings/RAG engine behind ``core.knowledge``. It turns the
drop-in note files into chunked text embeddings and answers similarity
queries — the same idea as SillyTavern's Vector Storage, scoped to one local
folder and one small local model.

Design notes:
  - Model: sentence-transformers ``all-MiniLM-L6-v2`` (384-dim, ~80 MB). It is
    loaded lazily on first use and cached. torch is already a project dep.
  - Remote option: if the ``embeddings:`` block in config.yaml is enabled
    (see ``configure()``), vectors come from that OpenAI-compatible
    ``/v1/embeddings`` API instead, with its own key. The local model is then
    never loaded, and the main LLM's key/quota is never touched.
  - The index lives in ``knowledge/.vector_index.json`` (gitignored). It tracks
    each file's mtime/size so ``sync()`` only re-embeds what actually changed.
  - Everything degrades gracefully: if sentence-transformers can't be imported
    or the model can't load (e.g. offline first run), ``is_available()`` returns
    False and callers fall back to the keyword search in ``core.knowledge``.

Public API:
  configure(cfg)                       — route embedding to a remote API (or not)
  is_available()                       — can we do semantic search at all?
  sync(force=False)                    — bring the index in line with the folder
  semantic_search(query, k, min_score) — list[Hit] sorted by similarity
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

_KNOWLEDGE_DIR = Path(__file__).parent.parent / "knowledge"
_INDEX_FILE = _KNOWLEDGE_DIR / ".vector_index.json"

_MODEL_NAME = "all-MiniLM-L6-v2"

# Chunking — small enough to be specific, with overlap so facts split across a
# boundary still get retrieved.
_CHUNK_CHARS = 600
_CHUNK_OVERLAP = 120

_DEFAULT_TOP_K = 4
_DEFAULT_MIN_SCORE = 0.25   # cosine similarity floor; below this is noise

# Match core.knowledge so the two stay in sync.
_ALLOWED_EXTENSIONS = {".txt", ".md", ".markdown", ".json", ".yaml", ".yml", ".csv", ".log"}
_IGNORED_NAMES = {"readme.txt", "readme.md", ".gitkeep"}

_lock = threading.Lock()
_model = None              # cached SentenceTransformer
_model_failed = False      # True once load has failed, to avoid retry storms

# Remote embeddings API (EmbeddingsConfig) — None means use the local model.
_api_cfg = None
_api_client = None


@dataclass
class Hit:
    file: str
    text: str
    score: float


# ── backend selection ──────────────────────────────────────────────────────

def configure(cfg) -> None:
    """Route embedding through a separate remote API (``EmbeddingsConfig``).

    Pass None (or a disabled config) to use the local sentence-transformers
    model. Call once at startup, before ``sync()``.
    """
    global _api_cfg, _api_client
    if cfg is not None and getattr(cfg, "enabled", False) and (cfg.api_key or cfg.base_url):
        _api_cfg = cfg
        logger.info("Data bank embeddings → remote API (%s, model %s).",
                    cfg.provider, cfg.model)
    else:
        if cfg is not None and getattr(cfg, "enabled", False):
            logger.warning("embeddings.enabled is set but no api_key/base_url — "
                           "using the local embedding model.")
        _api_cfg = None
    _api_client = None


def _backend_id() -> str:
    """Identifies which embedder made the stored vectors (switching invalidates)."""
    if _api_cfg is not None:
        return f"api:{_api_cfg.provider}:{_api_cfg.model}"
    return _MODEL_NAME


def _get_api_client():
    """OpenAI-compatible client for the embeddings API, built on first use."""
    global _api_client
    if _api_client is not None:
        return _api_client
    from openai import OpenAI
    from llm.factory import _KNOWN_BASE_URLS
    cfg = _api_cfg
    base_url = cfg.base_url or _KNOWN_BASE_URLS.get((cfg.provider or "").lower())
    _api_client = OpenAI(api_key=cfg.api_key or "no-key", base_url=base_url)
    return _api_client


def _embed_api(texts: List[str]):
    """Embed via the remote API, batched. Returns L2-normalized vectors or None."""
    try:
        import numpy as np
        client = _get_api_client()
        batch = max(1, int(_api_cfg.batch_size or 64))
        out = []
        for i in range(0, len(texts), batch):
            resp = client.embeddings.create(model=_api_cfg.model, input=texts[i:i + batch])
            out.extend(d.embedding for d in sorted(resp.data, key=lambda d: d.index))
        vecs = np.asarray(out, dtype="float32")
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return vecs / norms
    except Exception as exc:
        # Deliberately no local fallback: the point of the API is to keep this
        # work off the machine. Callers fall back to keyword search instead.
        logger.warning("Embeddings API call failed (%s) — keyword search this time.", exc)
        return None


# ── model loading ──────────────────────────────────────────────────────────

def is_available() -> bool:
    """True if semantic search can run (API configured or model loadable)."""
    if _api_cfg is not None:
        return True
    return _get_model() is not None


def _get_model():
    """Lazily import + load the embedding model. Returns None on any failure."""
    global _model, _model_failed
    if _model is not None:
        return _model
    if _model_failed:
        return None
    with _lock:
        if _model is not None:
            return _model
        if _model_failed:
            return None
        try:
            from sentence_transformers import SentenceTransformer
            logger.info("Loading embedding model %s ...", _MODEL_NAME)
            _model = SentenceTransformer(_MODEL_NAME)
            logger.info("Embedding model ready.")
        except Exception as exc:
            # Not installed, or model can't be downloaded (offline). Fall back.
            logger.warning("Semantic search unavailable (%s) — falling back to keyword.", exc)
            _model_failed = True
            _model = None
    return _model


def _embed(texts: List[str]):
    """Encode texts to L2-normalized vectors (list of float lists)."""
    if _api_cfg is not None:
        return _embed_api(texts)
    model = _get_model()
    if model is None:
        return None
    vecs = model.encode(texts, normalize_embeddings=True, convert_to_numpy=True)
    return vecs


# ── chunking ───────────────────────────────────────────────────────────────

def _note_files() -> List[Path]:
    if not _KNOWLEDGE_DIR.exists():
        return []
    return sorted(
        (p for p in _KNOWLEDGE_DIR.iterdir()
         if p.is_file()
         and not p.name.startswith(".")          # skip the index file + dotfiles
         and p.suffix.lower() in _ALLOWED_EXTENSIONS
         and p.name.lower() not in _IGNORED_NAMES),
        key=lambda p: p.name.lower(),
    )


def _chunk(text: str) -> List[str]:
    """Split text into overlapping chunks, preferring paragraph boundaries."""
    text = text.strip()
    if not text:
        return []

    # Pack paragraphs into chunks up to _CHUNK_CHARS.
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: List[str] = []
    buf = ""
    for para in paras:
        if len(para) > _CHUNK_CHARS:
            # Flush what we have, then hard-split the oversized paragraph.
            if buf:
                chunks.append(buf)
                buf = ""
            chunks.extend(_hard_split(para))
            continue
        if buf and len(buf) + 2 + len(para) > _CHUNK_CHARS:
            chunks.append(buf)
            # carry an overlap tail into the next buffer
            buf = (buf[-_CHUNK_OVERLAP:] + "\n\n" + para) if _CHUNK_OVERLAP else para
        else:
            buf = f"{buf}\n\n{para}" if buf else para
    if buf:
        chunks.append(buf)
    return chunks


def _hard_split(text: str) -> List[str]:
    """Character-window split for a single oversized paragraph."""
    step = _CHUNK_CHARS - _CHUNK_OVERLAP
    return [text[i:i + _CHUNK_CHARS] for i in range(0, len(text), step)]


# ── index persistence ──────────────────────────────────────────────────────

def _load_index() -> dict:
    backend = _backend_id()
    if not _INDEX_FILE.exists():
        return {"model": backend, "entries": []}
    try:
        data = json.loads(_INDEX_FILE.read_text(encoding="utf-8"))
        # A model/backend change invalidates every stored vector.
        if data.get("model") != backend:
            return {"model": backend, "entries": []}
        return data
    except Exception as exc:
        logger.warning("Could not read vector index (%s) — rebuilding.", exc)
        return {"model": backend, "entries": []}


def _save_index(data: dict) -> None:
    try:
        _KNOWLEDGE_DIR.mkdir(exist_ok=True)
        _INDEX_FILE.write_text(json.dumps(data), encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not write vector index: %s", exc)


def sync(force: bool = False) -> int:
    """Bring the index in line with the folder. Returns the chunk count.

    Re-embeds only new/changed files (by mtime+size) unless *force* is set, and
    drops entries for files that were deleted. Safe to call from a background
    thread at startup. Returns 0 (and does nothing) if the model is unavailable.
    """
    if not is_available():
        return 0

    with _lock:
        index = _load_index()
        old_entries = index.get("entries", [])

        # Existing file signatures so we can reuse unchanged embeddings.
        seen: dict = {}
        for e in old_entries:
            seen.setdefault(e["file"], (e.get("mtime"), e.get("size")))

        files = _note_files()
        current_names = {p.name for p in files}

        # Decide which files need re-embedding.
        to_embed: List[Path] = []
        for p in files:
            try:
                st = p.stat()
            except OSError:
                continue
            sig = (st.st_mtime, st.st_size)
            if force or seen.get(p.name) != sig:
                to_embed.append(p)

        if not to_embed and current_names == {e["file"] for e in old_entries}:
            return len(old_entries)  # nothing changed

        # Keep entries for unchanged, still-present files.
        changed_names = {p.name for p in to_embed}
        new_entries = [
            e for e in old_entries
            if e["file"] in current_names and e["file"] not in changed_names
        ]

        # Embed the changed files.
        for p in to_embed:
            try:
                st = p.stat()
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                logger.debug("Skipping %s during sync: %s", p.name, exc)
                continue
            chunks = _chunk(text)
            if not chunks:
                continue
            vecs = _embed(chunks)
            if vecs is None:
                return len(old_entries)
            for chunk_text, vec in zip(chunks, vecs):
                new_entries.append({
                    "file": p.name,
                    "mtime": st.st_mtime,
                    "size": st.st_size,
                    "text": chunk_text,
                    "vector": [round(float(x), 6) for x in vec],
                })

        index["entries"] = new_entries
        index["model"] = _backend_id()
        _save_index(index)
        logger.info("Vector index synced: %d chunks across %d files.",
                    len(new_entries), len(current_names))
        return len(new_entries)


# ── search ─────────────────────────────────────────────────────────────────

def semantic_search(query: str, top_k: int = _DEFAULT_TOP_K,
                    min_score: float = _DEFAULT_MIN_SCORE) -> List[Hit]:
    """Return the most semantically similar chunks to *query*.

    Empty list if the model is unavailable or nothing clears *min_score*.
    """
    query = (query or "").strip()
    if not query or not is_available():
        return []

    sync()  # cheap when nothing changed; keeps results fresh

    index = _load_index()
    entries = index.get("entries", [])
    if not entries:
        return []

    try:
        import numpy as np
    except Exception:
        return []

    qvec = _embed([query])
    if qvec is None:
        return []
    qvec = np.asarray(qvec[0], dtype="float32")

    mat = np.asarray([e["vector"] for e in entries], dtype="float32")
    # Vectors are already L2-normalized, so dot product == cosine similarity.
    scores = mat @ qvec

    order = np.argsort(-scores)[:top_k]
    hits: List[Hit] = []
    for i in order:
        score = float(scores[i])
        if score < min_score:
            continue
        e = entries[int(i)]
        hits.append(Hit(file=e["file"], text=e["text"], score=score))
    return hits
