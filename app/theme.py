"""Тёмная тема и современные элементы интерфейса."""
from __future__ import annotations

import ctypes
import math
import sys

from PySide6.QtCore import (QEasingCurve, QEvent, QObject, QPointF, QPropertyAnimation, QRectF, QSize, Qt, QTimer,
                            Signal)
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QAbstractButton, QAbstractScrollArea, QAbstractSlider, QAbstractSpinBox, QApplication,
                               QButtonGroup, QScrollBar,
                               QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox, QFrame, QGraphicsOpacityEffect,
                               QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget)

# ---------------------------------------------------------------- палитра

BG = "#0E0F13"
SURFACE = "#16181E"
SURFACE_2 = "#1C1F27"
SURFACE_3 = "#262A35"
BORDER = "#2A2E3A"
TEXT = "#E8EAF0"
MUTED = "#8A90A2"
ACCENT = "#7C5CFF"
ACCENT_2 = "#4F8CFF"
GREEN = "#22C55E"
RED = "#EF4444"
AMBER = "#F59E0B"


def ui_font_family() -> str:
    families = set(QFontDatabase.families())
    for name in ("Segoe UI Variable Text", "Segoe UI", "Inter"):
        if name in families:
            return name
    return QFont().family()


QSS = f"""
* {{ outline: none; }}
QWidget {{ color: {TEXT}; font-size: 10pt; }}
QMainWindow, QDialog {{ background: {BG}; }}
QToolTip {{ background: {SURFACE_3}; color: {TEXT}; border: 1px solid {BORDER}; padding: 6px 8px; border-radius: 6px; }}

QFrame#Header {{ background: {SURFACE}; border-bottom: 1px solid {BORDER}; }}
QFrame[card="true"] {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 14px; }}
QFrame[card="dock"] {{ background: {SURFACE}; border: 1px solid {BORDER}; border-top: none;
    border-top-left-radius: 0; border-top-right-radius: 0;
    border-bottom-left-radius: 12px; border-bottom-right-radius: 12px; }}
QLabel {{ background: transparent; }}
QLabel[role="title"] {{ font-size: 12pt; font-weight: 600; }}
QLabel[role="h1"] {{ font-size: 14pt; font-weight: 700; }}
QLabel[role="muted"] {{ color: {MUTED}; }}
QLabel[role="small"] {{ color: {MUTED}; font-size: 9pt; }}
QLabel[role="badge"] {{ background: {SURFACE_3}; color: {MUTED}; border-radius: 9px; padding: 1px 8px; font-size: 9pt; }}
QLabel[role="chip-ok"] {{ background: rgba(34,197,94,0.12); color: {GREEN}; border-radius: 11px; padding: 3px 10px; font-size: 9pt; }}
QLabel[role="chip-off"] {{ background: {SURFACE_3}; color: {MUTED}; border-radius: 11px; padding: 3px 10px; font-size: 9pt; }}

QPushButton {{
    background: {SURFACE_3}; border: 1px solid {BORDER}; border-radius: 9px;
    padding: 7px 14px; color: {TEXT};
}}
QPushButton:hover {{ background: #2F3442; border-color: #3A4050; }}
QPushButton:pressed {{ background: #232733; }}
QPushButton:disabled {{ color: #5A6072; background: {SURFACE_2}; }}
QPushButton[variant="ghost"] {{ background: transparent; border: 1px solid transparent; color: {MUTED}; }}
QPushButton[variant="ghost"]:hover {{ background: {SURFACE_3}; color: {TEXT}; }}
QPushButton[variant="icon"] {{ background: transparent; border: none; color: {MUTED}; padding: 4px; min-width: 28px; min-height: 28px; border-radius: 8px; font-size: 12pt; }}
QPushButton[variant="icon"]:hover {{ background: {SURFACE_3}; color: {TEXT}; }}
QPushButton[variant="primary"] {{
    border: none; color: white; font-weight: 600; padding: 10px 20px; border-radius: 11px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {ACCENT}, stop:1 {ACCENT_2});
}}
QPushButton[variant="primary"]:hover {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #8D72FF, stop:1 #66A0FF);
}}
QPushButton[variant="primary"]:disabled {{ background: {SURFACE_3}; color: #6A7084; }}
QPushButton[variant="chip"] {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 14px; padding: 5px 12px; color: {MUTED};
}}
QPushButton[variant="chip"]:hover {{ color: {TEXT}; border-color: #3A4050; }}
QPushButton[variant="chip"]:checked {{ background: rgba(124,92,255,0.18); border-color: {ACCENT}; color: {TEXT}; }}
QPushButton[variant="seg"] {{
    background: transparent; border: none; border-radius: 8px; padding: 6px 12px; color: {MUTED};
}}
QPushButton[variant="seg"]:hover {{ color: {TEXT}; }}
QPushButton[variant="seg"]:checked {{ background: {SURFACE_3}; color: {TEXT}; }}
QFrame#Segmented {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 10px; }}

QLineEdit, QPlainTextEdit, QAbstractSpinBox, QComboBox {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 8px;
    padding: 6px 9px; selection-background-color: {ACCENT};
}}
QLineEdit:focus, QPlainTextEdit:focus, QAbstractSpinBox:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QLineEdit:disabled, QPlainTextEdit:disabled, QAbstractSpinBox:disabled, QComboBox:disabled {{ color: #5A6072; }}
QPlainTextEdit {{ padding: 8px 10px; }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ width: 0; border: none; }}
QAbstractSpinBox {{ padding-right: 9px; }}
QComboBox::drop-down {{ border: none; width: 28px; }}
QComboBox::down-arrow {{ image: url(__CHEVRON__); width: 12px; height: 12px; margin-right: 8px; }}
QComboBox QAbstractItemView {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 8px; padding: 4px;
    selection-background-color: {SURFACE_3}; selection-color: {TEXT};
}}


QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #343947; border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #444A5A; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #343947; border-radius: 3px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ background: none; border: none; width: 0; height: 0; }}

QListWidget {{ background: transparent; border: none; }}
QListWidget::item {{ border: none; }}
QMenu {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 10px; padding: 6px; }}
QMenu::item {{ padding: 7px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: {SURFACE_3}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 5px 8px; }}
QProgressBar {{ background: {SURFACE_3}; border: none; border-radius: 4px; height: 8px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ border-radius: 4px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT}, stop:1 {ACCENT_2}); }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 5px; border: 1px solid #3A4050; background: {SURFACE_2}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QRadioButton {{ spacing: 8px; }}
QSplitter::handle {{ background: transparent; }}
QDockWidget {{ color: {MUTED}; font-size: 9pt; }}
QDockWidget::title {{ background: {BG}; padding: 5px 10px; text-align: left; }}
QDockWidget::close-button, QDockWidget::float-button {{ background: transparent; border: none; padding: 2px; }}
QDockWidget::close-button:hover, QDockWidget::float-button:hover {{ background: {SURFACE_3}; border-radius: 4px; }}
QMainWindow::separator {{ background: {BG}; width: 6px; height: 6px; }}
QMainWindow::separator:hover {{ background: #2A2340; }}
QPushButton[variant="menu"] {{ background: transparent; border: none; color: {MUTED}; padding: 6px 10px; border-radius: 7px; }}
QPushButton[variant="menu"]:hover {{ background: {SURFACE_3}; color: {TEXT}; }}
QPushButton[variant="menu"]::menu-indicator {{ image: none; width: 0; }}
QMessageBox {{ background: {SURFACE}; }}
QGroupBox {{ border: 1px solid {BORDER}; border-radius: 12px; margin-top: 14px; padding: 12px 10px 10px 10px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; color: {MUTED}; }}
"""


def _chevron_path() -> str:
    """Рисует стрелку для выпадающих списков (QSS принимает только файл-картинку)."""
    import tempfile
    from pathlib import Path

    from PySide6.QtGui import QPixmap
    path = Path(tempfile.gettempdir()) / "ae_subs" / "chevron.png"
    path.parent.mkdir(exist_ok=True)
    pm = QPixmap(24, 24)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(MUTED), 2.6)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    path_ = QPainterPath()
    path_.moveTo(6, 9)
    path_.lineTo(12, 15)
    path_.lineTo(18, 9)
    p.drawPath(path_)
    p.end()
    pm.save(str(path))
    return path.as_posix()


class WheelGuard(QObject):
    """Колёсико мыши не меняет значения ползунков, полей и списков — только прокручивает панель."""

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Wheel and isinstance(obj, (QAbstractSpinBox, QComboBox, QAbstractSlider)) \
                and not isinstance(obj, QScrollBar):
            # прокручиваем панель, в которой лежит элемент (Qt сам не передаёт событие выше)
            w = obj.parentWidget()
            while w is not None and not isinstance(w, QAbstractScrollArea):
                w = w.parentWidget()
            if w is not None:
                bar = w.verticalScrollBar()
                bar.setValue(bar.value() - event.angleDelta().y() // 120 * bar.singleStep() * 3)
            return True
        return False


def apply_theme(app):
    app.setStyle("Fusion")
    font = QFont(ui_font_family(), 10)
    font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    app.setFont(font)
    app.setStyleSheet(QSS.replace("__CHEVRON__", _chevron_path()))
    app._wheel_guard = WheelGuard(app)
    app.installEventFilter(app._wheel_guard)


class PanelTitleBar(QWidget):
    """Тонкая полоска панели вместо заголовка: за неё панель можно перетащить, крестик — закрыть."""

    def __init__(self, dock, parent=None):
        super().__init__(parent)
        self.dock = dock
        self.setFixedHeight(20)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setToolTip("Потяните, чтобы переместить панель; двойной клик — открепить")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 0)
        lay.addStretch()
        close = QPushButton("✕")
        close.setFixedSize(16, 14)
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setToolTip("Закрыть панель (вернуть — меню «Окно»)")
        close.setStyleSheet(f"QPushButton {{ background: transparent; border: none; color: {MUTED}; font-size: 8pt;"
                            f" padding: 0; }} QPushButton:hover {{ color: {TEXT}; background: {SURFACE_3};"
                            " border-radius: 4px; }}")
        close.clicked.connect(dock.close)
        lay.addWidget(close)

    def paintEvent(self, event):
        # верх карточки панели: фон и рамка как у карточки, «ручка» по центру
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, 0)
        path = QPainterPath()
        rad = 12
        path.moveTo(r.left(), r.bottom() + 1)
        path.lineTo(r.left(), r.top() + rad)
        path.quadTo(r.left(), r.top(), r.left() + rad, r.top())
        path.lineTo(r.right() - rad, r.top())
        path.quadTo(r.right(), r.top(), r.right(), r.top() + rad)
        path.lineTo(r.right(), r.bottom() + 1)
        p.fillPath(path, QColor(SURFACE))
        p.setPen(QPen(QColor(BORDER), 1))
        p.drawPath(path)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#3A3F4D"))
        w = 36
        p.drawRoundedRect(QRectF((self.width() - w) / 2, 8, w, 4), 2, 2)
        p.end()


def dark_title_bar(widget: QWidget):
    """Тёмный заголовок окна Windows 10/11."""
    if sys.platform != "win32":
        return
    try:
        hwnd = int(widget.winId())
        value = ctypes.c_int(1)
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (новые и старые сборки)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(value), 4) == 0:
                break
        color = ctypes.c_int(0x00131210)  # COLORREF 0x00BBGGRR — цвет фона заголовка
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(color), 4)
    except Exception:  # noqa: BLE001
        pass


def prop(widget, **props):
    for k, v in props.items():
        widget.setProperty(k, v)
    return widget


def label(text: str, role: str | None = None) -> QLabel:
    lb = QLabel(text)
    if role:
        lb.setProperty("role", role)
    return lb


def button(text: str, variant: str | None = None, tip: str = "", slot=None) -> QPushButton:
    b = QPushButton(text)
    if variant:
        b.setProperty("variant", variant)
    if tip:
        b.setToolTip(tip)
    if slot:
        b.clicked.connect(slot)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


class Card(QFrame):
    def __init__(self, parent=None, padding=16, spacing=12):
        super().__init__(parent)
        self.setProperty("card", True)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(padding, padding, padding, padding)
        self.lay.setSpacing(spacing)


# ---------------------------------------------------------------- элементы управления

class ToggleSwitch(QCheckBox):
    """Переключатель-«таблетка». Остаётся QCheckBox, поэтому совместим с обычным кодом."""

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggled.connect(lambda _: self.update())

    def sizeHint(self):
        w = 40
        if self.text():
            w += 10 + self.fontMetrics().horizontalAdvance(self.text())
        return QSize(w, 24)

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QRectF(1, 3, 38, 20)
        on = self.isChecked()
        enabled = self.isEnabled()
        if on:
            g = QLinearGradient(track.topLeft(), track.topRight())
            g.setColorAt(0, QColor(ACCENT))
            g.setColorAt(1, QColor(ACCENT_2))
            p.setBrush(g if enabled else QColor("#3A3F4D"))
        else:
            p.setBrush(QColor(SURFACE_3))
        p.setPen(QPen(QColor(BORDER if not on else ACCENT), 1))
        p.drawRoundedRect(track, 10, 10)
        knob_x = track.right() - 18 if on else track.left() + 2
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("white" if enabled else "#8A90A2"))
        p.drawEllipse(QRectF(knob_x, track.top() + 2, 16, 16))
        if self.text():
            p.setPen(QColor(TEXT if enabled else MUTED))
            p.drawText(QRectF(48, 0, self.width() - 48, self.height()),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text())
        p.end()


class ModernSlider(QSlider):
    """Тонкая дорожка с градиентной заливкой и белым кружком."""

    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setFixedHeight(24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def _pos_for(self, x: float) -> int:
        span = max(self.width() - 16, 1)
        k = min(max((x - 8) / span, 0.0), 1.0)
        return round(self.minimum() + k * (self.maximum() - self.minimum()))

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.setValue(self._pos_for(e.position().x()))
            self.setSliderDown(True)
            e.accept()

    def mouseMoveEvent(self, e):
        if self.isSliderDown():
            self.setValue(self._pos_for(e.position().x()))

    def mouseReleaseEvent(self, e):
        self.setSliderDown(False)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        span = self.maximum() - self.minimum()
        k = (self.value() - self.minimum()) / span if span else 0
        x0, x1, cy = 8.0, self.width() - 8.0, self.height() / 2
        hx = x0 + (x1 - x0) * k
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(SURFACE_3))
        p.drawRoundedRect(QRectF(x0, cy - 2, x1 - x0, 4), 2, 2)
        if self.isEnabled():
            g = QLinearGradient(x0, 0, x1, 0)
            g.setColorAt(0, QColor(ACCENT))
            g.setColorAt(1, QColor(ACCENT_2))
            p.setBrush(g)
        else:
            p.setBrush(QColor("#3A3F4D"))
        p.drawRoundedRect(QRectF(x0, cy - 2, hx - x0, 4), 2, 2)
        p.setBrush(QColor("white" if self.isEnabled() else MUTED))
        r = 8 if (self.isSliderDown() or self.underMouse()) else 7
        p.drawEllipse(QPointF(hx, cy), r, r)
        p.end()


class SliderSpin(QWidget):
    """Ползунок + числовое поле. slider_max — «удобный» предел ползунка, поле принимает больше."""
    valueChanged = Signal(float)

    def __init__(self, lo, hi, step=1.0, decimals=0, suffix="", slider_max=None, parent=None):
        super().__init__(parent)
        self._k = 10 ** decimals
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.slider = ModernSlider()
        smax = hi if slider_max is None else slider_max
        self.slider.setRange(int(lo * self._k), int(smax * self._k))
        self.spin = QDoubleSpinBox()
        self.spin.setRange(lo, hi)
        self.spin.setSingleStep(step)
        self.spin.setDecimals(decimals)
        self.spin.setSuffix(suffix)
        self.spin.setFixedWidth(92)
        self.spin.setAlignment(Qt.AlignmentFlag.AlignRight)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.spin)
        self.slider.valueChanged.connect(self._from_slider)
        self.spin.valueChanged.connect(self._from_spin)

    def _from_slider(self, v):
        self.spin.blockSignals(True)
        self.spin.setValue(v / self._k)
        self.spin.blockSignals(False)
        self.valueChanged.emit(self.spin.value())

    def _from_spin(self, v):
        self.slider.blockSignals(True)
        self.slider.setValue(int(round(v * self._k)))
        self.slider.blockSignals(False)
        self.valueChanged.emit(v)

    def value(self) -> float:
        return self.spin.value()

    def setValue(self, v):
        self.spin.setValue(v)


class ColorButton(QPushButton):
    changed = Signal()

    def __init__(self, color="#FFFFFF", parent=None):
        super().__init__(parent)
        self._color = color
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(132, 34)
        self.clicked.connect(self._pick)

    def color(self) -> str:
        return self._color

    def set_color(self, color: str):
        self._color = QColor(color).name().upper()
        self.update()

    def _pick(self):
        c = QColorDialog.getColor(QColor(self._color), self, "Цвет")
        if c.isValid():
            self.set_color(c.name())
            self.changed.emit()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor("#3A4050" if self.underMouse() else BORDER), 1))
        p.setBrush(QColor(SURFACE_2))
        p.drawRoundedRect(r, 8, 8)
        p.setPen(QPen(QColor(255, 255, 255, 60), 1))
        p.setBrush(QColor(self._color))
        p.drawEllipse(QRectF(8, (self.height() - 20) / 2, 20, 20))
        p.setPen(QColor(TEXT))
        p.drawText(QRectF(36, 0, self.width() - 40, self.height()), Qt.AlignmentFlag.AlignVCenter, self._color)
        p.end()


class Segmented(QFrame):
    """Переключатель разделов в виде «таблеток»."""
    currentChanged = Signal(int)

    def __init__(self, items: list[str], parent=None):
        super().__init__(parent)
        self.setObjectName("Segmented")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        for i, text in enumerate(items):
            b = button(text, "seg")
            b.setCheckable(True)
            self.group.addButton(b, i)
            lay.addWidget(b)
        self.group.button(0).setChecked(True)
        self.group.idClicked.connect(self.currentChanged.emit)

    def set_current(self, i: int):
        self.group.button(i).setChecked(True)


class Toast(QLabel):
    """Всплывающее уведомление внизу окна."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hide()
        self.effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.effect)
        self.anim = QPropertyAnimation(self.effect, b"opacity", self)
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._fade_out)

    def show_message(self, text: str, kind: str = "ok", ms: int = 4000):
        color = {"ok": GREEN, "warn": AMBER, "error": RED, "info": ACCENT_2}.get(kind, ACCENT_2)
        self.setStyleSheet(f"background: {SURFACE_3}; color: {TEXT}; border: 1px solid {BORDER};"
                           f"border-left: 4px solid {color}; border-radius: 10px; padding: 12px 18px;")
        self.setText(text)
        parent = self.parentWidget()
        self.setMaximumWidth(min(560, parent.width() - 40))
        self.adjustSize()
        self.move((parent.width() - self.width()) // 2, parent.height() - self.height() - 28)
        self.raise_()
        self.show()
        self.anim.stop()
        self.anim.setDuration(180)
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.start()
        self.timer.start(ms)

    def _fade_out(self):
        self.anim.stop()
        self.anim.setDuration(400)
        self.anim.setStartValue(1.0)
        self.anim.setEndValue(0.0)
        self.anim.finished.connect(self._hide_once)
        self.anim.start()

    def _hide_once(self):
        self.anim.finished.disconnect(self._hide_once)
        self.hide()


def _draw_icon(p: QPainter, name: str, color: QColor):
    """Иконки разделов стиля на сетке 24×24: одна толщина линий, один размер."""
    pen = QPen(color, 1.9)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    if name == "text":  # буква T
        p.drawLine(QPointF(5, 5), QPointF(19, 5))
        p.drawLine(QPointF(12, 5), QPointF(12, 19))
        p.drawLine(QPointF(9, 19), QPointF(15, 19))
    elif name == "fill":  # капля
        path = QPainterPath()
        path.moveTo(12, 3.5)
        path.cubicTo(12, 3.5, 5.5, 11, 5.5, 14.5)
        path.cubicTo(5.5, 18.2, 8.5, 20.5, 12, 20.5)
        path.cubicTo(15.5, 20.5, 18.5, 18.2, 18.5, 14.5)
        path.cubicTo(18.5, 11, 12, 3.5, 12, 3.5)
        p.setBrush(color)
        p.drawPath(path)
    elif name == "stroke":  # двойной контур
        p.drawRoundedRect(QRectF(4, 4, 16, 16), 4, 4)
        p.drawRoundedRect(QRectF(8, 8, 8, 8), 2, 2)
    elif name == "glow":  # солнце
        p.drawEllipse(QPointF(12, 12), 3.6, 3.6)
        for i in range(8):
            a = i * math.pi / 4
            p.drawLine(QPointF(12 + math.cos(a) * 6.5, 12 + math.sin(a) * 6.5),
                       QPointF(12 + math.cos(a) * 9.2, 12 + math.sin(a) * 9.2))
    elif name == "shadow":  # квадрат с тенью
        shadow = QColor(color)
        shadow.setAlphaF(0.45)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(shadow)
        p.drawRoundedRect(QRectF(8.5, 8.5, 12, 12), 2.5, 2.5)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(3.5, 3.5, 12, 12), 2.5, 2.5)
    elif name == "box":  # плашка со строками текста
        p.drawRoundedRect(QRectF(3, 6, 18, 12), 3, 3)
        p.drawLine(QPointF(7, 10.5), QPointF(17, 10.5))
        p.drawLine(QPointF(9, 13.8), QPointF(15, 13.8))
    elif name == "position":  # стрелки во все стороны
        p.drawLine(QPointF(12, 3.5), QPointF(12, 20.5))
        p.drawLine(QPointF(3.5, 12), QPointF(20.5, 12))
        for (x, y), (dx, dy) in (((12, 3.5), (0, 1)), ((12, 20.5), (0, -1)),
                                 ((3.5, 12), (1, 0)), ((20.5, 12), (-1, 0))):
            px, py = -dy, dx
            p.drawLine(QPointF(x, y), QPointF(x + dx * 3 + px * 2.6, y + dy * 3 + py * 2.6))
            p.drawLine(QPointF(x, y), QPointF(x + dx * 3 - px * 2.6, y + dy * 3 - py * 2.6))
    elif name == "anim":  # кружок в движении
        p.drawEllipse(QPointF(15, 12), 5, 5)
        p.drawLine(QPointF(3, 9), QPointF(8, 9))
        p.drawLine(QPointF(2, 12), QPointF(8, 12))
        p.drawLine(QPointF(3, 15), QPointF(8, 15))
    elif name == "highlight":  # искра
        path = QPainterPath()
        path.moveTo(12, 3)
        path.quadTo(13, 11, 21, 12)
        path.quadTo(13, 13, 12, 21)
        path.quadTo(11, 13, 3, 12)
        path.quadTo(11, 11, 12, 3)
        p.setBrush(color)
        p.drawPath(path)
    elif name == "gear":  # шестерёнка
        p.drawEllipse(QPointF(12, 12), 3, 3)
        outer = QPainterPath()
        for i in range(16):
            a = i * math.pi / 8
            r = 9 if i % 2 == 0 else 6.8
            pt = QPointF(12 + math.cos(a) * r, 12 + math.sin(a) * r)
            outer.moveTo(pt) if i == 0 else outer.lineTo(pt)
        outer.closeSubpath()
        p.drawPath(outer)


def nav_icon(name: str) -> QIcon:
    """Иконка с двумя состояниями: обычная (приглушённая) и выбранная (белая)."""
    from PySide6.QtGui import QPixmap
    icon = QIcon()
    for state, color in ((QIcon.State.Off, MUTED), (QIcon.State.On, "#FFFFFF")):
        pm = QPixmap(48, 48)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.scale(2, 2)
        _draw_icon(p, name, QColor(color))
        p.end()
        pm.setDevicePixelRatio(2)
        icon.addPixmap(pm, QIcon.Mode.Normal, state)
        icon.addPixmap(pm, QIcon.Mode.Active, state)
    return icon


def logo_pixmap(size: int):
    """Логотип: градиентный скруглённый квадрат с «строками субтитров»."""
    from PySide6.QtGui import QPixmap
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    g = QLinearGradient(0, 0, size, size)
    g.setColorAt(0, QColor(ACCENT))
    g.setColorAt(1, QColor(ACCENT_2))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(g)
    p.drawRoundedRect(QRectF(0, 0, size, size), size * 0.28, size * 0.28)
    p.setBrush(QColor(255, 255, 255, 235))
    k = size / 32
    p.drawRoundedRect(QRectF(7 * k, 15 * k, 18 * k, 3.4 * k), 1.7 * k, 1.7 * k)
    p.setBrush(QColor(255, 255, 255, 150))
    p.drawRoundedRect(QRectF(10 * k, 21 * k, 12 * k, 3.4 * k), 1.7 * k, 1.7 * k)
    path = QPainterPath()
    path.addEllipse(QPointF(16 * k, 9 * k), 2.2 * k, 2.2 * k)
    p.setBrush(QColor(255, 255, 255, 235))
    p.drawPath(path)
    p.end()
    return pm
