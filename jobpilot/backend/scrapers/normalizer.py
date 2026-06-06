from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from bs4 import BeautifulSoup

from backend.scrapers.base import RawJob

MAX_DESCRIPTION_CHARS = 8000


@dataclass(frozen=True)
class NormalizedJob:
    id: str
    title: str
    company: str
    location: str
    job_description: str
    url: str
    platform: str
    date_posted: datetime | None
    easy_apply: bool
    scraped_at: datetime
    status: str = "new"


def normalize(raw: RawJob, platform: str) -> NormalizedJob:
    title = normalize_title(raw.title)
    company = clean_display(raw.company) or "Unknown"
    location = clean_display(raw.location) or "United States"
    description = strip_html(raw.description)[:MAX_DESCRIPTION_CHARS]
    date_posted = parse_date(raw.date_posted)
    job_id = stable_job_id(title=title, company=company, location=location)
    return NormalizedJob(
        id=job_id,
        title=title,
        company=company,
        location=location,
        job_description=description,
        url=raw.url.strip(),
        platform=platform,
        date_posted=date_posted,
        easy_apply=raw.easy_apply,
        scraped_at=datetime.utcnow(),
        status="new",
    )


def stable_job_id(title: str, company: str, location: str) -> str:
    normalized_company = normalize_company_for_hash(company)
    key = "|".join([title.lower().strip(), normalized_company, location.lower().strip()])
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def normalize_title(title: str) -> str:
    cleaned = clean_display(title)
    suffix_patterns = [
        r"\s*\((?:remote|hybrid|onsite|on-site)\)\s*$",
        r"\s*[-|]\s*(?:remote|hybrid|onsite|on-site)\s*$",
        r"\s*[-|]\s*(?:us|usa|united states)\s*$",
        r"\s*,\s*(?:remote|hybrid|united states|usa)\s*$",
    ]
    for pattern in suffix_patterns:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
    return clean_display(cleaned)


def normalize_company_for_hash(company: str) -> str:
    cleaned = clean_display(company).lower()
    cleaned = re.sub(r"\b(?:inc|incorporated|llc|l\.l\.c|corp|corporation|co|company|ltd|limited)\b\.?", "", cleaned)
    cleaned = re.sub(r"[^\w\s]", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def clean_display(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def strip_html(value: str) -> str:
    soup = BeautifulSoup(value or "", "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return clean_display(soup.get_text(" "))


def parse_date(value: str | None) -> datetime | None:
    text = clean_display(value)
    if not text:
        return None
    lowered = text.lower()
    now = datetime.utcnow()
    if lowered in {"today", "just posted", "posted today"}:
        return now
    if lowered in {"yesterday", "1 day ago", "a day ago"}:
        return now - timedelta(days=1)

    relative = re.search(r"(\d+)\s*(minute|hour|day|week|month)s?\s+ago", lowered)
    if relative:
        amount = int(relative.group(1))
        unit = relative.group(2)
        if unit == "minute":
            return now - timedelta(minutes=amount)
        if unit == "hour":
            return now - timedelta(hours=amount)
        if unit == "day":
            return now - timedelta(days=amount)
        if unit == "week":
            return now - timedelta(weeks=amount)
        if unit == "month":
            return now - timedelta(days=amount * 30)

    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%b %d, %Y", "%B %d, %Y", "%a, %d %b %Y %H:%M:%S %Z"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass

    for fmt in ("%b %d", "%B %d"):
        try:
            parsed = datetime.strptime(text, fmt).replace(year=now.year)
            if parsed > now + timedelta(days=1):
                parsed = parsed.replace(year=now.year - 1)
            return parsed
        except ValueError:
            pass

    iso_match = re.search(r"\d{4}-\d{2}-\d{2}", text)
    if iso_match:
        try:
            return datetime.fromisoformat(iso_match.group(0))
        except ValueError:
            return None
    return None
