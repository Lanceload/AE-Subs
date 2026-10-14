"""Обратная связь: сообщение пользователя уходит на сервер-посредник, а оттуда — разработчику в Telegram.

Токена бота в программе нет: он хранится только на сервере (feedback-worker в репозитории).
"""
from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request

from .version import VERSION

URL = "https://aesubs-feedback.yaqen.workers.dev/feedback"
KINDS = [("bug", "🐞  Ошибка"), ("idea", "💡  Предложение"), ("review", "⭐  Отзыв")]
MAX_TEXT = 3000


def _windows() -> str:
    if sys.platform != "win32":
        return sys.platform
    v = sys.getwindowsversion()
    return "Windows 11" if v.major == 10 and v.build >= 22000 else f"Windows {v.major}"


def system_info(afterfx_path: str = "") -> dict:
    """Windows, видеокарта и версия After Effects — чтобы было проще разобраться с ошибкой."""
    info = {"os": _windows(), "gpu": "", "ae": ""}
    script = ("$g = (Get-CimInstance Win32_VideoController | Where-Object { $_.Name -notlike '*Basic*' } "
              "| Select-Object -ExpandProperty Name) -join ', '; Write-Output \"GPU:$g\";")
    if afterfx_path:
        path = afterfx_path.replace("'", "''")
        script += f" Write-Output \"AE:$((Get-Item -LiteralPath '{path}').VersionInfo.ProductVersion)\""
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True,
                             timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        for line in out.splitlines():
            key, _, value = line.strip().partition(":")
            if key == "GPU" and value:
                info["gpu"] = value
            elif key == "AE" and value:
                info["ae"] = "AE " + ".".join(value.split(".")[:2])
    except (OSError, subprocess.SubprocessError):
        pass
    return info


def send(kind: str, text: str, system: dict | None) -> None:
    """Отправляет сообщение. При ошибке — RuntimeError с понятным текстом."""
    data = {"type": kind, "text": text[:MAX_TEXT], "version": VERSION, "system": system or {}}
    req = urllib.request.Request(URL, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                                 {"Content-Type": "application/json; charset=utf-8", "X-AE-Subs": "1",
                                  "User-Agent": f"AE-Subs/{VERSION}"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            ok = json.loads(r.read().decode("utf-8")).get("ok")
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode("utf-8")).get("error")
        except ValueError:
            msg = None
        raise RuntimeError(msg or f"Сервер ответил ошибкой {e.code}.") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise RuntimeError("Нет соединения с сервером. Проверьте интернет и попробуйте ещё раз.") from e
    if not ok:
        raise RuntimeError("Сообщение не доставлено. Попробуйте позже.")
