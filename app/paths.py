"""Где программа хранит свои данные.

Всё тяжёлое (модели распознавания, библиотеки CUDA) и пресеты лежат рядом
с exe — программа «портативная» и не забивает системный диск.
При запуске из исходников используется та же папка «AE Subs», куда собирается exe.
Здесь не должно быть импортов Qt — модуль загружается и в процессе распознавания.
"""
import os
import sys
from pathlib import Path

if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).resolve().parent
else:
    APP_DIR = Path(__file__).resolve().parent.parent / "AE Subs"

PRESETS_DIR = APP_DIR / "presets"
MODELS_DIR = APP_DIR / "models"
CUDA_DIR = APP_DIR / "cuda"
# небольшой файл с последним состоянием окна — в профиле пользователя
STATE_FILE = Path(os.environ.get("APPDATA", Path.home())) / "AE_Subs" / "state.json"
