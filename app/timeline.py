"""Таймлайн: дорожки «Текст» и «Субтитры», волна звука, курсор воспроизведения.

Блоки двигаются мышью и тянутся за края (прилипают к курсору и соседям),
Ctrl+колесо — масштаб, колесо — прокрутка, клик по шкале — перемотка,
двойной клик по пустому месту — новый блок, S — разрезать по курсору.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QAction, QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QHBoxLayout, QMenu, QScrollBar, QVBoxLayout, QWidget

from .models import Subtitle, merge_subtitles, split_subtitle
from .theme import ACCENT, ACCENT_2, BORDER, MUTED, SURFACE_2, SURFACE_3, TEXT, button, label

LABEL_W = 92
RULER_H = 24
TRACK_H = 34
WAVE_H = 44
EDGE = 6
TRACK_NAMES = {"subs": "Субтитры"}
TRACK_COLORS = {"subs": QColor(ACCENT)}
MARK = QColor("#FFD84D")  # субтитр, стиль которого сейчас редактируется


def tc(t: float) -> str:
    m, s = divmod(max(t, 0.0), 60)
    return f"{int(m):02d}:{s:04.1f}"


class TimelineCanvas(QWidget):
    seekRequested = Signal(float)
    selected = Signal(str, int)
    edited = Signal(str)  # дорожка изменена (для отмены и автосохранения)
    addRequested = Signal(str, float)
    blockDoubleClicked = Signal(str, int)  # двойной клик по субтитру — редактировать его стиль
    resetStyleRequested = Signal(str, int)
    emptyClicked = Signal()  # клик мимо субтитров

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tracks: dict[str, list] = {"subs": []}
        self.order = ["subs"]
        self.active = "subs"
        self.sel = -1
        self.style_edit = -1  # субтитр, стиль которого редактируется
        self.playhead = 0.0
        self.duration = 30.0
        self.pps = 60.0  # пикселей в секунду
        self.offset = 0.0  # сек слева
        self.wave = None  # (пики, шаг, offset, rate)
        self._drag = None
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setMinimumHeight(RULER_H + TRACK_H + WAVE_H + 6)

    # ------------------------------------------------------------ геометрия

    def x_of(self, t: float) -> float:
        return LABEL_W + (t - self.offset) * self.pps

    def t_of(self, x: float) -> float:
        return self.offset + (x - LABEL_W) / self.pps

    def track_rect(self, name: str) -> QRectF:
        i = self.order.index(name)
        return QRectF(LABEL_W, RULER_H + i * TRACK_H, self.width() - LABEL_W, TRACK_H)

    def block_rect(self, name: str, s: Subtitle) -> QRectF:
        tr = self.track_rect(name)
        x0, x1 = self.x_of(s.start), self.x_of(s.end)
        return QRectF(x0, tr.top() + 4, max(x1 - x0, 3), tr.height() - 8)

    def total(self) -> float:
        ends = [s.end for tr in self.tracks.values() for s in tr]
        return max([self.duration] + ends) + 2

    def visible_span(self) -> float:
        return max(self.width() - LABEL_W, 1) / self.pps

    # ------------------------------------------------------------ отрисовка

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor("#121419"))
        small = QFont(self.font())
        small.setPointSizeF(small.pointSizeF() * 0.82)
        p.setFont(small)
        fm = QFontMetrics(small)

        # шкала
        p.fillRect(QRectF(0, 0, self.width(), RULER_H), QColor(SURFACE_2))
        span = self.visible_span()
        step = next((v for v in (0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300) if span / v <= 14), 600)
        t = int(self.offset / step) * step
        while t <= self.offset + span + step:
            x = self.x_of(t)
            if x >= LABEL_W:
                p.setPen(QColor("#3A3F4D"))
                p.drawLine(QPointF(x, RULER_H - 6), QPointF(x, self.height()))
                p.setPen(QColor(MUTED))
                p.drawText(QPointF(x + 3, RULER_H - 8), tc(t)[:-2] if step >= 1 else tc(t))
            t += step

        # дорожки
        for name in self.order:
            tr = self.track_rect(name)
            active = name == self.active
            p.fillRect(QRectF(0, tr.top(), self.width(), tr.height()), QColor(22, 24, 30) if active else QColor(18, 20, 25))
            p.setPen(QColor(TEXT if active else MUTED))
            p.drawText(QRectF(10, tr.top(), LABEL_W - 14, tr.height()),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, TRACK_NAMES[name])
            p.setPen(QPen(QColor(BORDER), 1))
            p.drawLine(QPointF(0, tr.bottom()), QPointF(self.width(), tr.bottom()))
            p.save()
            p.setClipRect(QRectF(LABEL_W, tr.top(), self.width() - LABEL_W, tr.height()))
            base = TRACK_COLORS[name]
            for i, s in enumerate(self.tracks.get(name, [])):
                r = self.block_rect(name, s)
                if r.right() < LABEL_W or r.left() > self.width():
                    continue
                is_sel = active and i == self.sel
                editing = i == self.style_edit
                c = QColor(ACCENT_2) if is_sel else QColor(base)
                c.setAlpha(235 if is_sel else (170 if active else 90))
                if editing:
                    p.setPen(QPen(MARK, 2.2))
                else:
                    p.setPen(QPen(QColor("white"), 1.2) if is_sel else Qt.PenStyle.NoPen)
                p.setBrush(c)
                p.drawRoundedRect(r, 5, 5)
                if r.width() > 24:
                    p.setPen(QColor("white"))
                    star = "★ " if s.has_own_style() else ""
                    text = fm.elidedText(star + (s.text.replace("\n", " ") or "…"), Qt.TextElideMode.ElideRight,
                                         int(r.width() - 10))
                    p.drawText(r.adjusted(6, 0, -4, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
            p.restore()

        # волна звука
        wy = RULER_H + len(self.order) * TRACK_H
        wave_rect = QRectF(LABEL_W, wy + 2, self.width() - LABEL_W, WAVE_H - 4)
        p.setPen(QColor(MUTED))
        p.drawText(QRectF(10, wy, LABEL_W - 14, WAVE_H), Qt.AlignmentFlag.AlignVCenter, "Звук")
        if self.wave is not None:
            peaks, step_s, w_off, rate = self.wave
            p.setPen(QPen(QColor(79, 140, 255, 150), 1))
            mid = wave_rect.center().y()
            half = wave_rect.height() / 2
            for px in range(int(wave_rect.left()), int(wave_rect.right()), 2):
                t0 = self.t_of(px)
                i0 = int(((t0 - w_off) / rate) / step_s)
                i1 = int(((t0 + 2 / self.pps - w_off) / rate) / step_s) + 1
                if i1 <= 0 or i0 >= len(peaks):
                    continue
                v = float(peaks[max(i0, 0):max(min(i1, len(peaks)), i0 + 1)].max()) if i1 > i0 else 0
                p.drawLine(QPointF(px, mid - v * half), QPointF(px, mid + v * half))
        else:
            p.setPen(QColor("#3A3F4D"))
            p.drawText(wave_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                       "  откройте видео, чтобы увидеть звук")

        # курсор
        x = self.x_of(self.playhead)
        if x >= LABEL_W:
            p.setPen(QPen(QColor("#FF4D6D"), 1.5))
            p.drawLine(QPointF(x, 0), QPointF(x, self.height()))
            head = QPainterPath()
            head.moveTo(x - 5, 0)
            head.lineTo(x + 5, 0)
            head.lineTo(x, 7)
            head.closeSubpath()
            p.fillPath(head, QColor("#FF4D6D"))
        p.fillRect(QRectF(LABEL_W - 1, 0, 1, self.height()), QColor(BORDER))
        p.end()

    # ------------------------------------------------------------ мышь

    def _block_at(self, pos: QPointF):
        for name in self.order:
            if not self.track_rect(name).contains(pos):
                continue
            for i, s in enumerate(self.tracks.get(name, [])):
                r = self.block_rect(name, s).adjusted(-2, 0, 2, 0)
                if r.contains(pos):
                    if pos.x() <= r.left() + EDGE:
                        return name, i, "start"
                    if pos.x() >= r.right() - EDGE:
                        return name, i, "end"
                    return name, i, "move"
            return name, -1, None
        return None, -1, None

    def _snap(self, t: float, name: str, skip: int) -> float:
        cands = [self.playhead] + [v for i, s in enumerate(self.tracks[name]) if i != skip for v in (s.start, s.end)]
        best = min(cands, key=lambda c: abs(c - t), default=t)
        return best if abs(best - t) * self.pps < 8 else t

    def mousePressEvent(self, e):
        pos = e.position()
        if pos.x() < LABEL_W:
            return
        if pos.y() < RULER_H or e.button() != Qt.MouseButton.LeftButton:
            if e.button() == Qt.MouseButton.LeftButton:
                self._drag = {"mode": "seek"}
                self.seekRequested.emit(max(self.t_of(pos.x()), 0.0))
            return
        name, i, part = self._block_at(pos)
        if name is None or i < 0:
            # пустое место: перемотка и завершение редактирования стиля
            self.emptyClicked.emit()
            self._drag = {"mode": "seek"}
            self.seekRequested.emit(max(self.t_of(pos.x()), 0.0))
            return
        self.active, self.sel = name, i
        self.selected.emit(name, i)
        s = self.tracks[name][i]
        self._drag = {"mode": part, "name": name, "i": i, "x0": pos.x(), "s0": s.start, "e0": s.end, "moved": False}
        self.update()

    def mouseMoveEvent(self, e):
        pos = e.position()
        d = self._drag
        if not d:
            _n, _i, part = self._block_at(pos)
            self.setCursor(Qt.CursorShape.SizeHorCursor if part in ("start", "end")
                           else Qt.CursorShape.OpenHandCursor if part == "move" else Qt.CursorShape.ArrowCursor)
            return
        if d["mode"] == "seek":
            self.seekRequested.emit(max(self.t_of(pos.x()), 0.0))
            return
        dt = (pos.x() - d["x0"]) / self.pps
        s = self.tracks[d["name"]][d["i"]]
        if d["mode"] == "move":
            start = max(0.0, d["s0"] + dt)
            snapped = self._snap(start, d["name"], d["i"])
            if snapped == start:
                end_snap = self._snap(start + (d["e0"] - d["s0"]), d["name"], d["i"])
                start = end_snap - (d["e0"] - d["s0"])
            else:
                start = snapped
            s.retime(max(start, 0.0), max(start, 0.0) + (d["e0"] - d["s0"]))
        elif d["mode"] == "start":
            start = min(self._snap(max(0.0, d["s0"] + dt), d["name"], d["i"]), d["e0"] - 0.1)
            s.retime(start, d["e0"])
        elif d["mode"] == "end":
            end = max(self._snap(d["e0"] + dt, d["name"], d["i"]), d["s0"] + 0.1)
            s.retime(d["s0"], end)
        d["moved"] = True
        self.selected.emit(d["name"], d["i"])  # обновить редактор времени
        self.update()

    def mouseReleaseEvent(self, e):
        d = self._drag
        self._drag = None
        if d and d.get("moved"):
            self.edited.emit(d["name"])

    def mouseDoubleClickEvent(self, e):
        name, i, _ = self._block_at(e.position())
        if name and i >= 0:
            self._drag = None
            self.blockDoubleClicked.emit(name, i)
        elif name:
            self.addRequested.emit(name, max(self.t_of(e.position().x()), 0.0))

    def wheelEvent(self, e):
        delta = e.angleDelta().y() or e.angleDelta().x()
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            anchor_t = self.t_of(e.position().x())
            self.pps = min(max(self.pps * (1.15 if delta > 0 else 1 / 1.15), 4), 600)
            self.offset = max(0.0, anchor_t - (e.position().x() - LABEL_W) / self.pps)
        else:
            self.offset = max(0.0, self.offset - delta / 120 * self.visible_span() * 0.15)
        self.parent().sync_scrollbar()
        self.update()

    def contextMenuEvent(self, e):
        name, i, _ = self._block_at(QPointF(e.pos()))
        if not name or i < 0:
            return
        self.active, self.sel = name, i
        self.selected.emit(name, i)
        menu = QMenu(self)
        actions = [("Редактировать стиль субтитра  (двойной клик)", lambda: self.blockDoubleClicked.emit(name, i))]
        if self.tracks[name][i].has_own_style():
            actions.append(("Сбросить свой стиль", lambda: self.resetStyleRequested.emit(name, i)))
        actions += [("Разрезать по курсору  (S)", self.split_at_playhead),
                    ("Склеить со следующим  (M)", self.merge_next),
                    ("Удалить  (Del)", self.delete_selected)]
        for text, slot in actions:
            a = QAction(text, menu)
            a.triggered.connect(slot)
            menu.addAction(a)
        menu.exec(e.globalPos())

    def keyPressEvent(self, e):
        key = e.key()
        if key == Qt.Key.Key_S:
            self.split_at_playhead()
        elif key == Qt.Key.Key_M:
            self.merge_next()
        elif key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_selected()
        else:
            super().keyPressEvent(e)

    # ------------------------------------------------------------ операции

    def split_at_playhead(self):
        items = self.tracks[self.active]
        idx = next((i for i, s in enumerate(items) if s.start < self.playhead < s.end), -1)
        if idx < 0:
            return
        parts = split_subtitle(items[idx], self.playhead)
        if parts:
            items[idx:idx + 1] = list(parts)
            self.sel = idx + 1
            self.edited.emit(self.active)
            self.selected.emit(self.active, self.sel)

    def merge_next(self):
        items = self.tracks[self.active]
        i = self.sel
        if 0 <= i < len(items) - 1:
            items[i:i + 2] = [merge_subtitles(items[i], items[i + 1])]
            self.edited.emit(self.active)
            self.selected.emit(self.active, i)

    def delete_selected(self):
        items = self.tracks[self.active]
        if 0 <= self.sel < len(items):
            del items[self.sel]
            self.sel = min(self.sel, len(items) - 1)
            self.edited.emit(self.active)
            self.selected.emit(self.active, self.sel)

    def ensure_visible(self, t: float):
        span = self.visible_span()
        if t < self.offset or t > self.offset + span * 0.92:
            self.offset = max(0.0, t - span * 0.1)
            self.parent().sync_scrollbar()


class TimelineView(QWidget):
    """Таймлайн с панелью кнопок и прокруткой."""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        bar = QHBoxLayout()
        bar.setSpacing(4)
        self.time_label = label("00:00.0", "badge")
        bar.addWidget(self.time_label)
        bar.addStretch()
        self.canvas = TimelineCanvas(self)
        for text, tip, slot in [("✂", "Разрезать по курсору (S)", self.canvas.split_at_playhead),
                                ("⊕", "Склеить со следующим (M)", self.canvas.merge_next),
                                ("−", "Уменьшить масштаб (Ctrl+колесо)", lambda: self.zoom(1 / 1.4)),
                                ("+", "Увеличить масштаб", lambda: self.zoom(1.4)),
                                ("⤢", "Показать всё", self.fit)]:
            bar.addWidget(button(text, "icon", tip, slot))
        lay.addLayout(bar)
        lay.addWidget(self.canvas, 1)
        self.scroll = QScrollBar(Qt.Orientation.Horizontal)
        self.scroll.valueChanged.connect(self._on_scroll)
        lay.addWidget(self.scroll)

    def sync_scrollbar(self):
        c = self.canvas
        self.scroll.blockSignals(True)
        self.scroll.setRange(0, int(max(c.total() - c.visible_span(), 0) * 100))
        self.scroll.setPageStep(int(c.visible_span() * 100))
        self.scroll.setValue(int(c.offset * 100))
        self.scroll.blockSignals(False)

    def _on_scroll(self, v):
        self.canvas.offset = v / 100
        self.canvas.update()

    def zoom(self, k: float):
        c = self.canvas
        center = c.offset + c.visible_span() / 2
        c.pps = min(max(c.pps * k, 4), 600)
        c.offset = max(0.0, center - c.visible_span() / 2)
        self.sync_scrollbar()
        c.update()

    def fit(self):
        c = self.canvas
        c.pps = max((c.width() - LABEL_W - 10) / max(c.total(), 1), 4)
        c.offset = 0
        self.sync_scrollbar()
        c.update()

    def set_playhead(self, t: float, follow: bool = False):
        self.canvas.playhead = t
        self.time_label.setText(tc(t))
        if follow:
            self.canvas.ensure_visible(t)
        self.canvas.update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.sync_scrollbar()
