"""Готовит архив обновления из собранной папки «AE Subs»: release/AE_Subs_<версия>.zip.

Архив прикладывается к релизу GitHub с тегом v<версия>:
    gh release create v2.2.0 release/AE_Subs_2.2.0.zip --title "AE Subs 2.2.0" --notes "Что нового"
Модели, CUDA, пресеты и проекты в архив не попадают.
"""
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.updater import KEEP_DIRS  # noqa: E402
from app.version import VERSION  # noqa: E402

src = ROOT / "AE Subs"
out = ROOT / "release"
out.mkdir(exist_ok=True)
name = f"AE_Subs_{VERSION}.zip"
with zipfile.ZipFile(out / name, "w", zipfile.ZIP_DEFLATED) as z:
    for f in src.rglob("*"):
        rel = f.relative_to(src)
        if f.is_file() and rel.parts[0] not in KEEP_DIRS:
            z.write(f, rel)
print(f"release/{name} готов")
