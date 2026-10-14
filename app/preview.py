"""Превью кадра: фон (кадр видео или картинка) и все видимые субтитры.

Субтитр можно перетаскивать мышью — положение уходит в стиль (pos_x/pos_y),
у центра кадра положение «прилипает». Клик мимо субтитров — «пустое место»
(завершает редактирование стиля субтитра или слова).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from .models import Style
from .render import render_item


@dataclass
class PreviewItem:
    key: tuple  # ("subs", индекс)
    style: Style
    text: str
    word_k: int | None = None
    selected: bool = False
    word_styles: dict = field(default_factory=dict)
    mark_words: set = field(default_factory=set)  # слова, стиль которых сейчас редактируется
    mark_all: bool = False  # редактируется стиль всего субтитра
    anim: object = None  # состояние эффекта появления/исчезновения (anim.AnimState)


class PreviewWidget(QWidget):
    moved = Signal(tuple, float, float)  # key, pos_x %, pos_y %
    moveFinished = Signal(tuple)
    itemClicked = Signal(tuple)
    emptyClicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.comp_w, self.comp_h = 1920, 1080
        self.items: list[PreviewItem] = []
        self.background: QImage | None = None
        self.video_frame: QImage | None = None
        self.show_safe = False
        self._bounds: dict[tuple, QRectF] = {}
        self._drag: dict | None = None
        self._snap = (False, False)
        self.setMinimumHeight(180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)

    # ------------------------------------------------------------ данные

    def set_comp_size(self, w: int, h: int):
        self.comp_w, self.comp_h = max(int(w), 1), max(int(h), 1)
        self.update()

    def set_items(self, items: list[PreviewItem]):
        self.items = items
        self.update()

    def set_background(self, img: QImage | None):
        self.background = img
        self.update()

    def set_video_frame(self, img: QImage | None):
        self.video_frame = img
        self.update()

    def frame_rect(self) -> QRectF:
        avail = QRectF(self.rect()).adjusted(6, 6, -6, -6)
        k = min(avail.width() / self.comp_w, avail.height() / self.comp_h)
        fw, fh = self.comp_w * k, self.comp_h * k
        return QRectF(avail.center().x() - fw / 2, avail.center().y() - fh / 2, fw, fh)

    # ------------------------------------------------------------ отрисовка

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.fillRect(self.rect(), QColor("#16181E"))
        frame = self.frame_rect()
        clip = QPainterPath()
        clip.addRoundedRect(frame, 8, 8)
        p.save()
        p.setClipPath(clip)
        self._paint_background(p, frame)
        self._bounds = {}
        for item in self.items:
            b = render_item(p, frame, self.comp_w, item.style, item.text, item.word_k, item.word_styles,
                            item.mark_words, item.mark_all, item.anim)
            self._bounds[item.key] = b
            if item.selected and not item.mark_all and not item.mark_words and not b.isNull():
                p.setPen(QPen(QColor(124, 92, 255, 200), 1, Qt.PenStyle.DashLine))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(b.adjusted(-4, -4, 4, 4), 4, 4)
        if self._drag:
            p.setPen(QPen(QColor(0, 229, 255, 160), 1, Qt.PenStyle.DashLine))
            if self._snap[0]:
                p.drawLine(QPointF(frame.center().x(), frame.top()), QPointF(frame.center().x(), frame.bottom()))
            if self._snap[1]:
                p.drawLine(QPointF(frame.left(), frame.center().y()), QPointF(frame.right(), frame.center().y()))
        if self.show_safe:
            p.setPen(QPen(QColor(255, 255, 255, 70), 1, Qt.PenStyle.DashLine))
            p.drawRect(frame.adjusted(frame.width() * 0.05, frame.height() * 0.05,
                                      -frame.width() * 0.05, -frame.height() * 0.05))
        p.restore()
        p.setPen(QPen(QColor("#2A2E3A"), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(frame, 8, 8)
        p.end()

    def _paint_background(self, p: QPainter, frame: QRectF):
        img = self.video_frame if self.video_frame is not None else self.background
        if img is not None and not img.isNull():
            k = max(frame.width() / img.width(), frame.height() / img.height())
            src_w, src_h = frame.width() / k, frame.height() / k
            src = QRectF((img.width() - src_w) / 2, (img.height() - src_h) / 2, src_w, src_h)
            p.drawImage(frame, img, src)
        else:
            g = QLinearGradient(frame.topLeft(), frame.bottomRight())
            g.setColorAt(0, QColor("#2B3A55"))
            g.setColorAt(0.55, QColor("#5A4E6E"))
            g.setColorAt(1, QColor("#7A5A4A"))
            p.fillRect(frame, QBrush(g))

    # ------------------------------------------------------------ мышь

    def _hit(self, pos: QPointF) -> PreviewItem | None:
        hits = [it for it in self.items if self._bounds.get(it.key, QRectF()).adjusted(-6, -6, 6, 6).contains(pos)]
        if not hits:
            return None
        sel = [it for it in hits if it.selected]
        return sel[0] if sel else hits[-1]

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        item = self._hit(e.position())
        if item is None:
            self.emptyClicked.emit()
            return
        self.itemClicked.emit(item.key)
        self._drag = {"key": item.key, "start": e.position(), "pos": (item.style.pos_x, item.style.pos_y)}
        self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, e):
        if not self._drag:
            self.setCursor(Qt.CursorShape.OpenHandCursor if self._hit(e.position()) else Qt.CursorShape.ArrowCursor)
            return
        frame = self.frame_rect()
        d = e.position() - self._drag["start"]
        x = self._drag["pos"][0] + d.x() / frame.width() * 100
        y = self._drag["pos"][1] + d.y() / frame.height() * 100
        snap_x, snap_y = abs(x - 50) < 1.2, abs(y - 50) < 1.2
        if snap_x:
            x = 50.0
        if snap_y:
            y = 50.0
        self._snap = (snap_x, snap_y)
        self.moved.emit(self._drag["key"], round(x, 1), round(y, 1))

    def mouseReleaseEvent(self, e):
        if self._drag:
            key = self._drag["key"]
            self._drag = None
            self._snap = (False, False)
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self.moveFinished.emit(key)
            self.update()
