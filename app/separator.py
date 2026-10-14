"""Отделение голоса от музыки перед распознаванием (модель MDX-Net «Kim Vocal 2», ONNX).

Без PyTorch: спектрограмма считается в numpy, сеть работает через onnxruntime.
Параметры модели — как в Ultimate Vocal Remover: n_fft=7680, dim_f=3072, dim_t=256, hop=1024.
"""
from __future__ import annotations

import os
import urllib.request
from pathlib import Path

import numpy as np

from .paths import MODELS_DIR

MODEL_NAME = "Kim_Vocal_2.onnx"
MODEL_URL = "https://github.com/TRvlvr/model_repo/releases/download/all_public_uvr_models/" + MODEL_NAME
MODEL_MB = 66
SR = 44100
N_FFT, HOP, DIM_F, DIM_T = 7680, 1024, 3072, 256
COMPENSATE = 1.009
CHUNK = HOP * (DIM_T - 1)  # 261120 сэмплов ≈ 5,9 с


def model_path() -> Path:
    return MODELS_DIR / "separator" / MODEL_NAME


def is_downloaded() -> bool:
    p = model_path()
    return p.is_file() and p.stat().st_size > 10_000_000


def download(progress=None):
    p = model_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".part")
    req = urllib.request.Request(MODEL_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length", 0)) or MODEL_MB * 1_000_000
        done = 0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if progress:
                progress(done / 1e6, total / 1e6)
    os.replace(tmp, p)


_WINDOW = np.hanning(N_FFT + 1)[:-1].astype(np.float32)  # периодическое окно Ханна, как torch.hann_window


def _stft(x: np.ndarray) -> np.ndarray:
    """x: [каналы, CHUNK] → комплексный спектр [каналы, бины, DIM_T] (как torch.stft, center=True)."""
    pad = N_FFT // 2
    x = np.pad(x, ((0, 0), (pad, pad)), mode="reflect")
    idx = np.arange(DIM_T)[:, None] * HOP + np.arange(N_FFT)[None, :]
    frames = x[:, idx] * _WINDOW  # [каналы, DIM_T, N_FFT]
    return np.fft.rfft(frames, axis=-1).transpose(0, 2, 1)


def _istft(spec: np.ndarray) -> np.ndarray:
    """Обратное преобразование: [каналы, бины, DIM_T] → [каналы, CHUNK]."""
    frames = np.fft.irfft(spec.transpose(0, 2, 1), n=N_FFT, axis=-1) * _WINDOW
    length = N_FFT + HOP * (DIM_T - 1)
    out = np.zeros((spec.shape[0], length), np.float32)
    norm = np.zeros(length, np.float32)
    for t in range(DIM_T):
        out[:, t * HOP:t * HOP + N_FFT] += frames[:, t]
        norm[t * HOP:t * HOP + N_FFT] += _WINDOW ** 2
    out /= np.maximum(norm, 1e-8)
    pad = N_FFT // 2
    return out[:, pad:pad + CHUNK]


def _to_model(spec: np.ndarray) -> np.ndarray:
    """[2, бины, T] комплекс → [1, 4, DIM_F, T]: re/im левого, re/im правого канала."""
    s = spec[:, :DIM_F]
    return np.stack([s[0].real, s[0].imag, s[1].real, s[1].imag])[None].astype(np.float32)


def _from_model(out: np.ndarray) -> np.ndarray:
    o = out[0]
    spec = np.zeros((2, N_FFT // 2 + 1, DIM_T), np.complex64)
    spec[0, :DIM_F] = o[0] + 1j * o[1]
    spec[1, :DIM_F] = o[2] + 1j * o[3]
    return spec


def extract_vocals(stereo: np.ndarray, progress=None) -> np.ndarray:
    """stereo: [2, N] float32 при 44,1 кГц → голос [2, N]."""
    import onnxruntime as ort

    opts = ort.SessionOptions()
    opts.log_severity_level = 3
    sess = ort.InferenceSession(str(model_path()), opts, providers=["CPUExecutionProvider"])
    in_name = sess.get_inputs()[0].name

    n = stereo.shape[1]
    trim = N_FFT // 2
    gen = CHUNK - 2 * trim  # полезная часть куска без краёв
    pad = gen - n % gen
    mix = np.concatenate([np.zeros((2, trim), np.float32), stereo, np.zeros((2, pad + trim), np.float32)], axis=1)
    out_parts = []
    steps = (n + pad) // gen
    for i, pos in enumerate(range(0, n + pad, gen)):
        wave = mix[:, pos:pos + CHUNK]
        spec = _stft(wave)
        pred = sess.run(None, {in_name: _to_model(spec)})[0]
        out_parts.append(_istft(_from_model(pred))[:, trim:-trim])
        if progress:
            progress(i + 1, steps)
    vocals = np.concatenate(out_parts, axis=1)[:, :n]
    return vocals * COMPENSATE


def resample_mono(stereo: np.ndarray, src_rate: int, dst_rate: int = 16000) -> np.ndarray:
    mono = stereo.mean(axis=0)
    # фильтр нижних частот (sinc с окном) перед прореживанием — без него появляются искажения
    cutoff = 0.45 * dst_rate / src_rate
    taps = np.arange(-48, 49)
    kernel = np.sinc(2 * cutoff * taps) * np.hamming(len(taps))
    mono = np.convolve(mono, (kernel / kernel.sum()).astype(np.float32), mode="same")
    n_out = int(len(mono) * dst_rate / src_rate)
    x_old = np.arange(len(mono)) / src_rate
    x_new = np.arange(n_out) / dst_rate
    return np.interp(x_new, x_old, mono).astype(np.float32)
