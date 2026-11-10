"""Расшифровка голосовых (faster-whisper, локально: в GitHub Actions или на Mac, без внешних API).

Модель скачивается один раз (в Actions кешируется), по умолчанию `small` — хорошо понимает русский.
"""
import os
import shutil
import subprocess

import numpy as np

_MODEL = None


def transcribe(path: str) -> str:
    global _MODEL
    from faster_whisper import WhisperModel  # noqa: PLC0415  (тяжёлый импорт — только когда нужно)

    if _MODEL is None:
        _MODEL = WhisperModel(os.getenv("WHISPER_MODEL", "small"), device="cpu", compute_type="int8")
    audio = _decode(path)
    segments, _info = _MODEL.transcribe(audio, language=os.getenv("WHISPER_LANG") or None,
                                        vad_filter=True, beam_size=5)
    return " ".join(s.text.strip() for s in segments).strip()


def _decode(path: str) -> "np.ndarray":
    """Любой формат Telegram (ogg/opus, m4a, mp4) → 16 кГц моно float32.
    Сначала PyAV (идёт вместе с faster-whisper, отдельный ffmpeg не нужен — важно для GitHub Actions),
    если не вышло — системный ffmpeg."""
    try:
        import av  # noqa: PLC0415

        chunks, total = [], 0
        with av.open(path) as c:
            rs = av.AudioResampler(format="flt", layout="mono", rate=16000)
            for frame in c.decode(audio=0):
                for out in rs.resample(frame):
                    chunks.append(out.to_ndarray().reshape(-1))
                total += sum(len(x) for x in chunks[-1:])
                if total > 16000 * 600:
                    break  # не больше 10 минут
            for out in rs.resample(None):
                chunks.append(out.to_ndarray().reshape(-1))
        if chunks:
            return np.concatenate(chunks).astype(np.float32)
    except Exception:  # noqa: BLE001
        pass
    ff = shutil.which("ffmpeg")
    if not ff:
        raise RuntimeError("не смог раскодировать звук: нет ни PyAV, ни ffmpeg")
    out = subprocess.run([ff, "-nostdin", "-loglevel", "error", "-i", path, "-t", "600",
                          "-f", "f32le", "-ac", "1", "-ar", "16000", "-"],
                         capture_output=True, check=True, timeout=180).stdout
    return np.frombuffer(out, dtype=np.float32)
