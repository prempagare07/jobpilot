from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any


def extract_json_object(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if not text:
        raise ValueError("Ollama returned an empty response")

    # Strip markdown fences
    text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\s*```\s*$', '', text).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError(f"Ollama did not return a JSON object: {text[:300]}") from exc
        data = json.loads(text[start : end + 1])

    if not isinstance(data, dict):
        raise ValueError("Ollama JSON response must be an object")
    return data


def as_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, Iterable) and not isinstance(value, (bytes, dict)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def clamp_int(value: Any, minimum: int, maximum: int, default: int = 0) -> int:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def clamp_float(value: Any, minimum: float, maximum: float, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def compact_words(text: str, limit: int) -> str:
    words = text.split()
    if len(words) <= limit:
        return text.strip()
    return " ".join(words[:limit]).rstrip(" ,;:") + "."
