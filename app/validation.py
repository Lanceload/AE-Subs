"""Проверка субтитров перед отправкой и автоисправление."""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QFontMetricsF

from .models import Style, Subtitle, balance_lines, wrap_text
from .render import _make_font

MIN_DUR = 0.5
MAX_CPS = 22  # символов в секунду — быстрее большинство не успевает прочитать
SAFE_WIDTH = 0.92


@dataclass
class Issue:
    index: int
    level: str  # error / warn
    text: str
    fix: str | None = None  # delete / extend / trim / wrap


def line_widths(sub: Subtitle, style: Style) -> list[float]:
    """Ширина каждой строки в пикселях композиции (с учётом обводок)."""
    font = _make_font(style, 1.0)
    fm = QFontMetricsF(font)
    text = wrap_text(sub.text, style.max_chars)
    if style.all_caps:
        text = text.upper()
    extra = (style.stroke_width if style.stroke_enabled else 0) + (2 * style.stroke2_width if style.stroke2_enabled else 0)
    return [fm.horizontalAdvance(line) * style.h_scale / 100 + extra for line in text.split("\n")]


def validate(items: list[Subtitle], default_style: Style, comp_w: int, comp_dur: float | None = None) -> list[Issue]:
    issues: list[Issue] = []
    order = sorted(range(len(items)), key=lambda i: items[i].start)
    for pos, i in enumerate(order):
        s = items[i]
        st = s.style or default_style
        if not s.text.strip():
            issues.append(Issue(i, "warn", "Пустой текст", "delete"))
            continue
        if s.end <= s.start:
            issues.append(Issue(i, "error", "Конец раньше начала", "extend"))
            continue
        if pos + 1 < len(order):
            nxt = items[order[pos + 1]]
            if nxt.start < s.end - 0.01:
                issues.append(Issue(i, "warn", f"Наезжает на следующий на {s.end - nxt.start:.2f} с", "trim"))
        dur = s.end - s.start
        if dur < MIN_DUR:
            issues.append(Issue(i, "warn", f"Слишком коротко на экране ({dur:.2f} с)", "extend"))
        chars = len(s.text.replace("\n", ""))
        if dur > 0 and chars / dur > MAX_CPS and not st.hl_enabled:
            issues.append(Issue(i, "warn", f"Быстро для чтения: {chars / dur:.0f} симв/с"))
        widest = max(line_widths(s, st), default=0)
        if widest > comp_w * SAFE_WIDTH:
            issues.append(Issue(i, "warn", f"Строка шире кадра ({widest:.0f} из {comp_w} px)", "wrap"))
        if comp_dur and s.start >= comp_dur:
            issues.append(Issue(i, "error", "Начинается после конца композиции"))
    return issues


def autofix(items: list[Subtitle], issues: list[Issue], default_style: Style, comp_w: int) -> int:
    """Исправляет то, что можно исправить автоматически. Возвращает число исправлений."""
    fixed = 0
    starts = sorted(s.start for s in items)
    to_delete = set()
    for iss in issues:
        s = items[iss.index]
        st = s.style or default_style
        if iss.fix == "delete":
            to_delete.add(iss.index)
        elif iss.fix == "trim":
            nxt = min((t for t in starts if t > s.start + 1e-6), default=None)
            if nxt is not None and nxt - 0.02 > s.start + 0.1:
                s.retime(s.start, nxt - 0.02)
                fixed += 1
        elif iss.fix == "extend":
            nxt = min((t for t in starts if t > s.start + 1e-6), default=s.start + 10)
            s.retime(s.start, max(min(s.start + 0.8, nxt - 0.02), s.start + 0.3))
            fixed += 1
        elif iss.fix == "wrap":
            # переносим по словам, пока самая широкая строка не влезет
            plain = s.text.replace("\n", " ")
            for parts in (2, 3, 4):
                candidate = balance_lines(plain, max(len(plain) // parts + 2, 4), parts)
                s.text = candidate
                if max(line_widths(s, st), default=0) <= comp_w * SAFE_WIDTH:
                    break
            fixed += 1
    for i in sorted(to_delete, reverse=True):
        del items[i]
        fixed += 1
    return fixed
