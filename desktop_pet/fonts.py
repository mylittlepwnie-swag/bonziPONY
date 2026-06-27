"""Custom font loading for speech bubbles."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from PyQt5.QtGui import QFont, QFontDatabase

logger = logging.getLogger(__name__)

_M3X6_FAMILY: Optional[str] = None
_M3X6_TRIED: bool = False

def _load_m3x6_family() -> Optional[str]:
    """Load m3x6.ttf into QFontDatabase; return family name."""
    global _M3X6_FAMILY, _M3X6_TRIED
    if _M3X6_TRIED:
        return _M3X6_FAMILY
    _M3X6_TRIED = True

    # Check both potential folders just in case
    font_path = Path("fonts/m3x6.ttf")
    if not font_path.exists():
        font_path = Path("assets/fonts/m3x6.ttf")

    if not font_path.exists():
        logger.info("m3x6 font not found at %s — using fallback", font_path)
        return None

    try:
        font_id = QFontDatabase.addApplicationFont(str(font_path))
        if font_id < 0:
            logger.warning("Failed to register %s with QFontDatabase", font_path)
            return None
        families = QFontDatabase.applicationFontFamilies(font_id)
        if not families:
            logger.warning("QFontDatabase returned no families for %s", font_path)
            return None
        _M3X6_FAMILY = families[0]
        logger.info("Loaded pixel font family: %s", _M3X6_FAMILY)
        return _M3X6_FAMILY
    except Exception as exc:
        logger.warning("m3x6 load failed: %s: %s", type(exc).__name__, exc)
        return None

# Adjusted to look good with m3x6 specifically
_PIXEL_POINT_SIZE = 12  
_PIXEL_FALLBACK_POINT_SIZE = 12  

def get_bubble_font(style: str, default_point_size: int = 11) -> QFont:
    if style == "m5x7" or style == "m3x6":
        family = _load_m3x6_family()
        if family:
            font = QFont(family, _PIXEL_POINT_SIZE)
            font.setStyleStrategy(QFont.NoAntialias)  # Keep it crisp
            return font
            
        for fam in ("Fixedsys", "Consolas", "Lucida Console", "Courier New"):
            f = QFont(fam, _PIXEL_FALLBACK_POINT_SIZE)
            f.setBold(True)
            f.setStyleHint(QFont.TypeWriter, QFont.PreferMatch)
            f.setStyleStrategy(QFont.NoAntialias)
            return f

    font = QFont("Segoe UI", default_point_size)
    font.setStyleStrategy(QFont.PreferAntialias)
    return font

def is_pixel_style(style: str) -> bool:
    return style in ("m5x7", "m3x6")