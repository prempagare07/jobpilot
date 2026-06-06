"""Scrapers for supported job platforms."""

from backend.scrapers.base import BaseScraper, RawJob
from backend.scrapers.dedup import DedupEngine
from backend.scrapers.indeed import IndeedScraper
from backend.scrapers.jobright import JobrightScraper, TokenExpiredError
from backend.scrapers.linkedin import LinkedInScraper
from backend.scrapers.monster import MonsterScraper
from backend.scrapers.normalizer import NormalizedJob, normalize
from backend.scrapers.simplify import SimplifyScraper

__all__ = [
    "BaseScraper",
    "DedupEngine",
    "IndeedScraper",
    "JobRightScraper",
    "JobrightScraper",
    "LinkedInScraper",
    "MonsterScraper",
    "NormalizedJob",
    "RawJob",
    "SimplifyScraper",
    "TokenExpiredError",
    "normalize",
]

JobRightScraper = JobrightScraper
