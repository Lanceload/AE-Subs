"""Модель данных: субтитры и текстовые блоки, стиль, таймкоды, разбивка, форматы."""
from __future__ import annotations

import copy
import json
import re
from dataclasses import asdict, dataclass, field, fields


# ---------------------------------------------------------------- стиль

@dataclass
class Style:
    # Текст
    font_family: str = "Arial"
    font_style: str = "Bold"
    font_size: float = 72
    tracking: int = 0
    auto_leading: bool = True
    leading: float = 86
    justification: str = "center"  # left / center / right
    all_caps: bool = False
    faux_bold: bool = False
    faux_italic: bool = False
    max_chars: int = 0  # автоперенос, 0 = выкл
    h_scale: float = 100  # %
    v_scale: float = 100  # %
    text_opacity: float = 100  # %

    # Заливка
    fill_color: str = "#FFFFFF"
    fill_type: str = "solid"  # solid / gradient
    fill_color2: str = "#FFD84D"
    gradient_angle: float = 90  # 0 — слева направо, 90 — сверху вниз

    # Обводки
    stroke_enabled: bool = True
    stroke_color: str = "#000000"
    stroke_width: float = 8
    stroke_over_fill: bool = False
    stroke2_enabled: bool = False
    stroke2_color: str = "#7C5CFF"
    stroke2_width: float = 8  # толщина второй обводки снаружи первой

    # Свечение
    glow_enabled: bool = False
    glow_color: str = "#7C5CFF"
    glow_radius: float = 30
    glow_opacity: float = 80

    # Тень
    shadow_enabled: bool = True
    shadow_color: str = "#000000"
    shadow_opacity: float = 70  # %
    shadow_direction: float = 135  # градусы
    shadow_distance: float = 6
    shadow_softness: float = 12

    # Плашка
    box_enabled: bool = False
    box_mode: str = "block"  # block — одна на весь текст, lines — своя на каждую строку
    box_color: str = "#000000"
    box_opacity: float = 60
    box_padding_x: float = 24
    box_padding_y: float = 12
    box_roundness: float = 12

    # Положение (в процентах от размера композиции)
    pos_x: float = 50
    pos_y: float = 85
    vertical_anchor: str = "bottom"  # top / center / bottom — какой край текста стоит в точке
    rotation: float = 0

    # Анимация
    anim_in: str = "fade"  # none / fade / pop / scale / slide / typewriter / words / blur
    anim_out: str = "fade"  # none / fade / pop / scale / slide / blur
    anim_in_dur: float = 0.2
    anim_out_dur: float = 0.2
    slide_distance: float = 40

    # Подсветка слов
    hl_enabled: bool = False
    hl_mode: str = "word"  # word — только текущее слово, karaoke — текущее и сказанные
    hl_color: str = "#FFD84D"
    hl_scale: float = 108  # % для текущего слова
    hl_reveal: bool = False  # слова появляются по мере речи

    def to_dict(self) -> dict:
        return asdict(self)

    def copy(self) -> "Style":
        return copy.deepcopy(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Style":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in (data or {}).items() if k in known})


# Свойства, которые можно задать отдельному слову (в AE — форматирование части текста)
CHAR_KEYS = ["font_family", "font_style", "font_size", "fill_color", "stroke_enabled", "stroke_color",
             "stroke_width", "faux_bold", "faux_italic", "all_caps", "tracking", "h_scale", "v_scale"]


def word_style(base: Style, overrides: dict | None) -> Style:
    """Стиль слова = стиль субтитра + его собственные отличия."""
    if not overrides:
        return base
    st = base.copy()
    for k, v in overrides.items():
        if k in CHAR_KEYS:
            setattr(st, k, v)
    return st


def style_diff(base: Style, edited: Style) -> dict:
    """Чем стиль слова отличается от стиля субтитра (только то, что можно задать слову)."""
    return {k: getattr(edited, k) for k in CHAR_KEYS if getattr(edited, k) != getattr(base, k)}


# ---------------------------------------------------------------- субтитры

@dataclass
class Subtitle:
    start: float  # секунды
    end: float
    text: str
    words: list = field(default_factory=list)  # [[начало, конец], ...] для каждого слова text.split()
    style: Style | None = None  # свой стиль субтитра (None — общий стиль)
    word_styles: dict = field(default_factory=dict)  # номер слова → отличия его стиля (см. CHAR_KEYS)

    def to_dict(self) -> dict:
        d = {"start": self.start, "end": self.end, "text": self.text}
        if self.words:
            d["words"] = self.words
        if self.style is not None:
            d["style"] = self.style.to_dict()
        if self.word_styles:
            d["word_styles"] = {str(k): v for k, v in self.word_styles.items()}
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Subtitle":
        ws = {int(k): v for k, v in (d.get("word_styles") or {}).items()}
        return cls(d["start"], d["end"], d.get("text", ""), d.get("words") or [],
                   Style.from_dict(d["style"]) if d.get("style") else None, ws)

    def has_own_style(self) -> bool:
        return self.style is not None or bool(self.word_styles)

    def clean_word_styles(self):
        """После правки текста убирает стили слов, которых больше нет."""
        n = len(self.text.split())
        self.word_styles = {k: v for k, v in self.word_styles.items() if k < n and v}

    def clone(self) -> "Subtitle":
        return copy.deepcopy(self)

    def word_times(self) -> list[list[float]]:
        """Тайминги слов, всегда в согласии с текущим текстом (при правке текста — пересчёт)."""
        n = len(self.text.split())
        if n == 0:
            return []
        if len(self.words) == n:
            return self.words
        return distribute_words(self.text, self.start, self.end)

    def retime(self, start: float, end: float):
        """Новые границы; тайминги слов растягиваются пропорционально."""
        old_s, old_e = self.start, self.end
        span = max(old_e - old_s, 1e-6)
        k = (end - start) / span
        if self.words:
            self.words = [[round(start + (a - old_s) * k, 3), round(start + (b - old_s) * k, 3)]
                          for a, b in self.words]
        self.start, self.end = round(start, 3), round(end, 3)


def distribute_words(text: str, start: float, end: float) -> list[list[float]]:
    """Равномерно раскладывает слова по времени пропорционально их длине."""
    words = text.split()
    if not words:
        return []
    total = sum(len(w) + 1 for w in words)
    t, out = start, []
    for w in words:
        d = (end - start) * (len(w) + 1) / total
        out.append([round(t, 3), round(t + d, 3)])
        t += d
    return out


def split_subtitle(sub: Subtitle, t: float) -> tuple[Subtitle, Subtitle] | None:
    """Разрезает субтитр в момент t по ближайшей границе слов."""
    if not (sub.start < t < sub.end):
        return None
    words = sub.text.split()
    times = sub.word_times()
    if len(words) < 2:
        return None
    cut = min(range(1, len(words)), key=lambda i: abs(times[i][0] - t))
    ws_a = {k: v for k, v in sub.word_styles.items() if k < cut}
    ws_b = {k - cut: v for k, v in sub.word_styles.items() if k >= cut}
    a = Subtitle(sub.start, round(times[cut][0], 3), " ".join(words[:cut]), times[:cut],
                 sub.style.copy() if sub.style else None, ws_a)
    b = Subtitle(round(times[cut][0], 3), sub.end, " ".join(words[cut:]), times[cut:],
                 sub.style.copy() if sub.style else None, ws_b)
    return a, b


def merge_subtitles(a: Subtitle, b: Subtitle) -> Subtitle:
    n = len(a.text.split())
    ws = dict(a.word_styles)
    ws.update({k + n: v for k, v in b.word_styles.items()})
    return Subtitle(a.start, b.end, f"{a.text} {b.text}".strip(), a.word_times() + b.word_times(),
                    a.style.copy() if a.style else None, ws)


# ---------------------------------------------------------------- таймкоды

def parse_time(value: str) -> float:
    """Принимает '83.5', '1:23.5', '00:01:23,500', '00:01:23.500'."""
    s = value.strip().replace(",", ".")
    if not s:
        raise ValueError("пустое время")
    parts = s.split(":")
    if len(parts) > 3:
        raise ValueError(f"неверное время: {value}")
    total = 0.0
    for p in parts:
        total = total * 60 + float(p)
    if total < 0:
        raise ValueError("время не может быть отрицательным")
    return total


def format_time(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


# ---------------------------------------------------------------- форматы

def parse_srt(content: str) -> list[Subtitle]:
    subs: list[Subtitle] = []
    blocks = re.split(r"\r?\n\s*\r?\n", content.strip().lstrip("﻿"))
    time_re = re.compile(r"([\d:.,]+)\s*-->\s*([\d:.,]+)")
    for block in blocks:
        lines = block.splitlines()
        for i, line in enumerate(lines):
            m = time_re.search(line)
            if m:
                text = "\n".join(lines[i + 1:]).strip()
                text = re.sub(r"</?[^>]+>", "", text)  # убрать <i>, <b>, <font>
                subs.append(Subtitle(parse_time(m.group(1)), parse_time(m.group(2)), text))
                break
    return subs


def to_srt(subs: list[Subtitle]) -> str:
    out = []
    for i, s in enumerate(subs, 1):
        a, b = format_time(s.start).replace(".", ","), format_time(s.end).replace(".", ",")
        out.append(f"{i}\n{a} --> {b}\n{s.text}\n")
    return "\n".join(out)


def to_vtt(subs: list[Subtitle]) -> str:
    out = ["WEBVTT", ""]
    for s in subs:
        out.append(f"{format_time(s.start)} --> {format_time(s.end)}\n{s.text}\n")
    return "\n".join(out)


def to_txt(subs: list[Subtitle], with_times: bool = False) -> str:
    if with_times:
        return "\n".join(f"[{format_time(s.start)}] {s.text.replace(chr(10), ' ')}" for s in subs)
    return "\n".join(s.text.replace("\n", " ") for s in subs)


def _ass_color(hex_color: str, opacity: float = 100) -> str:
    c = hex_color.lstrip("#")
    alpha = int(round((100 - opacity) * 2.55))
    return f"&H{alpha:02X}{c[4:6]}{c[2:4]}{c[0:2]}".upper()


def _ass_time(t: float) -> str:
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def to_ass(subs: list[Subtitle], style: Style, width: int, height: int) -> str:
    """ASS со стилем (шрифт, цвета, обводка, тень, положение) — открывается в плеерах и Premiere/Resolve."""
    align = {"left": 1, "center": 2, "right": 3}[style.justification]
    if style.vertical_anchor == "top":
        align += 6
    elif style.vertical_anchor == "center":
        align += 3
    margin_v = int(height * (1 - style.pos_y / 100)) if style.vertical_anchor == "bottom" else int(height * style.pos_y / 100)
    bold = -1 if "bold" in style.font_style.lower() or style.faux_bold else 0
    italic = -1 if "italic" in style.font_style.lower() or style.faux_italic else 0
    border = 3 if style.box_enabled else 1
    outline = style.stroke_width / 2 if style.stroke_enabled else 0
    back = _ass_color(style.box_color, style.box_opacity) if style.box_enabled else _ass_color(style.shadow_color, style.shadow_opacity)
    head = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {width}", f"PlayResY: {height}", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding",
        f"Style: Default,{style.font_family},{style.font_size:g},{_ass_color(style.fill_color)},"
        f"{_ass_color(style.hl_color)},{_ass_color(style.stroke_color)},{back},{bold},{italic},0,0,"
        f"{style.h_scale:g},{style.v_scale:g},{style.tracking / 1000 * style.font_size:.1f},{style.rotation:g},"
        f"{border},{outline:g},{style.shadow_distance if style.shadow_enabled else 0:g},{align},40,40,{margin_v},1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for s in subs:
        text = wrap_text(s.text, style.max_chars)
        if style.all_caps:
            text = text.upper()
        head.append(f"Dialogue: 0,{_ass_time(s.start)},{_ass_time(s.end)},Default,,0,0,0,,{text.replace(chr(10), r'\N')}")
    return "\n".join(head) + "\n"


def to_json(subs: list[Subtitle]) -> str:
    return json.dumps([s.to_dict() for s in subs], ensure_ascii=False, indent=2)


def split_plain_text(content: str, by_blank_lines: bool) -> list[str]:
    """Разбивает вставленный текст на фразы."""
    chunks = re.split(r"\r?\n\s*\r?\n", content) if by_blank_lines else content.splitlines()
    return [c.strip() for c in chunks if c.strip()]


def _greedy_wrap(text: str, max_chars: int) -> str:
    result, line = [], ""
    for word in text.split():
        if line and len(line) + 1 + len(word) > max_chars:
            result.append(line)
            line = word
        else:
            line = f"{line} {word}" if line else word
    result.append(line)
    return "\n".join(result)


def wrap_text(text: str, max_chars: int) -> str:
    """Раскладывает текст по строкам не длиннее max_chars. 0 — оставить переносы как есть.

    Существующие переносы при этом не учитываются: текст раскладывается заново, ровно
    (иначе смена «символов в строке» не действовала бы на уже разбитые субтитры).
    """
    if max_chars <= 0:
        return text
    plain = " ".join(text.split())
    if len(plain) <= max_chars:
        return plain
    lines_needed = -(-len(plain) // max_chars)
    if lines_needed == 2:
        balanced = balance_lines(plain, max_chars, 2)
        if all(len(line) <= max_chars for line in balanced.split("\n")):
            return balanced
    return _greedy_wrap(plain, max_chars)


def close_gaps(subs: list[Subtitle], max_gap: float = 0.0) -> int:
    """Смыкает субтитры: каждый держится до начала следующего (паузы длиннее max_gap не трогаем,
    0 — смыкать всегда). Возвращает число изменённых."""
    changed = 0
    order = sorted(subs, key=lambda s: s.start)
    for a, b in zip(order, order[1:]):
        gap = b.start - a.end
        if gap > 0.001 and (max_gap <= 0 or gap <= max_gap):
            a.retime(a.start, b.start)
            changed += 1
    return changed


def auto_timing(texts: list[str], start: float, sec_per_char: float,
                min_dur: float, max_dur: float, gap: float) -> list[Subtitle]:
    """Расставляет время по длине текста: длиннее фраза — дольше на экране."""
    subs = []
    t = start
    for text in texts:
        chars = len(text.replace("\n", ""))
        dur = min(max(chars * sec_per_char, min_dur), max_dur)
        subs.append(Subtitle(round(t, 3), round(t + dur, 3), text))
        t += dur + gap
    return subs


# ---------------------------------------------------------------- распознанные слова → субтитры

@dataclass
class Word:
    start: float
    end: float
    text: str


@dataclass
class SplitSettings:
    max_chars_line: int = 42
    max_lines: int = 2
    max_duration: float = 6.0
    max_words: int = 0  # 0 = без ограничения; 1–3 — режим «шортс»
    pause_split: float = 0.6  # пауза между словами, после которой начинается новый субтитр
    min_duration: float = 0.8
    no_gaps: bool = True  # субтитры вплотную: каждый держится до начала следующего

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "SplitSettings":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in (data or {}).items() if k in known})


_SENTENCE_END = (".", "!", "?", "…")
_SOFT_BREAK = (",", ";", ":", "—", "–")
# короткие служебные слова, которыми не должна заканчиваться строка или субтитр
_WEAK_WORDS = {
    "в", "во", "на", "с", "со", "к", "ко", "о", "об", "от", "до", "из", "за", "по", "у", "и", "а",
    "но", "не", "ни", "что", "как", "для", "при", "без", "через", "или", "же", "бы", "то",
    "the", "a", "an", "of", "to", "in", "on", "at", "and", "or", "for", "with", "by",
}


def _clean(word: str) -> str:
    return word.rstrip("»\"')")


def _is_weak(word: str) -> bool:
    return word.lower() in _WEAK_WORDS


def balance_lines(text: str, max_chars: int, max_lines: int) -> str:
    """Делит фразу на строки примерно одинаковой длины, предпочитая разрыв после знаков препинания."""
    words = text.split()
    if len(text) <= max_chars or max_lines <= 1 or len(words) < 2:
        return text
    lines_needed = min(max_lines, -(-len(text) // max_chars))
    if lines_needed <= 1:
        return text
    if lines_needed == 2:
        best, best_score = text, None
        for i in range(1, len(words)):
            a, b = " ".join(words[:i]), " ".join(words[i:])
            score = max(len(a), len(b))
            if _clean(words[i - 1]).endswith(_SENTENCE_END + _SOFT_BREAK):
                score -= 6
            if _is_weak(words[i - 1]):
                score += 8
            if best_score is None or score < best_score:
                best, best_score = f"{a}\n{b}", score
        return best
    return _greedy_wrap(text, -(-len(text) // lines_needed) + 2)


def words_to_subtitles(words: list[Word], cfg: SplitSettings) -> list[Subtitle]:
    max_total = cfg.max_chars_line * max(cfg.max_lines, 1)
    chunks: list[list[Word]] = []
    cur: list[Word] = []

    def text_len(items: list[Word]) -> int:
        return len(" ".join(w.text for w in items))

    for w in words:
        if not w.text:
            continue
        if cur:
            prev_text = _clean(cur[-1].text)
            # «естественный» разрыв: пауза или конец предложения
            natural = (w.start - cur[-1].end > cfg.pause_split
                       or prev_text.endswith(_SENTENCE_END)
                       or (prev_text.endswith(_SOFT_BREAK) and text_len(cur) > max_total * 0.6))
            # «вынужденный» разрыв: не влезает по длине, времени или числу слов
            forced = (text_len(cur + [w]) > max_total
                      or w.end - cur[0].start > cfg.max_duration
                      or (cfg.max_words and len(cur) >= cfg.max_words))
            if natural:
                chunks.append(cur)
                cur = []
            elif forced:
                cut = len(cur)
                if not cfg.max_words:
                    # ищем запятую во второй половине — резать там приятнее для глаза
                    for i in range(len(cur) - 1, len(cur) // 2 - 1, -1):
                        if _clean(cur[i].text).endswith(_SOFT_BREAK):
                            cut = i + 1
                            break
                # не оставляем предлог или союз висеть в конце субтитра
                while cut > 1 and _is_weak(_clean(cur[cut - 1].text)):
                    cut -= 1
                chunks.append(cur[:cut])
                cur = cur[cut:]
        cur.append(w)
    if cur:
        chunks.append(cur)

    subs: list[Subtitle] = []
    for i, chunk in enumerate(chunks):
        text = " ".join(w.text for w in chunk)
        text = balance_lines(text, cfg.max_chars_line, cfg.max_lines)
        start, end = chunk[0].start, chunk[-1].end
        next_start = chunks[i + 1][0].start if i + 1 < len(chunks) else None
        # короткие субтитры растягиваем, но не наезжаем на следующий
        end = max(end, start + cfg.min_duration)
        if next_start is not None:
            end = next_start if cfg.no_gaps else min(end, max(next_start - 0.04, chunk[-1].end))
        word_times = [[round(w.start, 3), round(w.end, 3)] for w in chunk]
        subs.append(Subtitle(round(start, 3), round(end, 3), text, word_times))
    return subs



# ---------------------------------------------------------------- настройки AE и проект

@dataclass
class AESettings:
    afterfx_path: str = ""
    # precomp — композиция «Субтитры» поверх всех слоёв активной композиции; layers — слоями прямо в неё
    target: str = "precomp"
    comp_name: str = "Субтитры"
    comp_width: int = 1920
    comp_height: int = 1080
    comp_fps: float = 30
    time_offset: float = 0  # сдвиг всех субтитров, сек
    layer_prefix: str = "SUB"
    remove_old: bool = True  # удалять слои с тем же префиксом перед вставкой
    propagate_style: bool = False  # правка общего стиля меняет и субтитры со своим стилем

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "AESettings":
        known = {f.name for f in fields(cls)}
        obj = cls(**{k: v for k, v in (data or {}).items() if k in known})
        if obj.target not in ("precomp", "layers"):  # старые значения active/new
            obj.target = "precomp"
        return obj


@dataclass
class ProjectState:
    subtitles: list[Subtitle] = field(default_factory=list)
    style: Style = field(default_factory=Style)
    ae: AESettings = field(default_factory=AESettings)
    words: list[Word] = field(default_factory=list)  # последнее распознавание
    split: SplitSettings = field(default_factory=SplitSettings)
    recognize: dict = field(default_factory=dict)  # настройки диалога распознавания
    translation: dict = field(default_factory=dict)  # {"lang": "en", "pairs": {оригинал: перевод}}
    video: dict = field(default_factory=dict)  # {"path": ..., "offset": сек, "rate": 1.0}
    comp: dict = field(default_factory=dict)  # последняя прочитанная композиция AE

    def to_dict(self) -> dict:
        return {
            "subtitles": [s.to_dict() for s in self.subtitles],
            "style": self.style.to_dict(),
            "ae": self.ae.to_dict(),
            "words": [[w.start, w.end, w.text] for w in self.words],
            "split": self.split.to_dict(),
            "recognize": self.recognize,
            "translation": self.translation,
            "video": self.video,
            "comp": self.comp,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_dict(cls, data: dict) -> "ProjectState":
        return cls(
            subtitles=[Subtitle.from_dict(s) for s in data.get("subtitles", [])],
            style=Style.from_dict(data.get("style", {})),
            ae=AESettings.from_dict(data.get("ae", {})),
            words=[Word(*w) for w in data.get("words", [])],
            split=SplitSettings.from_dict(data.get("split", {})),
            recognize=data.get("recognize", {}),
            translation=data.get("translation", {}),
            video=data.get("video", {}),
            comp=data.get("comp", {}),
        )

    @classmethod
    def from_json(cls, content: str) -> "ProjectState":
        return cls.from_dict(json.loads(content))
