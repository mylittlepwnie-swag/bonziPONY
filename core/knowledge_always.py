"""Always-on knowledge — files injected into EVERY system prompt, verbatim.

Unlike ``knowledge/`` (the data bank the pony searches on demand), everything
in ``knowledge_always/`` is read fresh each time the system prompt is built and
pasted in whole. Use it for lore, standing facts, or past conversations the
pony should never be without.

Each file becomes one tagged block, named after the file:

    knowledge_always/lore.txt   →   <lore>
                                    ...file contents...
                                    </lore>

Optionally, start a file with a ``description:`` line. That line is pulled out
and placed right after the closing tag, telling the model what the block is:

    description: These are past conversations with the user of relevance.

Public API:
  ensure_dir()        — create the folder + seed a how-to README
  block_for_prompt()  — all files as tagged blocks (empty string if none)
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)

ALWAYS_DIR = Path(__file__).parent.parent / "knowledge_always"

_ALLOWED_EXTENSIONS = {".txt", ".md", ".markdown", ".json", ".yaml", ".yml", ".csv", ".log"}
_IGNORED_NAMES = {"readme.txt", "readme.md", ".gitkeep"}

# Safety net so one giant file can't silently blow the context window.
# Files past this total are skipped (with a warning), not truncated mid-text.
_MAX_TOTAL_CHARS = 80_000

_DESCRIPTION_RE = re.compile(r"^\s*description\s*:\s*(.*)$", re.I)

_SEED_README = """\
Everything in this folder is ALWAYS given to your pony — every conversation,
every autonomous thought. No searching, no lookup: it's pasted straight into
her context each time.

Each file is wrapped in a tag named after the file:

    lore.txt  ->  <lore>
                  ...what you wrote...
                  </lore>

Optional: make the first line of a file a description, and it'll be placed
after the block so she knows what it is:

    description: These are past conversations with the user of relevance.

Tips:
  - Keep it lean. This costs tokens on every single request. For big
    reference material she only needs sometimes, use the knowledge/ folder.
  - Filenames become tag names, so keep them simple (lore.txt, rules.md,
    past_convos.txt).
  - Edits take effect on the next message — no restart needed.
"""


def ensure_dir() -> Path:
    """Create the knowledge_always folder (and seed a README the first time)."""
    ALWAYS_DIR.mkdir(exist_ok=True)
    readme = ALWAYS_DIR / "README.txt"
    if not readme.exists():
        try:
            readme.write_text(_SEED_README, encoding="utf-8")
        except OSError as exc:
            logger.debug("Could not seed knowledge_always README: %s", exc)
    return ALWAYS_DIR


def _note_files() -> List[Path]:
    """All readable files in the folder, sorted by name."""
    if not ALWAYS_DIR.exists():
        return []
    files = [
        p for p in ALWAYS_DIR.iterdir()
        if p.is_file()
        and not p.name.startswith(".")
        and p.suffix.lower() in _ALLOWED_EXTENSIONS
        and p.name.lower() not in _IGNORED_NAMES
    ]
    return sorted(files, key=lambda p: p.name.lower())


def _tag_name(path: Path) -> str:
    """Filename stem → safe tag name ('Past Convos.txt' → 'past_convos')."""
    tag = re.sub(r"[^a-z0-9_-]+", "_", path.stem.lower()).strip("_-")
    return tag or "knowledge"


def _format_file(path: Path) -> str:
    """Read one file and wrap it as <tag>...</tag> (+ description line)."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError as exc:
        logger.debug("Could not read %s: %s", path.name, exc)
        return ""
    if not text:
        return ""

    description = ""
    first, _, rest = text.partition("\n")
    m = _DESCRIPTION_RE.match(first)
    if m:
        description = m.group(1).strip()
        text = rest.strip()
        if not text:
            return ""

    tag = _tag_name(path)
    block = f"<{tag}>\n{text}\n</{tag}>"
    if description:
        block += f"\n{description}"
    return block


def block_for_prompt() -> str:
    """Every file in knowledge_always/ as tagged blocks, ready to append."""
    blocks: List[str] = []
    total = 0
    for path in _note_files():
        block = _format_file(path)
        if not block:
            continue
        if total + len(block) > _MAX_TOTAL_CHARS:
            logger.warning(
                "knowledge_always: skipping %s — total would exceed %d chars",
                path.name, _MAX_TOTAL_CHARS,
            )
            continue
        blocks.append(block)
        total += len(block)
    return "\n\n".join(blocks)
