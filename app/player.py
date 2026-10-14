"""Видео для предпросмотра: проигрывание со звуком, кадры в превью, волна звука для таймлайна.

Время везде — время композиции AE: comp = offset + media * rate
(offset/rate берутся из слоя видео в композиции, для обычного файла offset=0, rate=1).
"""
from __future__ import annotations

import multiprocessing as mp
import tempfile
import uuid
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoFrame, QVideoSink


class VideoPlayer(QObject):
    frameReady = Signal(QImage)
    timeChanged = Signal(float)  # время композиции, сек
    playingChanged = Signal(bool)
    loaded = Signal(str)
    failed = Signal(str)
    waveformReady = Signal(object)  # (пики numpy, шаг в сек, offset, rate)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.player = QMediaPlayer(self)
        self.sink = QVideoSink(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(0.8)
        self.player.setVideoSink(self.sink)
        self.player.setAudioOutput(self.audio)
        self.sink.videoFrameChanged.connect(self._on_frame)
        self.player.positionChanged.connect(lambda ms: self.timeChanged.emit(self.comp_time()))
        self.player.playbackStateChanged.connect(
            lambda s: self.playingChanged.emit(s == QMediaPlayer.PlaybackState.PlayingState))
        self.player.errorOccurred.connect(lambda e, msg: self.failed.emit(msg) if e else None)
        self.player.mediaStatusChanged.connect(self._on_status)
        self.path = ""
        self.offset = 0.0
        self.rate = 1.0
        self._wave_proc = None
        self._wave_timer = QTimer(self)
        self._wave_timer.setInterval(200)
        self._wave_timer.timeout.connect(self._poll_waveform)

    # ------------------------------------------------------------ загрузка

    def load(self, path: str, offset: float = 0.0, rate: float = 1.0):
        self.path, self.offset, self.rate = path, offset, rate or 1.0
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.pause()  # показать первый кадр
        self._start_waveform(path, offset, self.rate)

    def unload(self):
        self._wave_timer.stop()
        if self._wave_proc is not None and self._wave_proc.is_alive():
            self._wave_proc.terminate()  # волна старого видео больше не нужна
        self._wave_proc = None
        self.player.stop()
        self.player.setSource(QUrl())
        self.path = ""

    def has_media(self) -> bool:
        return bool(self.path)

    def _on_status(self, status):
        if status == QMediaPlayer.MediaStatus.LoadedMedia:
            self.loaded.emit(self.path)
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            self.failed.emit("Не удалось открыть видео.")

    def _start_waveform(self, path: str, offset: float, rate: float):
        """Волна считается в отдельном процессе (PyAV), результат забираем по таймеру."""
        from .transcriber import waveform_worker
        if self._wave_proc is not None and self._wave_proc.is_alive():
            self._wave_proc.terminate()
        self._wave_out = Path(tempfile.gettempdir()) / "ae_subs" / f"wave_{uuid.uuid4().hex[:8]}.npy"
        self._wave_out.parent.mkdir(exist_ok=True)
        self._wave_meta = (offset, rate)
        self._wave_proc = mp.Process(target=waveform_worker, args=(path, str(self._wave_out)), daemon=True)
        self._wave_proc.start()
        self._wave_timer.start()

    def _poll_waveform(self):
        proc = self._wave_proc
        if proc is None or proc.is_alive():
            return
        self._wave_timer.stop()
        self._wave_proc = None
        try:
            peaks = np.load(self._wave_out)
            self._wave_out.unlink(missing_ok=True)
        except (OSError, ValueError):
            peaks = np.zeros(0, np.float32)
        offset, rate = self._wave_meta
        self.waveformReady.emit((peaks, 0.01, offset, rate) if len(peaks) else None)

    def _on_frame(self, frame: QVideoFrame):
        if frame.isValid():
            img = frame.toImage()
            if not img.isNull():
                self.frameReady.emit(img)

    # ------------------------------------------------------------ управление

    def comp_time(self) -> float:
        return self.offset + self.player.position() / 1000 * self.rate

    def duration(self) -> float:
        return self.player.duration() / 1000 * self.rate

    def seek(self, comp_t: float):
        if not self.path:
            return
        media_ms = int(max(0.0, (comp_t - self.offset) / self.rate) * 1000)
        self.player.setPosition(media_ms)

    def toggle(self):
        if not self.path:
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def pause(self):
        self.player.pause()

    def is_playing(self) -> bool:
        return self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def set_volume(self, v: float):
        self.audio.setVolume(v)
