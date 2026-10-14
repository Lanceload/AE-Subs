"""Горячие клавиши в стиле After Effects или Premiere Pro + окно-шпаргалка."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QHeaderView, QTableWidget, QTableWidgetItem

from .dialogs import BaseDialog
from .theme import button, label

SCHEMES = {"ae": "After Effects", "premiere": "Premiere Pro"}

# id, название, клавиши AE, клавиши Premiere
HOTKEYS = [
    ("play", "Воспроизвести / пауза", ["Space"], ["Space"]),
    ("shuttle_fwd", "Воспроизвести (ещё раз — быстрее)", [], ["L"]),
    ("shuttle_stop", "Стоп", [], ["K"]),
    ("shuttle_back", "Назад на 1 секунду", [], ["J"]),
    ("frame_prev", "Кадр назад", ["PgUp", "Ctrl+Left"], ["Left"]),
    ("frame_next", "Кадр вперёд", ["PgDown", "Ctrl+Right"], ["Right"]),
    ("frames_prev", "Несколько кадров назад (AE: 10, Premiere: 5)", ["Shift+PgUp"], ["Shift+Left"]),
    ("frames_next", "Несколько кадров вперёд", ["Shift+PgDown"], ["Shift+Right"]),
    ("home", "В начало", ["Home"], ["Home"]),
    ("end", "В конец", ["End"], ["End"]),
    ("item_prev", "Предыдущий субтитр", ["J"], ["Up"]),
    ("item_next", "Следующий субтитр", ["K"], ["Down"]),
    ("set_in", "Начало выделенного — на курсор", ["Alt+["], ["I"]),
    ("set_out", "Конец выделенного — на курсор", ["Alt+]"], ["O"]),
    ("split", "Разрезать по курсору", ["Ctrl+Shift+D"], ["Ctrl+K"]),
    ("duplicate", "Дублировать выделенное", ["Ctrl+D"], ["Ctrl+D"]),
    ("delete", "Удалить выделенное", ["Delete"], ["Delete"]),
    ("add", "Добавить субтитр", ["Ctrl+N"], ["Ctrl+N"]),
    ("tl_zoom_in", "Таймлайн крупнее", ["="], ["="]),
    ("tl_zoom_out", "Таймлайн мельче", ["-"], ["-"]),
    ("tl_fit", "Таймлайн целиком", ["\\"], ["\\"]),
    ("undo", "Отменить", ["Ctrl+Z"], ["Ctrl+Z"]),
    ("redo", "Повторить", ["Ctrl+Shift+Z", "Ctrl+Y"], ["Ctrl+Shift+Z", "Ctrl+Y"]),
    ("recognize", "Распознать речь", ["Ctrl+R"], ["Ctrl+R"]),
    ("send", "Отправить в After Effects", ["Ctrl+Return"], ["Ctrl+Return"]),
    ("save", "Сохранить проект", ["Ctrl+S"], ["Ctrl+S"]),
    ("open", "Открыть проект", ["Ctrl+O"], ["Ctrl+O"]),
    ("import_srt", "Импорт SRT", ["Ctrl+I"], ["Ctrl+I"]),
    ("paste_text", "Вставить текст", ["Ctrl+Shift+V"], ["Ctrl+Shift+V"]),
    ("tab1", "Вкладка «Субтитры»", ["Ctrl+1"], ["Ctrl+1"]),
    ("tab2", "Вкладка «Перевод»", ["Ctrl+2"], ["Ctrl+2"]),
    ("export", "Экспорт", ["Ctrl+E"], ["Ctrl+E"]),
    ("fullscreen", "Превью на всё окно", ["F11"], ["F11"]),
    ("escape", "Выйти из превью на всё окно / завершить редактирование стиля", ["Escape"], ["Escape"]),
    ("help", "Шпаргалка по клавишам", ["F1"], ["F1"]),
]


def keys_for(scheme: str) -> dict[str, list[str]]:
    return {hid: (ae if scheme == "ae" else pr) for hid, _t, ae, pr in HOTKEYS}


class HotkeysDialog(BaseDialog):
    def __init__(self, scheme: str, parent=None):
        super().__init__("Горячие клавиши", parent)
        self.resize(620, 720)
        row = QHBoxLayout()
        row.addWidget(label("Раскладка", "muted"))
        self.scheme = QComboBox()
        for key, name in SCHEMES.items():
            self.scheme.addItem(f"Как в {name}", key)
        self.scheme.setCurrentIndex(max(self.scheme.findData(scheme), 0))
        self.scheme.currentIndexChanged.connect(self._fill)
        row.addWidget(self.scheme, 1)
        self.lay.addLayout(row)
        self.lay.addWidget(label("Клавиши не срабатывают, пока курсор стоит в поле ввода текста.", "small"))
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Действие", "Клавиши"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setStyleSheet("QTableWidget { background: #1C1F27; border: none; border-radius: 10px;"
                                 " gridline-color: #2A2E3A; } QHeaderView::section { background: #16181E;"
                                 " color: #8A90A2; border: none; padding: 6px; }")
        self.lay.addWidget(self.table, 1)
        r = QHBoxLayout()
        r.addStretch()
        r.addWidget(button("Готово", "primary", slot=self.accept))
        self.lay.addLayout(r)
        self._fill()

    def chosen(self) -> str:
        return self.scheme.currentData()

    def _fill(self):
        keys = keys_for(self.chosen())
        rows = [(t, keys[h]) for h, t, _a, _p in HOTKEYS if keys[h]]
        self.table.setRowCount(len(rows))
        for i, (title, ks) in enumerate(rows):
            self.table.setItem(i, 0, QTableWidgetItem(title))
            k = QTableWidgetItem("   или   ".join(ks))
            k.setForeground(Qt.GlobalColor.white)
            self.table.setItem(i, 1, k)
