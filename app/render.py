"""Отрисовка субтитра со стилем — для превью и миниатюр стилей.

Повторяет то, как скрипт строит слои в After Effects: плашка → свечение → тень →
вторая обводка → первая обводка → заливка (сплошная или градиент), с подсветкой
и собственными стилями отдельных слов.
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QFont, QFontDatabase, QFontMetricsF, QImage, QLinearGradient,
                           QPainter, QPainterPath, QPainterPathStroker, QPen, QPixmap, QTransform)
from PySide6.QtWidgets import QGraphicsBlurEffect, QGraphicsPixmapItem, QGraphicsScene

from .models import Style, Subtitle, word_style, wrap_text

MARK_COLOR = "#FFD84D"  # подсветка того, что сейчас редактируется


def blur_image(img: QImage, radius: float) -> QImage:
    if radius <= 0.5:
        return img
    scene = QGraphicsScene()
    item = QGraphicsPixmapItem(QPixmap.fromImage(img))
    effect = QGraphicsBlurEffect()
    effect.setBlurRadius(radius)
    effect.setBlurHints(QGraphicsBlurEffect.BlurHint.QualityHint)
    item.setGraphicsEffect(effect)
    scene.addItem(item)
    out = QImage(img.size(), QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    scene.render(p, QRectF(out.rect()), QRectF(img.rect()))
    p.end()
    return out


def prepare_text(text: str, style: Style) -> str:
    text = wrap_text(text, style.max_chars)
    return text.upper() if style.all_caps else text


def current_word(sub: Subtitle, t: float | None) -> int | None:
    """Индекс слова, которое звучит в момент t (последнее начавшееся)."""
    if t is None:
        return None
    k = -1
    for i, (s, _e) in enumerate(sub.word_times()):
        if t >= s:
            k = i
    return k


@dataclass
class LaidWord:
    path: QPainterPath
    line: int
    index: int  # номер слова в субтитре
    style: Style  # итоговый стиль слова


@dataclass
class Layout:
    words: list  # [LaidWord]
    lines: int


def _make_font(s: Style, scale: float) -> QFont:
    font = QFontDatabase.font(s.font_family, s.font_style, 12)
    font.setPixelSize(max(1, round(s.font_size * scale)))
    if s.faux_bold:
        font.setBold(True)
    if s.faux_italic:
        font.setItalic(True)
    # трекинг в AE — тысячные доли кегля
    font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, s.tracking / 1000 * s.font_size * scale)
    font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return font


def build_layout(style: Style, text: str, scale: float, word_styles: dict | None = None) -> Layout:
    """Пути слов в «координатах слоя»: начало — базовая линия первой строки.

    word_styles — номер слова → его отличия от стиля субтитра.
    """
    word_styles = word_styles or {}
    base_font = _make_font(style, scale)
    space = QFontMetricsF(base_font).horizontalAdvance(" ") * style.h_scale / 100
    fonts: dict[int, QFont] = {}
    out = []
    lines = text.split("\n")
    gi = 0  # номер слова во всём субтитре
    baseline = 0.0
    for li, line in enumerate(lines):
        items = []
        for word in line.split():
            ws = word_style(style, word_styles.get(gi))
            key = id(ws)
            font = fonts.get(key) or fonts.setdefault(key, _make_font(ws, scale))
            shown = word.upper() if ws.all_caps else word
            adv = QFontMetricsF(font).horizontalAdvance(shown) * ws.h_scale / 100
            items.append((gi, shown, ws, font, adv))
            gi += 1
        if li > 0:
            biggest = max([it[2].font_size for it in items] + [style.font_size])
            baseline += (biggest * 1.2 if style.auto_leading else style.leading) * scale
        width = sum(it[4] for it in items) + space * max(len(items) - 1, 0)
        x = 0 if style.justification == "left" else -width if style.justification == "right" else -width / 2
        for idx, shown, ws, font, adv in items:
            path = QPainterPath()
            path.addText(QPointF(0, 0), font, shown)
            t = QTransform().translate(x, baseline).scale(ws.h_scale / 100, ws.v_scale / 100)
            out.append(LaidWord(t.map(path), li, idx, ws))
            x += adv + space
    return Layout(out, len(lines))


def _union(paths) -> QPainterPath:
    full = QPainterPath()
    for p in paths:
        full.addPath(p)
    return full


def _outline(path: QPainterPath, width: float) -> QPainterPath:
    """Контур пути с обводкой шириной width (как обводка в AE — по центру линии)."""
    if width <= 0:
        return QPainterPath(path)
    stroker = QPainterPathStroker()
    stroker.setWidth(width)
    stroker.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
    stroker.setMiterLimit(4)
    return stroker.createStroke(path).united(path)


def _fill_brush(style: Style, bounds: QRectF) -> QBrush:
    if style.fill_type != "gradient":
        return QBrush(QColor(style.fill_color))
    a = math.radians(style.gradient_angle)
    c = bounds.center()
    half = (abs(math.cos(a)) * bounds.width() + abs(math.sin(a)) * bounds.height()) / 2
    d = QPointF(math.cos(a) * half, math.sin(a) * half)
    g = QLinearGradient(c - d, c + d)
    g.setColorAt(0, QColor(style.fill_color))
    g.setColorAt(1, QColor(style.fill_color2))
    return QBrush(g)


def render_item(p: QPainter, frame: QRectF, comp_w: int, style: Style, text: str,
                word_k: int | None = None, word_styles: dict | None = None,
                mark_words: set | None = None, mark_all: bool = False, anim=None) -> QRectF:
    """Рисует один субтитр в кадре frame. Возвращает его границы (для перетаскивания мышью).

    mark_words — номера слов, которые сейчас редактируются (обводятся рамкой),
    mark_all — весь субтитр редактируется своим стилем,
    anim — состояние эффекта появления/исчезновения (anim.AnimState) в текущий момент.
    """
    if anim is not None and anim.blur > 0.5:
        # «из размытия»: рисуем во временную картинку и размываем её целиком
        dev = p.device()
        img = QImage(dev.width(), dev.height(), QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        ip = QPainter(img)
        ip.setTransform(p.transform())
        sharp = copy.copy(anim)
        sharp.blur = 0.0
        bounds = render_item(ip, frame, comp_w, style, text, word_k, word_styles, mark_words, mark_all, sharp)
        ip.end()
        p.save()
        p.resetTransform()
        p.drawImage(0, 0, blur_image(img, anim.blur * frame.width() / max(comp_w, 1)))
        p.restore()
        return bounds
    s = style
    word_styles = word_styles or {}
    scale = frame.width() / max(comp_w, 1)
    if anim is not None and (anim.opacity <= 0.003 or anim.sx < 0.01 or anim.sy < 0.01):
        return QRectF()  # текст ещё (или уже) не виден
    text = prepare_text(text, s)
    if not text.strip():
        return QRectF()
    layout = build_layout(s, text, scale, word_styles)
    if not layout.words:
        return QRectF()

    # привязка и поворот — как anchorPoint/position/rotation в AE
    bounds = _union(w.path for w in layout.words).boundingRect()
    ax = bounds.left() if s.justification == "left" else bounds.right() if s.justification == "right" else bounds.center().x()
    ay = bounds.top() if s.vertical_anchor == "top" else bounds.center().y() if s.vertical_anchor == "center" else bounds.bottom()
    target = QPointF(frame.left() + frame.width() * s.pos_x / 100, frame.top() + frame.height() * s.pos_y / 100)
    a = anim
    t = QTransform()
    t.translate(target.x() + (a.dx * scale if a else 0), target.y() + (a.dy * scale if a else 0))
    t.rotate(s.rotation + (a.rot if a else 0))
    if a and (a.sx != 1 or a.sy != 1):
        t.scale(a.sx, a.sy)
    t.translate(-ax, -ay)

    # постепенное проявление: доля каждого слова (0 — не видно, 1 — целиком)
    reveal_of = {}
    if a is not None and a.reveal is not None:
        mode, frac, _pop = a.reveal
        n = len(layout.words)
        if mode == "words":
            for w in layout.words:
                reveal_of[w.index] = min(max(frac * n - w.index, 0.0), 1.0)
        else:
            lens = {w.index: max(len(text.split()[w.index]) if w.index < len(text.split()) else 1, 1)
                    for w in layout.words}
            total = sum(lens.values())
            shown, before = frac * total, 0
            for w in layout.words:
                reveal_of[w.index] = min(max((shown - before) / lens[w.index], 0.0), 1.0)
                before += lens[w.index]

    hl = s.hl_enabled and word_k is not None
    words = []  # (путь, строка, подсвечено, стиль слова, номер)
    for w in layout.words:
        i, path = w.index, w.path
        if hl and s.hl_reveal and i > word_k:
            continue  # слово ещё не прозвучало
        if reveal_of:
            lp = reveal_of.get(i, 1.0)
            if lp <= 0.02 or (not a.reveal[2] and a.reveal[0] == "words" and lp < 0.5):
                continue
            if a.reveal[2] and lp < 1.0:  # «выскакивание»: слово растёт из маленького
                c = path.boundingRect().center()
                k = 0.3 + 0.7 * lp
                path = QTransform().translate(c.x(), c.y()).scale(k, k).translate(-c.x(), -c.y()).map(path)
        lit = hl and (i == word_k or (s.hl_mode == "karaoke" and i <= word_k))
        if hl and i == word_k and abs(s.hl_scale - 100) > 0.5:
            c = path.boundingRect().center()
            k = s.hl_scale / 100
            path = QTransform().translate(c.x(), c.y()).scale(k, k).translate(-c.x(), -c.y()).map(path)
        words.append((t.map(path), w.line, lit, w.style, i))
    if not words:
        return t.mapRect(bounds)
    full = _union(w[0] for w in words)

    def w1_of(ws: Style) -> float:
        return ws.stroke_width * scale if ws.stroke_enabled else 0

    w2 = s.stroke2_width * scale if s.stroke2_enabled else 0
    # силуэт со всеми обводками (у каждого слова может быть своя толщина)
    outer = _union(_outline(path, w1_of(ws) + 2 * w2) if (w1_of(ws) or w2) else path
                   for path, _l, _lit, ws, _i in words)

    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setOpacity(s.text_opacity / 100 * (a.opacity if a else 1.0))

    # плашка
    if s.box_enabled:
        color = QColor(s.box_color)
        color.setAlphaF(s.box_opacity / 100)
        px, py = s.box_padding_x * scale, s.box_padding_y * scale
        rects = []
        if s.box_mode == "lines":
            for li in range(layout.lines):
                line_paths = [w[0] for w in words if w[1] == li]
                if line_paths:
                    rects.append(_union(line_paths).boundingRect())
        else:
            rects.append(full.boundingRect())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        for r in rects:
            r = r.adjusted(-px, -py, px, py)
            radius = min(s.box_roundness * scale, r.height() / 2, r.width() / 2)
            p.drawRoundedRect(r, radius, radius)

    def blurred(path: QPainterPath, color: str, radius: float, opacity: float, offset=QPointF()):
        # размываем только область вокруг текста — так превью успевает за видео
        margin = radius * 2.5 + 4
        area = path.translated(offset).boundingRect().adjusted(-margin, -margin, margin, margin).toAlignedRect()
        if area.width() <= 0 or area.height() <= 0:
            return
        img = QImage(area.size(), QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        ip = QPainter(img)
        ip.setRenderHint(QPainter.RenderHint.Antialiasing)
        ip.setPen(Qt.PenStyle.NoPen)
        ip.setBrush(QColor(color))
        ip.drawPath(path.translated(offset - QPointF(area.topLeft())))
        ip.end()
        img = blur_image(img, radius)
        p.save()
        p.setOpacity(p.opacity() * opacity)
        p.drawImage(area.topLeft(), img)
        p.restore()

    if s.glow_enabled:
        blurred(outer, s.glow_color, s.glow_radius * scale * 1.2, s.glow_opacity / 100)
    if s.shadow_enabled:
        # направление в AE: 0° — вверх, по часовой стрелке
        rad = math.radians(s.shadow_direction)
        off = QPointF(math.sin(rad) * s.shadow_distance * scale, -math.cos(rad) * s.shadow_distance * scale)
        blurred(outer, s.shadow_color, s.shadow_softness * scale * 1.5, s.shadow_opacity / 100, off)

    if w2:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(s.stroke2_color))
        p.drawPath(outer)

    base_brush = _fill_brush(s, full.boundingRect())
    hl_brush = QBrush(QColor(s.hl_color))

    def draw_strokes():
        for path, _l, _lit, ws, _i in words:
            w1 = w1_of(ws)
            if w1:
                pen = QPen(QColor(ws.stroke_color), w1)
                pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
                pen.setMiterLimit(4)
                p.setPen(pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPath(path)

    def draw_fills():
        p.setPen(Qt.PenStyle.NoPen)
        for path, _l, lit, ws, i in words:
            own_fill = "fill_color" in (word_styles.get(i) or {})
            p.setBrush(hl_brush if lit else QBrush(QColor(ws.fill_color)) if own_fill else base_brush)
            p.drawPath(path)

    if s.stroke_over_fill:
        draw_fills()
        draw_strokes()
    else:
        draw_strokes()
        draw_fills()

    # рамка вокруг того, что редактируется
    if mark_all or mark_words:
        p.setOpacity(1.0)
        pen = QPen(QColor(MARK_COLOR), 1.6, Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        if mark_all:
            p.drawRoundedRect(outer.boundingRect().adjusted(-6, -6, 6, 6), 5, 5)
        for path, _l, _lit, _ws, i in words:
            if mark_words and i in mark_words:
                p.drawRoundedRect(path.boundingRect().adjusted(-4, -4, 4, 4), 4, 4)
    p.restore()
    return outer.boundingRect()


def style_thumbnail(style: Style, width: int = 168, height: int = 96, text: str = "Пример текста") -> QImage:
    """Миниатюра стиля для галереи пресетов."""
    img = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    g = QLinearGradient(0, 0, width, height)
    g.setColorAt(0, QColor("#2B3A55"))
    g.setColorAt(0.55, QColor("#5A4E6E"))
    g.setColorAt(1, QColor("#7A5A4A"))
    clip = QPainterPath()
    clip.addRoundedRect(QRectF(0, 0, width, height), 8, 8)
    p.setClipPath(clip)
    p.fillRect(img.rect(), QBrush(g))
    st = style.copy()
    st.pos_x, st.pos_y, st.vertical_anchor, st.rotation, st.max_chars = 50, 50, "center", 0, 0
    # «виртуальная композиция» подбирается так, чтобы текст со всеми обводками занял ~85% ширины
    lay = build_layout(st, prepare_text(text, st), 1.0)
    natural = _union(w.path for w in lay.words).boundingRect().width()
    extra = (st.stroke_width if st.stroke_enabled else 0) + (2 * st.stroke2_width if st.stroke2_enabled else 0) \
        + (st.box_padding_x * 2 if st.box_enabled else 0) + (st.glow_radius if st.glow_enabled else 0)
    comp_w = max((natural + extra) / 0.85, st.font_size * 2)
    words = len(text.split())
    render_item(p, QRectF(0, 0, width, height), comp_w, st, text, 0 if words else None)
    p.end()
    return img
