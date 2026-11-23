# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
"""Shows a snapshot of the game window and lets the user drag a box on it."""

import numpy as np
from PySide6.QtCore import QPoint, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from retrobard.config import NormRect


def frame_to_image(frame: np.ndarray) -> QImage:
    """Converts a BGRA (or BGR) frame to a QImage that owns its own pixels."""
    height, width = frame.shape[:2]
    if frame.shape[2] == 3:
        frame = np.dstack([frame, np.full((height, width), 255, dtype=np.uint8)])
    data = np.ascontiguousarray(frame)
    # BGRA bytes are what Qt calls RGB32 on little-endian machines; alpha is ignored
    return QImage(data.data, width, height, width * 4, QImage.Format.Format_RGB32).copy()


class RegionSelector(QWidget):
    """Displays an image scaled to fit and reports a dragged rectangle in image pixels."""

    region_selected = Signal(int, int, int, int)  # x, y, width, height in image pixels

    def __init__(self) -> None:
        super().__init__()
        self._image: QImage | None = None
        self._region: NormRect | None = None
        self._drag_start: QPoint | None = None
        self._drag_now: QPoint | None = None
        self.setMinimumSize(320, 240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.CrossCursor)

    def set_frame(self, frame: np.ndarray | None) -> None:
        self._image = None if frame is None else frame_to_image(frame)
        self.update()

    def set_region(self, region: NormRect | None) -> None:
        self._region = region
        self.update()

    def has_image(self) -> bool:
        return self._image is not None

    # ------------------------------------------------------------------- geometry

    def _image_rect(self) -> QRectF:
        """Where the image is drawn inside the widget (aspect ratio kept, centred)."""
        if self._image is None:
            return QRectF()
        scale = min(self.width() / self._image.width(), self.height() / self._image.height())
        w, h = self._image.width() * scale, self._image.height() * scale
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def _to_image_pixels(self, rect: QRect) -> tuple[int, int, int, int] | None:
        area = self._image_rect()
        if self._image is None or area.isEmpty():
            return None
        clipped = QRectF(rect).intersected(area)
        if clipped.width() < 4 or clipped.height() < 4:
            return None
        scale = self._image.width() / area.width()
        return (
            round((clipped.x() - area.x()) * scale),
            round((clipped.y() - area.y()) * scale),
            round(clipped.width() * scale),
            round(clipped.height() * scale),
        )

    # ---------------------------------------------------------------------- mouse

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._image is not None:
            self._drag_start = self._drag_now = event.position().toPoint()
            self.update()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_start is not None:
            self._drag_now = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton or self._drag_start is None:
            return
        rect = QRect(self._drag_start, event.position().toPoint()).normalized()
        self._drag_start = self._drag_now = None
        box = self._to_image_pixels(rect)
        if box is not None:
            self.region_selected.emit(*box)
        self.update()

    # ---------------------------------------------------------------------- paint

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(24, 24, 28))
        if self._image is None:
            painter.setPen(QColor(170, 170, 170))
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "No picture of the game yet.\nLink the game window and keep it open.",
            )
            return

        area = self._image_rect()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawImage(area, self._image)

        if self._region is not None and self._drag_start is None:
            saved = QRectF(
                area.x() + self._region.x * area.width(),
                area.y() + self._region.y * area.height(),
                self._region.w * area.width(),
                self._region.h * area.height(),
            )
            painter.setPen(QPen(QColor(0, 220, 120), 2))
            painter.drawRect(saved)

        if self._drag_start is not None and self._drag_now is not None:
            painter.setPen(QPen(QColor(255, 200, 0), 2, Qt.PenStyle.DashLine))
            painter.drawRect(QRect(self._drag_start, self._drag_now).normalized())
