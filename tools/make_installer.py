"""Собирает установщик release/AE_Subs_Setup_<версия>.exe из сборки PyInstaller (build/out/AE Subs).

Перед запуском: собрать программу (PyInstaller в build/out) — см. README.
Нужен Inno Setup 6.5+ (ISCC.exe); путь можно задать переменной окружения ISCC.
Размеры скачиваемых частей берутся из локальной папки «AE Subs» (models, cuda) — там лежат те же файлы,
что и на Hugging Face.
"""
import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from app.version import VERSION  # noqa: E402
from make_icon import draw  # noqa: E402

INST = ROOT / "installer"
LOCAL = ROOT / "AE Subs"
HF = "https://huggingface.co"
MODEL_REPO = f"{HF}/mobiuslabsgmbh/faster-whisper-large-v3-turbo/resolve/main"
CUDA_REPO = f"{HF}/Yaqen/AE-Subs/resolve/main/cuda"
VOCALS_URL = f"{HF}/seanghay/uvr_models/resolve/main/Kim_Vocal_2.onnx"
MODEL_FILES = ["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.json"]
ISCC = os.environ.get("ISCC", r"E:\Tools\InnoSetup6\ISCC.exe")


def line(url: str, dest_dir: str, name: str, size: int, component: str) -> str:
    return (f'Source: "{url}"; DestDir: "{dest_dir}"; DestName: "{name}"; ExternalSize: {size}; '
            f'Components: {component}; Flags: external download ignoreversion')


def downloads() -> str:
    out = ["; создано tools\\make_installer.py — не править вручную"]
    model = LOCAL / "models" / "large-v3-turbo"
    for name in MODEL_FILES:
        out.append(line(f"{MODEL_REPO}/{name}", r"{app}\models\large-v3-turbo", name,
                        (model / name).stat().st_size, "model"))
    for dll in sorted((LOCAL / "cuda").glob("*.dll")):
        out.append(line(f"{CUDA_REPO}/{dll.name}", r"{app}\cuda", dll.name, dll.stat().st_size, "gpu"))
    onnx = LOCAL / "models" / "separator" / "Kim_Vocal_2.onnx"
    out.append(line(VOCALS_URL, r"{app}\models\separator", onnx.name, onnx.stat().st_size, "vocals"))
    return "\n".join(out) + "\n"


def wizard_images():
    """Картинки мастера установки из иконки программы (обычная и для экранов с увеличением)."""
    def save(w: int, h: int, icon_side: float, path: Path, top: float = 0.5):
        img = QImage(w, h, QImage.Format.Format_RGB32)
        img.fill(QColor("#2d6cdf"))
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawImage(QRectF((w - icon_side) / 2, h * top - icon_side / 2, icon_side, icon_side), draw(1024))
        p.end()
        img.save(str(path))

    save(55, 55, 52, INST / "wizard_small.bmp")
    save(110, 110, 104, INST / "wizard_small_2x.bmp")
    save(164, 314, 140, INST / "wizard.bmp", top=0.4)
    save(328, 628, 280, INST / "wizard_2x.bmp", top=0.4)


if __name__ == "__main__":
    if not (ROOT / "build" / "out" / "AE Subs" / "AE Subs.exe").is_file():
        sys.exit("Нет сборки build/out/AE Subs — сначала соберите программу PyInstaller'ом.")
    app = QGuiApplication(sys.argv)
    (INST / "downloads.iss").write_text(downloads(), encoding="utf-8-sig")
    # Inno Setup читает сценарий как UTF-8 только с BOM, иначе русский текст побьётся
    iss = INST / "AE_Subs.iss"
    iss.write_text(iss.read_text(encoding="utf-8-sig"), encoding="utf-8-sig")
    wizard_images()
    subprocess.run([ISCC, f"/DAppVersion={VERSION}", "/Qp", str(INST / "AE_Subs.iss")], check=True)
    print(f"release/AE_Subs_Setup_{VERSION}.exe готов")
