from __future__ import annotations

import asyncio
import functools
import random
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import ParamSpec, TypeVar
from urllib.parse import urlencode

from bs4 import BeautifulSoup

MIN_DELAY = 4.0
MAX_DELAY = 10.0

P = ParamSpec("P")
T = TypeVar("T")


USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7_1) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6_1) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_7_1) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 11.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
]


@dataclass(frozen=True)
class RawJob:
    title: str
    company: str
    location: str
    description: str
    url: str
    platform: str
    date_posted: str
    easy_apply: bool


def retry(
    max_attempts: int = 3,
    backoff: float = 2.0,
) -> Callable[[Callable[P, Awaitable[T]]], Callable[P, Awaitable[T]]]:
    def decorator(func: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
        @functools.wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            last_error: BaseException | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except Exception as exc:
                    last_error = exc
                    if attempt >= max_attempts:
                        raise
                    await asyncio.sleep(backoff * attempt)
            raise RuntimeError("retry exhausted") from last_error

        return wrapper

    return decorator


class BaseScraper(ABC):
    platform: str
    min_delay: float = MIN_DELAY
    max_delay: float = MAX_DELAY

    @abstractmethod
    async def scrape(self, query: str, location: str, max_jobs: int) -> list[RawJob]:
        raise NotImplementedError

    async def wait(self) -> None:
        await asyncio.sleep(random.uniform(self.min_delay, self.max_delay))

    def random_user_agent(self) -> str:
        return random.choice(USER_AGENTS)

    def headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {
            "User-Agent": self.random_user_agent(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        if extra:
            headers.update(extra)
        return headers

    def log_request(self, url: str) -> None:
        print(f"[scraper] ts={datetime.utcnow().isoformat()}Z platform={self.platform} url={url}")

    @staticmethod
    def clean_text(value: str | None) -> str:
        if not value:
            return ""
        return " ".join(value.split())

    @staticmethod
    def text_from_html(html: str) -> str:
        soup = BeautifulSoup(html or "", "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        return " ".join(soup.get_text(" ").split())

    @staticmethod
    def url_with_params(url: str, **params: str | int | bool | None) -> str:
        clean_params = {key: value for key, value in params.items() if value is not None}
        return f"{url}?{urlencode(clean_params)}"


ScrapedJob = RawJob
