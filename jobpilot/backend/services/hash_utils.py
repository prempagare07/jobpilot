from __future__ import annotations

import hashlib
import re

WHITESPACE_RE = re.compile(r"\s+")


def normalize_text(value: str) -> str:
    cleaned = value.strip().lower()
    cleaned = WHITESPACE_RE.sub(" ", cleaned)
    return cleaned


def sha256_text(value: str) -> str:
    return hashlib.sha256(normalize_text(value).encode("utf-8")).hexdigest()


def job_id_for(title: str, company: str, url: str) -> str:
    return sha256_text(f"{title}|{company}|{url}")
