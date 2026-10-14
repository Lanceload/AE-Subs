"""Распознавание речи через faster-whisper.

Работает в отдельном процессе: так GUI не зависает, распознавание можно
прервать, а падение CUDA-библиотек не роняет всю программу.
Здесь не должно быть импортов Qt — модуль загружается в дочернем процессе.
"""
from __future__ import annotations

import os
import sys
import threading
import time
import traceback
from pathlib import Path

from .paths import CUDA_DIR, MODELS_DIR

# (имя модели, подпись, примерный размер в МБ)
MODELS = [
    ("large-v3-turbo", "Turbo — лучшее качество и быстро (рекомендуется)", 1620),
    ("large-v3", "Large v3 — максимум точности, медленнее", 3100),
    ("medium", "Medium — хорошо, средняя скорость", 1530),
    ("small", "Small — быстро, точность ниже", 490),
    ("base", "Base — очень быстро, для черновика", 145),
]

LANGUAGES = [
    ("ru", "Русский"), ("", "Определить автоматически"), ("en", "English"),
    ("uk", "Українська"), ("be", "Беларуская"), ("kk", "Қазақша"), ("de", "Deutsch"),
    ("fr", "Français"), ("es", "Español"), ("it", "Italiano"), ("pt", "Português"),
    ("pl", "Polski"), ("tr", "Türkçe"), ("ja", "日本語"), ("ko", "한국어"), ("zh", "中文"),
]

MEDIA_FILTER = ("Видео и аудио (*.mp4 *.mov *.mkv *.avi *.webm *.m4v *.mxf *.wav *.mp3 *.m4a "
                "*.aac *.flac *.ogg *.opus *.wma *.aif *.aiff);;Все файлы (*.*)")


def _cuda_dll_dirs() -> list[Path]:
    """Папки с DLL CUDA/cuDNN: папка cuda рядом с программой или pip-пакеты nvidia-*."""
    dirs = [CUDA_DIR]
    for root in (Path(p) for p in sys.path if p):
        base = root / "nvidia"
        if base.is_dir():
            dirs += [d for d in base.glob("*/bin") if d.is_dir()]
    seen, out = set(), []
    for d in dirs:
        if d not in seen and any(d.glob("*.dll")):
            seen.add(d)
            out.append(d)
    return out


def setup_cuda_dlls():
    for d in _cuda_dll_dirs():
        try:
            os.add_dll_directory(str(d))
        except (OSError, AttributeError):
            pass
        os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")


def model_dir(name: str) -> Path:
    # обычная папка с файлами модели — без кэша и ссылок Hugging Face, её можно переносить
    return MODELS_DIR / name


def is_model_downloaded(name: str) -> bool:
    folder = model_dir(name)
    return (folder / "model.bin").is_file() and (folder / "config.json").is_file()


def _dir_size(path: Path) -> int:
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def decode_audio(path: str, rate: int = 16000):
    """Достаёт звук из видео/аудио: моно, 16 кГц, float32 — так, как ждёт Whisper.

    Свой декодер вместо faster_whisper.decode_audio: тот ломается на новых версиях PyAV.
    """
    import av
    import numpy as np

    chunks = []
    with av.open(path, mode="r") as container:
        if not container.streams.audio:
            raise RuntimeError("В файле нет звуковой дорожки.")
        stream = container.streams.audio[0]
        resampler = av.audio.resampler.AudioResampler(format="s16", layout="mono", rate=rate)
        for frame in container.decode(stream):
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray().reshape(-1))
        for out in resampler.resample(None):
            chunks.append(out.to_ndarray().reshape(-1))
    if not chunks:
        raise RuntimeError("Не удалось прочитать звук из файла.")
    return np.concatenate(chunks).astype(np.float32) / 32768.0


def waveform_worker(path: str, out_path: str) -> None:
    """Отдельный процесс: пики громкости для таймлайна (шаг 10 мс) → .npy.

    В процессе с окном PyAV конфликтует с FFmpeg видеоплеера Qt, поэтому — отдельно.
    """
    import numpy as np
    try:
        audio = decode_audio(path, 8000)
        step = 80
        n = len(audio) // step
        peaks = np.abs(audio[: n * step]).reshape(n, step).max(axis=1) if n else np.zeros(1, np.float32)
        peaks = peaks / max(float(peaks.max()), 1e-6)
        np.save(out_path, peaks.astype(np.float32))
    except Exception:  # noqa: BLE001 — нет звуковой дорожки и т.п.
        np.save(out_path, np.zeros(0, np.float32))


def mix_sources(sources: list[dict], duration: float, rng: tuple[float, float] | None = None,
                rate: int = 16000):
    """Собирает звук композиции AE: каждый слой кладётся на своё место по времени.

    source: path, comp_start (время композиции, где у файла 0 с), rate (скорость слоя),
    a, b — видимый отрезок слоя во времени композиции.
    """
    import numpy as np

    total = int(duration * rate) + 1
    mix = np.zeros(total, np.float32)
    cache: dict[str, np.ndarray] = {}
    for src in sources:
        path = src["path"]
        if path not in cache:
            cache[path] = decode_audio(path, rate)
        audio = cache[path]
        a, b = max(src["a"], 0.0), min(src["b"], duration)
        if rng:
            a, b = max(a, rng[0]), min(b, rng[1])
        if b <= a:
            continue
        i0, i1 = int(a * rate), min(int(b * rate), total)
        t = np.arange(i0, i1, dtype=np.float64) / rate
        # время композиции → время внутри файла
        s = (t - src["comp_start"]) / src["rate"]
        idx = np.round(s * rate).astype(np.int64)
        ok = (idx >= 0) & (idx < len(audio))
        mix[i0:i1][ok] += audio[idx[ok]]
    peak = float(np.max(np.abs(mix))) if total else 0.0
    if peak > 1.0:
        mix /= peak
    return mix


def transcribe_worker(params: dict, queue) -> None:
    """Точка входа дочернего процесса. Шлёт в очередь кортежи (тип, данные...)."""
    try:
        # весь служебный кэш загрузчика — тоже рядом с программой, а не на диске C:
        os.environ["HF_HOME"] = str(MODELS_DIR / ".hf")
        os.environ["HF_XET_CACHE"] = str(MODELS_DIR / ".hf" / "xet")
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
        os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
        setup_cuda_dlls()
        MODELS_DIR.mkdir(parents=True, exist_ok=True)

        import ctranslate2
        from faster_whisper import WhisperModel

        device = params.get("device", "auto")
        if device == "auto":
            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        compute_type = "float16" if device == "cuda" else "int8"
        queue.put(("device", device))

        model_name = params["model"]
        if not is_model_downloaded(model_name):
            size_mb = dict((m[0], m[2]) for m in MODELS).get(model_name, 0)
            queue.put(("status", f"Скачиваю модель «{model_name}» (~{size_mb} МБ, только в первый раз)…"))
            stop = threading.Event()

            def report():
                start_size = _dir_size(MODELS_DIR)
                while not stop.wait(1.0):
                    done = (_dir_size(MODELS_DIR) - start_size) / 1e6
                    queue.put(("download", done, size_mb))

            threading.Thread(target=report, daemon=True).start()
            try:
                from faster_whisper.utils import download_model
                download_model(model_name, output_dir=str(model_dir(model_name)))
            finally:
                stop.set()

        queue.put(("status", "Загружаю модель…"))
        model = WhisperModel(str(model_dir(model_name)), device=device, compute_type=compute_type)

        separate = params.get("separate", False)
        rate = 44100 if separate else 16000  # модели отделения голоса нужен 44,1 кГц
        if params.get("sources"):
            queue.put(("status", "Собираю звук композиции After Effects…"))
            rng = params.get("range")
            audio = mix_sources(params["sources"], params["duration"], tuple(rng) if rng else None, rate)
        else:
            queue.put(("status", "Читаю звук из файла…"))
            audio = decode_audio(params["path"], rate)

        if separate:
            import numpy as np

            from . import separator
            if not separator.is_downloaded():
                queue.put(("status", f"Скачиваю модель отделения голоса (~{separator.MODEL_MB} МБ, только в первый раз)…"))
                separator.download(lambda d, t: queue.put(("download", d, int(t))))
            queue.put(("status", "Отделяю голос от музыки…"))
            vocals = separator.extract_vocals(np.stack([audio, audio]),
                                              lambda i, n: queue.put(("separate", i, n)))
            audio = separator.resample_mono(vocals, 44100)

        queue.put(("status", "Распознаю речь…"))
        t0 = time.time()
        segments, info = model.transcribe(
            audio,
            language=params.get("language") or None,
            initial_prompt=params.get("prompt") or None,
            word_timestamps=True,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 400},
            beam_size=5,
            condition_on_previous_text=False,
        )
        queue.put(("info", float(info.duration), info.language))
        words = []
        for seg in segments:
            for w in seg.words or []:
                text = w.word.strip()
                if text:
                    words.append((round(float(w.start), 3), round(float(w.end), 3), text))
            queue.put(("progress", float(seg.end), float(info.duration), seg.text.strip()))
        queue.put(("done", words, time.time() - t0))
    except BaseException:  # noqa: BLE001 — всё отправляем в GUI
        queue.put(("error", traceback.format_exc()))
