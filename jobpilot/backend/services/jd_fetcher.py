from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx

# Domains that render their JD via JavaScript — need Playwright
_JS_HEAVY = {"ashbyhq.com", "greenhouse.io", "lever.co", "myworkdayjobs.com", "workday.com", "bamboohr.com"}

# Selectors tried in order to extract the JD from the loaded page
_CONTENT_SELECTORS = [
    # Ashby
    "._content_1d7ow_19",
    "[class*='posting-description']",
    "[class*='job-description']",
    "[class*='jobDescription']",
    # Greenhouse
    "#content",
    ".job__description",
    # Lever
    ".section-wrapper",
    # Generic fallbacks
    "main",
    "article",
    "[role='main']",
]


# Phrases that strongly indicate we landed on an application form, not a JD overview.
_FORM_INDICATORS = [
    "first name", "last name", "email address", "phone number",
    "upload resume", "attach resume", "submit application",
    "apply with linkedin", "personal details", "powered by greenhouse",
    "powered by lever", "powered by workday", "résumé / cv",
    "allow us to process your personal information",
    "accepted file formats", "personal summary",
]

# Phrases that indicate we have real JD content.
_JD_INDICATORS = [
    "responsibilities", "qualifications", "requirements", "about the role",
    "what you'll do", "what we're looking for", "about us", "job description",
    "you will", "we are looking", "preferred qualifications", "nice to have",
    "minimum qualifications", "basic qualifications", "who you are",
]


def _needs_js(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return any(domain in host for domain in _JS_HEAVY)


def _looks_like_application_form(text: str) -> bool:
    """Return True if the text looks like an application form rather than a JD."""
    lower = text.lower()
    form_hits = sum(1 for phrase in _FORM_INDICATORS if phrase in lower)
    jd_hits = sum(1 for phrase in _JD_INDICATORS if phrase in lower)
    # Treat as form if ≥3 form indicators and more form hits than JD hits.
    return form_hits >= 3 and form_hits > jd_hits


def _strip_overview_tracking(url: str) -> str:
    """Return the job overview URL (not /application) without tracking params."""
    # Remove /application or /apply suffix so we land on the overview page
    base = re.sub(r"/(application|apply)\b.*", "", url, flags=re.IGNORECASE)
    # Drop query string tracking params (utm_source, etc.)
    return base.split("?")[0]


async def fetch_full_jd(url: str, timeout: float = 30.0) -> str | None:
    """
    Fetch the full job description from the source URL.
    Returns None if:
    - The page is unreachable
    - The scraped content looks like an application form (not an overview)
    - Content is too short to be useful
    """
    clean_url = _strip_overview_tracking(url)
    try:
        if _needs_js(clean_url):
            text = await _fetch_playwright(clean_url, timeout)
        else:
            text = await _fetch_httpx(clean_url, timeout)

        if text is None:
            return None

        if _looks_like_application_form(text):
            print(f"[jd_fetcher] skipped — page looks like an application form: {clean_url}")
            return None

        return text
    except Exception as exc:
        print(f"[jd_fetcher] failed url={clean_url} error={exc}")
        return None


async def _fetch_httpx(url: str, timeout: float) -> str | None:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }
    async with httpx.AsyncClient(headers=headers, timeout=timeout, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()
        html = response.text

    return _extract_text_from_html(html)


async def _fetch_playwright(url: str, timeout: float) -> str | None:
    from backend.services.apply_common import close_browser, new_stealth_page

    playwright, context, page = await new_stealth_page(headless=True)
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=int(timeout * 1000))
        # Give React/SPAs time to hydrate
        await page.wait_for_timeout(2500)

        # Try known content selectors
        for selector in _CONTENT_SELECTORS:
            try:
                el = page.locator(selector).first
                if await el.count():
                    text = (await el.inner_text()).strip()
                    if len(text) > 200:
                        return _clean_text(text)
            except Exception:
                continue

        # Fallback: full visible body text
        text = await page.locator("body").inner_text(timeout=5000)
        return _clean_text(text) if text and len(text) > 200 else None
    finally:
        await close_browser(playwright, context)


def _extract_text_from_html(html: str) -> str | None:
    """Minimal HTML → plain text without external dependencies."""
    # Remove script/style blocks
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    # Replace common block tags with newlines
    html = re.sub(r"<(br|p|li|h[1-6]|div|section|article)[^>]*>", "\n", html, flags=re.IGNORECASE)
    # Strip all remaining tags
    html = re.sub(r"<[^>]+>", " ", html)
    # Decode common HTML entities
    for entity, char in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&nbsp;", " "), ("&#39;", "'"), ("&quot;", '"')):
        html = html.replace(entity, char)
    return _clean_text(html)


def _clean_text(text: str) -> str:
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines)
