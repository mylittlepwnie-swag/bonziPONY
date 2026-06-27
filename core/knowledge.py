"""Knowledge base — a personal data bank the pony can search and reference.

Drop plain-text files into the ``knowledge/`` folder and the pony can look
things up in them ("ctrl+f"), browse what topics exist, and quietly mull them
over in the background. It's the long-term reference shelf that sits alongside
``memory/`` (session summaries) and ``diary/`` (in-character journaling).

Search is deliberately dumb-but-honest: a case-insensitive substring scan over
the files, returning the matching lines with a little surrounding context and
the file they came from — exactly what a human ctrl+f would show you. No
embeddings, no external service, no API calls.

Public API:
  ensure_dir()                  — create the folder + seed a how-to README
  list_topics()                 — files in the data bank (name + size)
  search(query, max_results)    — ctrl+f across every file
  read_topic(name)              — read one file by name
  index_for_prompt(max_files)   — short topic list for prompt injection
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

KNOWLEDGE_DIR = Path(__file__).parent.parent / "knowledge"

# What counts as a readable note. Same spirit as query_tools._ALLOWED_EXTENSIONS.
_ALLOWED_EXTENSIONS = {".txt", ".md", ".markdown", ".json", ".yaml", ".yml", ".csv", ".log"}

# The seeded how-to README is for the human, not a note the pony should recall.
_IGNORED_NAMES = {"readme.txt", "readme.md", ".gitkeep"}

# Per-match context and overall result caps, so a query never floods the prompt.
_CONTEXT_CHARS = 240          # chars of surrounding context shown per hit
_MAX_RESULTS = 8              # matches returned by default
_MAX_FILE_CHARS = 8000        # cap when reading a whole file
_MAX_FILE_BYTES = 1_000_000   # skip files larger than this when searching

_SEED_README = """\
This is your pony's data bank.

Drop plain-text files in this folder (.txt or .md work best) and she can look
things up in them. Think of it as her reference shelf — notes, lists, facts,
anything you want her to be able to recall later.

How she uses it:
  - In conversation, ask her to check her notes / data bank / what she knows
    about something. She searches these files like ctrl+f and answers from
    what she finds.
  - In the background she's aware of what topics live here and may quietly
    think about them without saying anything.

Tips:
  - One topic per file keeps things tidy (groceries.txt, passwords_hints.txt,
    project_ideas.md, ...). The filename is the topic label she sees.
  - Plain text only. No need to format anything fancy.
  - This folder is yours — nothing here is shared or uploaded anywhere.
"""


def ensure_dir() -> Path:
    """Create the knowledge folder (and seed a README the first time)."""
    KNOWLEDGE_DIR.mkdir(exist_ok=True)
    readme = KNOWLEDGE_DIR / "README.txt"
    if not readme.exists():
        try:
            readme.write_text(_SEED_README, encoding="utf-8")
        except OSError as exc:
            logger.debug("Could not seed knowledge README: %s", exc)
    return KNOWLEDGE_DIR


def _note_files() -> List[Path]:
    """All readable note files in the data bank, sorted by name."""
    if not KNOWLEDGE_DIR.exists():
        return []
    files = [
        p for p in KNOWLEDGE_DIR.iterdir()
        if p.is_file()
        and not p.name.startswith(".")          # skip the vector index + dotfiles
        and p.suffix.lower() in _ALLOWED_EXTENSIONS
        and p.name.lower() not in _IGNORED_NAMES
    ]
    return sorted(files, key=lambda p: p.name.lower())


def list_topics() -> str:
    """Human/LLM-readable listing of what's in the data bank."""
    files = _note_files()
    if not files:
        return (
            "Your data bank is empty. The user can drop .txt files into the "
            "knowledge/ folder and you'll be able to search them."
        )

    lines = ["=== DATA BANK CONTENTS ==="]
    for p in files:
        try:
            size = p.stat().st_size
        except OSError:
            size = 0
        lines.append(f"  - {p.name} ({_fmt_size(size)})")
    lines.append(
        "\nSearch any of these with [QUERY:KNOWLEDGE:your search term]."
    )
    return "\n".join(lines)


def search(query: str, max_results: int = _MAX_RESULTS) -> str:
    """Search the data bank for *query*, semantic-first with keyword fallback.

    Used by the [QUERY:KNOWLEDGE:term] tag. When the embedding model is
    available this is true semantic search (matches meaning, not just literal
    words); otherwise it falls back to the case-insensitive ctrl+f scan below.
    """
    query = (query or "").strip()
    if not query:
        return list_topics()

    if not _note_files():
        return (
            "Your data bank is empty — nothing to search yet. The user can add "
            ".txt files to the knowledge/ folder."
        )

    # Try semantic search first.
    try:
        from core.knowledge_index import semantic_search
        hits = semantic_search(query, top_k=max_results)
        if hits:
            lines = [f"=== DATA BANK (most relevant to '{query}') ==="]
            for h in hits:
                snippet = h.text.strip().replace("\n", " ")
                if len(snippet) > _CONTEXT_CHARS * 2:
                    snippet = snippet[: _CONTEXT_CHARS * 2] + "..."
                lines.append(f"  [{h.file}] (match {h.score:.2f}) {snippet}")
            return "\n".join(lines)
    except Exception as exc:
        logger.debug("Semantic search failed, falling back to keyword: %s", exc)

    # Fallback: literal keyword scan.
    return _keyword_search(query, max_results)


def _keyword_search(query: str, max_results: int = _MAX_RESULTS) -> str:
    """Case-insensitive substring search across every note ("ctrl+f").

    Returns the matching lines with a little surrounding context and the file
    each came from, capped at *max_results* hits.
    """
    files = _note_files()
    if not files:
        return (
            "Your data bank is empty — nothing to search yet. The user can add "
            ".txt files to the knowledge/ folder."
        )

    needle = query.lower()
    hits: List[str] = []

    for p in files:
        try:
            if p.stat().st_size > _MAX_FILE_BYTES:
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.debug("Skipping %s during search: %s", p.name, exc)
            continue

        for line_no, line in enumerate(text.splitlines(), start=1):
            if needle in line.lower():
                hits.append(f"  [{p.name}:{line_no}] ...{_snippet(line, needle)}...")
                if len(hits) >= max_results:
                    break
        if len(hits) >= max_results:
            break

    if not hits:
        topics = ", ".join(p.name for p in files)
        return (
            f"No matches for '{query}' in the data bank.\n"
            f"Files searched: {topics}"
        )

    header = f"=== DATA BANK MATCHES for '{query}' ==="
    footer = ""
    if len(hits) >= max_results:
        footer = (
            f"\n(Showing first {max_results} matches. Narrow the search term "
            f"for fewer, or read a whole file with [QUERY:KNOWLEDGE_READ:filename].)"
        )
    return header + "\n" + "\n".join(hits) + footer


def retrieve_context_block(query: str, top_k: int = 3) -> str:
    """Relevant data-bank chunks for *query*, formatted for prompt injection.

    Returns an empty string when nothing is relevant (or semantic search is
    unavailable) so callers can cheaply skip injection. This is the
    auto-retrieval that fires each conversation turn — the SillyTavern-style
    "feed the model relevant memories without being asked" behavior.
    """
    query = (query or "").strip()
    if len(query) < 3:
        return ""
    try:
        from core.knowledge_index import semantic_search
        hits = semantic_search(query, top_k=top_k)
    except Exception as exc:
        logger.debug("retrieve_context_block failed: %s", exc)
        return ""
    if not hits:
        return ""

    parts = []
    for h in hits:
        snippet = h.text.strip()
        if len(snippet) > 500:
            snippet = snippet[:500] + "..."
        parts.append(f"- (from {h.file}) {snippet}")
    body = "\n".join(parts)
    return (
        "[FROM YOUR DATA BANK — notes you remember that may be relevant here. "
        "Use naturally if it helps; ignore if not. Don't say you looked it up.]\n"
        f"{body}\n[/DATA BANK]"
    )


def read_topic(name: str) -> str:
    """Read one note file by name (with or without extension)."""
    name = (name or "").strip().strip("\"'")
    if not name:
        return "No filename given. Use [QUERY:KNOWLEDGE] to see what's available."

    files = _note_files()
    if not files:
        return "Your data bank is empty."

    # Match on exact name, then on stem (so 'groceries' finds 'groceries.txt').
    target: Optional[Path] = None
    lname = name.lower()
    for p in files:
        if p.name.lower() == lname:
            target = p
            break
    if target is None:
        for p in files:
            if p.stem.lower() == lname:
                target = p
                break
    if target is None:
        available = ", ".join(p.name for p in files)
        return f"No file named '{name}' in the data bank. Available: {available}"

    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"Couldn't read {target.name}: {exc}"

    truncated = len(text) > _MAX_FILE_CHARS
    if truncated:
        text = text[:_MAX_FILE_CHARS]
    header = f"=== {target.name} ==="
    footer = "\n... (truncated — file continues)" if truncated else ""
    return f"{header}\n{text}{footer}"


def index_for_prompt(max_files: int = 12) -> str:
    """A one-line-ish topic index for injecting into prompts.

    Returns an empty string when the data bank is empty, so callers can cheaply
    skip injection.
    """
    files = _note_files()
    if not files:
        return ""
    names = [p.name for p in files[:max_files]]
    more = "" if len(files) <= max_files else f", +{len(files) - max_files} more"
    return ", ".join(names) + more


# ── helpers ────────────────────────────────────────────────────────────────

def _snippet(line: str, needle: str) -> str:
    """Trim a matching line down to a window around the matched term."""
    line = line.strip()
    if len(line) <= _CONTEXT_CHARS:
        return line
    idx = line.lower().find(needle)
    if idx == -1:
        return line[:_CONTEXT_CHARS]
    half = _CONTEXT_CHARS // 2
    start = max(0, idx - half)
    end = min(len(line), idx + len(needle) + half)
    return line[start:end]


def _fmt_size(b: int) -> str:
    if b < 1024:
        return f"{b} B"
    if b < 1024 ** 2:
        return f"{b / 1024:.1f} KB"
    return f"{b / (1024 ** 2):.1f} MB"
