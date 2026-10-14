"""Связь с After Effects: поиск AfterFX.exe, запуск скриптов и получение ответа.

Скрипт запускается через «AfterFX.exe -r файл.jsx» — если AE уже открыт, команда уходит
в работающую копию. Ответ AE записывает в JSON-файл, который мы ждём по таймеру.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import os
import string
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

WORK_DIR = Path(tempfile.gettempdir()) / "ae_subs"

HOW_TO_ENABLE = ("В After Effects включите: Правка → Настройки → Скрипты и выражения → "
                 "«Разрешить скриптам записывать файлы и получать доступ к сети».")


def find_afterfx() -> list[str]:
    """Ищет AfterFX.exe на всех дисках, новые версии первыми."""
    found = []
    roots = [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]
    subdirs = ["Program Files\\Adobe", "Program Files (x86)\\Adobe", "Adobe"]
    for root in roots:
        for sub in subdirs:
            base = Path(root) / sub
            if not base.is_dir():
                continue
            try:
                for d in base.iterdir():
                    exe = d / "Support Files" / "AfterFX.exe"
                    if "After Effects" in d.name and exe.is_file():
                        found.append(str(exe))
            except OSError:
                continue
    return sorted(set(found), reverse=True)


class _ProcessEntry(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wt.DWORD),
                ("cntThreads", wt.DWORD), ("th32ParentProcessID", wt.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD), ("szExeFile", ctypes.c_wchar * 260)]


def is_ae_running() -> bool:
    """Быстрая проверка через снимок процессов Windows (без запуска tasklist)."""
    try:
        k32 = ctypes.windll.kernel32
        k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
        snap = k32.CreateToolhelp32Snapshot(2, 0)  # TH32CS_SNAPPROCESS
        if not snap or snap == wt.HANDLE(-1).value:
            return False
        try:
            entry = _ProcessEntry()
            entry.dwSize = ctypes.sizeof(entry)
            ok = k32.Process32FirstW(snap, ctypes.byref(entry))
            while ok:
                if entry.szExeFile.lower() == "afterfx.exe":
                    return True
                ok = k32.Process32NextW(snap, ctypes.byref(entry))
        finally:
            k32.CloseHandle(snap)
    except (OSError, AttributeError):
        pass
    return False


def new_request() -> tuple[Path, Path]:
    """Уникальные пути для скрипта и ответа (временная папка — латиница в пути, AE так надёжнее)."""
    WORK_DIR.mkdir(exist_ok=True)
    for old in WORK_DIR.glob("*"):  # чистим старые запросы
        try:
            if time.time() - old.stat().st_mtime > 3600:
                old.unlink()
        except OSError:
            pass
    rid = uuid.uuid4().hex[:10]
    return WORK_DIR / f"ae_subs_{rid}.jsx", WORK_DIR / f"ae_subs_{rid}.json"


def write_script(jsx: str, path: Path):
    # BOM нужен, чтобы ExtendScript прочитал кириллицу в сообщениях как UTF-8
    path.write_text(jsx, encoding="utf-8-sig")


def launch(afterfx_path: str, script: Path):
    if not afterfx_path or not Path(afterfx_path).is_file():
        raise FileNotFoundError("Не найден AfterFX.exe — укажите путь в настройках «After Effects».")
    subprocess.Popen([afterfx_path, "-r", str(script)],
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


class AEJob(QObject):
    """Запускает скрипт в AE и ждёт файл с ответом."""
    finished = Signal(dict)
    failed = Signal(str)

    def __init__(self, afterfx_path: str, build_script, timeout: float, parent=None):
        """build_script(result_path) -> текст JSX."""
        super().__init__(parent)
        self.afterfx_path = afterfx_path
        self.build_script = build_script
        self.timeout = timeout
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self._poll)
        self.result_path: Path | None = None
        self.started_at = 0.0
        self.ae_was_running = True

    def start(self):
        script, self.result_path = new_request()
        try:
            write_script(self.build_script(str(self.result_path)), script)
            self.ae_was_running = is_ae_running()
            launch(self.afterfx_path, script)
        except (OSError, FileNotFoundError) as e:
            QTimer.singleShot(0, lambda: self.failed.emit(str(e)))
            return
        self.started_at = time.time()
        self.timer.start()

    def cancel(self):
        self.timer.stop()

    def elapsed(self) -> float:
        return time.time() - self.started_at

    def _poll(self):
        if self.result_path.exists():
            try:
                data = json.loads(self.result_path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                return  # файл ещё дописывается
            self.timer.stop()
            try:
                self.result_path.unlink()
            except OSError:
                pass
            self.finished.emit(data)
            return
        # если AE пришлось запускать, ждём дольше: он стартует до пары минут
        limit = self.timeout if self.ae_was_running else max(self.timeout, 180)
        if self.elapsed() > limit:
            self.timer.stop()
            if self.ae_was_running:
                self.failed.emit("After Effects не ответил.\n\n" + HOW_TO_ENABLE +
                                 "\n\nТакже проверьте, что в AE не открыто модальное окно.")
            else:
                self.failed.emit("After Effects не запустился вовремя. Откройте AE и повторите.")
