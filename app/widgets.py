"""Список субтитров/текстов с метками проблем."""
from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QAbstractItemView, QListWidget, QListWidgetItem, QStyle, QStyledItemDelegate

from .models import Subtitle
from .theme import ACCENT, AMBER, MUTED, RED, SURFACE_2, SURFACE_3, TEXT

ISSUE_ROLE = Qt.ItemDataRole.UserRole + 1
EDIT_ROLE = Qt.ItemDataRole.UserRole + 2  # стиль этого субтитра сейчас редактируется
MARK = "#FFD84D"


def short_time(t: float) -> str:
    m, s = divmod(max(t, 0), 60)
    if m >= 60:
        h, m = divmod(m, 60)
        return f"{int(h)}:{int(m):02d}:{s:05.2f}"
    return f"{int(m):02d}:{s:05.2f}"


class SubtitleDelegate(QStyledItemDelegate):
    ROW_H = 64

    def sizeHint(self, option, index):
        return QSize(10, self.ROW_H)  # ширина подстраивается под список

    def paint(self, p: QPainter, option, index):
        sub: Subtitle = index.data(Qt.ItemDataRole.UserRole)
        if sub is None:
            return
        issue = index.data(ISSUE_ROLE)  # (уровень, текст) или None
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(option.rect).adjusted(2, 3, -6, -3)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if index.data(EDIT_ROLE):
            p.setPen(QPen(QColor(MARK), 1.6))
            p.setBrush(QColor(255, 216, 77, 26))
        elif selected:
            p.setPen(QPen(QColor(ACCENT), 1))
            p.setBrush(QColor(124, 92, 255, 34))
        else:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(SURFACE_3 if hover else SURFACE_2))
        p.drawRoundedRect(r, 10, 10)

        base = QFont(option.font)
        num_font = QFont(base)
        num_font.setPointSizeF(base.pointSizeF() * 0.85)
        p.setFont(num_font)
        p.setPen(QColor(ACCENT if selected else MUTED))
        p.drawText(QRectF(r.left() + 10, r.top(), 30, r.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, str(index.row() + 1))

        x = r.left() + 44
        bad = sub.end <= sub.start
        time_font = QFont("Consolas", 1)
        time_font.setPointSizeF(base.pointSizeF() * 0.85)
        p.setFont(time_font)
        p.setPen(QColor(RED if bad else MUTED))
        extra = ("   ✦" if sub.words else "") + ("   ★ свой стиль" if sub.has_own_style() else "")
        p.drawText(QRectF(x, r.top() + 8, r.width() - 70, 16), Qt.AlignmentFlag.AlignLeft,
                   f"{short_time(sub.start)} → {short_time(sub.end)}   {sub.end - sub.start:.1f} с{extra}")
        p.setFont(base)
        p.setPen(QColor(TEXT if sub.text.strip() else MUTED))
        text = sub.text.replace("\n", "  ⏎  ") if sub.text.strip() else "пустой"
        fm = QFontMetrics(base)
        text = fm.elidedText(text, Qt.TextElideMode.ElideRight, int(r.width() - 72))
        p.drawText(QRectF(x, r.top() + 28, r.width() - 70, 24),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
        if issue:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(RED if issue[0] == "error" else AMBER))
            p.drawEllipse(QRectF(r.right() - 18, r.top() + 10, 8, 8))
        p.restore()


class SubtitleList(QListWidget):
    emptyClicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setItemDelegate(SubtitleDelegate(self))
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setMouseTracking(True)
        self.setUniformItemSizes(True)
        self.setSpacing(0)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)

    def mousePressEvent(self, e):
        if self.itemAt(e.position().toPoint()) is None:
            self.emptyClicked.emit()  # клик ниже всех субтитров — «пустое место»
        super().mousePressEvent(e)

    def set_editing(self, row: int):
        for i in range(self.count()):
            self.item(i).setData(EDIT_ROLE, i == row)
        self.viewport().update()

    def set_subtitles(self, subs: list[Subtitle], issues: dict[int, tuple] | None = None):
        issues = issues or {}
        self.blockSignals(True)
        if self.count() != len(subs):
            self.clear()
            for _ in subs:
                self.addItem(QListWidgetItem())
        for i, s in enumerate(subs):
            item = self.item(i)
            item.setData(Qt.ItemDataRole.UserRole, s)
            item.setData(ISSUE_ROLE, issues.get(i))
            item.setToolTip(issues[i][1] if i in issues else "")
        self.blockSignals(False)
        self.viewport().update()

    def refresh_row(self, row: int, sub: Subtitle):
        item = self.item(row)
        if item:
            item.setData(Qt.ItemDataRole.UserRole, sub)
            self.viewport().update()

    def current_row(self) -> int:
        rows = self.selected_rows()
        return rows[0] if rows else -1

    def selected_rows(self) -> list[int]:
        return sorted(self.row(i) for i in self.selectedItems())

    def select_row(self, row: int):
        self.blockSignals(True)
        self.clearSelection()
        item = self.item(row)
        if item:
            item.setSelected(True)
            self.setCurrentItem(item)
            self.scrollToItem(item)
        self.blockSignals(False)
