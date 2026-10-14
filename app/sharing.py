"""Обмен стилями: код для мессенджера, файлы-наборы и синхронизация через облачную папку.

Облако — это любая папка, которую синхронизирует клиент Google Диска, Яндекс Диска, Dropbox
или OneDrive. Программа сама держит в ней копию пресетов и сверяет её с локальной.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import zlib
from pathlib import Path

from .models import Style
from .paths import PRESETS_DIR

CODE_PREFIX = "AESUBS-STYLE:"
PACK_EXT = ".aesubs-styles"
CLOUD_SUBDIR = "AE Subs — стили"
TOMBSTONES = "_удалённые.json"


def safe_name(name: str) -> str:
    return "".join(c for c in name if c not in '<>:"/\\|?*').strip() or "Стиль"


# ---------------------------------------------------------------- коды

def encode(items: list[tuple[str, Style]]) -> str:
    """Стили → одна строка, которую можно переслать в мессенджере."""
    data = [{"name": n, "style": s.to_dict()} for n, s in items]
    raw = zlib.compress(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), 9)
    return CODE_PREFIX + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode(text: str) -> list[tuple[str, Style]]:
    """Ищет в тексте все коды стилей (можно вставить сообщение целиком)."""
    out = []
    for part in text.split(CODE_PREFIX)[1:]:
        token = "".join(ch for ch in part.split()[0] if ch.isalnum() or ch in "-_") if part.split() else ""
        if not token:
            continue
        try:
            raw = zlib.decompress(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
            for item in json.loads(raw.decode("utf-8")):
                out.append((safe_name(item.get("name", "Стиль")), Style.from_dict(item.get("style", {}))))
        except (ValueError, zlib.error, json.JSONDecodeError):
            continue
    return out


# ---------------------------------------------------------------- наборы

def local_presets() -> dict[str, Path]:
    return {p.stem: p for p in sorted(PRESETS_DIR.glob("*.json"))} if PRESETS_DIR.is_dir() else {}


def load_style(path: Path) -> Style:
    return Style.from_dict(json.loads(path.read_text(encoding="utf-8")))


def save_preset(name: str, style: Style, overwrite: bool = False) -> str:
    """Сохраняет пресет; при совпадении имени без overwrite добавляет « (2)». Возвращает итоговое имя."""
    PRESETS_DIR.mkdir(parents=True, exist_ok=True)
    name = safe_name(name)
    final, n = name, 2
    while (PRESETS_DIR / f"{final}.json").exists() and not overwrite:
        if load_style(PRESETS_DIR / f"{final}.json") == style:
            return final  # такой же уже есть
        final, n = f"{name} ({n})", n + 1
    (PRESETS_DIR / f"{final}.json").write_text(json.dumps(style.to_dict(), ensure_ascii=False, indent=2),
                                               encoding="utf-8")
    return final


def export_pack(path: Path, names: list[str] | None = None) -> int:
    presets = local_presets()
    items = [{"name": n, "style": load_style(p).to_dict()} for n, p in presets.items() if names is None or n in names]
    path.write_text(json.dumps({"format": "aesubs-styles", "version": 1, "styles": items}, ensure_ascii=False,
                               indent=2), encoding="utf-8")
    return len(items)


def import_pack(path: Path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    items = data.get("styles", []) if isinstance(data, dict) else []
    if isinstance(data, dict) and not items and "font_family" in data:  # одиночный пресет .json
        items = [{"name": path.stem, "style": data}]
    return [save_preset(it.get("name", "Стиль"), Style.from_dict(it.get("style", {}))) for it in items]


# ---------------------------------------------------------------- облачная папка

def cloud_dir(root: str) -> Path:
    return Path(root) / CLOUD_SUBDIR


def _tombstones(folder: Path) -> set[str]:
    try:
        return set(json.loads((folder / TOMBSTONES).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def _save_tombstones(folder: Path, names: set[str]):
    (folder / TOMBSTONES).write_text(json.dumps(sorted(names), ensure_ascii=False), encoding="utf-8")


def sync(root: str) -> tuple[int, int]:
    """Двусторонняя синхронизация: новее — побеждает. Возвращает (получено, отправлено)."""
    if not root or not Path(root).is_dir():
        raise FileNotFoundError("Облачная папка не найдена")
    folder = cloud_dir(root)
    folder.mkdir(exist_ok=True)
    PRESETS_DIR.mkdir(parents=True, exist_ok=True)
    dead = _tombstones(folder)
    got = sent = 0
    for name in dead:
        (PRESETS_DIR / f"{name}.json").unlink(missing_ok=True)
    remote = {p.stem: p for p in folder.glob("*.json") if p.name != TOMBSTONES and p.stem not in dead}
    local = {n: p for n, p in local_presets().items() if n not in dead}
    for name in set(remote) | set(local):
        r, l = remote.get(name), local.get(name)
        if r and (not l or r.stat().st_mtime > l.stat().st_mtime + 1):
            shutil.copy2(r, PRESETS_DIR / r.name)
            got += 1
        elif l and (not r or l.stat().st_mtime > r.stat().st_mtime + 1):
            shutil.copy2(l, folder / l.name)
            sent += 1
    return got, sent


def cloud_delete(root: str, name: str):
    """Удаление синхронизируется: имя попадает в список удалённых, чтобы не вернулось с другого компьютера."""
    if not root or not Path(root).is_dir():
        return
    folder = cloud_dir(root)
    folder.mkdir(exist_ok=True)
    (folder / f"{name}.json").unlink(missing_ok=True)
    dead = _tombstones(folder)
    dead.add(name)
    _save_tombstones(folder, dead)


def cloud_revive(root: str, name: str):
    """Пресет с этим именем снова сохранён — убираем его из списка удалённых."""
    if not root or not Path(root).is_dir():
        return
    folder = cloud_dir(root)
    dead = _tombstones(folder)
    if name in dead:
        dead.discard(name)
        _save_tombstones(folder, dead)


def guess_cloud_folders() -> list[str]:
    """Типичные папки облачных дисков на этом компьютере."""
    home = Path(os.environ.get("USERPROFILE", Path.home()))
    names = ["Google Drive", "Мой диск", "My Drive", "YandexDisk", "Яндекс.Диск", "Dropbox", "OneDrive"]
    found = []
    for base in [home] + [Path(f"{d}:\\") for d in "DEFGH" if Path(f"{d}:\\").exists()]:
        for n in names:
            p = base / n
            if p.is_dir():
                found.append(str(p))
    return found
