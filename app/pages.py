"""Страницы вкладок: «Субтитры»/«Текст» (список + редактор), «Перевод», «Экспорт»."""
from __future__ import annotations

import re
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QAction, QColor, QFont, QGuiApplication, QTextCursor
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QFrame, QHBoxLayout, QHeaderView,
                               QLineEdit, QMenu, QPlainTextEdit, QProgressBar, QTableWidget, QTableWidgetItem,
                               QTextEdit, QVBoxLayout, QWidget)

from . import translator
from .models import Subtitle, format_time, parse_time, to_ass, to_json, to_srt, to_txt, to_vtt, wrap_text
from .theme import Card, Segmented, ToggleSwitch, button, label
from .widgets import SubtitleList, short_time


class TrackPage(Card):
    """Список субтитров и редактор выбранного."""
    selectionChanged = Signal(int)
    itemEdited = Signal(int)  # текст или время изменены
    styleEditRequested = Signal(int)  # редактировать стиль субтитра
    styleResetRequested = Signal(int)
    wordStyleRequested = Signal(int, list)  # субтитр, номера слов
    wordStyleResetRequested = Signal(int, list)
    emptyClicked = Signal()

    def __init__(self, title: str, header_buttons, actions, empty_hint: str, parent=None):
        super().__init__(parent)
        self.items: list[Subtitle] = []
        self._updating = False
        head = QHBoxLayout()
        head.setSpacing(2)
        title_lbl = label(title, "title")
        title_lbl.setMinimumWidth(title_lbl.sizeHint().width() + 4)
        head.addWidget(title_lbl)
        head.addSpacing(6)
        self.count_badge = label("0", "badge")
        head.addWidget(self.count_badge)
        head.addStretch()
        for text, tip, slot in header_buttons:
            b = button(text, "icon", tip, slot)
            b.setFixedWidth(30)
            head.addWidget(b)
        self.lay.addLayout(head)

        row = QHBoxLayout()
        row.setSpacing(8)
        for i, (text, variant, tip, slot) in enumerate(actions):
            b = button(text, variant, tip, slot)
            if variant == "primary":
                b.setMinimumHeight(42)
                row.addWidget(b, 1)
            else:
                row.addWidget(b)
        self.lay.addLayout(row)

        self.list = SubtitleList()
        self.list.itemSelectionChanged.connect(self._on_selection)
        self.list.emptyClicked.connect(self.emptyClicked.emit)
        self.list.itemDoubleClicked.connect(lambda it: self.styleEditRequested.emit(self.list.row(it)))
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._list_menu)
        self.lay.addWidget(self.list, 1)
        self.empty_hint = label(empty_hint, "muted")
        self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_hint.setWordWrap(True)
        self.lay.addWidget(self.empty_hint, 1)

        editor = QFrame()
        editor.setStyleSheet("QFrame { background: #1C1F27; border-radius: 12px; }")
        el = QVBoxLayout(editor)
        el.setContentsMargins(12, 10, 12, 12)
        el.setSpacing(8)
        times = QHBoxLayout()
        times.setSpacing(6)
        self.start_edit = QLineEdit()
        self.end_edit = QLineEdit()
        for e in (self.start_edit, self.end_edit):
            e.setPlaceholderText("00:00:00.000")
            e.setMinimumWidth(96)
            e.setFont(QFont("Consolas", 9))
            e.editingFinished.connect(self._on_time_edited)
        times.addWidget(label("С", "small"))
        times.addWidget(self.start_edit, 1)
        times.addWidget(label("до", "small"))
        times.addWidget(self.end_edit, 1)
        self.dur_label = label("", "badge")
        times.addWidget(self.dur_label)
        el.addLayout(times)
        self.text_edit = QPlainTextEdit()
        self.text_edit.setPlaceholderText("Текст (Enter — новая строка)")
        self.text_edit.setFixedHeight(72)
        self.text_edit.setStyleSheet("QPlainTextEdit { background: #16181E; font-size: 11pt; }")
        self.text_edit.textChanged.connect(self._on_text_edited)
        self.text_edit.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.text_edit.customContextMenuRequested.connect(self._text_menu)
        self.text_edit.setToolTip("Выделите слово и нажмите правой кнопкой → «Редактировать стиль слова»")
        el.addWidget(self.text_edit)
        self.lay.addWidget(editor)
        self.time_error = None  # колбэк для сообщения об ошибке времени

    # ------------------------------------------------------------ меню и слова

    def _list_menu(self, pos):
        item = self.list.itemAt(pos)
        if item is None:
            return
        row = self.list.row(item)
        menu = QMenu(self)
        edit = menu.addAction("Редактировать стиль субтитра")
        reset = menu.addAction("Сбросить свой стиль") if self.items[row].has_own_style() else None
        act = menu.exec(self.list.viewport().mapToGlobal(pos))
        if act == edit:
            self.styleEditRequested.emit(row)
        elif reset is not None and act == reset:
            self.styleResetRequested.emit(row)

    def selected_words(self) -> list[int]:
        """Номера слов под выделением (или под курсором) в поле текста."""
        text = self.text_edit.toPlainText()
        cur = self.text_edit.textCursor()
        a, b = cur.selectionStart(), cur.selectionEnd()
        out = []
        for i, m in enumerate(re.finditer(r"\S+", text)):
            if (a == b and m.start() <= a <= m.end()) or (a != b and m.start() < b and m.end() > a):
                out.append(i)
        return out

    def _text_menu(self, pos):
        row = self.current_row()
        menu = self.text_edit.createStandardContextMenu()
        if 0 <= row < len(self.items):
            words = self.selected_words()
            if not words:  # правый клик без выделения — слово под мышью
                self.text_edit.setTextCursor(self.text_edit.cursorForPosition(pos))
                words = self.selected_words()
            if words:
                first = menu.actions()[0] if menu.actions() else None
                label_ = "Редактировать стиль слова" if len(words) == 1 else f"Редактировать стиль слов ({len(words)})"
                edit = QAction("✎  " + label_, menu)
                edit.triggered.connect(lambda: self.wordStyleRequested.emit(row, words))
                menu.insertAction(first, edit)
                if any(w in self.items[row].word_styles for w in words):
                    reset = QAction("Сбросить стиль слова", menu)
                    reset.triggered.connect(lambda: self.wordStyleResetRequested.emit(row, words))
                    menu.insertAction(first, reset)
                menu.insertSeparator(first)
        menu.exec(self.text_edit.viewport().mapToGlobal(pos))

    def mark_words(self, words: list[int] | None):
        """Золотая подсветка слов, стиль которых редактируется."""
        sels = []
        if words:
            text = self.text_edit.toPlainText()
            for i, m in enumerate(re.finditer(r"\S+", text)):
                if i in words:
                    sel = QTextEdit.ExtraSelection()
                    sel.format.setBackground(QColor(255, 216, 77, 90))
                    sel.format.setForeground(QColor("#FFFFFF"))
                    c = self.text_edit.textCursor()
                    c.setPosition(m.start())
                    c.setPosition(m.end(), QTextCursor.MoveMode.KeepAnchor)
                    sel.cursor = c
                    sels.append(sel)
        self.text_edit.setExtraSelections(sels)

    def set_items(self, items: list[Subtitle], issues: dict | None = None, select: int | None = None):
        self.items = items
        self.list.set_subtitles(items, issues)
        self.count_badge.setText(str(len(items)))
        self.empty_hint.setVisible(not items)
        self.list.setVisible(bool(items))
        if select is not None and 0 <= select < len(items):
            self.list.select_row(select)
        elif self.current_row() >= len(items):
            self.list.clearSelection()
        self.load_editor()

    def current_row(self) -> int:
        return self.list.current_row()

    def select(self, row: int):
        if 0 <= row < len(self.items):
            self.list.select_row(row)
            self.load_editor()

    def load_editor(self):
        row = self.current_row()
        self._updating = True
        ok = 0 <= row < len(self.items)
        for w in (self.start_edit, self.end_edit, self.text_edit):
            w.setEnabled(ok)
        if ok:
            s = self.items[row]
            self.start_edit.setText(format_time(s.start))
            self.end_edit.setText(format_time(s.end))
            if self.text_edit.toPlainText() != s.text:
                self.text_edit.setPlainText(s.text)
            self.dur_label.setText(f"{s.end - s.start:.2f} с")
        else:
            self.start_edit.clear()
            self.end_edit.clear()
            self.text_edit.clear()
            self.dur_label.setText("")
        self._updating = False

    def _on_selection(self):
        self.load_editor()
        self.selectionChanged.emit(self.current_row())

    def _on_time_edited(self):
        row = self.current_row()
        if row < 0 or self._updating:
            return
        s = self.items[row]
        try:
            start, end = parse_time(self.start_edit.text()), parse_time(self.end_edit.text())
        except ValueError as e:
            if self.time_error:
                self.time_error(f"Неверное время: {e}")
            return
        if (start, end) != (s.start, s.end):
            s.retime(start, end)
            self.itemEdited.emit(row)
        self.load_editor()
        self.list.refresh_row(row, s)

    def _on_text_edited(self):
        row = self.current_row()
        if row < 0 or self._updating:
            return
        self.items[row].text = self.text_edit.toPlainText()
        self.items[row].clean_word_styles()
        self.list.refresh_row(row, self.items[row])
        self.itemEdited.emit(row)


# ---------------------------------------------------------------- перевод

def translated_texts(subs: list[Subtitle], translation: dict) -> list[str]:
    """Перевод для каждого субтитра. Перевод привязан к тексту оригинала, а не к номеру строки —
    поэтому разрезание, удаление и перестановка субтитров его не сбивают."""
    pairs = translation.get("pairs")
    if pairs is None and translation.get("texts"):  # старый формат: списки по порядку
        pairs = dict(zip(translation.get("source", []), translation["texts"]))
    pairs = pairs or {}
    return [pairs.get(s.text, "") for s in subs]


class _TranslateWorker(QObject):
    progress = Signal(int, int)
    done = Signal(list)
    failed = Signal(str)

    def run(self, lines, target):
        def work():
            try:
                self.done.emit(translator.translate_lines(lines, target, progress=self.progress.emit))
            except Exception as e:  # noqa: BLE001
                self.failed.emit(str(e))
        threading.Thread(target=work, daemon=True).start()


class TranslatePage(QWidget):
    """Перевод субтитров: таблица «оригинал → перевод», правка, применение и отправка в AE."""
    changed = Signal()
    applyRequested = Signal(list)  # заменить текст субтитров переводом
    sendRequested = Signal(str, list)  # язык, тексты — отправить в AE отдельной композицией

    def __init__(self, parent=None):
        super().__init__(parent)
        self.subs: list[Subtitle] = []
        self.translation: dict = {}
        self.worker = _TranslateWorker(self)
        self.worker.progress.connect(lambda d, t: (self.progress.setRange(0, t), self.progress.setValue(d)))
        self.worker.done.connect(self._on_done)
        self.worker.failed.connect(self._on_failed)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        card = Card()
        lay.addWidget(card)
        head = QHBoxLayout()
        head.addWidget(label("Перевод субтитров", "title"))
        head.addStretch()
        head.addWidget(label("Язык перевода", "muted"))
        self.lang = QComboBox()
        for code, name in translator.LANGUAGES:
            self.lang.addItem(name, code)
        self.lang.setMinimumWidth(180)
        head.addWidget(self.lang)
        self.go = button("🌐  Перевести", "primary", "Перевести все субтитры через Google Translate (нужен интернет)",
                         self.translate)
        head.addWidget(self.go)
        card.lay.addLayout(head)
        self.status = label("Перевод через Google Translate. Готовый перевод можно поправить прямо в таблице.", "muted")
        card.lay.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setFixedHeight(8)
        self.progress.setValue(0)
        card.lay.addWidget(self.progress)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Время", "Оригинал", "Перевод"])
        self.table.verticalHeader().setVisible(False)
        self.table.setWordWrap(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setStyleSheet("QTableWidget { background: #1C1F27; border: none; border-radius: 10px;"
                                 " gridline-color: #2A2E3A; } QHeaderView::section { background: #16181E;"
                                 " color: #8A90A2; border: none; padding: 6px; }")
        self.table.itemChanged.connect(self._on_cell_changed)
        card.lay.addWidget(self.table, 1)

        bottom = QHBoxLayout()
        bottom.addWidget(button("Заменить текст субтитров переводом", slot=self._apply,
                                tip="Текст субтитров заменится переводом (можно отменить Ctrl+Z)"))
        bottom.addStretch()
        bottom.addWidget(button("▶  Отправить перевод в AE", "primary", slot=self._send,
                                tip="Отдельной композицией «Субтитры EN» поверх слоёв, со стилем субтитров"))
        card.lay.addLayout(bottom)

    def set_data(self, subs: list[Subtitle], translation: dict):
        self.subs, self.translation = subs, translation
        if translation.get("lang"):
            self.lang.setCurrentIndex(max(self.lang.findData(translation["lang"]), 0))
        self._fill()

    def _texts(self) -> list[str]:
        return translated_texts(self.subs, self.translation)

    def _fill(self):
        texts = self._texts()
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.subs))
        for i, s in enumerate(self.subs):
            for col, value in enumerate([short_time(s.start), s.text.replace("\n", " "), texts[i]]):
                item = QTableWidgetItem(value)
                if col < 2:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    item.setForeground(Qt.GlobalColor.gray if col == 0 else Qt.GlobalColor.white)
                self.table.setItem(i, col, item)
        self.table.resizeRowsToContents()
        self.table.blockSignals(False)

    def _on_cell_changed(self, item: QTableWidgetItem):
        if item.column() != 2 or item.row() >= len(self.subs):
            return
        pairs = self.translation.setdefault("pairs", {})
        pairs[self.subs[item.row()].text] = item.text()
        self.changed.emit()

    def translate(self):
        if not self.subs:
            self.status.setText("Нет субтитров для перевода.")
            return
        self.go.setEnabled(False)
        self.status.setText("Перевожу…")
        self.progress.setRange(0, len(self.subs))
        self.progress.setValue(0)
        self.worker.run([s.text for s in self.subs], self.lang.currentData())

    def _on_done(self, texts: list):
        self.go.setEnabled(True)
        self.translation.clear()
        self.translation.update({"lang": self.lang.currentData(),
                                 "pairs": {s.text: t for s, t in zip(self.subs, texts)}})
        self._fill()
        self.status.setText(f"Готово: переведено {len(texts)} субтитров на «{self.lang.currentText()}».")
        self.changed.emit()

    def _on_failed(self, msg: str):
        self.go.setEnabled(True)
        self.status.setText(f"⚠ {msg}")

    def _apply(self):
        texts = self._texts()
        if any(texts):
            self.applyRequested.emit(texts)

    def _send(self):
        texts = self._texts()
        if any(texts):
            self.sendRequested.emit(self.translation.get("lang") or self.lang.currentData(), texts)


# ---------------------------------------------------------------- экспорт

FORMATS = [
    ("srt", "SRT", "Универсальный: YouTube, Premiere, DaVinci, плееры", ".srt"),
    ("vtt", "VTT", "Для сайтов и YouTube", ".vtt"),
    ("ass", "ASS — со стилем", "Шрифт, цвета, обводка и положение — для плееров и DaVinci", ".ass"),
    ("txt", "TXT", "Просто текст", ".txt"),
    ("txt_times", "TXT с таймкодами", "Текст с временем начала каждой фразы", ".txt"),
    ("json", "JSON", "Все данные, включая тайминги слов", ".json"),
]


class ExportPage(QWidget):
    def __init__(self, get_items, parent=None):
        """get_items(track) -> (список элементов, стиль, (ширина, высота))"""
        super().__init__(parent)
        self.get_items = get_items
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        card = Card()
        lay.addWidget(card)
        row = QHBoxLayout()
        row.addWidget(label("Что экспортировать", "muted"))
        self.track = Segmented(["Субтитры", "Перевод"])
        self.track.currentChanged.connect(lambda _: self.refresh())
        row.addWidget(self.track)
        row.addStretch()
        card.lay.addLayout(row)
        row2 = QHBoxLayout()
        row2.addWidget(label("Формат", "muted"))
        self.fmt = QComboBox()
        for key, name, desc, _ext in FORMATS:
            self.fmt.addItem(f"{name}  —  {desc}", key)
        self.fmt.currentIndexChanged.connect(lambda _: self.refresh())
        row2.addWidget(self.fmt, 1)
        card.lay.addLayout(row2)
        self.wrap = ToggleSwitch("Переносить строки как в стиле (символов в строке)")
        self.wrap.setChecked(True)
        self.wrap.toggled.connect(lambda _: self.refresh())
        card.lay.addWidget(self.wrap)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFont(QFont("Consolas", 10))
        card.lay.addWidget(self.preview, 1)
        bottom = QHBoxLayout()
        self.info = label("", "muted")
        bottom.addWidget(self.info, 1)
        bottom.addWidget(button("Копировать", slot=self._copy))
        bottom.addWidget(button("⤓  Сохранить файл…", "primary", slot=self._save))
        card.lay.addLayout(bottom)

    def _track(self) -> str:
        return ["subs", "translation"][self.track.group.checkedId()]

    def render(self) -> str:
        items, style, (w, h) = self.get_items(self._track())
        if self.wrap.isChecked():
            items = [Subtitle(s.start, s.end, wrap_text(s.text, (s.style or style).max_chars), s.words, s.style)
                     for s in items]
        key = self.fmt.currentData()
        if key == "srt":
            return to_srt(items)
        if key == "vtt":
            return to_vtt(items)
        if key == "ass":
            return to_ass(items, style, w, h)
        if key == "txt":
            return to_txt(items)
        if key == "txt_times":
            return to_txt(items, True)
        return to_json(items)

    def refresh(self):
        text = self.render()
        self.preview.setPlainText(text)
        items, _s, _wh = self.get_items(self._track())
        self.info.setText(f"Элементов: {len(items)}")

    def _copy(self):
        QGuiApplication.clipboard().setText(self.preview.toPlainText())
        self.info.setText("Скопировано в буфер обмена")

    def _save(self):
        key = self.fmt.currentData()
        ext = next(f[3] for f in FORMATS if f[0] == key)
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить", f"subtitles{ext}", f"*{ext}")
        if path:
            Path(path).write_text(self.render(), encoding="utf-8-sig" if key in ("srt", "ass") else "utf-8")
            self.info.setText(f"Сохранено: {Path(path).name}")
