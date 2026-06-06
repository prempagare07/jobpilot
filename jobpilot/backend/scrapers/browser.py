from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from playwright.async_api import Browser, BrowserContext, Page, async_playwright


async def apply_stealth(page: Page) -> None:
    try:
        from playwright_stealth import stealth_async
        await stealth_async(page)
    except ImportError:
        from playwright_stealth import Stealth

        await Stealth().apply_stealth_async(page)


@asynccontextmanager
async def stealth_page(headless: bool = True) -> AsyncIterator[Page]:
    playwright = await async_playwright().start()
    browser: Browser | None = None
    context: BrowserContext | None = None
    try:
        browser = await playwright.chromium.launch(headless=headless)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Apple Silicon Mac OS X 14_5) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
            ),
            viewport={"width": 1440, "height": 1000},
        )
        page = await context.new_page()
        await apply_stealth(page)
        yield page
    finally:
        if context:
            await context.close()
        if browser:
            await browser.close()
        await playwright.stop()
