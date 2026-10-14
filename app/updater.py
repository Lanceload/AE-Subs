"""Обновление программы из релизов GitHub.

Каждый релиз репозитория — тег vX.Y.Z и архив AE_Subs_X.Y.Z.zip с содержимым папки программы.
Тот же архив лежит на Hugging Face: у части провайдеров файлы GitHub качаются очень медленно.
Модели, CUDA, пресеты и проекты при обновлении не трогаются.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

from .paths import APP_DIR
from .version import VERSION

REPO = "Lanceload/AE-Subs"
RELEASES_URL = f"https://github.com/{REPO}/releases"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
HF_FILES = "https://huggingface.co/Yaqen/AE-Subs/resolve/main"  # копии архивов релизов
SLOW_SPEED = 150 * 1024  # байт/с: медленнее — пробуем следующий источник
KEEP_DIRS = ["models", "cuda", "presets", "projects"]
_HEADERS = {"User-Agent": "AE-Subs-updater", "Accept": "application/vnd.github+json"}


def parse_version(v: str) -> tuple:
    out = []
    for part in str(v).strip().lstrip("vV").split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out + [0] * (3 - len(out)))


def latest() -> dict:
    """Последний релиз: {"version", "notes", "url" (архив), "size", "page"}."""
    req = urllib.request.Request(API_LATEST, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.loads(r.read().decode("utf-8"))
    assets = [a for a in data.get("assets", []) if a.get("name", "").lower().endswith(".zip")]
    return {"version": data.get("tag_name", "0").lstrip("vV"),
            "notes": data.get("body") or "",
            "url": assets[0]["browser_download_url"] if assets else "",
            "size": assets[0].get("size", 0) if assets else 0,
            "page": data.get("html_url") or RELEASES_URL}


def check() -> dict | None:
    """Информация о новой версии или None, если установлена последняя."""
    info = latest()
    if info["url"] and parse_version(info["version"]) > parse_version(VERSION):
        return info
    return None


class Cancelled(Exception):
    pass


def mirrors(info: dict) -> list[tuple[str, str]]:
    """Откуда качать: Hugging Face (быстро и там, где GitHub замедляют), затем сам GitHub."""
    name = f"AE_Subs_{info['version']}.zip"
    out = [("Hugging Face", f"{HF_FILES}/{name}")]
    if info.get("url"):
        out.append(("GitHub", info["url"]))
    return out


def _fetch(url: str, archive: Path, info: dict, progress, cancel, allow_slow: bool, source: str):
    """Качает файл. Если скорость слишком низкая и есть запасной источник — бросает TimeoutError."""
    req = urllib.request.Request(url, headers={"User-Agent": "AE-Subs-updater"})
    with urllib.request.urlopen(req, timeout=30) as r, open(archive, "wb") as f:
        total = int(r.headers.get("Content-Length", 0)) or info.get("size", 0)
        done, t0 = 0, time.monotonic()
        while chunk := r.read(1 << 18):
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            f.write(chunk)
            done += len(chunk)
            elapsed = max(time.monotonic() - t0, 1e-3)
            speed = done / elapsed
            if progress:
                try:
                    progress(done, total, speed, source)
                except Exception:  # noqa: BLE001 — ошибка показа прогресса не должна срывать загрузку
                    progress = None
            if not allow_slow and elapsed > 8 and speed < SLOW_SPEED:
                raise TimeoutError(f"{source}: слишком медленно")
    if total and archive.stat().st_size < total:
        raise OSError("Загрузка прервалась.")


def download(info: dict, progress=None, cancel=None) -> Path:
    """Скачивает архив и распаковывает во временную папку. Возвращает папку с программой.

    progress(скачано_байт, всего_байт, скорость_байт_в_сек, источник); cancel — threading.Event.
    """
    root = Path(tempfile.gettempdir()) / "ae_subs_update"
    if root.is_dir():  # остатки прошлых попыток; занятые файлы просто пропускаем
        for old in root.iterdir():
            if old.is_dir():
                shutil.rmtree(old, ignore_errors=True)
                continue
            try:
                old.unlink(missing_ok=True)
            except OSError:
                pass
    work = root / f"{info['version']}_{os.getpid()}_{int(time.time())}"
    work.mkdir(parents=True)
    archive = work / "update.zip"
    sources = mirrors(info)
    errors = []
    for i, (source, url) in enumerate(sources):
        try:
            _fetch(url, archive, info, progress, cancel, allow_slow=i == len(sources) - 1, source=source)
            break
        except Cancelled:
            raise
        except (OSError, TimeoutError, ValueError) as e:  # urllib.error.* — наследники OSError
            errors.append(f"{source}: {e}")
    else:
        raise RuntimeError("Не удалось скачать обновление.\n" + "\n".join(errors))
    out = work / "files"
    with zipfile.ZipFile(archive) as z:
        z.extractall(out)
    archive.unlink(missing_ok=True)
    # архив может содержать папку «AE Subs» внутри
    inner = list(out.iterdir())
    if len(inner) == 1 and inner[0].is_dir() and (inner[0] / "AE Subs.exe").exists():
        out = inner[0]
    if not (out / "AE Subs.exe").exists():
        raise RuntimeError("В архиве обновления нет «AE Subs.exe».")
    return out


def install_and_restart(new_files: Path):
    """Запускает установщик-скрипт и закрывает программу: файлы заменяются после выхода."""
    if not getattr(sys, "frozen", False):
        raise RuntimeError("Обновление работает только в собранной программе (AE Subs.exe).")
    exe = Path(sys.executable)
    bat = Path(tempfile.gettempdir()) / "ae_subs_update" / "install.bat"
    xd = " ".join(f'"{d}"' for d in KEEP_DIRS)
    bat.write_text(
        "@echo off\r\n"
        "chcp 65001 >nul\r\n"
        f":wait\r\ntasklist /FI \"PID eq {os.getpid()}\" | find \"{os.getpid()}\" >nul && "
        "(timeout /t 1 /nobreak >nul & goto wait)\r\n"
        f'robocopy "{new_files}" "{APP_DIR}" /E /XD {xd} /NFL /NDL /NJH /NJS /NP >nul\r\n'
        f'start "" "{exe}"\r\n',
        encoding="utf-8")
    subprocess.Popen(["cmd", "/c", str(bat)], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
