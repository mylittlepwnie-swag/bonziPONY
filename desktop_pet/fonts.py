"""Custom font loading for speech bubbles.

Loads assets/fonts/m5x7.ttf on demand via QFontDatabase. Caches the family
name after the first successful load. If the TTF is missing, returns None
and callers should fall back to the default font.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PyQt5.QtGui import QFont, QFontDatabase

logger = logging.getLogger(__name__)

_M5X7_FAMILY: Optional[str] = None
_M5X7_TRIED: bool = False
_M5X7_PATH = Path("assets/fonts/m5x7.ttf")


def _load_m5x7_family() -> Optional[str]:
    """Load assets/fonts/m5x7.ttf into QFontDatabase; return family name."""
    global _M5X7_FAMILY, _M5X7_TRIED
    if _M5X7_TRIED:
        return _M5X7_FAMILY
    _M5X7_TRIED = True

    if not _M5X7_PATH.exists():
        logger.info("m5x7 font not found at %s — using fallback", _M5X7_PATH)
        return None

    try:
        font_id = QFontDatabase.addApplicationFont(str(_M5X7_PATH))
        if font_id < 0:
            logger.warning("Failed to register %s with QFontDatabase", _M5X7_PATH)
            return None
        families = QFontDatabase.applicationFontFamilies(font_id)
        if not families:
            logger.warning("QFontDatabase returned no families for %s", _M5X7_PATH)
            return None
        _M5X7_FAMILY = families[0]
        logger.info("Loaded pixel font family: %s", _M5X7_FAMILY)
        return _M5X7_FAMILY
    except Exception as exc:
        logger.warning("m5x7 load failed: %s: %s", type(exc).__name__, exc)
        return None


_PIXEL_POINT_SIZE = 20  # m5x7 is a pixel font — needs real size to be readable
_PIXEL_FALLBACK_POINT_SIZE = 14  # monospace fallback is denser, smaller works


def get_bubble_font(style: str, default_point_size: int = 11) -> QFont:
    """Return a QFont for speech bubbles based on config style.

    style == "m5x7" → pixel font at a large point size (readable like a
    Minecraft nametag). If the TTF isn't present, falls through to a
    monospace chain so the toggle still looks visibly different.
    style == "default" (or anything else) → Segoe UI at default_point_size.
    """
    if style == "m5x7":
        family = _load_m5x7_family()
        if family:
            font = QFont(family, _PIXEL_POINT_SIZE)
            font.setStyleStrategy(QFont.NoAntialias)  # pixel fonts are crisper without AA
            return font
        # Fallback chain. Fixedsys is the most pixel-accurate Windows font
        # but is a bitmap raster that Qt can't always substitute cleanly,
        # so fall through to Consolas / Courier New which are guaranteed
        # present and visibly different from Segoe UI.
        for fam in ("Fixedsys", "Consolas", "Lucida Console", "Courier New"):
            f = QFont(fam, _PIXEL_FALLBACK_POINT_SIZE)
            f.setBold(True)  # bold monospace reads closer to a pixel font at this size
            f.setStyleHint(QFont.TypeWriter, QFont.PreferMatch)
            f.setStyleStrategy(QFont.NoAntialias)
            if fam == "Fixedsys":
                try:
                    from PyQt5.QtGui import QFontInfo
                    info = QFontInfo(f)
                    if info.family().lower() in ("fixedsys", "fixedsys excelsior"):
                        return f
                except Exception:
                    pass
                continue
            return f
        # Last-resort: Qt's generic TypeWriter hint
        font = QFont("monospace", _PIXEL_FALLBACK_POINT_SIZE)
        font.setBold(True)
        font.setStyleHint(QFont.TypeWriter)
        font.setStyleStrategy(QFont.NoAntialias)
        return font

    font = QFont("Segoe UI", default_point_size)
    font.setStyleStrategy(QFont.PreferAntialias)
    return font


def is_pixel_style(style: str) -> bool:
    return style == "m5x7"
