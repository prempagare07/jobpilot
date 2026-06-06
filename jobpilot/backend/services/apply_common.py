from __future__ import annotations

import json
from dataclasses import dataclass, field
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from playwright.async_api import BrowserContext, Page, async_playwright

from backend.config import PROJECT_ROOT


class AuditLog:
    """Append-only log of every action taken during an application attempt.

    Set ``flush_callback`` to a zero-argument callable and it will be invoked
    after every append so callers can persist ``entries`` to the DB in real time.
    Set ``status_callback`` to a ``Callable[[str], None]`` to receive live
    status transitions (e.g. "waiting_captcha", "running").
    """

    def __init__(
        self,
        flush_callback: "Callable[[], None] | None" = None,
        status_callback: "Callable[[str], None] | None" = None,
    ) -> None:
        self.entries: list[dict[str, Any]] = []
        self.flush_callback = flush_callback
        self.status_callback = status_callback

    def set_status(self, status: str) -> None:
        """Trigger a live status change without writing an audit entry."""
        if self.status_callback is not None:
            try:
                self.status_callback(status)
            except Exception:
                pass

    def _append(self, entry: dict[str, Any]) -> None:
        self.entries.append(entry)
        if self.flush_callback is not None:
            try:
                self.flush_callback()
            except Exception:
                pass  # never let a flush error abort the apply

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def navigate(self, url: str) -> None:
        self._append({"ts": self._now(), "event": "navigate", "url": url})

    def fill(self, field_label: str, value: str, field_type: str = "text", selector: str | None = None) -> None:
        entry: dict[str, Any] = {"ts": self._now(), "event": "fill", "field": field_label, "value": value, "field_type": field_type}
        if selector:
            entry["selector"] = selector
        self._append(entry)

    def upload(self, field_label: str, file_name: str) -> None:
        self._append({"ts": self._now(), "event": "upload", "field": field_label, "file": file_name})

    def step(self, action: str, page_number: int | None = None) -> None:
        entry: dict[str, Any] = {"ts": self._now(), "event": "step", "action": action}
        if page_number is not None:
            entry["page"] = page_number
        self._append(entry)

    def submit(self, url: str | None = None) -> None:
        entry: dict[str, Any] = {"ts": self._now(), "event": "submit"}
        if url:
            entry["url"] = url
        self._append(entry)

    def error(self, message: str) -> None:
        self._append({"ts": self._now(), "event": "error", "message": message})

    def info(self, message: str) -> None:
        self._append({"ts": self._now(), "event": "info", "message": message})

    def skip(self, field_label: str, reason: str) -> None:
        self._append({"ts": self._now(), "event": "skip", "field": field_label, "reason": reason})


@dataclass(frozen=True)
class ApplyResult:
    success: bool
    screenshot_path: str | None = None
    questions_encountered: list[str] = field(default_factory=list)
    questions_needing_human: list[str] = field(default_factory=list)
    error: str | None = None
    reason: str | None = None
    url: str | None = None
    ats_platform: str | None = None


async def apply_stealth(page: Page) -> None:
    """Apply playwright-stealth patches as a fallback when patchright is not used."""
    try:
        from playwright_stealth import stealth_async
        await stealth_async(page)
        return
    except Exception:
        pass
    try:
        from playwright_stealth import Stealth
        await Stealth().apply_stealth_async(page)
    except Exception:
        return


# ---------------------------------------------------------------------------
# Browser factory — priority order for anti-bot stealth:
#   1. patchright  (patches Chromium binary → best reCAPTCHA v3 scores ~0.7-0.9)
#   2. camoufox    (patched Firefox → hard to fingerprint)
#   3. playwright  (standard Chromium + playwright-stealth patches)
# ---------------------------------------------------------------------------

async def new_stealth_page(headless: bool = True) -> tuple[Any, BrowserContext, Page]:
    """
    Return (playwright_or_driver, context, page) using the best available stealth browser.
    Tries patchright → camoufox → playwright in that order.
    """
    # 1. patchright — drop-in Playwright replacement that patches Chrome at C++ level
    try:
        return await _new_patchright_page(headless)
    except Exception as exc:
        print(f"[stealth] patchright unavailable ({exc}), trying camoufox…")

    # 2. camoufox — stealthy Firefox browser
    try:
        return await _new_camoufox_page(headless)
    except Exception as exc:
        print(f"[stealth] camoufox unavailable ({exc}), falling back to playwright…")

    # 3. standard playwright + stealth patches
    return await _new_playwright_page(headless)


async def _new_patchright_page(headless: bool) -> tuple[Any, BrowserContext, Page]:
    from patchright.async_api import async_playwright as patchright_playwright
    pw = await patchright_playwright().start()
    browser = await pw.chromium.launch(
        headless=headless,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ],
    )
    ctx = await browser.new_context(
        viewport={"width": 1440, "height": 900},
        locale="en-US",
        timezone_id="America/Phoenix",
        permissions=["geolocation"],
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
    )
    # patchright already patches webdriver/runtime; add extra JS hardening
    await ctx.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
        window.chrome = {runtime: {}, loadTimes: function(){}, csi: function(){}, app: {}};
    """)
    page = await ctx.new_page()
    return pw, ctx, page


async def _new_camoufox_page(headless: bool) -> tuple[Any, BrowserContext, Page]:
    from camoufox.async_api import AsyncCamoufox
    # Camoufox returns an async context manager wrapping a Playwright browser
    # We adapt it to the (playwright, context, page) tuple our code expects
    browser_cm = AsyncCamoufox(headless=headless, humanize=True)
    browser = await browser_cm.__aenter__()
    ctx = await browser.new_context(
        viewport={"width": 1440, "height": 900},
        locale="en-US",
        timezone_id="America/Phoenix",
    )
    page = await ctx.new_page()
    # Stash the context manager so close_browser can exit it properly
    ctx._camoufox_cm = browser_cm  # type: ignore[attr-defined]
    return browser, ctx, page


async def _new_playwright_page(headless: bool) -> tuple[Any, BrowserContext, Page]:
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        headless=headless,
        args=["--disable-blink-features=AutomationControlled"],
    )
    ctx = await browser.new_context(
        viewport={"width": 1440, "height": 900},
        locale="en-US",
        timezone_id="America/Phoenix",
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
    )
    page = await ctx.new_page()
    await apply_stealth(page)
    return pw, ctx, page


async def close_browser(playwright: Any, context: BrowserContext) -> None:
    # Handle camoufox cleanup
    camoufox_cm = getattr(context, "_camoufox_cm", None)
    try:
        await context.close()
    except Exception:
        pass
    if camoufox_cm is not None:
        try:
            await camoufox_cm.__aexit__(None, None, None)
        except Exception:
            pass
        return
    try:
        await playwright.stop()
    except Exception:
        pass


def data_path(*parts: str) -> Path:
    path = PROJECT_ROOT / "data" / Path(*parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def screenshots_path(file_name: str) -> Path:
    path = PROJECT_ROOT / "data" / "screenshots" / file_name
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


async def load_cookies(context: BrowserContext, cookie_path: Path) -> bool:
    if not cookie_path.exists():
        return False
    try:
        payload = json.loads(cookie_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False

    cookies = payload.get("cookies") if isinstance(payload, dict) else payload
    if not isinstance(cookies, list) or not cookies:
        return False
    await context.add_cookies(cookies)
    return True


async def save_cookies(context: BrowserContext, cookie_path: Path) -> None:
    cookie_path.parent.mkdir(parents=True, exist_ok=True)
    cookies = await context.cookies()
    cookie_path.write_text(json.dumps(cookies, indent=2), encoding="utf-8")


async def visible_text(page: Page) -> str:
    try:
        return await page.locator("body").inner_text(timeout=3000)
    except Exception:
        return ""


def model_to_dict(obj: Any) -> dict[str, Any]:
    if isinstance(obj, dict):
        return dict(obj)
    return {
        key: value
        for key, value in vars(obj).items()
        if not key.startswith("_") and not callable(value)
    }
