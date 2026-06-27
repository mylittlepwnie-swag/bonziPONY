"""Separate transparent window that shows comic-style speech bubbles."""

from __future__ import annotations

import logging

from PyQt5.QtCore import Qt, QTimer, QRectF
from PyQt5.QtGui import (
    QColor,
    QFont,
    QPainter,
    QPainterPath,
    QPen,
    QFontMetrics,
)
from PyQt5.QtWidgets import QApplication, QWidget

from desktop_pet.fonts import get_bubble_font, is_pixel_style
from desktop_pet.typewriter_sound import TypewriterSound

logger = logging.getLogger(__name__)

_BUBBLE_PADDING = 12
_BUBBLE_RADIUS = 14
_POINTER_SIZE = 12
_BORDER_WIDTH = 2
_TYPING_SPEED_MS = 30  # ms per character
_DISPLAY_DURATION_MS = 5000  # how long bubble stays after typing finishes
_MAX_BUBBLE_WIDTH = 320
_MIN_BUBBLE_WIDTH = 80
# Pixel style has no bubble frame → let text breathe wider like a
# Minecraft nametag instead of being pinched to pony width.
_MAX_BUBBLE_WIDTH_PIXEL = 640
_MIN_BUBBLE_WIDTH_PIXEL = 120


class SpeechBubble(QWidget):
    """Comic-style speech bubble that appears near the sprite."""

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
        self._pointer_below = False  # True = pointer points downward (bubble above sprite)
        self._anchor_x = 0
        self._anchor_y = 0

        self._thinking = False
        self._thinking_dots = 0

        self._anchor_widget = None  # pet_window — follow its position

        self._typing_timer = QTimer(self)
        self._typing_timer.timeout.connect(self._typing_tick)

        self._thinking_timer = QTimer(self)
        self._thinking_timer.timeout.connect(self._thinking_tick)

        self._follow_timer = QTimer(self)
        self._follow_timer.setInterval(33)  # ~30 fps
        self._follow_timer.timeout.connect(self._follow_tick)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide_bubble)

        self._font_style = "default"
        self._font = get_bubble_font(self._font_style, default_point_size=11)
        self._typewriter = TypewriterSound()

    def set_font_style(self, style: str) -> None:
        """Switch bubble font between 'default' and 'm5x7'. Safe live swap."""
        if style == self._font_style:
            return
        self._font_style = style
        self._font = get_bubble_font(style, default_point_size=10)
        # Pixel style drops the frame → re-measure so the widget picks up
        # the wider nametag layout immediately.
        if self._full_text:
            self._resize_for_full_text()
            self._reposition()
        self.update()

    def _width_bounds(self) -> tuple[int, int]:
        """Return (max_width, min_width) for current font style."""
        if is_pixel_style(self._font_style):
            return _MAX_BUBBLE_WIDTH_PIXEL, _MIN_BUBBLE_WIDTH_PIXEL
        return _MAX_BUBBLE_WIDTH, _MIN_BUBBLE_WIDTH

    def set_typewriter_sound(self, enabled: bool) -> None:
        """Enable/disable the character-click sound."""
        self._typewriter.set_enabled(enabled)

    def set_anchor_widget(self, widget) -> None:
        """Set the pet window widget to follow. Bubble will track its position."""
        self._anchor_widget = widget

    def _follow_tick(self) -> None:
        """Update bubble position to follow the pony while visible."""
        if not self.isVisible() or self._anchor_widget is None:
            return
        w = self._anchor_widget
        self._anchor_x = w.x() + w.width() // 2
        self._anchor_y = w.y()
        self._reposition()

    def show_thinking(self, anchor_x: int, anchor_y: int) -> None:
        """Show an animated '...' thinking bubble."""
        self._hide_timer.stop()
        self._typing_timer.stop()
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
        """Cycle dots: . .. ... . .. ..."""
        self._thinking_dots = (self._thinking_dots % 3) + 1
        self._visible_text = "." * self._thinking_dots
        self.update()

    def show_text(self, text: str, anchor_x: int, anchor_y: int, sprite_h: int = 0) -> None:
        """Show a speech bubble with typing animation near the given anchor point."""
        self._thinking = False
        self._thinking_timer.stop()
        self._hide_timer.stop()
        self._typing_timer.stop()

        self._full_text = text.strip()
        self._visible_text = ""
        self._char_index = 0
        self._anchor_x = anchor_x
        self._anchor_y = anchor_y

        # Pre-size to full text so the bubble is positioned correctly from the start
        self._resize_for_full_text()
        self._reposition()
        self.show()
        self.raise_()
        self._typing_timer.start(_TYPING_SPEED_MS)
        if self._anchor_widget:
            self._follow_timer.start()

    def hide_bubble(self) -> None:
        """Hide the speech bubble."""
        self._thinking = False
        self._thinking_timer.stop()
        self._typing_timer.stop()
        self._hide_timer.stop()
        self._follow_timer.stop()
        self.hide()

    def paintEvent(self, event) -> None:
        if not self._visible_text:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        pixel_mode = is_pixel_style(self._font_style)
        max_w, min_w = self._width_bounds()

        if pixel_mode:
            # Pixel nametag: widget is tight to text (see _compute_widget_size).
            # No bubble frame → use the full widget area for the text rect.
            pad = 4
            bubble_x = 0
            bubble_y = 0
            bubble_w = self.width()
            bubble_h = self.height()
        else:
            fm = QFontMetrics(self._font)
            # Use FULL text for bubble dimensions — keeps bubble stable during typing
            measure_text = self._full_text or self._visible_text
            text_rect = fm.boundingRect(
                0, 0, max_w - 2 * _BUBBLE_PADDING, 2000,
                Qt.TextWordWrap, measure_text,
            )
            text_w = max(text_rect.width(), min_w) + 2 * _BUBBLE_PADDING
            text_h = text_rect.height() + 2 * _BUBBLE_PADDING

            bubble_x = _BORDER_WIDTH
            bubble_y = _POINTER_SIZE if not self._pointer_below else _BORDER_WIDTH
            bubble_w = text_w
            bubble_h = text_h

        if not pixel_mode:
            # Draw bubble background
            path = QPainterPath()
            bubble_rect = QRectF(bubble_x, bubble_y, bubble_w, bubble_h)
            path.addRoundedRect(bubble_rect, _BUBBLE_RADIUS, _BUBBLE_RADIUS)

            painter.setPen(QPen(QColor(60, 60, 60), _BORDER_WIDTH))
            painter.setBrush(QColor(255, 255, 255, 240))
            painter.drawPath(path)

            # Draw pointer triangle
            pointer_path = QPainterPath()
            ptr_cx = bubble_w // 2  # center of bubble
            if not self._pointer_below:
                py = bubble_y
                pointer_path.moveTo(ptr_cx - 6, py)
                pointer_path.lineTo(ptr_cx, py - _POINTER_SIZE + 2)
                pointer_path.lineTo(ptr_cx + 6, py)
                pointer_path.closeSubpath()
            else:
                py = bubble_y + bubble_h
                pointer_path.moveTo(ptr_cx - 6, py)
                pointer_path.lineTo(ptr_cx, py + _POINTER_SIZE - 2)
                pointer_path.lineTo(ptr_cx + 6, py)
                pointer_path.closeSubpath()

            painter.setBrush(QColor(255, 255, 255, 240))
            painter.setPen(QPen(QColor(60, 60, 60), _BORDER_WIDTH))
            painter.drawPath(pointer_path)

            # Fill over the pointer base to merge with bubble
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(255, 255, 255, 240))
            if not self._pointer_below:
                painter.drawRect(ptr_cx - 5, int(bubble_y), 10, 4)
            else:
                painter.drawRect(ptr_cx - 5, int(py) - 4, 10, 4)

        # Draw text with outline halo (pixel style) or subtle shadow (default)
        painter.setFont(self._font)
        if pixel_mode:
            inner_pad = 4
            text_draw_rect = QRectF(
                bubble_x + inner_pad,
                bubble_y + inner_pad,
                bubble_w - 2 * inner_pad,
                bubble_h - 2 * inner_pad,
            )
        else:
            text_draw_rect = QRectF(
                bubble_x + _BUBBLE_PADDING,
                bubble_y + _BUBBLE_PADDING,
                bubble_w - 2 * _BUBBLE_PADDING,
                bubble_h - 2 * _BUBBLE_PADDING,
            )

        if pixel_mode:
            # 2px black halo (sized for the bigger pixel font) then crisp
            # white main text. Bigger halo at bigger size keeps it readable
            # over busy desktop content without a bubble frame.
            halo = QColor(0, 0, 0)
            painter.setPen(halo)
            for dx in (-2, -1, 0, 1, 2):
                for dy in (-2, -1, 0, 1, 2):
                    if dx == 0 and dy == 0:
                        continue
                    offset_rect = text_draw_rect.translated(dx, dy)
                    painter.drawText(offset_rect,
                                     Qt.TextWordWrap | Qt.AlignHCenter,
                                     self._visible_text)
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(text_draw_rect,
                             Qt.TextWordWrap | Qt.AlignHCenter,
                             self._visible_text)
        else:
            # Default: soft 1px drop shadow + dark text
            painter.setPen(QColor(0, 0, 0, 80))
            painter.drawText(text_draw_rect.translated(1, 1),
                             Qt.TextWordWrap, self._visible_text)
            painter.setPen(QColor(30, 30, 30))
            painter.drawText(text_draw_rect, Qt.TextWordWrap, self._visible_text)

        painter.end()

    def _typing_tick(self) -> None:
        """Reveal one more character."""
        if self._char_index < len(self._full_text):
            ch = self._full_text[self._char_index]
            self._char_index += 1
            self._visible_text = self._full_text[: self._char_index]
            # Skip click on whitespace — sounds cleaner that way
            if not ch.isspace():
                self._typewriter.play()
            self.update()  # repaint only — widget stays at full-text size
        else:
            self._typing_timer.stop()
            # Auto-hide after display duration
            display_ms = max(_DISPLAY_DURATION_MS, len(self._full_text) * 60)
            self._hide_timer.start(display_ms)

    def _compute_widget_size(self, text: str) -> tuple[int, int]:
        """Compute the widget (w, h) needed to hold *text* at current font/style.

        Pixel mode has no pointer triangle and no border, so the pointer/border
        allowance collapses. That stops a fat dead margin from sitting above
        the nametag text when there is no bubble frame.
        """
        max_w, min_w = self._width_bounds()
        pixel_mode = is_pixel_style(self._font_style)
        fm = QFontMetrics(self._font)
        text_rect = fm.boundingRect(
            0, 0, max_w - 2 * _BUBBLE_PADDING, 2000,
            Qt.TextWordWrap, text or " ",
        )
        if pixel_mode:
            # Pixel nametag: just enough room for text + 1px halo + tiny pad.
            # No bubble frame → no pointer, no border.
            pad = 4  # halo + breathing room
            w = max(text_rect.width(), min_w) + 2 * pad
            h = text_rect.height() + 2 * pad
        else:
            w = max(text_rect.width(), min_w) + 2 * _BUBBLE_PADDING + 2 * _BORDER_WIDTH
            h = text_rect.height() + 2 * _BUBBLE_PADDING + _POINTER_SIZE + 2 * _BORDER_WIDTH
        return int(w), int(h)

    def _resize_to_text(self) -> None:
        """Resize the widget to fit the current visible text and reposition."""
        w, h = self._compute_widget_size(self._visible_text)
        self.setFixedSize(w, h)
        self._reposition()

    def _resize_for_full_text(self) -> None:
        """Pre-size widget to full text dimensions for correct initial positioning."""
        w, h = self._compute_widget_size(self._full_text)
        self.setFixedSize(w, h)

    def _reposition(self) -> None:
        """Position the bubble ABOVE the sprite using stored anchor coordinates."""
        w = self.width()
        h = self.height()
        gap = 10  # pixels between bubble and sprite

        # Always above the sprite, pointer points down
        self._pointer_below = True
        bx = self._anchor_x - w // 2
        by = self._anchor_y - h - gap

        # Clamp to screen edges but never flip below
        from PyQt5.QtCore import QPoint
        screen = QApplication.screenAt(QPoint(self._anchor_x, self._anchor_y))
        if screen is None:
            screen = QApplication.primaryScreen()
        if screen:
            geom = screen.availableGeometry()
            by = max(geom.top(), by)
            bx = max(geom.left(), min(bx, geom.right() - w))

        self.move(bx, by)
