"""Эффекты появления и исчезновения субтитров.

Здесь — список эффектов и расчёт состояния текста в момент t для превью.
Те же эффекты строит скрипт After Effects (jsx_builder.animate / textAnimators).
"""
from __future__ import annotations

from dataclasses import dataclass

from .models import Style

ANIM_IN = [
    ("none", "Нет"), ("fade", "Растворение"), ("pop", "Выскакивание"), ("bounce", "Отскок"),
    ("scale", "Рост из точки"), ("stretch", "Растяжение по ширине"), ("slide", "Выезд снизу"),
    ("drop", "Падение сверху"), ("slide_left", "Выезд слева"), ("slide_right", "Выезд справа"),
    ("rotate", "Поворот"), ("blur", "Из размытия"), ("typewriter", "Печатная машинка"),
    ("letters_pop", "Буквы выскакивают"), ("words", "Слова проявляются"), ("words_pop", "Слова выскакивают"),
]
ANIM_OUT = [
    ("none", "Нет"), ("fade", "Растворение"), ("pop", "Сжатие"), ("scale", "Уменьшение в точку"),
    ("stretch", "Сжатие по ширине"), ("slide", "Уезд вниз"), ("rise", "Уход вверх"),
    ("slide_left", "Уезд влево"), ("slide_right", "Уезд вправо"), ("rotate", "Поворот"), ("blur", "В размытие"),
]
# эффекты, которые проявляют текст постепенно (по буквам или словам)
REVEAL = {"typewriter": "chars", "letters_pop": "chars", "words": "words", "words_pop": "words"}


@dataclass
class AnimState:
    opacity: float = 1.0
    sx: float = 1.0  # масштаб по ширине
    sy: float = 1.0
    dx: float = 0.0  # сдвиг в пикселях композиции
    dy: float = 0.0
    rot: float = 0.0
    blur: float = 0.0  # px
    reveal: tuple | None = None  # ("chars"/"words", доля 0..1, «выскакивание»)

    def is_identity(self) -> bool:
        return (self.opacity >= 0.999 and self.sx == self.sy == 1.0 and not (self.dx or self.dy or self.rot)
                and self.blur < 0.5 and self.reveal is None)


def _ease(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


def durations(style: Style, start: float, end: float) -> tuple[float, float]:
    d_in = 0.0 if style.anim_in == "none" else style.anim_in_dur
    d_out = 0.0 if style.anim_out == "none" else style.anim_out_dur
    total = end - start
    if d_in + d_out > total > 0:
        k = total / (d_in + d_out)
        d_in, d_out = d_in * k, d_out * k
    return d_in, d_out


def _pop(p: float) -> float:
    """70% → 108% → 100% (как ключи в AE)."""
    if p < 0.6:
        return 0.7 + (1.08 - 0.7) * _ease(p / 0.6)
    return 1.08 - 0.08 * _ease((p - 0.6) / 0.4)


def _bounce(p: float) -> float:
    """0 → 115% → 95% → 100%."""
    if p < 0.45:
        return 1.15 * _ease(p / 0.45)
    if p < 0.7:
        return 1.15 - 0.2 * _ease((p - 0.45) / 0.25)
    return 0.95 + 0.05 * _ease((p - 0.7) / 0.3)


def anim_state(style: Style, start: float, end: float, t: float) -> AnimState:
    s = AnimState()
    d_in, d_out = durations(style, start, end)
    dist = style.slide_distance
    if d_in > 0 and t < start + d_in:
        p = max(t - start, 0.0) / d_in
        e = _ease(p)
        kind = style.anim_in
        if kind in ("fade", "slide", "drop", "slide_left", "slide_right", "rotate", "scale", "stretch", "blur"):
            s.opacity = e
        if kind == "pop":
            s.opacity = _ease(p / 0.5)
            s.sx = s.sy = _pop(p)
        elif kind == "bounce":
            s.opacity = _ease(p / 0.3)
            s.sx = s.sy = _bounce(p)
        elif kind == "scale":
            s.sx = s.sy = e
        elif kind == "stretch":
            s.sx = e
        elif kind == "slide":
            s.dy = dist * (1 - e)
        elif kind == "drop":
            s.dy = -dist * (1 - e)
        elif kind == "slide_left":
            s.dx = -dist * (1 - e)
        elif kind == "slide_right":
            s.dx = dist * (1 - e)
        elif kind == "rotate":
            s.rot = -12 * (1 - e)
            s.sx = s.sy = 0.8 + 0.2 * e
        elif kind == "blur":
            s.blur = 40 * (1 - e)
        elif kind in REVEAL:
            s.reveal = (REVEAL[kind], p, kind.endswith("_pop"))
    if d_out > 0 and t > end - d_out:
        p = min(max(end - t, 0.0) / d_out, 1.0)  # 1 → 0 к концу
        e = _ease(p)
        kind = style.anim_out
        if kind != "none":
            s.opacity = min(s.opacity, e)
        if kind == "pop":
            s.sx = s.sy = 0.7 + 0.3 * e
        elif kind == "scale":
            s.sx = s.sy = e
        elif kind == "stretch":
            s.sx = e
        elif kind == "slide":
            s.dy = dist * (1 - e)
        elif kind == "rise":
            s.dy = -dist * (1 - e)
        elif kind == "slide_left":
            s.dx = -dist * (1 - e)
        elif kind == "slide_right":
            s.dx = dist * (1 - e)
        elif kind == "rotate":
            s.rot = 12 * (1 - e)
            s.sx = s.sy = 0.8 + 0.2 * e
        elif kind == "blur":
            s.blur = 40 * (1 - e)
    return s
