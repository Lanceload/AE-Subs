"""Галерея стилей: миниатюры пресетов, применение кликом, удаление правым кликом."""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QFrame, QGridLayout, QMenu, QScrollArea, QToolButton, QWidget

from .models import Style
from .paths import PRESETS_DIR
from .render import style_thumbnail
from .theme import ACCENT, BORDER, MUTED, SURFACE_2, TEXT

CARD_QSS = f"""
QToolButton {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 10px; padding: 4px;
              color: {MUTED}; font-size: 9pt; }}
QToolButton:hover {{ border-color: #4A5060; color: {TEXT}; }}
QToolButton:checked {{ border: 2px solid {ACCENT}; color: {TEXT}; }}
"""

DEFAULTS_MARK = ".defaults_v2"


def default_presets() -> dict[str, Style]:
    return {
        "Классика": Style(),
        "Шортс жёлтый": Style(font_size=96, fill_color="#FFE14D", stroke_width=12, all_caps=True,
                              pos_y=70, vertical_anchor="center", anim_in="pop", anim_out="none",
                              anim_in_dur=0.25, shadow_distance=8, shadow_softness=6),
        "Плашка": Style(font_style="Regular", font_size=56, stroke_enabled=False, shadow_enabled=False,
                        box_enabled=True, box_opacity=65, anim_in="fade", anim_out="fade"),
        "Кино": Style(font_style="Regular", font_size=52, stroke_width=3, shadow_opacity=80,
                      shadow_distance=3, shadow_softness=8, pos_y=90, anim_in="none", anim_out="none"),
        "Hormozi": Style(font_family="Montserrat", font_style="Black", font_size=92, all_caps=True,
                         stroke_width=14, shadow_enabled=True, shadow_distance=6, shadow_softness=4,
                         pos_y=68, vertical_anchor="center", anim_in="pop", anim_out="none", anim_in_dur=0.18,
                         hl_enabled=True, hl_mode="word", hl_color="#FFE14D", hl_scale=110),
        "MrBeast": Style(font_family="Montserrat", font_style="ExtraBold", font_size=100, all_caps=True,
                         stroke_width=10, stroke2_enabled=True, stroke2_color="#FFFFFF", stroke2_width=6,
                         shadow_enabled=True, shadow_distance=10, shadow_softness=0, shadow_opacity=90,
                         pos_y=65, vertical_anchor="center", anim_in="scale", anim_in_dur=0.15, anim_out="none",
                         hl_enabled=True, hl_color="#FF3B3B", hl_scale=108),
        "Неон": Style(font_family="Segoe UI", font_style="Bold", font_size=80, fill_color="#FFFFFF",
                      stroke_enabled=False, glow_enabled=True, glow_color="#00E5FF", glow_radius=35,
                      glow_opacity=90, shadow_enabled=False, pos_y=75, anim_in="blur", anim_out="blur"),
        "Караоке": Style(font_size=70, stroke_width=8, all_caps=True, pos_y=78,
                         hl_enabled=True, hl_mode="karaoke", hl_color="#00E5FF", hl_scale=100,
                         anim_in="fade", anim_out="fade"),
        "Градиент": Style(font_family="Montserrat", font_style="ExtraBold", font_size=90, all_caps=True,
                          fill_type="gradient", fill_color="#FFFFFF", fill_color2="#FFC93C", gradient_angle=90,
                          stroke_width=8, stroke2_enabled=True, stroke2_color="#7C5CFF", stroke2_width=8,
                          pos_y=70, vertical_anchor="center", anim_in="pop", anim_out="none"),
        "Минимал": Style(font_family="Segoe UI", font_style="Semibold", font_size=54, stroke_enabled=False,
                         shadow_enabled=True, shadow_distance=2, shadow_softness=14, shadow_opacity=60,
                         pos_y=88, anim_in="fade", anim_out="fade"),
        "Плашка по строкам": Style(font_family="Segoe UI", font_style="Bold", font_size=60, stroke_enabled=False,
                                   shadow_enabled=False, box_enabled=True, box_mode="lines", box_color="#FFFFFF",
                                   box_opacity=100, fill_color="#111111", box_padding_x=18, box_padding_y=8,
                                   box_roundness=10, pos_y=80, anim_in="slide", anim_out="fade"),
    }


def ensure_default_presets():
    """Кладёт стандартные пресеты, не трогая пользовательские; удалённые не возвращает."""
    PRESETS_DIR.mkdir(parents=True, exist_ok=True)
    mark = PRESETS_DIR / DEFAULTS_MARK
    if mark.exists():
        return
    for name, style in default_presets().items():
        path = PRESETS_DIR / f"{name}.json"
        if not path.exists():
            path.write_text(json.dumps(style.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    mark.write_text("ok", encoding="utf-8")


class StyleGallery(QScrollArea):
    applied = Signal(object, str)  # Style, имя
    saveRequested = Signal()
    shareRequested = Signal(str)  # имя пресета — скопировать код
    exportRequested = Signal(str)
    deleted = Signal(str)

    CARD_W, CARD_H, GAP = 116, 88, 8

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMinimumHeight(self.CARD_H + 4)
        holder = QWidget()
        # карточки идут строками сверху вниз; столбцов столько, сколько влезает по ширине
        self.grid = QGridLayout(holder)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(self.GAP)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.setWidget(holder)
        self.cards: list[QToolButton] = []
        self.buttons: dict[str, QToolButton] = {}
        self._cols = 0
        self.reload()

    def _columns(self) -> int:
        w = self.viewport().width()
        return max(1, (w + self.GAP) // (self.CARD_W + self.GAP))

    def _layout_cards(self, force: bool = False):
        cols = self._columns()
        if cols == self._cols and not force:
            return
        self._cols = cols
        for c in self.cards:
            self.grid.removeWidget(c)
        for i, c in enumerate(self.cards):
            self.grid.addWidget(c, i // cols, i % cols)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_cards()

    def _card(self, text: str, icon: QPixmap | None = None) -> QToolButton:
        b = QToolButton()
        b.setStyleSheet(CARD_QSS)
        b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        b.setIconSize(QSize(106, 60))
        b.setFixedSize(self.CARD_W, self.CARD_H)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setText(text)
        if icon is not None:
            b.setIcon(QIcon(icon))
        return b

    def reload(self, select: str | None = None):
        for c in self.cards:
            self.grid.removeWidget(c)
            c.deleteLater()
        self.cards = []
        self.buttons = {}
        add = self._card("＋\nСохранить\nтекущий")
        add.setToolTip("Сохранить текущий стиль как пресет")
        add.clicked.connect(self.saveRequested.emit)
        self.cards.append(add)
        for path in sorted(PRESETS_DIR.glob("*.json")) if PRESETS_DIR.is_dir() else []:
            try:
                style = Style.from_dict(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
            b = self._card(path.stem, QPixmap.fromImage(style_thumbnail(style, 212, 120)))
            b.setCheckable(True)
            b.setChecked(path.stem == select)
            b.setToolTip("Клик — применить, правый клик — поделиться, экспорт, удалить")
            b.clicked.connect(lambda _=False, s=style, n=path.stem: self._apply(s, n))
            b.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            b.customContextMenuRequested.connect(lambda _pos, p=path, btn=b: self._menu(p, btn))
            self.cards.append(b)
            self.buttons[path.stem] = b
        self._layout_cards(force=True)

    def _apply(self, style: Style, name: str):
        for n, b in self.buttons.items():
            b.setChecked(n == name)
        self.applied.emit(style.copy(), name)

    def clear_selection(self):
        for b in self.buttons.values():
            b.setChecked(False)

    def _menu(self, path: Path, btn: QToolButton):
        menu = QMenu(self)
        share = menu.addAction("Скопировать код стиля — для мессенджера")
        export = menu.addAction("Экспорт в файл…")
        menu.addSeparator()
        delete = menu.addAction(f"Удалить «{path.stem}»")
        act = menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))
        if act == share:
            self.shareRequested.emit(path.stem)
        elif act == export:
            self.exportRequested.emit(path.stem)
        elif act == delete:
            path.unlink(missing_ok=True)
            self.deleted.emit(path.stem)
            self.reload()
