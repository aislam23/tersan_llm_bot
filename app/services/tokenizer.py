"""
Утилиты для оценки числа токенов с помощью tiktoken.
"""
from __future__ import annotations

from typing import Dict, List

import tiktoken


def _get_encoding(model: str):
    try:
        return tiktoken.encoding_for_model(model)
    except Exception:
        # Универсальная кодировка для GPT-4/4o/5 семейств
        return tiktoken.get_encoding("cl100k_base")


def count_text_tokens(text: str, model: str) -> int:
    enc = _get_encoding(model)
    return len(enc.encode(text or ""))


def count_messages_tokens(messages: List[Dict[str, str]], model: str) -> int:
    """Грубая оценка числа токенов для массива сообщений.

    Замечание: точное число зависит от формата API. Эта функция даёт
    стабильную верхнюю оценку для контроля бюджета.
    """
    enc = _get_encoding(model)
    total = 0
    # Переоценка на служебные токены/структуру
    overhead_per_message = 4
    for m in messages:
        total += overhead_per_message
        total += len(enc.encode(m.get("role", "")))
        content = m.get("content", "")
        if isinstance(content, str):
            total += len(enc.encode(content))
        else:
            # На случай сложных content-структур
            total += len(enc.encode(str(content)))
    return total


