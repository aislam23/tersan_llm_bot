"""
Утилиты для работы с аудио: конвертация в WAV (PCM16) через ffmpeg.
"""
from __future__ import annotations

import os
import subprocess
import uuid
from typing import Optional


def convert_to_wav(input_path: str, *, sample_rate: int = 16000) -> Optional[str]:
    """Конвертировать аудио-файл в WAV (PCM16, mono) для STT. Возвращает путь к .wav или None при ошибке.

    Требует доступности `ffmpeg` в системе.
    """
    if not os.path.exists(input_path):
        return None

    output_path = f"/tmp/{uuid.uuid4().hex}.wav"
    cmd = [
        "ffmpeg",
        "-y",  # перезаписывать
        "-i",
        input_path,
        "-ac",
        "1",  # mono
        "-ar",
        str(sample_rate),  # 16 kHz
        "-c:a",
        "pcm_s16le",  # PCM16
        output_path,
    ]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return output_path
    except Exception:
        return None


