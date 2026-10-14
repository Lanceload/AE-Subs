"""Обновление программы из релизов GitHub.

Каждый релиз репозитория — тег vX.Y.Z и архив AE_Subs_X.Y.Z.zip с содержимым папки программы.
Модели, CUDA, пресеты и проекты при обновлении не трогаются.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from .paths import APP_DIR
from .version import VERSION

REPO = "Lanceload/AE-Subs"
RELEASES_URL = f"https://github.com/{REPO}/releases"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
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


def download(info: dict, progress=None) -> Path:
    """Скачивает архив и распаковывает во временную папку. Возвращает папку с программой."""
    work = Path(tempfile.gettempdir()) / "ae_subs_update"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    archive = work / "update.zip"
    req = urllib.request.Request(info["url"], headers={"User-Agent": "AE-Subs-updater"})
    with urllib.request.urlopen(req, timeout=60) as r, open(archive, "wb") as f:
        total = int(r.headers.get("Content-Length", 0)) or info.get("size", 0)
        done = 0
        while chunk := r.read(1 << 20):
            f.write(chunk)
            done += len(chunk)
            if progress and total:
                progress(min(done / total, 1.0))
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
