"""Главное окно AE Subs: субтитры, перевод, панели (как в After Effects), превью, таймлайн."""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QGuiApplication, QImage, QKeySequence, QShortcut
from PySide6.QtWidgets import (QDialog, QDockWidget, QFileDialog, QFrame, QHBoxLayout, QInputDialog, QMainWindow,
                               QMenu, QMessageBox, QProgressDialog, QStackedWidget, QTabBar, QVBoxLayout, QWidget)

from . import ae_bridge, fonts, sharing, updater
from .dialogs import ExportDialog, FeedbackDialog, PasteTextDialog, TimingDialog, ValidationDialog
from .gallery import StyleGallery, ensure_default_presets
from .hotkeys import HotkeysDialog, keys_for
from .jsx_builder import build_jsx, build_query_jsx, build_sync_jsx
from .anim import anim_state, durations
from .models import (AESettings, ProjectState, Style, Subtitle, close_gaps, distribute_words, parse_srt,
                     style_diff, word_style)
from .pages import TrackPage, TranslatePage, translated_texts
from .paths import APP_DIR, PRESETS_DIR, STATE_FILE
from .player import VideoPlayer
from .preview import PreviewItem, PreviewWidget
from .recognize_dialog import RecognizeDialog
from .render import current_word
from .style_panel import AEPanel, StylePanel, font_has_cyrillic
from .theme import (BORDER, MUTED, TEXT, Card, PanelTitleBar, Toast, button, dark_title_bar, label,
                    logo_pixmap)
from .timeline import TimelineView
from .validation import autofix, validate
from .version import VERSION

PROJECTS_DIR = APP_DIR / "projects"

TABS_QSS = f"""
QTabBar {{ background: transparent; }}
QTabBar::tab {{
    background: transparent; color: {MUTED}; padding: 9px 16px; margin: 10px 2px 0 2px;
    border-top-left-radius: 10px; border-top-right-radius: 10px; border: 1px solid transparent; border-bottom: none;
    min-width: 90px;
}}
QTabBar::tab:hover {{ color: {TEXT}; background: #1A1D24; }}
QTabBar::tab:selected {{ color: {TEXT}; background: #0E0F13; border-color: {BORDER}; }}
"""

EDIT_BAR_QSS = ("QFrame#EditBar { background: rgba(255,216,77,0.10); border: 1px solid rgba(255,216,77,0.55);"
                " border-radius: 10px; }")


class _Bus(QObject):
    """Сигналы из фоновых потоков (проверка обновлений, загрузка)."""
    updateFound = Signal(dict)
    updateReady = Signal(object)
    updateFailed = Signal(str)
    updateProgress = Signal(object)  # (скачано, всего, скорость, источник)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"AE Subs {VERSION}")
        self.resize(1560, 980)
        self.setMinimumSize(1100, 700)
        self.settings = QSettings("AE_Subs", "AE_Subs")
        self.scheme = self.settings.value("hotkey_scheme", "ae")
        self.doc = ProjectState()
        self.project_path: Path | None = None
        self.playhead = 0.0
        self.history: list[str] = []
        self.hist_pos = -1
        self._restoring = False
        self.ae_job: ae_bridge.AEJob | None = None
        self._shortcuts: list[QShortcut] = []
        self.mode = "edit"
        # что сейчас редактирует панель стиля: None — общий стиль, ("sub", i), ("words", i, [слова])
        self.style_target: tuple | None = None
        self.preview_full = False
        self._full_saved: dict = {}
        self.bus = _Bus()
        self.bus.updateFound.connect(self._on_update_found)
        self.bus.updateReady.connect(self._on_update_ready)
        self.bus.updateFailed.connect(self._on_update_failed)
        self.bus.updateProgress.connect(self._on_update_progress)

        self.player = VideoPlayer(self)
        self.player.frameReady.connect(self._on_video_frame)
        self.player.timeChanged.connect(lambda t: self._set_playhead(t, follow=True))
        self.player.playingChanged.connect(self._on_playing)
        self.player.failed.connect(lambda m: self.toast.show_message(m, "error"))
        self.player.waveformReady.connect(self._on_waveform)
        self.clock = QTimer(self, interval=33, timeout=self._tick)  # проигрывание без видео
        self.demo_timer = QTimer(self, interval=16, timeout=self._demo_tick)  # показ эффекта появления
        self._demo = None
        self.snap_timer = QTimer(self, singleShot=True, interval=450, timeout=self._snapshot)
        self.save_timer = QTimer(self, singleShot=True, interval=1500, timeout=self._autosave)

        fonts.warm_up()
        ensure_default_presets()
        self._build_ui()
        self._build_hotkeys()
        self._load_state()
        self._snapshot()
        self.ae_timer = QTimer(self, timeout=self._update_ae_chip)
        self.ae_timer.start(3000)
        self._update_ae_chip()
        QTimer.singleShot(1200, self._startup_tasks)

    def showEvent(self, event):
        super().showEvent(event)
        dark_title_bar(self)

    # ============================================================ интерфейс

    def _build_ui(self):
        self.header = self._build_header()
        self.setMenuWidget(self.header)
        self.setDockNestingEnabled(True)
        self.setCorner(Qt.Corner.BottomLeftCorner, Qt.DockWidgetArea.BottomDockWidgetArea)
        self.setCorner(Qt.Corner.BottomRightCorner, Qt.DockWidgetArea.BottomDockWidgetArea)

        self.central = QStackedWidget()
        self.setCentralWidget(self.central)
        self.central.addWidget(self._padded(self._build_center(), 0))
        self.translate_page = TranslatePage()
        self.translate_page.changed.connect(self._changed)
        self.translate_page.applyRequested.connect(self._apply_translation)
        self.translate_page.sendRequested.connect(self._send_translation)
        self.central.addWidget(self._padded(self.translate_page, 16))

        self.dock_list = self._dock("Список", "list", self._build_left(), Qt.DockWidgetArea.LeftDockWidgetArea)
        self.dock_gallery = self._dock("Галерея стилей", "gallery", self._build_gallery(),
                                       Qt.DockWidgetArea.RightDockWidgetArea)
        self.dock_style = self._dock("Стиль", "style", self._build_style_column(), Qt.DockWidgetArea.RightDockWidgetArea)
        self.splitDockWidget(self.dock_gallery, self.dock_style, Qt.Orientation.Vertical)
        self.dock_timeline = self._dock("Таймлайн", "timeline", self._build_timeline(),
                                        Qt.DockWidgetArea.BottomDockWidgetArea)
        self.docks = {"list": self.dock_list, "gallery": self.dock_gallery, "style": self.dock_style,
                      "timeline": self.dock_timeline}
        self.resizeDocks([self.dock_list, self.dock_style], [400, 440], Qt.Orientation.Horizontal)
        self.resizeDocks([self.dock_gallery, self.dock_timeline], [140, 200], Qt.Orientation.Vertical)
        self._default_state = self.saveState()
        self.dock_vis = {"edit": {k: True for k in self.docks}, "full": {k: False for k in self.docks}}
        self.toast = Toast(self)

    def _dock(self, title: str, name: str, widget: QWidget, area) -> QDockWidget:
        d = QDockWidget(title, self)
        d.setObjectName(f"dock_{name}")
        d.setWidget(widget)
        d.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetClosable
                      | QDockWidget.DockWidgetFeature.DockWidgetMovable
                      | QDockWidget.DockWidgetFeature.DockWidgetFloatable)
        # без названия: тонкая полоска для перетаскивания и крестик (как у панелей AE)
        d.setTitleBarWidget(PanelTitleBar(d))
        widget.setProperty("card", "dock")  # карточка продолжает полоску панели — одна цельная панель
        widget.style().unpolish(widget)  # стиль уже применён как к обычной карточке — пересчитать
        widget.style().polish(widget)
        self.addDockWidget(area, d)
        return d

    @staticmethod
    def _padded(w: QWidget, m: int) -> QWidget:
        holder = QWidget()
        lay = QVBoxLayout(holder)
        lay.setContentsMargins(m, m, m, m)
        lay.addWidget(w)
        return holder

    def _build_header(self) -> QWidget:
        header = QFrame()
        header.setObjectName("Header")
        header.setFixedHeight(56)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(14, 0, 12, 0)
        hl.setSpacing(4)
        logo = label("")
        logo.setPixmap(logo_pixmap(28))
        hl.addWidget(logo)
        hl.addSpacing(4)
        hl.addWidget(label("AE Subs", "title"))
        hl.addSpacing(10)
        self.menus = {}
        for key, title in (("file", "Файл"), ("edit", "Правка"), ("window", "Окно"), ("help", "Справка")):
            b = button(title, "menu")
            m = QMenu(self)
            m.aboutToShow.connect(lambda k=key, menu=m: self._fill_menu(k, menu))
            b.setMenu(m)
            hl.addWidget(b)
            self.menus[key] = m
        hl.addSpacing(10)
        self.tabs = QTabBar()
        self.tabs.setStyleSheet(TABS_QSS)
        self.tabs.setDrawBase(False)
        self.tabs.setExpanding(False)
        for text in ("🎬  Субтитры", "🌐  Перевод"):
            self.tabs.addTab(text)
        self.tabs.currentChanged.connect(self._on_tab)
        hl.addWidget(self.tabs, 0, Qt.AlignmentFlag.AlignBottom)
        hl.addStretch()
        self.undo_btn = button("↶", "icon", "Отменить (Ctrl+Z)", self.undo)
        self.redo_btn = button("↷", "icon", "Повторить (Ctrl+Y)", self.redo)
        hl.addWidget(self.undo_btn)
        hl.addWidget(self.redo_btn)
        hl.addSpacing(6)
        self.ae_chip = label("● After Effects", "chip-off")
        self.ae_chip.setFixedHeight(26)
        hl.addWidget(self.ae_chip, 0, Qt.AlignmentFlag.AlignVCenter)
        hl.addSpacing(8)
        self.send_info = label("", "small")
        self.send_info.setMaximumWidth(220)
        hl.addWidget(self.send_info)
        # кнопка отправки — в шапке, чтобы была видна при любом расположении панелей
        self.send_btn = button("▶  Отправить в AE", "primary", "Ctrl+Enter", self.send_current)
        self.send_btn.setMinimumHeight(38)
        hl.addWidget(self.send_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        return header

    def _act(self, menu: QMenu, text: str, slot, hotkey: str | None = None, checkable=False, checked=False):
        a = QAction(text, menu)
        keys = keys_for(self.scheme).get(hotkey, []) if hotkey else []
        if keys:
            a.setShortcut(QKeySequence(keys[0]))
            a.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)  # только подпись — сами клавиши в _build_hotkeys
        a.setCheckable(checkable)
        a.setChecked(checked)
        a.triggered.connect(slot)
        menu.addAction(a)
        return a

    def _fill_menu(self, key: str, m: QMenu):
        m.clear()
        act = self._act
        if key == "file":
            act(m, "Новый проект", self.new_project)
            act(m, "Открыть проект…", self.open_project, "open")
            recent = m.addMenu("Проекты композиций")
            files = sorted(PROJECTS_DIR.glob("*.aesubs"), key=lambda p: p.stat().st_mtime, reverse=True) \
                if PROJECTS_DIR.is_dir() else []
            for p in files[:15]:
                act(recent, p.stem, lambda _=False, path=p: self._open_path(path))
            if not files:
                recent.addAction("— пока нет —").setEnabled(False)
            act(m, "Сохранить проект", self.save_project, "save")
            act(m, "Сохранить проект как…", lambda: self.save_project(ask=True))
            m.addSeparator()
            act(m, "Импорт SRT…", self.import_srt, "import_srt")
            act(m, "Экспорт…", self.export_dialog, "export")
            act(m, "Сохранить скрипт .jsx…", self.save_jsx)
            m.addSeparator()
            styles = m.addMenu("Стили")
            act(styles, "Импорт стилей из файла…", self.import_styles)
            act(styles, "Вставить код стиля из буфера", self.paste_style_code)
            act(styles, "Экспорт всех стилей в файл…", self.export_styles)
            styles.addSeparator()
            act(styles, "Облачная папка стилей…", self.setup_cloud)
            if self.settings.value("cloud_dir", ""):
                act(styles, "Синхронизировать сейчас", lambda: self.cloud_sync(verbose=True))
            m.addSeparator()
            act(m, "Выход", self.close)
        elif key == "edit":
            act(m, "Отменить", self.undo, "undo")
            act(m, "Повторить", self.redo, "redo")
            m.addSeparator()
            act(m, "Добавить субтитр", self.add_item, "add")
            act(m, "Дублировать", self.duplicate_item, "duplicate")
            act(m, "Разрезать по курсору", self.split_item, "split")
            act(m, "Удалить", self.delete_items, "delete")
            m.addSeparator()
            row = self.current_row()
            if row >= 0:
                act(m, "Редактировать стиль субтитра", lambda: self.enter_sub_style(row))
                if self.doc.subtitles[row].has_own_style():
                    act(m, "Сбросить свой стиль субтитра", lambda: self.reset_sub_style(row))
                m.addSeparator()
            act(m, "Вставить текст…", self.paste_text, "paste_text")
            act(m, "Авто-тайминг…", self.auto_timing)
            act(m, "Сомкнуть субтитры (убрать паузы)", self.close_gaps)
            act(m, "Сортировать по времени", self.sort_current)
            m.addSeparator()
            keys = m.addMenu("Раскладка клавиш")
            for sk, name in (("ae", "Как в After Effects"), ("premiere", "Как в Premiere Pro")):
                act(keys, name, lambda _=False, s=sk: self.set_scheme(s), checkable=True, checked=self.scheme == sk)
        elif key == "window":
            for name, d in self.docks.items():
                a = act(m, d.windowTitle(), lambda on, dock=d: dock.setVisible(on), checkable=True,
                        checked=d.isVisible())
                a.setEnabled(self.mode != "full" and not self.preview_full)
            m.addSeparator()
            act(m, "Выйти из превью на всё окно" if self.preview_full else "Превью на всё окно",
                self.toggle_preview_full, "fullscreen")
            m.addSeparator()
            act(m, "Сбросить расположение окон", self.reset_layout)
        elif key == "help":
            act(m, "Горячие клавиши", self.show_hotkeys, "help")
            m.addSeparator()
            act(m, "Обратная связь… (ошибка, идея, отзыв)", self.send_feedback)
            m.addSeparator()
            act(m, "Проверить обновления", lambda: self.check_updates(verbose=True))
            act(m, "Все версии на GitHub", self.open_github)
            m.addSeparator()
            act(m, f"О программе (версия {VERSION})", self.about)

    def _build_left(self) -> QWidget:
        self.subs_page = TrackPage(
            "Субтитры",
            [("⟲", "Забрать правки из After Effects (текст и время)", self.sync_from_ae),
             ("＋", "Добавить субтитр", self.add_item), ("✕", "Удалить выбранные", self.delete_items),
             ("↑", "Выше", lambda: self.move_item(-1)), ("↓", "Ниже", lambda: self.move_item(1))],
            [("✦  Распознать речь", "primary", "Создать субтитры из речи в композиции AE или в файле", self.recognize),
             ("Вставить текст", None, "Вставить сценарий и разбить на субтитры", self.paste_text),
             ("SRT", None, "Импорт субтитров из файла SRT", self.import_srt)],
            "Здесь появятся субтитры.\nНажмите «Распознать речь» — программа сама послушает композицию After Effects.")
        p = self.subs_page
        p.selectionChanged.connect(self._on_list_selection)
        p.itemEdited.connect(self._on_item_edited)
        p.time_error = lambda m: self.toast.show_message(m, "error")
        p.styleEditRequested.connect(self.enter_sub_style)
        p.styleResetRequested.connect(self.reset_sub_style)
        p.wordStyleRequested.connect(self.enter_word_style)
        p.wordStyleResetRequested.connect(self.reset_word_style)
        p.emptyClicked.connect(self.finish_style_edit)
        return p

    def _build_center(self) -> QWidget:
        pcard = Card(padding=10, spacing=8)
        self.preview = PreviewWidget()
        self.preview.setMinimumHeight(200)
        self.preview.moved.connect(self._on_preview_drag)
        self.preview.moveFinished.connect(lambda _k: self._changed())
        self.preview.itemClicked.connect(lambda key: self._on_timeline_select(*key))
        self.preview.emptyClicked.connect(self.finish_style_edit)
        pcard.lay.addWidget(self.preview, 1)
        tr = QHBoxLayout()
        tr.setSpacing(4)
        self.play_btn = button("▶", "icon", "Воспроизвести / пауза (пробел)", self.toggle_play)
        tr.addWidget(self.play_btn)
        self.time_lbl = label("00:00.0", "badge")
        tr.addWidget(self.time_lbl)
        tr.addSpacing(6)
        tr.addWidget(button("🎞 Из AE", "ghost", "Взять видео из открытой композиции After Effects", self.video_from_ae))
        tr.addWidget(button("📂 Видео", "ghost", "Открыть видеофайл для предпросмотра", self.open_video))
        tr.addWidget(button("🖼 Фон", "ghost", "Картинка-фон, если видео нет", self.load_background))
        tr.addStretch()
        self.video_lbl = label("видео не открыто", "small")
        self.video_lbl.setMaximumWidth(180)
        tr.addWidget(self.video_lbl)
        safe = button("▣", "icon", "Показать безопасную зону кадра")
        safe.setCheckable(True)
        safe.toggled.connect(self._toggle_safe)
        tr.addWidget(safe)
        self.full_btn = button("⤢", "icon", "Превью на всё окно (F11, выход — Esc)", self.toggle_preview_full)
        tr.addWidget(self.full_btn)
        pcard.lay.addLayout(tr)
        return pcard

    def _build_gallery(self) -> QWidget:
        card = Card(padding=8, spacing=6)
        row = QHBoxLayout()
        row.setSpacing(4)
        row.addStretch()
        for text, tip, slot in [("⤓ Импорт", "Импорт стилей из файла", self.import_styles),
                                ("📋 Код", "Вставить код стиля из буфера обмена", self.paste_style_code),
                                ("☁ Облако", "Синхронизация стилей через облачную папку", self.setup_cloud)]:
            row.addWidget(button(text, "ghost", tip, slot))
        card.lay.addLayout(row)
        self.gallery = StyleGallery()
        self.gallery.applied.connect(self._apply_preset)
        self.gallery.saveRequested.connect(self.save_preset)
        self.gallery.shareRequested.connect(self.share_style)
        self.gallery.exportRequested.connect(lambda n: self.export_styles([n]))
        self.gallery.deleted.connect(self._on_preset_deleted)
        card.lay.addWidget(self.gallery)
        card.lay.addStretch()
        return card

    def _build_style_column(self) -> QWidget:
        scard = Card(padding=12, spacing=8)
        head = QHBoxLayout()
        self.style_title = label("Стиль субтитров", "title")
        head.addWidget(self.style_title)
        head.addStretch()
        self.style_hint = label("", "small")
        head.addWidget(self.style_hint)
        scard.lay.addLayout(head)
        # полоска режима «свой стиль субтитра / слова»
        self.edit_bar = QFrame()
        self.edit_bar.setObjectName("EditBar")
        self.edit_bar.setStyleSheet(EDIT_BAR_QSS)
        eb = QHBoxLayout(self.edit_bar)
        eb.setContentsMargins(10, 6, 6, 6)
        self.edit_info = label("", "small")
        self.edit_info.setStyleSheet("color: #FFD84D;")
        self.edit_info.setWordWrap(True)
        eb.addWidget(self.edit_info, 1)
        eb.addWidget(button("Сбросить", "ghost", "Вернуть общий стиль", self._reset_current_target))
        eb.addWidget(button("✓ Готово", "chip", "Завершить (или клик в пустое место, Esc)", self.finish_style_edit))
        self.edit_bar.hide()
        scard.lay.addWidget(self.edit_bar)
        self.style_panel = StylePanel()
        self.ae_panel = AEPanel()
        self.style_panel.add_page("gear", "After Effects", self.ae_panel)
        self.style_panel.changed.connect(self._on_style_changed)
        self.style_panel.animPreview.connect(self.play_anim_demo)
        self.ae_panel.changed.connect(self._on_ae_changed)
        scard.lay.addWidget(self.style_panel, 1)
        scard.setMinimumWidth(360)
        return scard

    def _build_timeline(self) -> QWidget:
        tcard = Card(padding=10, spacing=4)
        self.timeline = TimelineView()
        c = self.timeline.canvas
        c.seekRequested.connect(self.seek)
        c.selected.connect(self._on_timeline_select)
        c.edited.connect(self._on_timeline_edited)
        c.addRequested.connect(self._on_timeline_add)
        c.blockDoubleClicked.connect(lambda _t, i: self.enter_sub_style(i))
        c.resetStyleRequested.connect(lambda _t, i: self.reset_sub_style(i))
        c.emptyClicked.connect(self.finish_style_edit)
        tcard.lay.addWidget(self.timeline)
        return tcard

    # ============================================================ панели, режимы, полный экран

    def _set_mode(self, mode: str):
        if mode == self.mode:
            return
        self.dock_vis[self.mode] = {k: d.isVisible() for k, d in self.docks.items()}
        self.mode = mode
        for k, d in self.docks.items():
            d.setVisible(self.dock_vis[mode].get(k, True))

    def reset_layout(self):
        if self.preview_full:
            self.toggle_preview_full()
        self.restoreState(self._default_state)
        self.dock_vis = {"edit": {k: True for k in self.docks}, "full": {k: False for k in self.docks}}
        current, self.mode = self.mode, "edit"
        self._set_mode(current)
        self.toast.show_message("Расположение окон сброшено", "info", 1500)

    def toggle_preview_full(self):
        """Превью на всё окно программы: панели и шапка прячутся, Esc — вернуть."""
        if self.tabs.currentIndex() != 0 and not self.preview_full:
            self.tabs.setCurrentIndex(0)
        if not self.preview_full:
            self._full_saved = {k: d.isVisible() for k, d in self.docks.items()}
            for d in self.docks.values():
                d.hide()
            self.header.hide()
            self.preview_full = True
            self.full_btn.setText("⤡")
            self.toast.show_message("Превью на всё окно. Esc или ⤡ — вернуть панели", "info", 2500)
        else:
            for k, d in self.docks.items():
                d.setVisible(self._full_saved.get(k, True))
            self.header.show()
            self.preview_full = False
            self.full_btn.setText("⤢")

    def on_escape(self):
        if self.preview_full:
            self.toggle_preview_full()
        else:
            self.finish_style_edit()

    # ============================================================ горячие клавиши

    def _hotkey_actions(self) -> dict:
        frames = 10 if self.scheme == "ae" else 5
        return {
            "play": self.toggle_play, "shuttle_fwd": self.shuttle_forward, "shuttle_stop": self.stop_play,
            "shuttle_back": lambda: self.seek(max(self.playhead - 1, 0)),
            "frame_prev": lambda: self.step_frames(-1), "frame_next": lambda: self.step_frames(1),
            "frames_prev": lambda: self.step_frames(-frames), "frames_next": lambda: self.step_frames(frames),
            "home": lambda: self.seek(0), "end": lambda: self.seek(self._content_end()),
            "item_prev": lambda: self.select_neighbor(-1), "item_next": lambda: self.select_neighbor(1),
            "set_in": lambda: self.set_edge_to_playhead("start"), "set_out": lambda: self.set_edge_to_playhead("end"),
            "split": self.split_item, "duplicate": self.duplicate_item, "delete": self.delete_items,
            "add": self.add_item, "tl_zoom_in": lambda: self.timeline.zoom(1.4),
            "tl_zoom_out": lambda: self.timeline.zoom(1 / 1.4), "tl_fit": self.timeline.fit,
            "undo": self.undo, "redo": self.redo, "recognize": self.recognize, "send": self.send_current,
            "save": self.save_project, "open": self.open_project, "import_srt": self.import_srt,
            "paste_text": self.paste_text, "help": self.show_hotkeys, "export": self.export_dialog,
            "fullscreen": self.toggle_preview_full, "escape": self.on_escape,
            "tab1": lambda: self.tabs.setCurrentIndex(0), "tab2": lambda: self.tabs.setCurrentIndex(1),
        }

    def _build_hotkeys(self):
        for sc in self._shortcuts:
            sc.setEnabled(False)
            sc.deleteLater()
        self._shortcuts = []
        actions = self._hotkey_actions()
        for hid, keys in keys_for(self.scheme).items():
            for k in keys:
                sc = QShortcut(QKeySequence(k), self)
                sc.activated.connect(actions[hid])
                self._shortcuts.append(sc)

    def set_scheme(self, scheme: str):
        self.scheme = scheme
        self.settings.setValue("hotkey_scheme", scheme)
        self._build_hotkeys()
        self.toast.show_message(f"Раскладка клавиш: как в {'After Effects' if scheme == 'ae' else 'Premiere Pro'}",
                                "info", 1800)

    def show_hotkeys(self):
        dlg = HotkeysDialog(self.scheme, self)
        dlg.exec()
        if dlg.chosen() != self.scheme:
            self.set_scheme(dlg.chosen())

    def _fps(self) -> float:
        return float(self.doc.comp.get("fps") or self.ae_panel.get_settings().comp_fps or 30)

    def step_frames(self, n: int):
        self.stop_play()
        self.seek(max(self.playhead + n / self._fps(), 0.0))

    def _content_end(self) -> float:
        return max([s.end for s in self.doc.subtitles] + [self.doc.comp.get("duration") or 0])

    def shuttle_forward(self):
        if self.player.has_media():
            rate = self.player.player.playbackRate()
            new = 1.0 if not self.player.is_playing() else min(rate * 2, 8.0)
            self.player.player.setPlaybackRate(new)
            if not self.player.is_playing():
                self.player.player.play()
            self.toast.show_message(f"▶ ×{new:g}", "info", 700)
        elif not self.clock.isActive():
            self.toggle_play()

    def stop_play(self):
        if self.player.has_media():
            self.player.pause()
            self.player.player.setPlaybackRate(1.0)
        if self.clock.isActive():
            self.clock.stop()
            self.play_btn.setText("▶")

    def select_neighbor(self, delta: int):
        lst = self.doc.subtitles
        if not lst:
            return
        row = self.current_row()
        if row < 0:
            later = [i for i, s in enumerate(lst) if s.start > self.playhead]
            row = (later[0] - 1) if later else len(lst)
        new = min(max(row + delta, 0), len(lst) - 1)
        self.select_item(new)
        self.seek(lst[new].start + 0.001)

    def set_edge_to_playhead(self, edge: str):
        lst, row = self.doc.subtitles, self.current_row()
        if not 0 <= row < len(lst):
            return
        s = lst[row]
        t = round(self.playhead, 3)
        if edge == "start" and t < s.end - 0.05:
            s.retime(t, s.end)
        elif edge == "end" and t > s.start + 0.05:
            s.retime(s.start, t)
        else:
            return
        self._refresh_views(select=row)
        self._changed()

    # ============================================================ выбор

    def current_row(self) -> int:
        return self.subs_page.current_row()

    def select_item(self, i: int):
        self.subs_page.select(i)
        self.timeline.canvas.sel = i
        self.timeline.canvas.update()

    def _on_tab(self, index: int):
        if index == 0:
            self._set_mode("edit")
            self.central.setCurrentIndex(0)
            self.send_btn.setVisible(True)
        else:
            self.finish_style_edit()
            if self.preview_full:
                self.toggle_preview_full()
            self.send_btn.setVisible(False)  # у «Перевода» своя кнопка отправки
            self._set_mode("full")
            self.central.setCurrentIndex(1)
            self.translate_page.set_data(self.doc.subtitles, self.doc.translation)
        self._refresh_views()

    # ============================================================ свой стиль субтитра и слов

    def _target_valid(self) -> bool:
        t = self.style_target
        return t is not None and 0 <= t[1] < len(self.doc.subtitles)

    def _base_style(self, i: int) -> Style:
        s = self.doc.subtitles[i]
        return s.style or self.doc.style

    def enter_sub_style(self, i: int):
        if not 0 <= i < len(self.doc.subtitles):
            return
        if self.tabs.currentIndex() != 0:
            self.tabs.setCurrentIndex(0)
        self.style_target = ("sub", i)
        self.select_item(i)
        self._show_sub(i)
        self.dock_style.show()
        self._bind_style()
        self.toast.show_message(f"Свой стиль субтитра №{i + 1}. Клик в пустое место — готово", "info", 2600)

    def enter_word_style(self, i: int, words: list):
        if not 0 <= i < len(self.doc.subtitles) or not words:
            return
        self.style_target = ("words", i, sorted(words))
        self._show_sub(i)
        self.dock_style.show()
        self._bind_style()

    def finish_style_edit(self):
        if self.style_target is None:
            return
        self.style_target = None
        self._bind_style()
        self._refresh_views(select=self.current_row())
        self.toast.show_message("Стиль применён", "ok", 1200)

    def reset_sub_style(self, i: int):
        if 0 <= i < len(self.doc.subtitles):
            s = self.doc.subtitles[i]
            s.style, s.word_styles = None, {}
            if self.style_target and self.style_target[1] == i:
                self.style_target = None
            self._bind_style()
            self._refresh_views(select=i)
            self._changed()
            self.toast.show_message(f"У субтитра №{i + 1} снова общий стиль", "info")

    def reset_word_style(self, i: int, words: list):
        if 0 <= i < len(self.doc.subtitles):
            s = self.doc.subtitles[i]
            for w in words:
                s.word_styles.pop(w, None)
            self._bind_style()
            self._refresh_views(select=i)
            self._changed()

    def _reset_current_target(self):
        t = self.style_target
        if not self._target_valid():
            return
        if t[0] == "sub":
            self.reset_sub_style(t[1])
        else:
            self.reset_word_style(t[1], t[2])
            self.finish_style_edit()

    def _bind_style(self):
        """Показать в панели стиль того, что сейчас редактируется, и подсветить его."""
        if self.style_target is not None and not self._target_valid():
            self.style_target = None
        t = self.style_target
        page = self.subs_page
        if t is None:
            self.style_title.setText("Стиль субтитров")
            self.style_hint.setText("общий для всех субтитров")
            self.edit_bar.hide()
            self.style_panel.set_style(self.doc.style, silent=True)
            page.mark_words(None)
            page.list.set_editing(-1)
            self.timeline.canvas.style_edit = -1
        elif t[0] == "sub":
            i = t[1]
            self.style_title.setText(f"Стиль субтитра №{i + 1}")
            self.style_hint.setText("")
            self.edit_info.setText("Меняется только этот субтитр. Клик в пустое место или «Готово» — применить.")
            self.edit_bar.show()
            self.style_panel.set_style(self._base_style(i), silent=True)
            page.mark_words(None)
            page.list.set_editing(i)
            self.timeline.canvas.style_edit = i
        else:
            i, words = t[1], t[2]
            sub = self.doc.subtitles[i]
            names = [w for k, w in enumerate(sub.text.split()) if k in words]
            shown = ", ".join(names[:4]) + ("…" if len(names) > 4 else "")
            self.style_title.setText("Стиль слова" if len(words) == 1 else "Стиль слов")
            self.style_hint.setText(f"субтитр №{i + 1}")
            self.edit_info.setText(f"«{shown}» — для слова меняются шрифт, размер, цвет, обводка, капс, трекинг "
                                   "и масштаб. Клик в пустое место — применить.")
            self.edit_bar.show()
            self.style_panel.set_style(word_style(self._base_style(i), sub.word_styles.get(words[0])), silent=True)
            if page.current_row() == i:
                page.mark_words(words)
            page.list.set_editing(i)
            self.timeline.canvas.style_edit = i
        self.timeline.canvas.update()
        self.gallery.clear_selection()
        self._refresh_preview()

    # ============================================================ обновление видов

    def _issues(self) -> dict:
        ae = self.ae_panel.get_settings()
        out = {}
        for iss in validate(self.doc.subtitles, self.doc.style, ae.comp_width, self.doc.comp.get("duration")):
            if iss.index not in out or iss.level == "error":
                out[iss.index] = (iss.level, iss.text)
        return out

    def _refresh_views(self, select: int | None = None):
        self.subs_page.set_items(self.doc.subtitles, self._issues(), select)
        t = self.style_target
        self.subs_page.list.set_editing(t[1] if t else -1)
        if t and t[0] == "words" and self.subs_page.current_row() == t[1]:
            self.subs_page.mark_words(t[2])
        c = self.timeline.canvas
        c.tracks = {"subs": self.doc.subtitles}
        c.active = "subs"
        c.sel = self.current_row()
        c.style_edit = t[1] if t else -1
        if self.doc.comp.get("duration"):
            c.duration = self.doc.comp["duration"]
        elif self.player.has_media():
            c.duration = max(self.player.duration(), 10)
        self.timeline.sync_scrollbar()
        c.update()
        self.undo_btn.setEnabled(self.hist_pos > 0)
        self.redo_btn.setEnabled(self.hist_pos < len(self.history) - 1)
        self._refresh_preview()

    # ------------------------------------------------------------ показ эффекта появления

    def _demo_index(self) -> int:
        lst = self.doc.subtitles
        if self.style_target is not None and self._target_valid():
            return self.style_target[1]
        row = self.current_row()
        if 0 <= row < len(lst):
            return row
        for i, s in enumerate(lst):
            if s.start <= self.playhead < s.end:
                return i
        return 0 if lst else -1

    def play_anim_demo(self):
        """Проигрывает в превью появление и исчезновение субтитра (видео и курсор не двигаются)."""
        i = self._demo_index()
        sub = self.doc.subtitles[i] if i >= 0 else Subtitle(0.0, 2.5, "Пример субтитра")
        style = self.style_panel.get_style() if self.style_target is None or self.style_target[0] == "sub" \
            else self._base_style(i)
        if style.anim_in == "none" and style.anim_out == "none":
            self.toast.show_message("Выберите эффект появления или исчезновения", "info", 1500)
            return
        d_in, d_out = durations(style, sub.start, sub.end)
        a, b = sub.start, sub.end
        hold_in = min(a + d_in + 0.7, b - d_out)
        segs = [(a - 0.3, hold_in)]
        out_from = max(b - d_out - 0.5, hold_in)
        if style.anim_out != "none":
            if out_from > hold_in + 0.01:
                segs.append((out_from, b + 0.25))  # середину длинного субтитра пропускаем
            else:
                segs[0] = (a - 0.3, b + 0.25)
        self._demo = {"sub": sub, "style": style, "segs": segs, "seg": 0, "t": segs[0][0], "key": i}
        self.demo_timer.start()
        self._demo_last = time.perf_counter()
        self._refresh_preview()

    def _demo_tick(self):
        d = self._demo
        if d is None:
            self.demo_timer.stop()
            return
        now = time.perf_counter()
        d["t"] += now - self._demo_last
        self._demo_last = now
        if d["t"] >= d["segs"][d["seg"]][1]:
            d["seg"] += 1
            if d["seg"] >= len(d["segs"]):
                self.stop_anim_demo()
                return
            d["t"] = d["segs"][d["seg"]][0]
        self._refresh_preview()

    def stop_anim_demo(self):
        if self._demo is not None:
            self._demo = None
            self.demo_timer.stop()
            self._refresh_preview()

    def _demo_items(self) -> list:
        d = self._demo
        sub, style, t = d["sub"], d["style"], d["t"]
        if not sub.start <= t < sub.end:
            return []
        return [PreviewItem(("subs", max(d["key"], 0)), style, sub.text, current_word(sub, t), False,
                            sub.word_styles, set(), False, anim_state(style, sub.start, sub.end, t))]

    def _refresh_preview(self):
        if self._demo is not None:
            ae = self.ae_panel.get_settings()
            self.preview.set_comp_size(ae.comp_width, ae.comp_height)
            self.preview.set_items(self._demo_items())
            return
        t = self.playhead
        sel = self.current_row()
        target = self.style_target
        items = []
        for i, s in enumerate(self.doc.subtitles):
            editing_this = target is not None and target[1] == i
            if s.start <= t < s.end or editing_this:
                style = self._base_style(i)
                items.append(PreviewItem(
                    ("subs", i), style, s.text, current_word(s, t), i == sel, s.word_styles,
                    set(target[2]) if editing_this and target[0] == "words" else set(),
                    editing_this and target[0] == "sub",
                    None if editing_this else anim_state(style, s.start, s.end, t)))
        ae = self.ae_panel.get_settings()
        self.preview.set_comp_size(ae.comp_width, ae.comp_height)
        self.preview.set_items(items)
        m, s = divmod(t, 60)
        self.time_lbl.setText(f"{int(m):02d}:{s:04.1f}")

    # ============================================================ время и проигрывание

    def _set_playhead(self, t: float, follow: bool = False):
        if self._demo is not None and abs(t - self.playhead) > 1e-6:
            self._demo = None
            self.demo_timer.stop()
        self.playhead = max(t, 0.0)
        self.timeline.set_playhead(self.playhead, follow)
        self._refresh_preview()

    def seek(self, t: float):
        if self.player.has_media():
            self.player.seek(t)
        self._set_playhead(t)

    def toggle_play(self):
        self.stop_anim_demo()
        if self.player.has_media():
            self.player.player.setPlaybackRate(1.0)
            self.player.toggle()
        elif self.clock.isActive():
            self.clock.stop()
            self.play_btn.setText("▶")
        else:
            self.clock.start()
            self.play_btn.setText("⏸")

    def _tick(self):
        end = max(self._content_end(), self.timeline.canvas.duration)
        t = self.playhead + 0.033
        if t > end:
            self.clock.stop()
            self.play_btn.setText("▶")
            t = 0.0
        self._set_playhead(t, follow=True)

    def _on_playing(self, playing: bool):
        self.play_btn.setText("⏸" if playing else "▶")

    def _on_video_frame(self, img: QImage):
        self.preview.set_video_frame(img)

    def _on_waveform(self, data):
        self.timeline.canvas.wave = data
        self.timeline.canvas.update()

    def _load_video(self, path: str, offset: float = 0.0, rate: float = 1.0, quiet: bool = False,
                    source: str = "file"):
        if not Path(path).is_file():
            if not quiet:
                self.toast.show_message("Видео не найдено", "error")
            return
        self.timeline.canvas.wave = None
        self.timeline.canvas.update()
        self.player.load(path, offset, rate)
        self.doc.video = {"path": path, "offset": offset, "rate": rate, "source": source}
        self.video_lbl.setText(Path(path).name)
        self.player.seek(self.playhead)
        self._schedule_save()

    def open_video(self):
        from .transcriber import MEDIA_FILTER
        path, _ = QFileDialog.getOpenFileName(self, "Видео для предпросмотра", "", MEDIA_FILTER)
        if path:
            self._load_video(path.replace("/", "\\"))

    def video_from_ae(self):
        self._ae_run(build_query_jsx, 25, self._on_video_query, "Читаю композицию…")

    def _on_video_query(self, data: dict):
        if not data.get("ok"):
            self.toast.show_message("Откройте композицию с видео в After Effects", "warn")
            return
        self._remember_comp(data["comp"])
        videos = [s for s in data.get("sources", []) if s.get("has_video") and s.get("exists", True)]
        if not videos:
            self.toast.show_message("В композиции нет видеослоёв", "warn")
            return
        best = max(videos, key=lambda s: (s["depth"] == 0, s["b"] - s["a"]))
        self._load_video(best["path"], best["comp_start"], best["rate"], source="ae")
        self.toast.show_message(f"Видео: {best['name']}", "info")

    def load_background(self):
        path, _ = QFileDialog.getOpenFileName(self, "Картинка для фона превью", "",
                                              "Изображения (*.png *.jpg *.jpeg *.bmp *.webp)")
        if path:
            img = QImage(path)
            self.preview.set_background(None if img.isNull() else img)

    def _toggle_safe(self, on: bool):
        self.preview.show_safe = on
        self.preview.update()

    # ============================================================ события редактирования

    def _on_list_selection(self, row: int):
        t = self.style_target
        if t is not None and t[1] != row:
            self.finish_style_edit()  # выбрали другой субтитр — редактирование стиля завершено
        self.timeline.canvas.sel = row
        lst = self.doc.subtitles
        if 0 <= row < len(lst):
            self._show_sub(row)
        self._refresh_preview()
        self.timeline.canvas.update()

    def _show_sub(self, i: int):
        """Курсор на момент, когда субтитр уже появился (после эффекта появления)."""
        s = self.doc.subtitles[i]
        d_in, d_out = durations(self._base_style(i), s.start, s.end)
        if not (s.start + d_in <= self.playhead <= s.end - d_out):
            self.seek(min(s.start + d_in + 0.02, (s.start + s.end) / 2))

    def _on_item_edited(self, row: int):
        t = self.style_target
        if t and t[0] == "words" and t[1] == row:
            n = len(self.doc.subtitles[row].text.split())
            words = [w for w in t[2] if w < n]
            self.style_target = ("words", row, words) if words else None
            self.subs_page.mark_words(words or None)
        self.timeline.canvas.update()
        self._refresh_preview()
        self._changed()

    def _on_timeline_select(self, _track: str, i: int):
        if self.tabs.currentIndex() != 0:
            self.tabs.setCurrentIndex(0)
        t = self.style_target
        if t is not None and t[1] != i:
            self.finish_style_edit()
        self.select_item(i)
        self._refresh_preview()

    def _on_timeline_edited(self, _track: str):
        self._refresh_views(select=self.timeline.canvas.sel)
        self._changed()

    def _on_timeline_add(self, _track: str, t: float):
        self.seek(t)
        self.add_item()

    def _on_preview_drag(self, key: tuple, x: float, y: float):
        _track, i = key
        t = self.style_target
        if t is not None and t[0] == "sub" and t[1] == i:
            st = self._base_style(i).copy()  # двигаем только этот субтитр
            st.pos_x, st.pos_y = x, y
            self.doc.subtitles[i].style = st
        else:
            self.doc.style.pos_x, self.doc.style.pos_y = x, y
        for k, v in (("pos_x", x), ("pos_y", y)):
            w = self.style_panel.w[k]
            w.blockSignals(True)
            w.setValue(v)
            w.blockSignals(False)
            w.slider.update()
        self._refresh_preview()

    def _on_style_changed(self):
        st = self.style_panel.get_style()
        t = self.style_target
        if t is not None and self._target_valid():
            sub = self.doc.subtitles[t[1]]
            if t[0] == "sub":
                sub.style = st
            else:
                diff = style_diff(self._base_style(t[1]), st)
                for w in t[2]:
                    if diff:
                        sub.word_styles[w] = dict(diff)
                    else:
                        sub.word_styles.pop(w, None)
            self.subs_page.list.refresh_row(t[1], sub)
            self.timeline.canvas.update()
        else:
            old = self.doc.style
            changed = [k for k in old.to_dict() if getattr(old, k) != getattr(st, k)]
            own = [sub for sub in self.doc.subtitles if sub.style is not None]
            if self.doc.ae.propagate_style and own:
                # у субтитров со своим стилем меняем только то, что сейчас поменяли в общем
                for sub in own:
                    for k in changed:
                        setattr(sub.style, k, getattr(st, k))
            self.doc.style = st
            if "max_chars" in changed or (self.doc.ae.propagate_style and own):
                self._refresh_views(select=self.current_row())
        self.gallery.clear_selection()
        self._refresh_preview()
        self._changed()

    def _on_ae_changed(self):
        self.doc.ae = self.ae_panel.get_settings()
        self._refresh_preview()
        self._schedule_save()

    # ============================================================ стили: пресеты и обмен

    def _apply_preset(self, style: Style, name: str):
        self.style_panel.set_style(style)  # вызовет _on_style_changed — применится к тому, что редактируется
        self.toast.show_message(f"Стиль «{name}» применён", "info", 1800)

    def save_preset(self):
        name, ok = QInputDialog.getText(self, "Сохранить стиль", "Название стиля:")
        safe = sharing.safe_name(name)
        if not ok or not name.strip():
            return
        path = PRESETS_DIR / f"{safe}.json"
        if path.exists() and QMessageBox.question(self, "Стиль", f"Стиль «{safe}» уже есть. Перезаписать?") \
                != QMessageBox.StandardButton.Yes:
            return
        sharing.save_preset(safe, self.style_panel.get_style(), overwrite=True)
        sharing.cloud_revive(self.settings.value("cloud_dir", ""), safe)
        self.gallery.reload(select=safe)
        self.cloud_sync()
        self.toast.show_message(f"Стиль «{safe}» сохранён")

    def _on_preset_deleted(self, name: str):
        sharing.cloud_delete(self.settings.value("cloud_dir", ""), name)

    def share_style(self, name: str):
        path = PRESETS_DIR / f"{name}.json"
        if not path.exists():
            return
        code = sharing.encode([(name, sharing.load_style(path))])
        QGuiApplication.clipboard().setText(code)
        self.toast.show_message(f"Код стиля «{name}» скопирован. Отправьте его — у получателя: «📋 Код» в галерее.",
                                "ok", 5000)

    def paste_style_code(self):
        text = QGuiApplication.clipboard().text()
        items = sharing.decode(text)
        if not items:
            code, ok = QInputDialog.getMultiLineText(self, "Код стиля", "Вставьте код стиля (AESUBS-STYLE:…):")
            if not ok:
                return
            items = sharing.decode(code)
        if not items:
            QMessageBox.information(self, "Код стиля", "Код стиля не найден.")
            return
        names = [sharing.save_preset(n, s) for n, s in items]
        self.gallery.reload(select=names[-1])
        self.cloud_sync()
        self.toast.show_message("Добавлены стили: " + ", ".join(names))

    def import_styles(self):
        path, _ = QFileDialog.getOpenFileName(self, "Импорт стилей", "",
                                              f"Стили AE Subs (*{sharing.PACK_EXT} *.json)")
        if not path:
            return
        try:
            names = sharing.import_pack(Path(path))
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "Импорт стилей", f"Не удалось прочитать файл:\n{e}")
            return
        self.gallery.reload(select=names[-1] if names else None)
        self.cloud_sync()
        self.toast.show_message(f"Импортировано стилей: {len(names)}")

    def export_styles(self, names: list[str] | None = None):
        default = (names[0] if names and len(names) == 1 else "Мои стили") + sharing.PACK_EXT
        path, _ = QFileDialog.getSaveFileName(self, "Экспорт стилей", default, f"Стили AE Subs (*{sharing.PACK_EXT})")
        if path:
            n = sharing.export_pack(Path(path), names)
            self.toast.show_message(f"Сохранено стилей: {n} → {Path(path).name}")

    def setup_cloud(self):
        current = self.settings.value("cloud_dir", "")
        hint = "\n".join(sharing.guess_cloud_folders()) or "не найдены"
        box = QMessageBox(self)
        box.setWindowTitle("Облачная папка стилей")
        box.setText("Стили будут храниться в папке облачного диска (Google Диск, Яндекс Диск, Dropbox, OneDrive) "
                    "и синхронизироваться между вашими компьютерами и с командой, у которой есть доступ к папке.\n\n"
                    f"Сейчас: {current or 'не настроено'}\n\nНайденные облачные папки:\n{hint}")
        choose = box.addButton("Выбрать папку…", QMessageBox.ButtonRole.AcceptRole)
        off = box.addButton("Отключить", QMessageBox.ButtonRole.DestructiveRole) if current else None
        box.addButton("Закрыть", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() == choose:
            start = current or (sharing.guess_cloud_folders() or [""])[0]
            folder = QFileDialog.getExistingDirectory(self, "Папка облачного диска", start)
            if folder:
                self.settings.setValue("cloud_dir", folder)
                self.cloud_sync(verbose=True)
        elif off is not None and box.clickedButton() == off:
            self.settings.setValue("cloud_dir", "")
            self.toast.show_message("Облачная синхронизация стилей отключена", "info")

    def cloud_sync(self, verbose: bool = False):
        root = self.settings.value("cloud_dir", "")
        if not root:
            return
        try:
            got, sent = sharing.sync(root)
        except OSError as e:
            if verbose:
                QMessageBox.warning(self, "Облако", f"Не удалось синхронизировать:\n{e}")
            return
        if got:
            self.gallery.reload()
        if verbose or got:
            self.toast.show_message(f"☁ Стили синхронизированы: получено {got}, отправлено {sent}", "info")

    # ============================================================ действия со списком

    def add_item(self):
        if self.tabs.currentIndex() != 0:
            return
        lst = self.doc.subtitles
        row = self.current_row()
        if row >= 0:
            start, idx = lst[row].end + 0.1, row + 1
        else:
            start = lst[-1].end + 0.1 if lst else self.playhead
            idx = len(lst)
        lst.insert(idx, Subtitle(round(start, 3), round(start + 2, 3), ""))
        self._refresh_views(select=idx)
        self._on_list_selection(idx)
        self.subs_page.text_edit.setFocus()
        self._changed()

    def duplicate_item(self):
        lst, row = self.doc.subtitles, self.current_row()
        if not 0 <= row < len(lst):
            return
        s = lst[row].clone()
        dur = s.end - s.start
        s.retime(lst[row].end + 0.05, lst[row].end + 0.05 + dur)
        lst.insert(row + 1, s)
        self._refresh_views(select=row + 1)
        self._on_list_selection(row + 1)
        self._changed()

    def split_item(self):
        c = self.timeline.canvas
        c.active = "subs"
        c.split_at_playhead()

    def delete_items(self):
        rows = self.subs_page.list.selected_rows()
        if not rows:
            return
        self.style_target = None
        self._bind_style()
        lst = self.doc.subtitles
        for r in reversed(rows):
            del lst[r]
        self._refresh_views(select=min(rows[0], len(lst) - 1))
        self._changed()

    def move_item(self, delta: int):
        lst, row = self.doc.subtitles, self.current_row()
        new = row + delta
        if row < 0 or not 0 <= new < len(lst):
            return
        self.finish_style_edit()
        lst[row], lst[new] = lst[new], lst[row]
        self._refresh_views(select=new)
        self._changed()

    def sort_current(self):
        self.finish_style_edit()
        self.doc.subtitles.sort(key=lambda s: s.start)
        self._refresh_views(select=0)
        self._changed()

    # ============================================================ распознавание и текст

    def recognize(self):
        self.tabs.setCurrentIndex(0)
        self.finish_style_edit()
        settings = dict(self.doc.recognize)
        v = self.doc.video
        if v.get("path") and v.get("source", "file") == "file" and Path(v["path"]).is_file():
            # видео уже выбрано в проекте — не нужно выбирать его второй раз
            settings.update(path=v["path"], source="file")
        dlg = RecognizeDialog(settings, self.doc.split, self.doc.words,
                              self.ae_panel.get_settings().afterfx_path, self)
        accepted = dlg.exec() == QDialog.DialogCode.Accepted
        self.doc.recognize = dlg.settings()
        self.doc.split = dlg.split_settings()
        self.doc.words = dlg.words
        if not accepted:
            return
        new = dlg.result_subs
        if dlg.replace.isChecked():
            self.doc.subtitles[:] = new
            select = 0
        else:
            select = len(self.doc.subtitles)
            self.doc.subtitles.extend(new)
        if dlg.used_source == "ae" and dlg.comp_info:
            self.doc.ae.time_offset = 0.0
            self._remember_comp(dlg.comp_info)
            if not self.player.has_media() and dlg.video_source:
                v = dlg.video_source
                self._load_video(v["path"], v["comp_start"], v["rate"], quiet=True, source="ae")
        elif dlg.used_source == "file" and dlg.settings()["path"] != self.doc.video.get("path"):
            self._load_video(dlg.settings()["path"], quiet=True)
        self._refresh_views(select=select)
        self._changed()
        self.toast.show_message(f"✓ Создано субтитров: {len(new)}. Проверьте текст и отправьте в After Effects.")

    def paste_text(self):
        self.tabs.setCurrentIndex(0)
        start = self.doc.subtitles[-1].end + 0.1 if self.doc.subtitles else 0.0
        dlg = PasteTextDialog(round(start, 2), self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        new = dlg.result_subs()
        if dlg.replace.isChecked():
            self.doc.subtitles[:] = new
        else:
            self.doc.subtitles.extend(new)
        self._refresh_views(select=len(self.doc.subtitles) - len(new) if new else None)
        self._changed()
        self.toast.show_message(f"Добавлено субтитров: {len(new)}")

    def close_gaps(self):
        n = close_gaps(self.doc.subtitles)
        self._refresh_views(select=self.current_row())
        self._changed()
        self.toast.show_message(f"Сомкнуто субтитров: {n}" if n else "Пауз между субтитрами нет", "info")

    def auto_timing(self):
        subs = self.doc.subtitles
        if not subs:
            return
        dlg = TimingDialog(subs[0].start, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        new = dlg.timing.apply([s.text for s in subs])
        for s, n in zip(subs, new):
            s.retime(n.start, n.end)
        self._refresh_views(select=0)
        self._changed()

    # ============================================================ перевод и экспорт

    def _apply_translation(self, texts: list):
        n = 0
        for s, t in zip(self.doc.subtitles, texts):
            if t and t != s.text:
                s.text = t
                s.words = distribute_words(t, s.start, s.end)
                s.clean_word_styles()
                n += 1
        self._refresh_views()
        self.translate_page.set_data(self.doc.subtitles, self.doc.translation)
        self._changed()
        self.toast.show_message(f"Текст заменён переводом: {n} субтитров (Ctrl+Z — отменить)")

    def _send_translation(self, lang: str, texts: list):
        items = [Subtitle(s.start, s.end, t, style=s.style.copy() if s.style else None)
                 for s, t in zip(self.doc.subtitles, texts) if t]
        ae = AESettings.from_dict(self.ae_panel.get_settings().to_dict())
        ae.comp_name = f"{ae.comp_name} {lang.upper()}"
        ae.layer_prefix = f"{ae.layer_prefix}_{lang.upper()}"
        self._send(items, ae)

    def _export_items(self, track: str):
        ae = self.ae_panel.get_settings()
        size = (ae.comp_width, ae.comp_height)
        if track == "translation":
            texts = translated_texts(self.doc.subtitles, self.doc.translation)
            return [Subtitle(s.start, s.end, t) for s, t in zip(self.doc.subtitles, texts) if t], self.doc.style, size
        return self.doc.subtitles, self.doc.style, size

    def export_dialog(self):
        ExportDialog(self._export_items, self).exec()

    # ============================================================ After Effects

    def _ae_run(self, build, timeout, on_done, info=""):
        if self.ae_job:
            self.toast.show_message("Подождите — After Effects ещё отвечает на прошлую команду", "warn")
            return
        self.send_info.setText(info if ae_bridge.is_ae_running() else "After Effects запускается…")
        self.ae_job = ae_bridge.AEJob(self.ae_panel.get_settings().afterfx_path, build, timeout, self)

        def finish(data):
            self.ae_job = None
            self.send_info.setText("")
            self.send_btn.setEnabled(True)
            on_done(data)

        def fail(msg):
            self.ae_job = None
            self.send_info.setText("")
            self.send_btn.setEnabled(True)
            QMessageBox.warning(self, "After Effects", msg)

        self.ae_job.finished.connect(finish)
        self.ae_job.failed.connect(fail)
        self.ae_job.start()

    def send_current(self):
        if self.tabs.currentIndex() != 0:
            return
        self.finish_style_edit()
        self._send(self.doc.subtitles, self.ae_panel.get_settings())

    def _send(self, items: list[Subtitle], ae: AESettings):
        default_style = self.doc.style
        if not [s for s in items if s.text.strip()]:
            self.toast.show_message("Нечего отправлять — нет субтитров с текстом", "warn")
            return
        # порядок в AE = порядок по времени; держим список так же, чтобы работала синхронизация
        if items is self.doc.subtitles:
            items.sort(key=lambda s: s.start)
        issues = validate(items, default_style, ae.comp_width, self.doc.comp.get("duration"))
        if issues:
            dlg = ValidationDialog(issues, items, self)
            if dlg.exec() != QDialog.DialogCode.Accepted or dlg.choice is None:
                return
            if dlg.choice == "fix":
                n = autofix(items, issues, default_style, ae.comp_width)
                self.toast.show_message(f"Исправлено: {n}", "info", 2000)
                self._refresh_views()
                self._changed()
        fams = set()
        for s in items:
            base = s.style or default_style
            fams.add(base.font_family)
            fams.update(v.get("font_family") for v in s.word_styles.values() if v.get("font_family"))
        has_cyr = any(re.search("[А-Яа-яЁё]", s.text) for s in items)
        bad_fonts = sorted(f for f in fams if not font_has_cyrillic(f))
        if has_cyr and bad_fonts and QMessageBox.question(
                self, "Шрифт без русских букв",
                f"В шрифте «{', '.join(bad_fonts)}» нет русских букв — After Effects заменит их "
                "стандартным шрифтом с засечками.\n\nВсё равно отправить?") != QMessageBox.StandardButton.Yes:
            return
        snapshot = [s.clone() for s in items]
        builder = lambda rp: build_jsx(snapshot, default_style, ae, rp, fonts.postscript_name)  # noqa: E731
        self.send_btn.setEnabled(False)
        self._ae_run(builder, 90, self._on_sent, "Отправляю в After Effects…")
        self._schedule_save()

    def _on_sent(self, r: dict):
        if not r.get("ok"):
            QMessageBox.warning(self, "After Effects", str(r.get("error")))
            return
        if r.get("width"):
            self._remember_comp({"name": r.get("main"), "width": r["width"], "height": r["height"],
                                 "fps": r.get("fps"), "duration": r.get("duration")})
        msg = f"✓ Добавлено: {r.get('added')} в «{r.get('comp')}»"
        warnings = r.get("warnings") or []
        if r.get("skipped"):
            warnings.append(f"Пропущено (вне композиции или нулевой длины): {r['skipped']}")
        if warnings:
            self.toast.show_message(msg, "warn", 6000)
            QMessageBox.warning(self, "After Effects", "\n\n".join(warnings))
        else:
            self.toast.show_message(msg, "ok", 5000)

    def sync_from_ae(self):
        ae = self.ae_panel.get_settings()
        self._ae_run(lambda rp: build_sync_jsx(ae, rp), 25, self._on_synced, "Забираю правки из After Effects…")

    def _on_synced(self, data: dict):
        if not data.get("ok"):
            err = data.get("error")
            msg = {"no_comp": "Откройте композицию с видео в After Effects.",
                   "no_holder": "В композиции нет отправленных субтитров — сначала отправьте их."}.get(err, str(err))
            QMessageBox.information(self, "Синхронизация", msg)
            return
        self.finish_style_edit()
        groups: dict[int, list] = {}
        for it in data["items"]:
            groups.setdefault(it["num"], []).append(it)
        lst = self.doc.subtitles
        lst.sort(key=lambda s: s.start)
        changed = 0
        keep = []
        for n, s in enumerate(lst, 1):
            g = groups.get(n)
            if not g:
                continue  # слой удалили в AE
            g.sort(key=lambda x: x["line"])
            text = "\n".join(x["text"] for x in g)
            start, end = min(x["start"] for x in g), max(x["end"] for x in g)
            start, end = round(start - self.doc.ae.time_offset, 3), round(end - self.doc.ae.time_offset, 3)
            if text != s.text:
                if len(text.split()) != len(s.text.split()):
                    s.words = []
                s.text = text
                s.clean_word_styles()
                changed += 1
            if abs(start - s.start) > 0.005 or abs(end - s.end) > 0.005:
                s.retime(start, end)
                changed += 1
            keep.append(s)
        removed = len(lst) - len(keep)
        lst[:] = keep
        self._refresh_views()
        self._changed()
        self.toast.show_message(f"Из AE: изменений {changed}, удалено {removed}", "info")

    def save_jsx(self):
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить скрипт", "subtitles.jsx", "ExtendScript (*.jsx)")
        if not path:
            return
        jsx = build_jsx(self.doc.subtitles, self.doc.style, self.ae_panel.get_settings(), "", fonts.postscript_name)
        Path(path).write_text(jsx, encoding="utf-8-sig")
        self.toast.show_message("Скрипт сохранён. В AE: File → Scripts → Run Script File")

    def _remember_comp(self, comp: dict):
        """Запоминает композицию AE: размер для превью, длительность, проект по имени композиции."""
        if not comp or not comp.get("name"):
            return
        prev = self.doc.comp.get("name")
        self.doc.comp = {k: comp.get(k) for k in ("name", "width", "height", "fps", "duration") if comp.get(k)}
        ae = self.ae_panel.get_settings()
        if comp.get("width") and (ae.comp_width, ae.comp_height) != (comp["width"], comp["height"]):
            ae.comp_width, ae.comp_height = int(comp["width"]), int(comp["height"])
            if comp.get("fps"):
                ae.comp_fps = float(comp["fps"])
            self.ae_panel.set_settings(ae)
        name = comp["name"]
        path = self._comp_project_path(name)
        if path.exists() and self.project_path != path and ((prev and prev != name) or not self.doc.subtitles):
            if QMessageBox.question(self, "Проект композиции",
                                    f"Для композиции «{name}» есть сохранённый проект. Открыть его?\n"
                                    "(«Нет» — продолжить с текущими субтитрами)") == QMessageBox.StandardButton.Yes:
                self._open_path(path)
                return
        self.project_path = path
        self._schedule_save()

    @staticmethod
    def _comp_project_path(name: str) -> Path:
        return PROJECTS_DIR / f"{sharing.safe_name(name)}.aesubs"

    def _update_ae_chip(self):
        running = ae_bridge.is_ae_running()
        comp = self.doc.comp.get("name")
        text = ("● After Effects" + (f" · {comp}" if comp else "")) if running else "○ After Effects не запущен"
        self.ae_chip.setText(text)
        self.ae_chip.setProperty("role", "chip-ok" if running else "chip-off")
        self.ae_chip.style().unpolish(self.ae_chip)
        self.ae_chip.style().polish(self.ae_chip)

    # ============================================================ обновления

    def _startup_tasks(self):
        self.cloud_sync()
        self.check_updates(verbose=False)

    def check_updates(self, verbose: bool = False):
        """verbose — пользователь сам нажал «Проверить обновления»: сообщаем и об отсутствии новой версии."""
        def work():
            try:
                info = updater.check()
                if info:
                    info["verbose"] = verbose
                    self.bus.updateFound.emit(info)
                elif verbose:
                    self.bus.updateFound.emit({})
            except Exception as e:  # noqa: BLE001
                if verbose:
                    self.bus.updateFailed.emit(f"Не удалось проверить обновления (нет интернета?):\n{e}")
        threading.Thread(target=work, daemon=True).start()

    def _on_update_found(self, info: dict):
        if not info:
            QMessageBox.information(self, "Обновление", f"У вас последняя версия ({VERSION}).")
            return
        if not info.get("verbose") and self.settings.value("skip_version", "") == info["version"]:
            return  # от этой версии уже отказались — при запуске не напоминаем
        notes = info.get("notes", "").strip()
        if len(notes) > 900:
            notes = notes[:900] + "…"
        size = f" ({info['size'] / 1048576:.0f} МБ)" if info.get("size") else ""
        box = QMessageBox(QMessageBox.Icon.Question, "Доступно обновление",
                          f"Новая версия {info['version']}{size}, у вас {VERSION}.\n\n"
                          + (f"Что нового:\n{notes}\n\n" if notes else "")
                          + "Обновить сейчас? Программа перезапустится, проекты, пресеты и модели сохранятся.",
                          parent=self)
        yes = box.addButton("Обновить", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Позже", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is not yes:
            self.settings.setValue("skip_version", info["version"])
            return
        cancel = threading.Event()
        self._update_dialog = dlg = QProgressDialog("Подключаюсь…", "Отмена", 0, 100, self)
        dlg.setWindowTitle(f"Обновление до {info['version']}")
        dlg.setMinimumWidth(420)
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        dlg.canceled.connect(cancel.set)
        dlg.show()

        def work():
            try:
                self.bus.updateReady.emit(updater.download(info, self.bus.updateProgress.emit, cancel))
            except updater.Cancelled:
                self.bus.updateFailed.emit("")
            except Exception as e:  # noqa: BLE001
                self.bus.updateFailed.emit(f"Не удалось загрузить обновление:\n{e}")
        threading.Thread(target=work, daemon=True).start()

    def _on_update_progress(self, p):
        dlg = getattr(self, "_update_dialog", None)
        if dlg is None or dlg.wasCanceled():
            return
        done, total, speed, source = p
        mb = 1048576
        size = f"{done / mb:.0f} из {total / mb:.0f} МБ" if total else f"{done / mb:.0f} МБ"
        speed_text = f"{speed / mb:.1f} МБ/с" if speed >= mb / 10 else f"{speed / 1024:.0f} КБ/с"
        left = f" · осталось ~{(total - done) / max(speed, 1) / 60:.0f} мин" if total and speed else ""
        dlg.setLabelText(f"Загружаю обновление ({source})\n{size} · {speed_text}{left}")
        if total:
            dlg.setValue(int(done / total * 100))

    def _on_update_failed(self, msg: str):
        dlg = getattr(self, "_update_dialog", None)
        if dlg is not None:
            dlg.close()
            self._update_dialog = None
        if msg:  # пустое сообщение — пользователь сам отменил загрузку
            QMessageBox.warning(self, "Обновление", msg)

    def _on_update_ready(self, folder):
        self._update_dialog.close()
        self._update_dialog = None
        try:
            updater.install_and_restart(Path(folder))
        except RuntimeError as e:
            QMessageBox.warning(self, "Обновление", str(e))
            return
        self.close()

    def send_feedback(self):
        dlg = FeedbackDialog(self.ae_panel.get_settings().afterfx_path, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.toast.show_message("Спасибо! Сообщение отправлено разработчику")

    def open_github(self):
        QDesktopServices.openUrl(QUrl(updater.RELEASES_URL))

    def about(self):
        QMessageBox.about(self, "AE Subs",
                          f"<b>AE Subs {VERSION}</b><br>Автоматические субтитры для After Effects."
                          "<br><br>Распознавание: Whisper (faster-whisper). Отделение голоса: Kim Vocal 2 (UVR).")

    # ============================================================ отмена и сохранение

    def _changed(self):
        if self._restoring:
            return
        self.snap_timer.start()
        self._schedule_save()

    def _schedule_save(self):
        self.save_timer.start()

    def _doc_snapshot(self) -> str:
        d = self.doc.to_dict()
        d.pop("video", None)
        d.pop("recognize", None)
        return json.dumps(d, ensure_ascii=False, sort_keys=True)

    def _snapshot(self):
        snap = self._doc_snapshot()
        if self.history and self.history[self.hist_pos] == snap:
            return
        del self.history[self.hist_pos + 1:]
        self.history.append(snap)
        if len(self.history) > 200:
            self.history.pop(0)
        self.hist_pos = len(self.history) - 1
        self.undo_btn.setEnabled(self.hist_pos > 0)
        self.redo_btn.setEnabled(False)

    def _restore(self, snap: str):
        self._restoring = True
        try:
            data = json.loads(snap)
            video, recognize = self.doc.video, self.doc.recognize
            sel = self.current_row()
            self.doc = ProjectState.from_dict(data)
            self.doc.video, self.doc.recognize = video, recognize
            self.ae_panel.set_settings(self.doc.ae)
            self._refresh_views(select=min(sel, len(self.doc.subtitles) - 1) if sel >= 0 else None)
            self._bind_style()
        finally:
            self._restoring = False
        self._schedule_save()

    def undo(self):
        self.snap_timer.stop()
        self._snapshot()  # зафиксировать несохранённое изменение
        if self.hist_pos > 0:
            self.hist_pos -= 1
            self._restore(self.history[self.hist_pos])
            self.toast.show_message("Отменено", "info", 900)

    def redo(self):
        if self.hist_pos < len(self.history) - 1:
            self.hist_pos += 1
            self._restore(self.history[self.hist_pos])
            self.toast.show_message("Повторено", "info", 900)

    def _doc_file_content(self) -> str:
        return json.dumps(self.doc.to_dict(), ensure_ascii=False, indent=2)

    def _autosave(self):
        content = self._doc_file_content()
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            STATE_FILE.write_text(content, encoding="utf-8")
            if self.project_path is not None:
                self.project_path.parent.mkdir(parents=True, exist_ok=True)
                self.project_path.write_text(content, encoding="utf-8")
            elif self.doc.comp.get("name"):
                PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
                self._comp_project_path(self.doc.comp["name"]).write_text(content, encoding="utf-8")
        except OSError:
            pass

    def _apply_doc(self, data: dict):
        self._restoring = True
        try:
            self.style_target = None
            self.doc = ProjectState.from_dict(data)
            self.ae_panel.set_settings(self.doc.ae)
            self.translate_page.set_data(self.doc.subtitles, self.doc.translation)
            v = self.doc.video
            if v.get("path") and Path(v["path"]).is_file():
                self._load_video(v["path"], v.get("offset", 0.0), v.get("rate", 1.0), quiet=True,
                                 source=v.get("source", "file"))
            else:
                self.player.unload()
                self.video_lbl.setText("видео не открыто")
                self.preview.set_video_frame(None)
                self.preview.set_background(None)
                self.timeline.canvas.wave = None  # звук прошлого проекта не должен оставаться
                self.timeline.canvas.duration = 30.0
                self.timeline.fit()
            self._set_playhead(0.0)
            self._refresh_views(select=0 if self.doc.subtitles else None)
            self._bind_style()
        finally:
            self._restoring = False
        self.history, self.hist_pos = [], -1
        self._snapshot()

    def _load_state(self):
        """Каждый запуск — новый проект. Из прошлого запуска берём только настройки."""
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            self._apply_doc({k: data[k] for k in ("ae", "style", "split", "recognize") if k in data})
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            self._refresh_views()
            self._bind_style()
        geo = self.settings.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        state = self.settings.value("dock_state_v2")
        if state is not None:
            self.restoreState(state)

    def new_project(self):
        if self.doc.subtitles and QMessageBox.question(
                self, "Новый проект", "Очистить субтитры?") != QMessageBox.StandardButton.Yes:
            return
        keep_ae = self.doc.ae
        self.project_path = None
        self._apply_doc({"ae": keep_ae.to_dict(), "style": self.doc.style.to_dict()})

    def open_project(self):
        path, _ = QFileDialog.getOpenFileName(self, "Открыть проект", str(PROJECTS_DIR), "AE Subs (*.aesubs)")
        if path:
            self._open_path(Path(path))

    def _open_path(self, path: Path):
        try:
            self._apply_doc(json.loads(path.read_text(encoding="utf-8")))
            self.project_path = path
            self.toast.show_message(f"Открыт проект «{path.stem}»", "info")
        except (OSError, ValueError, TypeError, KeyError) as e:
            QMessageBox.warning(self, "Открыть", f"Не удалось открыть проект:\n{e}")

    def save_project(self, ask=False):
        path = self.project_path
        if ask or path is None:
            p, _ = QFileDialog.getSaveFileName(self, "Сохранить проект", str(PROJECTS_DIR), "AE Subs (*.aesubs)")
            if not p:
                return
            path = Path(p)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self._doc_file_content(), encoding="utf-8")
        self.project_path = path
        self.toast.show_message(f"Сохранено: {path.name}")

    def import_srt(self):
        path, _ = QFileDialog.getOpenFileName(self, "Импорт SRT", "", "Субтитры (*.srt)")
        if not path:
            return
        raw = Path(path).read_bytes()
        content = ""
        for enc in ("utf-8-sig", "cp1251"):
            try:
                content = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        subs = parse_srt(content)
        if not subs:
            self.toast.show_message("В файле не найдено субтитров", "warn")
            return
        self.tabs.setCurrentIndex(0)
        self.finish_style_edit()
        lst = self.doc.subtitles
        if lst and QMessageBox.question(self, "Импорт SRT", "Заменить текущие?\n(«Нет» — добавить в конец)") \
                != QMessageBox.StandardButton.Yes:
            lst.extend(subs)
        else:
            lst[:] = subs
        self._refresh_views(select=0)
        self._changed()
        self.toast.show_message(f"Импортировано: {len(subs)}")

    def closeEvent(self, event):
        if self.doc.subtitles and self.project_path is None and not self.doc.comp.get("name"):
            ans = QMessageBox.question(
                self, "Выход", "Сохранить субтитры в проект? При следующем запуске откроется новый проект.",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel)
            if ans == QMessageBox.StandardButton.Cancel:
                event.ignore()
                return
            if ans == QMessageBox.StandardButton.Save:
                self.save_project()
                if self.project_path is None:
                    event.ignore()
                    return
        self.save_timer.stop()
        self._autosave()
        if self.preview_full:
            self.toggle_preview_full()
        self.settings.setValue("geometry", self.saveGeometry())
        # раскладку сохраняем в режиме редактора — на «Переводе» панели скрыты
        self._set_mode("edit")
        self.settings.setValue("dock_state_v2", self.saveState())
        self.player.unload()
        super().closeEvent(event)
