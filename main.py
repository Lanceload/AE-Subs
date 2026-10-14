import multiprocessing
import sys
from pathlib import Path

# в собранном exe ресурсы распакованы во временную папку sys._MEIPASS
RES_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def main():
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from app.main_window import MainWindow
    from app.theme import apply_theme

    app = QApplication(sys.argv)
    app.setApplicationName("AE Subs")
    apply_theme(app)
    app.setWindowIcon(QIcon(str(RES_DIR / "assets" / "icon.png")))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


def selftest(media: str, out: str, device: str = "auto"):
    """Диагностика без окна: «AE Subs.exe --selftest видео.mp4 отчёт.txt [cuda|cpu]»."""
    import time

    from app.transcriber import transcribe_worker

    q = multiprocessing.Queue()
    p = multiprocessing.Process(target=transcribe_worker,
                                args=({"path": media, "model": "large-v3-turbo", "device": device,
                                       "language": ""}, q))
    p.start()
    lines = []
    while True:
        try:
            msg = q.get(timeout=1)
        except Exception:  # noqa: BLE001
            if not p.is_alive():
                lines.append(f"process died, exitcode {p.exitcode}")
                break
            continue
        if msg[0] == "done":
            lines.append(f"done: {len(msg[1])} words in {msg[2]:.1f}s")
            break
        if msg[0] != "download":
            lines.append(repr(msg))
        if msg[0] == "error":
            break
    p.join(5)
    Path(out).write_text("\n".join(lines), encoding="utf-8")


def selftest_video(media: str, out: str):
    """Диагностика видео-превью: «AE Subs.exe --selftest-video видео.mp4 отчёт.txt»."""
    import time

    from PySide6.QtWidgets import QApplication

    from app.player import VideoPlayer

    app = QApplication(sys.argv)
    player = VideoPlayer()
    got = {"frames": 0, "wave": None, "error": ""}
    player.frameReady.connect(lambda img: got.__setitem__("frames", got["frames"] + 1))
    player.waveformReady.connect(lambda w: got.__setitem__("wave", None if w is None else len(w[0])))
    player.failed.connect(lambda m: got.__setitem__("error", m))
    player.load(media)
    player.set_volume(0)
    player.player.play()
    t = time.time()
    while time.time() - t < 9:
        app.processEvents()
        time.sleep(0.01)
    Path(out).write_text(f"frames={got['frames']} wave={got['wave']} error={got['error']}", encoding="utf-8")


if __name__ == "__main__":
    # распознавание идёт в дочернем процессе; в exe без этого он запустит вторую копию программы
    multiprocessing.freeze_support()
    if len(sys.argv) >= 4 and sys.argv[1] == "--selftest":
        selftest(*sys.argv[2:5])
    elif len(sys.argv) >= 4 and sys.argv[1] == "--selftest-video":
        selftest_video(sys.argv[2], sys.argv[3])
    else:
        main()
