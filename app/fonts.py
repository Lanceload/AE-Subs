"""Определение PostScript-имени шрифта по семейству и начертанию.

After Effects принимает шрифт только по PostScript-имени (например «ArialRoundedMTBold»),
а Qt/Windows показывают семейство и начертание («Arial Rounded MT» + «Bold»).
Если передать в AE неверное имя, он молча отклоняет весь стиль текста —
поэтому имя читаем прямо из файла шрифта.
"""
from __future__ import annotations

import os
import threading
import winreg
from pathlib import Path

_FONT_KEY = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"
_index: list[dict] | None = None
_lock = threading.Lock()


def _font_files() -> list[Path]:
    files: list[Path] = []
    win_fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            key = winreg.OpenKey(hive, _FONT_KEY)
        except OSError:
            continue
        with key:
            i = 0
            while True:
                try:
                    _, value, _ = winreg.EnumValue(key, i)
                except OSError:
                    break
                i += 1
                p = Path(value)
                if not p.is_absolute():
                    p = win_fonts / p
                if p.suffix.lower() in (".ttf", ".otf", ".ttc", ".otc") and p.is_file():
                    files.append(p)
    return list(dict.fromkeys(files))


_REGULAR = {"regular", "normal", "roman", "book", ""}


def _norm_style(s: str) -> str:
    s = s.strip().lower()
    return "regular" if s in _REGULAR else s


def _names(font) -> dict:
    # названия бывают на разных языках («Bold», «Negreta», «Полужирный») — собираем все
    by_id: dict[int, set[str]] = {}
    for rec in font["name"].names:
        if rec.nameID in (1, 2, 4, 6, 16, 17, 21, 22):
            try:
                text = rec.toUnicode().strip()
            except Exception:  # noqa: BLE001
                continue
            if text:
                by_id.setdefault(rec.nameID, set()).add(text)
    ps = sorted(by_id.get(6, {""}))[0]
    pairs = []
    for fam_id, sty_id in ((16, 17), (21, 22), (1, 2)):
        fams = {f.lower() for f in by_id.get(fam_id, ())}
        stys = {_norm_style(s) for s in by_id.get(sty_id, ())} or ({"regular"} if fams else set())
        if fams:
            pairs.append((fams, stys))
    return {"ps": ps, "full": {f.lower() for f in by_id.get(4, ())}, "pairs": pairs}


def build_index() -> list[dict]:
    from fontTools.ttLib import TTCollection, TTFont

    index = []
    for path in _font_files():
        try:
            if path.suffix.lower() in (".ttc", ".otc"):
                fonts = TTCollection(str(path), lazy=True).fonts
            else:
                fonts = [TTFont(str(path), lazy=True, fontNumber=0)]
            for f in fonts:
                n = _names(f)
                if n["ps"]:
                    index.append(n)
        except Exception:  # noqa: BLE001 — битые файлы шрифтов пропускаем
            continue
    return index


def _get_index() -> list[dict]:
    global _index
    with _lock:
        if _index is None:
            _index = build_index()
        return _index


def warm_up():
    """Строит индекс в фоне, чтобы первая отправка в AE не ждала."""
    threading.Thread(target=_get_index, daemon=True).start()


def postscript_name(family: str, style: str) -> str:
    """PostScript-имя для семейства/начертания, или '' если не нашли."""
    fam, sty = family.strip().lower(), _norm_style(style or "Regular")
    full = f"{fam} {sty}" if sty != "regular" else fam
    best = ""
    for n in _get_index():
        for fams, stys in n["pairs"]:
            if fam in fams and sty in stys:
                return n["ps"]
        if full in n["full"] and not best:
            best = n["ps"]
    return best
