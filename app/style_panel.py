"""Панели настроек: стиль текста (с разделами слева) и параметры After Effects."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QDoubleSpinBox, QFileDialog, QFontComboBox, QFrame,
                               QGridLayout, QHBoxLayout, QLineEdit, QScrollArea, QSpinBox, QStackedWidget,
                               QVBoxLayout, QWidget)

from .ae_bridge import find_afterfx
from .anim import ANIM_IN, ANIM_OUT
from .models import AESettings, Style
from .theme import (BORDER, MUTED, SURFACE_2, SURFACE_3, TEXT, ColorButton, SliderSpin, ToggleSwitch, button,
                    label, nav_icon)


def font_has_cyrillic(family: str) -> bool:
    return QFontDatabase.WritingSystem.Cyrillic in QFontDatabase.writingSystems(family)


def _dspin(lo, hi, step=1.0, decimals=1, suffix="") -> QDoubleSpinBox:
    w = QDoubleSpinBox()
    w.setRange(lo, hi)
    w.setSingleStep(step)
    w.setDecimals(decimals)
    if suffix:
        w.setSuffix(suffix)
    return w


def _spin(lo, hi, step=1) -> QSpinBox:
    w = QSpinBox()
    w.setRange(lo, hi)
    w.setSingleStep(step)
    return w


def _combo(items: list[tuple[str, str]]) -> QComboBox:
    """items: (значение, подпись)"""
    w = QComboBox()
    for value, text in items:
        w.addItem(text, value)
    return w


class FormPage(QScrollArea):
    """Страница настроек: подписи слева, элементы справа, с прокруткой."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        self.grid = QGridLayout(body)
        self.grid.setContentsMargins(4, 4, 10, 6)
        self.grid.setHorizontalSpacing(16)
        self.grid.setVerticalSpacing(11)
        self.grid.setColumnMinimumWidth(0, 112)
        self.grid.setColumnStretch(1, 1)
        self.setWidget(body)
        self._row = 0
        self.rows: dict[QWidget, QWidget] = {}  # элемент → его подпись (чтобы прятать/блокировать вместе)

    def add_row(self, text: str, widget: QWidget, hint: str = ""):
        lb = label(text, "muted")
        lb.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        if hint:
            lb.setToolTip(hint)
            widget.setToolTip(hint)
        self.grid.addWidget(lb, self._row, 0)
        self.grid.addWidget(widget, self._row, 1)
        self.rows[widget] = lb
        self._row += 1
        return widget

    def add_section(self, text: str):
        lb = label(text.upper(), "small")
        lb.setStyleSheet("letter-spacing: 1px; padding-top: 8px;")
        self.grid.addWidget(lb, self._row, 0, 1, 2)
        self._row += 1

    def add_note(self, text: str):
        lb = label(text, "small")
        lb.setWordWrap(True)
        self.grid.addWidget(lb, self._row, 0, 1, 2)
        self._row += 1
        return lb

    def finish(self):
        self.grid.setRowStretch(self._row, 1)


NAV_QSS = f"""
QListWidget {{ background: transparent; border: none; outline: none; }}
QListWidget::item {{ color: {MUTED}; padding: 7px 10px; border-radius: 8px; margin: 1px 0; }}
QListWidget::item:hover {{ background: {SURFACE_2}; color: {TEXT}; }}
QListWidget::item:selected {{ background: {SURFACE_3}; color: {TEXT}; }}
"""


class StylePanel(QWidget):
    changed = Signal()
    animPreview = Signal()  # показать эффект появления/исчезновения в превью

    PAGES = [("text", "Текст"), ("fill", "Заливка"), ("stroke", "Обводка"), ("glow", "Свечение"),
             ("shadow", "Тень"), ("box", "Плашка"), ("position", "Положение"), ("anim", "Анимация"),
             ("highlight", "Подсветка слов")]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loading = False
        self.w: dict[str, QWidget] = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        # разделы — строкой иконок (название в подсказке и в заголовке раздела)
        bar = QFrame()
        bar.setObjectName("Segmented")
        self.nav_lay = QHBoxLayout(bar)
        self.nav_lay.setContentsMargins(3, 3, 3, 3)
        self.nav_lay.setSpacing(2)
        self.nav = QButtonGroup(self)
        self.nav.setExclusive(True)
        lay.addWidget(bar)
        self.page_title = label("", "title")
        lay.addWidget(self.page_title)
        self.stack = QStackedWidget()
        lay.addWidget(self.stack, 1)
        self.titles: list[str] = []
        for icon, text in self.PAGES:
            self._nav_button(icon, text)
        self.nav.idClicked.connect(self._go)

        self._build_text()
        self._build_fill()
        self._build_stroke()
        self._build_glow()
        self._build_shadow()
        self._build_box()
        self._build_position()
        self._build_anim()
        self._build_highlight()
        self._go(0)
        self.set_style(Style())

    def _nav_button(self, icon: str, title: str):
        b = button("", "seg", title)
        b.setIcon(nav_icon(icon))
        b.setIconSize(QSize(22, 22))
        b.setCheckable(True)
        b.setMinimumSize(36, 34)
        self.nav.addButton(b, len(self.titles))
        self.nav_lay.addWidget(b)
        self.titles.append(title)

    def _go(self, i: int):
        self.nav.button(i).setChecked(True)
        self.stack.setCurrentIndex(i)
        self.page_title.setText(self.titles[i])

    def add_page(self, icon: str, title: str, widget: QWidget):
        self._nav_button(icon, title)
        self.stack.addWidget(widget)

    def _page(self) -> FormPage:
        page = FormPage()
        self.stack.addWidget(page)
        return page

    def _reg(self, key: str, widget: QWidget) -> QWidget:
        self.w[key] = widget
        if isinstance(widget, ColorButton):
            widget.changed.connect(self._emit)
        elif isinstance(widget, (QDoubleSpinBox, QSpinBox, SliderSpin)):
            widget.valueChanged.connect(self._emit)
        elif isinstance(widget, ToggleSwitch):
            widget.toggled.connect(self._emit)
        elif isinstance(widget, QComboBox):
            widget.currentIndexChanged.connect(self._emit)
        return widget

    # ------------------------------------------------------------ страницы

    def _build_text(self):
        p = self._page()
        self.font_combo = QFontComboBox()
        self.font_combo.setMaxVisibleItems(16)
        self.font_combo.currentFontChanged.connect(self._on_family_changed)
        p.add_row("Шрифт", self.font_combo)
        cyr = QWidget()
        cl = QVBoxLayout(cyr)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(4)
        self.cyr_only = ToggleSwitch("Только с русскими буквами")
        self.cyr_only.setChecked(True)
        self.cyr_only.toggled.connect(self._apply_font_filter)
        cl.addWidget(self.cyr_only)
        self.cyr_warning = label("⚠ В этом шрифте нет русских букв — After Effects заменит их "
                                 "стандартным шрифтом с засечками.", "small")
        self.cyr_warning.setStyleSheet("color: #F59E0B;")
        self.cyr_warning.setWordWrap(True)
        self.cyr_warning.hide()
        cl.addWidget(self.cyr_warning)
        p.add_row("", cyr)
        self.font_style_combo = QComboBox()
        self.font_style_combo.currentIndexChanged.connect(self._emit)
        p.add_row("Начертание", self.font_style_combo)
        p.add_row("Размер", self._reg("font_size", SliderSpin(1, 1000, 1, 0, " px", slider_max=300)))
        p.add_row("Трекинг", self._reg("tracking", SliderSpin(-200, 1000, 5, 0, "", slider_max=400)),
                  "Расстояние между буквами (тысячные доли кегля, как в AE)")
        p.add_row("Выравнивание", self._reg("justification", _combo(
            [("center", "По центру"), ("left", "По левому краю"), ("right", "По правому краю")])))
        p.add_row("Все прописные", self._reg("all_caps", ToggleSwitch()))
        p.add_row("Псевдо-жирный", self._reg("faux_bold", ToggleSwitch()))
        p.add_row("Псевдо-курсив", self._reg("faux_italic", ToggleSwitch()))
        p.add_row("Ширина букв", self._reg("h_scale", SliderSpin(10, 400, 1, 0, " %", slider_max=200)))
        p.add_row("Высота букв", self._reg("v_scale", SliderSpin(10, 400, 1, 0, " %", slider_max=200)))
        p.add_row("Непрозрачность", self._reg("text_opacity", SliderSpin(0, 100, 5, 0, " %")))
        p.add_section("Строки")
        p.add_row("Авто-интерлиньяж", self._reg("auto_leading", ToggleSwitch()))
        p.add_row("Интерлиньяж", self._reg("leading", SliderSpin(0, 2000, 1, 0, " px", slider_max=400)))
        mc = self._reg("max_chars", SliderSpin(0, 200, 1, 0, "", slider_max=80))
        mc.spin.setSpecialValueText("выкл")
        p.add_row("Символов в строке", mc, "Автоматический перенос по словам, если строка длиннее. 0 — выкл")
        p.finish()

    def _build_fill(self):
        p = self._page()
        p.add_row("Заливка", self._reg("fill_type", _combo([("solid", "Сплошной цвет"), ("gradient", "Градиент")])))
        p.add_row("Цвет", self._reg("fill_color", ColorButton()))
        p.add_row("Второй цвет", self._reg("fill_color2", ColorButton()))
        p.add_row("Угол градиента", self._reg("gradient_angle", SliderSpin(0, 360, 5, 0, " °")),
                  "0° — слева направо, 90° — сверху вниз")
        p.finish()
        self._fill_page = p

    def _build_stroke(self):
        p = self._page()
        p.add_row("Обводка", self._reg("stroke_enabled", ToggleSwitch()))
        p.add_row("Цвет", self._reg("stroke_color", ColorButton()))
        p.add_row("Толщина", self._reg("stroke_width", SliderSpin(0, 200, 0.5, 1, " px", slider_max=60)))
        p.add_row("Поверх заливки", self._reg("stroke_over_fill", ToggleSwitch()),
                  "Выкл — обводка под буквами (буквы не «худеют»)")
        p.add_section("Вторая обводка")
        p.add_row("Вторая обводка", self._reg("stroke2_enabled", ToggleSwitch()), "Ещё один контур снаружи первого")
        p.add_row("Цвет", self._reg("stroke2_color", ColorButton()))
        p.add_row("Толщина", self._reg("stroke2_width", SliderSpin(0, 200, 0.5, 1, " px", slider_max=60)))
        p.finish()

    def _build_glow(self):
        p = self._page()
        p.add_row("Свечение", self._reg("glow_enabled", ToggleSwitch()))
        p.add_row("Цвет", self._reg("glow_color", ColorButton()))
        p.add_row("Радиус", self._reg("glow_radius", SliderSpin(0, 300, 1, 0, " px", slider_max=120)))
        p.add_row("Яркость", self._reg("glow_opacity", SliderSpin(0, 100, 5, 0, " %")))
        p.finish()

    def _build_shadow(self):
        p = self._page()
        p.add_row("Тень", self._reg("shadow_enabled", ToggleSwitch()))
        p.add_row("Цвет", self._reg("shadow_color", ColorButton()))
        p.add_row("Непрозрачность", self._reg("shadow_opacity", SliderSpin(0, 100, 5, 0, " %")))
        p.add_row("Направление", self._reg("shadow_direction", SliderSpin(0, 360, 5, 0, " °")))
        p.add_row("Расстояние", self._reg("shadow_distance", SliderSpin(0, 500, 1, 0, " px", slider_max=80)))
        p.add_row("Мягкость", self._reg("shadow_softness", SliderSpin(0, 500, 1, 0, " px", slider_max=120)))
        p.finish()

    def _build_box(self):
        p = self._page()
        p.add_row("Плашка", self._reg("box_enabled", ToggleSwitch()))
        p.add_row("Вид", self._reg("box_mode", _combo([("block", "Одна на весь текст"), ("lines", "На каждую строку")])))
        p.add_row("Цвет", self._reg("box_color", ColorButton()))
        p.add_row("Непрозрачность", self._reg("box_opacity", SliderSpin(0, 100, 5, 0, " %")))
        p.add_row("Отступ по бокам", self._reg("box_padding_x", SliderSpin(0, 500, 1, 0, " px", slider_max=150)))
        p.add_row("Отступ сверху/снизу", self._reg("box_padding_y", SliderSpin(0, 500, 1, 0, " px", slider_max=150)))
        p.add_row("Скругление", self._reg("box_roundness", SliderSpin(0, 500, 1, 0, " px", slider_max=100)))
        p.finish()

    def _build_position(self):
        p = self._page()
        p.add_note("Подсказка: текст можно перетаскивать мышью прямо в превью.")
        p.add_row("По горизонтали", self._reg("pos_x", SliderSpin(-50, 150, 0.5, 1, " %", slider_max=100)))
        p.add_row("По вертикали", self._reg("pos_y", SliderSpin(-50, 150, 0.5, 1, " %", slider_max=100)))
        p.add_row("Привязка текста", self._reg("vertical_anchor", _combo(
            [("bottom", "Нижний край — текст растёт вверх"), ("center", "Центр"),
             ("top", "Верхний край — текст растёт вниз")])))
        p.add_row("Поворот", self._reg("rotation", SliderSpin(-180, 180, 1, 0, " °")))
        p.finish()

    def _build_anim(self):
        p = self._page()
        demo = button("▶  Показать эффект", None, "Проиграть появление и исчезновение в превью. "
                      "При смене эффекта он показывается сам", self.animPreview.emit)
        p.add_row("Предпросмотр", demo)
        p.add_row("Появление", self._reg("anim_in", _combo(ANIM_IN)))
        p.add_row("Длительность", self._reg("anim_in_dur", SliderSpin(0, 10, 0.05, 2, " с", slider_max=2)))
        p.add_row("Исчезновение", self._reg("anim_out", _combo(ANIM_OUT)))
        p.add_row("Длительность", self._reg("anim_out_dur", SliderSpin(0, 10, 0.05, 2, " с", slider_max=2)))
        p.add_row("Дистанция сдвига", self._reg("slide_distance", SliderSpin(0, 1000, 5, 0, " px", slider_max=200)))
        # смена эффекта — сразу показать его (с задержкой, чтобы не дёргать на каждом шаге ползунка)
        self._demo_timer = QTimer(self, singleShot=True, interval=350, timeout=self.animPreview.emit)
        for k in ("anim_in", "anim_in_dur", "anim_out", "anim_out_dur", "slide_distance"):
            w = self.w[k]
            sig = w.currentIndexChanged if isinstance(w, QComboBox) else w.valueChanged
            sig.connect(lambda *_: None if self._loading else self._demo_timer.start())
        p.finish()

    def _build_highlight(self):
        p = self._page()
        p.add_note("Текущее слово выделяется по таймингу речи — как в шортсах. Тайминги слов берутся из "
                   "распознавания; если текст правился вручную, они распределяются по длине слов.")
        p.add_row("Подсветка слов", self._reg("hl_enabled", ToggleSwitch()))
        p.add_row("Режим", self._reg("hl_mode", _combo(
            [("word", "Только текущее слово"), ("karaoke", "Караоке — сказанные остаются")])))
        p.add_row("Цвет", self._reg("hl_color", ColorButton()))
        p.add_row("Увеличение слова", self._reg("hl_scale", SliderSpin(50, 200, 1, 0, " %", slider_max=150)))
        p.add_row("Слова появляются", self._reg("hl_reveal", ToggleSwitch()),
                  "Слова, которые ещё не прозвучали, скрыты")
        p.finish()

    # ------------------------------------------------------------ шрифты

    def _apply_font_filter(self, on: bool):
        current = self.font_combo.currentFont()
        self.font_combo.blockSignals(True)
        self.font_combo.setWritingSystem(QFontDatabase.WritingSystem.Cyrillic if on
                                         else QFontDatabase.WritingSystem.Any)
        self.font_combo.setCurrentFont(current)
        self.font_combo.blockSignals(False)

    def _update_cyr_warning(self):
        self.cyr_warning.setVisible(not font_has_cyrillic(self.font_combo.currentFont().family()))

    def _on_family_changed(self, font: QFont):
        self._fill_font_styles(font.family(), self.font_style_combo.currentText())
        self._update_cyr_warning()
        self._emit()

    def _fill_font_styles(self, family: str, prefer: str):
        styles = QFontDatabase.styles(family) or ["Regular"]
        self.font_style_combo.blockSignals(True)
        self.font_style_combo.clear()
        self.font_style_combo.addItems(styles)
        idx = self.font_style_combo.findText(prefer)
        if idx < 0:
            idx = self.font_style_combo.findText("Regular")
        self.font_style_combo.setCurrentIndex(max(idx, 0))
        self.font_style_combo.blockSignals(False)

    # ------------------------------------------------------------ данные

    def _update_enabled(self):
        """Блокирует настройки, которые сейчас ни на что не влияют."""
        w = self.w
        on = lambda k: w[k].isChecked()  # noqa: E731
        grad = w["fill_type"].currentData() == "gradient"
        groups = {
            ("fill_color2", "gradient_angle"): grad,
            ("leading",): not on("auto_leading"),
            ("stroke_color", "stroke_width", "stroke_over_fill"): on("stroke_enabled"),
            ("stroke2_color", "stroke2_width"): on("stroke2_enabled"),
            ("glow_color", "glow_radius", "glow_opacity"): on("glow_enabled"),
            ("shadow_color", "shadow_opacity", "shadow_direction", "shadow_distance", "shadow_softness"): on("shadow_enabled"),
            ("box_mode", "box_color", "box_opacity", "box_padding_x", "box_padding_y", "box_roundness"): on("box_enabled"),
            ("hl_mode", "hl_color", "hl_scale", "hl_reveal"): on("hl_enabled"),
            ("anim_in_dur",): w["anim_in"].currentData() != "none",
            ("anim_out_dur",): w["anim_out"].currentData() != "none",
        }
        for keys, enabled in groups.items():
            for k in keys:
                w[k].setEnabled(enabled)

    def _emit(self, *_):
        if not self._loading:
            self._update_enabled()
            self.changed.emit()

    def get_style(self) -> Style:
        data = {"font_family": self.font_combo.currentFont().family(),
                "font_style": self.font_style_combo.currentText() or "Regular"}
        for key, w in self.w.items():
            if isinstance(w, ColorButton):
                data[key] = w.color()
            elif isinstance(w, (QDoubleSpinBox, QSpinBox, SliderSpin)):
                data[key] = w.value()
            elif isinstance(w, ToggleSwitch):
                data[key] = w.isChecked()
            elif isinstance(w, QComboBox):
                data[key] = w.currentData()
        data["tracking"] = int(data["tracking"])
        data["max_chars"] = int(data["max_chars"])
        return Style.from_dict(data)

    def set_style(self, style: Style, silent: bool = False):
        self._loading = True
        try:
            if not font_has_cyrillic(style.font_family) and self.cyr_only.isChecked():
                # иначе фильтр спрячет шрифт и он тихо подменится другим
                self.cyr_only.setChecked(False)
            self.font_combo.blockSignals(True)
            self.font_combo.setCurrentFont(QFont(style.font_family))
            self.font_combo.blockSignals(False)
            self._fill_font_styles(style.font_family, style.font_style)
            self._update_cyr_warning()
            for key, w in self.w.items():
                value = getattr(style, key)
                if isinstance(w, ColorButton):
                    w.set_color(value)
                elif isinstance(w, (QDoubleSpinBox, QSpinBox, SliderSpin)):
                    w.setValue(value)
                elif isinstance(w, ToggleSwitch):
                    w.setChecked(bool(value))
                elif isinstance(w, QComboBox):
                    w.setCurrentIndex(max(w.findData(value), 0))
        finally:
            self._loading = False
        self._update_enabled()
        if not silent:
            self.changed.emit()


class AEPanel(FormPage):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loading = False

        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(8)
        self.path = QComboBox()
        self.path.setEditable(True)
        self.path.addItems(find_afterfx())
        rl.addWidget(self.path, 1)
        rl.addWidget(button("Обзор…", slot=self._browse))
        self.add_row("AfterFX.exe", row)

        self.target = _combo([("precomp", "Отдельная композиция поверх всех слоёв"),
                              ("layers", "Слоями прямо в композицию")])
        self.add_row("Куда вставлять", self.target,
                     "Субтитры попадают в открытую в AE композицию с видео. По умолчанию — отдельной "
                     "композицией самым верхним слоем, внутри неё — сами субтитры.")
        self.comp_name = QLineEdit()
        self.add_row("Композиция субтитров", self.comp_name)
        self.remove_old = ToggleSwitch()
        self.add_row("Заменять старые", self.remove_old,
                     "Перед вставкой удалить слои, добавленные раньше (с тем же префиксом)")
        self.offset = _dspin(-36000, 36000, 0.1, 3, " с")
        self.add_row("Сдвиг времени", self.offset, "Сдвинуть все субтитры на это время")
        self.prefix = QLineEdit()
        self.add_row("Префикс слоёв", self.prefix, "Слои будут называться «ПРЕФИКС 001 | текст…»")

        self.add_section("Свой стиль субтитров")
        self.propagate = ToggleSwitch()
        self.add_row("Общий стиль меняет и их", self.propagate,
                     "Вкл — изменения общего стиля применяются и к субтитрам со своим стилем (меняются только те "
                     "настройки, которые вы трогаете). Выкл — субтитры со своим стилем не меняются.")

        self.add_section("Превью")
        size_row = QWidget()
        sl = QHBoxLayout(size_row)
        sl.setContentsMargins(0, 0, 0, 0)
        self.width_ = _spin(16, 16000)
        self.height_ = _spin(16, 16000)
        sl.addWidget(self.width_)
        sl.addWidget(label("×", "muted"))
        sl.addWidget(self.height_)
        for text, w, h in (("16:9", 1920, 1080), ("9:16", 1080, 1920), ("1:1", 1080, 1080)):
            sl.addWidget(button(text, "chip", slot=lambda _=False, w=w, h=h: self._set_size(w, h)))
        sl.addStretch()
        self.add_row("Размер кадра", size_row, "Берётся из композиции AE автоматически")
        self.fps = _dspin(1, 240, 1, 3)  # хранится для совместимости, в интерфейсе не нужен
        self.finish()

        for w in (self.width_, self.height_, self.fps, self.offset):
            w.valueChanged.connect(self._emit)
        self.target.currentIndexChanged.connect(self._emit)
        self.remove_old.toggled.connect(self._emit)
        self.propagate.toggled.connect(self._emit)
        self.path.currentTextChanged.connect(self._emit)
        for w in (self.comp_name, self.prefix):
            w.textChanged.connect(self._emit)
        self.set_settings(AESettings())

    def _set_size(self, w: int, h: int):
        self.width_.setValue(w)
        self.height_.setValue(h)

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "Где AfterFX.exe?", "", "AfterFX.exe (AfterFX.exe)")
        if path:
            self.path.setCurrentText(path.replace("/", "\\"))

    def _emit(self, *_):
        if not self._loading:
            self.changed.emit()

    def get_settings(self) -> AESettings:
        return AESettings(
            afterfx_path=self.path.currentText().strip(),
            target=self.target.currentData(),
            comp_name=self.comp_name.text().strip() or "Субтитры",
            comp_width=self.width_.value(),
            comp_height=self.height_.value(),
            comp_fps=self.fps.value(),
            time_offset=self.offset.value(),
            layer_prefix=self.prefix.text().strip() or "SUB",
            remove_old=self.remove_old.isChecked(),
            propagate_style=self.propagate.isChecked(),
        )

    def set_settings(self, s: AESettings):
        self._loading = True
        try:
            if s.afterfx_path:
                self.path.setCurrentText(s.afterfx_path)
            self.target.setCurrentIndex(max(self.target.findData(s.target), 0))
            self.comp_name.setText(s.comp_name)
            self.width_.setValue(s.comp_width)
            self.height_.setValue(s.comp_height)
            self.fps.setValue(s.comp_fps)
            self.offset.setValue(s.time_offset)
            self.prefix.setText(s.layer_prefix)
            self.remove_old.setChecked(s.remove_old)
            self.propagate.setChecked(s.propagate_style)
        finally:
            self._loading = False
        self.changed.emit()
