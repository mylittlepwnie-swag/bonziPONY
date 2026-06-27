"""Separate transparent window that shows floating text with a drop shadow and fade out."""

from __future__ import annotations

import logging

from PyQt5.QtCore import Qt, QTimer, QRectF, QPropertyAnimation
from PyQt5.QtGui import QColor, QFontMetrics, QPainter
from PyQt5.QtWidgets import QApplication, QWidget

# Use upstream's new font and sound engines
from desktop_pet.fonts import get_bubble_font, is_pixel_style
from desktop_pet.typewriter_sound import TypewriterSound

logger = logging.getLogger(__name__)

_BUBBLE_PADDING = 8
_TYPING_SPEED_MS = 30  # ms per character
_DISPLAY_DURATION_MS = 5000  # how long text stays after typing finishes

# Using upstream's wider "pixel nametag" bounds so text breathes properly
_MAX_BUBBLE_WIDTH_PIXEL = 640
_MIN_BUBBLE_WIDTH_PIXEL = 120


class SpeechBubble(QWidget):
    """Floating text that appears near the sprite with typing animation."""

    def __init__(self) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowTransparentForInput
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)

        self._full_text = ""
        self._visible_text = ""
        self._char_index = 0
        self._anchor_x = 0
        self._anchor_y = 0
        self._thinking = False
        self._thinking_dots = 0
        self._anchor_widget = None

        self._typing_timer = QTimer(self)
        self._typing_timer.timeout.connect(self._typing_tick)

        self._thinking_timer = QTimer(self)
        self._thinking_timer.timeout.connect(self._thinking_tick)

        self._follow_timer = QTimer(self)
        self._follow_timer.setInterval(33)
        self._follow_timer.timeout.connect(self._follow_tick)

        # Triggers fade out instead of hiding instantly
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._start_fade_out)

        # Fade Animation
        self._fade_anim = QPropertyAnimation(self, b"windowOpacity")
        self._fade_anim.setDuration(700)
        self._fade_anim.setStartValue(1.0)
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.finished.connect(self._on_fade_finished)

        # Force the style to look for your m3x6 font from the fonts.py file
        self._font_style = "m5x7" 
        self._font = get_bubble_font(self._font_style, default_point_size=12)
        
        # Upstream's built-in sound generator (no .wav file needed!)
        self._typewriter = TypewriterSound()

    def set_font_style(self, style: str) -> None:
        """Allow upstream menu to toggle, but we already customized it."""
        pass

    def set_typewriter_sound(self, enabled: bool) -> None:
        self._typewriter.set_enabled(enabled)

    def set_anchor_widget(self, widget) -> None:
        self._anchor_widget = widget

    def _follow_tick(self) -> None:
        if not self.isVisible() or self._anchor_widget is None:
            return
        w = self._anchor_widget
        self._anchor_x = w.x() + w.width() // 2
        self._anchor_y = w.y()
        self._reposition()

    def show_thinking(self, anchor_x: int, anchor_y: int) -> None:
        self._reset_visibility()
        self._thinking = True
        self._thinking_dots = 1
        self._anchor_x = anchor_x
        self._anchor_y = anchor_y
        self._full_text = "..."
        self._visible_text = "."
        self._resize_for_full_text()
        self._reposition()
        self.show()
        self.raise_()
        self._thinking_timer.start(400)
        if self._anchor_widget:
            self._follow_timer.start()

    def _thinking_tick(self) -> None:
        self._thinking_dots = (self._thinking_dots % 3) + 1
        self._visible_text = "." * self._thinking_dots
        self.update()

    def show_text(self, text: str, anchor_x: int, anchor_y: int, sprite_h: int = 0) -> None:
        self._reset_visibility()
        self._full_text = text.strip()
        self._visible_text = ""
        self._char_index = 0
        self._anchor_x = anchor_x
        self._anchor_y = anchor_y

        self._resize_for_full_text()
        self._reposition()
        self.show()
        self.raise_()
        self._typing_timer.start(_TYPING_SPEED_MS)
        if self._anchor_widget:
            self._follow_timer.start()

    def _reset_visibility(self):
        self._thinking = False
        self._thinking_timer.stop()
        self._hide_timer.stop()
        self._typing_timer.stop()
        self._fade_anim.stop()
        self.setWindowOpacity(1.0)

    def _start_fade_out(self):
        self._fade_anim.start()

    def _on_fade_finished(self):
        self.hide()
        self.setWindowOpacity(1.0)

    def hide_bubble(self) -> None:
        self._reset_visibility()
        self._follow_timer.stop()
        self.hide()

    def paintEvent(self, event) -> None:
        if not self._visible_text:
            return

        painter = QPainter(self)
        painter.setFont(self._font)

        # Use full widget bounds minus padding for text
        text_draw_rect = QRectF(
            _BUBBLE_PADDING,
            _BUBBLE_PADDING,
            self.width() - 2 * _BUBBLE_PADDING,
            self.height() - 2 * _BUBBLE_PADDING,
        )

        # 1. Pleasant Drop Shadow (offset by 2px right, 2px down)
        shadow_rect = text_draw_rect.translated(2, 2)
        painter.setPen(QColor(0, 0, 0, 160)) # Smooth dark shadow
        painter.drawText(shadow_rect, Qt.TextWordWrap | Qt.AlignHCenter, self._visible_text)

        # 2. White main text
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(text_draw_rect, Qt.TextWordWrap | Qt.AlignHCenter, self._visible_text)

        painter.end()

    def _typing_tick(self) -> None:
        if self._char_index < len(self._full_text):
            ch = self._full_text[self._char_index]
            self._char_index += 1
            self._visible_text = self._full_text[: self._char_index]
            
            # Use upstream's synth sound!
            if not ch.isspace():
                self._typewriter.play()
                
            self.update() 
        else:
            self._typing_timer.stop()
            display_ms = max(_DISPLAY_DURATION_MS, len(self._full_text) * 60)
            self._hide_timer.start(display_ms)

    def _compute_widget_size(self, text: str) -> tuple[int, int]:
        """Upstream v3.5 Math - Perfectly calculates bounding box so text isn't scuffed"""
        fm = QFontMetrics(self._font)
        
        text_rect = fm.boundingRect(
            0, 0, _MAX_BUBBLE_WIDTH_PIXEL - 2 * _BUBBLE_PADDING, 2000,
            Qt.TextWordWrap | Qt.AlignHCenter, text or " ",
        )
        
        # Add padding + extra room for the drop shadow (+4) so it doesn't clip
        w = max(text_rect.width(), _MIN_BUBBLE_WIDTH_PIXEL) + 2 * _BUBBLE_PADDING + 4
        h = text_rect.height() + 2 * _BUBBLE_PADDING + 4
        return int(w), int(h)

    def _resize_to_text(self) -> None:
        w, h = self._compute_widget_size(self._visible_text)
        self.setFixedSize(w, h)
        self._reposition()

    def _resize_for_full_text(self) -> None:
        w, h = self._compute_widget_size(self._full_text)
        self.setFixedSize(w, h)

    def _reposition(self) -> None:
        w = self.width()
        h = self.height()
        gap = 10 

        bx = self._anchor_x - w // 2
        by = self._anchor_y - h - gap

        from PyQt5.QtCore import QPoint
        screen = QApplication.screenAt(QPoint(self._anchor_x, self._anchor_y))
        if screen is None:
            screen = QApplication.primaryScreen()
        if screen:
            geom = screen.availableGeometry()
            by = max(geom.top(), by)
            bx = max(geom.left(), min(bx, geom.right() - w))

        self.move(int(bx), int(by))