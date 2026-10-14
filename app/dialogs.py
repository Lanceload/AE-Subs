"""Диалоги: вставка текста, авто-тайминг, проверка перед отправкой, отдельное окно превью."""
from __future__ import annotations

import threading
import time

from PySide6.QtCore import QObject, QSettings, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QButtonGroup, QDialog, QHBoxLayout, QListWidget, QListWidgetItem,
                               QPlainTextEdit, QRadioButton, QVBoxLayout)

from .models import Subtitle, auto_timing, split_plain_text
from .preview import PreviewWidget
from .style_panel import FormPage, _dspin
from . import feedback
from .theme import AMBER, RED, Segmented, ToggleSwitch, button, dark_title_bar, label
from .validation import Issue
from .widgets import short_time


class BaseDialog(QDialog):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(18, 18, 18, 18)
        self.lay.setSpacing(12)
        self.lay.addWidget(label(title, "h1"))

    def add_buttons(self, ok_text="Готово", cancel_text="Отмена"):
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(button(cancel_text, "ghost", slot=self.reject))
        row.addWidget(button(ok_text, "primary", slot=self.accept))
        self.lay.addLayout(row)
        return row

    def showEvent(self, event):
        super().showEvent(event)
        dark_title_bar(self)


class TimingParams(FormPage):
    def __init__(self, with_start=True, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(220)
        self.start = _dspin(0, 36000, 0.5, 2, " с")
        if with_start:
            self.add_row("Начать с", self.start)
        self.per_char = _dspin(0.01, 1, 0.01, 3, " с")
        self.per_char.setValue(0.07)
        self.add_row("Время на символ", self.per_char)
        self.min_dur = _dspin(0.1, 60, 0.1, 2, " с")
        self.min_dur.setValue(1.2)
        self.add_row("Минимум на экране", self.min_dur)
        self.max_dur = _dspin(0.1, 60, 0.1, 2, " с")
        self.max_dur.setValue(6)
        self.add_row("Максимум на экране", self.max_dur)
        self.gap = _dspin(0, 10, 0.05, 2, " с")
        self.gap.setValue(0.1)
        self.add_row("Пауза между", self.gap)
        self.finish()

    def apply(self, texts: list[str]) -> list[Subtitle]:
        return auto_timing(texts, self.start.value(), self.per_char.value(), self.min_dur.value(),
                           self.max_dur.value(), self.gap.value())


class PasteTextDialog(BaseDialog):
    def __init__(self, start: float, parent=None):
        super().__init__("Вставить текст", parent)
        self.resize(620, 680)
        self.lay.addWidget(label("Вставьте сценарий — программа разобьёт его на субтитры и расставит время.", "muted"))
        self.edit = QPlainTextEdit()
        self.lay.addWidget(self.edit, 1)
        self.by_line = QRadioButton("Каждая строка — отдельный субтитр")
        self.by_blank = QRadioButton("Субтитры разделены пустой строкой")
        self.by_line.setChecked(True)
        group = QButtonGroup(self)
        group.addButton(self.by_line)
        group.addButton(self.by_blank)
        self.lay.addWidget(self.by_line)
        self.lay.addWidget(self.by_blank)
        self.timing = TimingParams()
        self.timing.start.setValue(start)
        self.lay.addWidget(self.timing)
        self.replace = ToggleSwitch("Заменить текущие субтитры")
        self.lay.addWidget(self.replace)
        self.add_buttons("Добавить")

    def result_subs(self) -> list[Subtitle]:
        return self.timing.apply(split_plain_text(self.edit.toPlainText(), self.by_blank.isChecked()))


class TimingDialog(BaseDialog):
    def __init__(self, start: float, parent=None):
        super().__init__("Авто-тайминг", parent)
        self.resize(460, 380)
        self.lay.addWidget(label("Пересчитать время всех субтитров по длине текста.", "muted"))
        self.timing = TimingParams()
        self.timing.start.setValue(start)
        self.lay.addWidget(self.timing)
        self.add_buttons("Пересчитать")


class ValidationDialog(BaseDialog):
    """Результат проверки перед отправкой. Возвращает: 'fix', 'send' или None."""

    def __init__(self, issues: list[Issue], items: list[Subtitle], parent=None):
        super().__init__("Проверка перед отправкой", parent)
        self.resize(640, 480)
        self.choice = None
        errors = sum(1 for i in issues if i.level == "error")
        fixable = sum(1 for i in issues if i.fix)
        self.lay.addWidget(label(f"Найдено замечаний: {len(issues)} (ошибок: {errors}). "
                                 f"Автоматически можно исправить: {fixable}.", "muted"))
        lst = QListWidget()
        lst.setStyleSheet("QListWidget { background: #1C1F27; border-radius: 10px; padding: 6px; }"
                          "QListWidget::item { padding: 6px; }")
        for iss in issues:
            s = items[iss.index]
            mark = "●"
            it = QListWidgetItem(f"{mark}  №{iss.index + 1}  [{short_time(s.start)}]  {iss.text}"
                                 + ("" if iss.fix else "  — вручную"))
            it.setForeground(Qt.GlobalColor.red if iss.level == "error" else Qt.GlobalColor.yellow)
            it.setToolTip(s.text)
            lst.addItem(it)
        self.lay.addWidget(lst, 1)
        row = QHBoxLayout()
        row.addWidget(button("Отмена", "ghost", slot=self.reject))
        row.addStretch()
        send = button("Отправить как есть", slot=lambda: self._done("send"))
        row.addWidget(send)
        fix = button("✓  Исправить и отправить", "primary", slot=lambda: self._done("fix"))
        fix.setEnabled(fixable > 0)
        row.addWidget(fix)
        self.lay.addLayout(row)

    def _done(self, choice: str):
        self.choice = choice
        self.accept()


class ExportDialog(BaseDialog):
    """Файл → Экспорт: отдельное окно с выбором дорожки и формата."""

    def __init__(self, get_items, parent=None):
        super().__init__("Экспорт", parent)
        from .pages import ExportPage
        self.resize(760, 640)
        self.page = ExportPage(get_items)
        self.lay.addWidget(self.page, 1)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(button("Закрыть", "ghost", slot=self.accept))
        self.lay.addLayout(row)
        self.page.refresh()




class _FeedbackBus(QObject):
    done = Signal(str)  # пустая строка — отправлено, иначе текст ошибки


class FeedbackDialog(BaseDialog):
    """Ошибка, предложение или отзыв — уходит разработчику (в Telegram через сервер-посредник)."""

    PLACEHOLDERS = {
        "bug": "Что случилось? Что вы делали перед этим и что ожидали увидеть?",
        "idea": "Например: «Хочу экспорт сразу в Premiere Pro» или «Добавьте эффект появления по слогам»",
        "review": "Например: «Удобно, что субтитры сразу ложатся в композицию. Не хватает тёмной темы в превью»",
    }

    def __init__(self, afterfx_path: str = "", parent=None):
        super().__init__("Обратная связь", parent)
        self.afterfx_path = afterfx_path
        self.resize(560, 460)
        self.lay.addWidget(label("Сообщение получит разработчик.", "muted"))
        self.kind = Segmented([t for _, t in feedback.KINDS])
        self.kind.currentChanged.connect(self._on_kind)
        self.lay.addWidget(self.kind)
        self.text = QPlainTextEdit()
        self.text.textChanged.connect(self._on_text)
        self.lay.addWidget(self.text, 1)
        self.counter = label("", "small")
        self.counter.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.lay.addWidget(self.counter)
        self.attach = ToggleSwitch("Приложить сведения о системе (Windows, видеокарта, версия AE)")
        self.attach.setChecked(True)
        self.lay.addWidget(self.attach)
        row = QHBoxLayout()
        self.status = label("", "small")
        row.addWidget(self.status, 1)
        row.addWidget(button("Отмена", "ghost", slot=self.reject))
        self.send_btn = button("Отправить", "primary", slot=self._send)
        row.addWidget(self.send_btn)
        self.lay.addLayout(row)
        self.bus = _FeedbackBus(self)
        self.bus.done.connect(self._on_done)
        self._on_kind(0)
        self._on_text()

    def _kind(self) -> str:
        return feedback.KINDS[self.kind.group.checkedId()][0]

    def _on_kind(self, _i):
        self.text.setPlaceholderText(self.PLACEHOLDERS[self._kind()])

    def _on_text(self):
        n = len(self.text.toPlainText())
        self.counter.setText(f"{n} / {feedback.MAX_TEXT}")
        self.send_btn.setEnabled(3 <= len(self.text.toPlainText().strip()) and n <= feedback.MAX_TEXT)

    def _send(self):
        last = QSettings().value("feedback_last", 0.0, float)
        if time.time() - last < 30:
            self.status.setText("Подождите полминуты перед следующим сообщением.")
            return
        self.send_btn.setEnabled(False)
        self.text.setReadOnly(True)
        self.status.setText("Отправляю…")
        kind, text, attach = self._kind(), self.text.toPlainText().strip(), self.attach.isChecked()

        def work():
            try:
                feedback.send(kind, text, feedback.system_info(self.afterfx_path) if attach else None)
                self.bus.done.emit("")
            except RuntimeError as e:
                self.bus.done.emit(str(e))
        threading.Thread(target=work, daemon=True).start()

    def _on_done(self, error: str):
        if error:
            self.status.setText(f"⚠ {error}")
            self.text.setReadOnly(False)
            self._on_text()
            return
        QSettings().setValue("feedback_last", time.time())
        self.accept()
