"""
Утилиты для работы с аудио.

- Для Telegram voice (OGG/Opus) используем opusdec (из пакета opus-tools),
  чтобы конвертировать в WAV (PCM16, mono). Это минимум зависимостей.
- Для аудио в форматах, которые поддерживает OpenAI (mp3/mp4/mpeg/mpga/m4a/wav/webm),
  конвертация не требуется — их отдаём напрямую в STT.
"""
from __future__ import annotations

import os
import subprocess
import uuid
from typing import Optional


def convert_to_wav(input_path: str, *, sample_rate: int = 16000) -> Optional[str]:
    """Конвертировать OGG/Opus в WAV (PCM16, mono). Для поддерживаемых OpenAI форматов — пропустить.

    Если расширение файла входит в список допустимых для OpenAI — возвращает исходный путь.
    Если .oga/.ogg — конвертирует с помощью opusdec в WAV (16kHz, mono) и возвращает путь к .wav.
    Иначе — None.
    """
    if not os.path.exists(input_path):
        return None

    _, ext = os.path.splitext(input_path.lower())
    # Поддерживаемые OpenAI напрямую
    supported_direct = {".mp3", ".mp4", ".mpeg", ".mpga", ".m4a", ".wav", ".webm"}
    if ext in supported_direct:
        return input_path

    # Голосовые Telegram: .oga/.ogg → opusdec в WAV
    if ext in {".oga", ".ogg"}:
        output_path = f"/tmp/{uuid.uuid4().hex}.wav"
        # Конвертация потоком через ffmpeg нам не нужна — используем opusdec
        # Принудительно зададим частоту дискретизации и моно через sox-пайп не будем; opusdec выдаёт PCM WAV
        cmd = [
            "opusdec",
            "--rate",
            str(sample_rate),
            "--force-wav",
            input_path,
            output_path,
        ]
        try:
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return output_path
        except Exception:
            return None

    # Неподдерживаемый формат
    return None


