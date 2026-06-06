from __future__ import annotations

import asyncio
import os
from pathlib import Path

from playwright.async_api import async_playwright

try:
    from playwright_stealth import stealth_async
    HAS_STEALTH = True
except ImportError:
    stealth_async = None
    HAS_STEALTH = False

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = PROJECT_ROOT / ".env"
PROFILE_DIR = PROJECT_ROOT / "data" / "jobright_profile"


def update_env_file(session_id: str, cookie_string: str) -> None:
    if not ENV_PATH.exists():
        return
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines(keepends=True)
    new_lines = []
    found_session = found_cookie = False
    for line in lines:
        if line.startswith("JOBRIGHT_SESSION_ID="):
            new_lines.append(f"JOBRIGHT_SESSION_ID={session_id}\n")
            found_session = True
        elif line.startswith("JOBRIGHT_COOKIE="):
            new_lines.append(f"JOBRIGHT_COOKIE={cookie_string}\n")
            found_cookie = True
        else:
            new_lines.append(line)
    if not found_session:
        new_lines.append(f"JOBRIGHT_SESSION_ID={session_id}\n")
    if not found_cookie:
        new_lines.append(f"JOBRIGHT_COOKIE={cookie_string}\n")
    ENV_PATH.write_text("".join(new_lines), encoding="utf-8")
    os.environ["JOBRIGHT_SESSION_ID"] = session_id
    os.environ["JOBRIGHT_COOKIE"] = cookie_string
    try:
        from backend.config import get_settings
        get_settings.cache_clear()
    except Exception:
        pass
    print(f"[jobright_auth] .env updated — SESSION_ID: {session_id[:16]}...")


async def extract_cookies(context) -> tuple[str, str]:
    """Extract SESSION_ID and full cookie string from browser context."""
    cookies = await context.cookies()
    cookie_dict = {c["name"]: c["value"] for c in cookies}
    session_id = cookie_dict.get("SESSION_ID", "")
    if not session_id:
        print(f"[jobright_auth] SESSION_ID missing. Got keys: {list(cookie_dict.keys())}")
        return "", ""
    priority = ["SESSION_ID", "_ga", "_gcl_au", "__stripe_mid",
                "_tt_enable_cookie", "_ttp", "_uetsid", "_uetvid"]
    parts = [f"{n}={cookie_dict[n]}" for n in priority if n in cookie_dict]
    for name, value in cookie_dict.items():
        if name not in priority:
            parts.append(f"{name}={value}")
    return session_id, "; ".join(parts)


async def headless_refresh() -> tuple[str, str]:
    """
    Use saved persistent profile to silently get fresh cookies.
    Works only after first_time_setup() has been run once.
    """
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=True,
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/148.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )
        page = await context.new_page()
        if HAS_STEALTH and stealth_async:
            await stealth_async(page)
        else:
            await apply_stealth_fallback(page)

        print("[jobright_auth] Loading jobs page with saved profile...")
        await page.goto(
            "https://jobright.ai/jobs/recommend",
            wait_until="domcontentloaded",
            timeout=30000,
        )
        await page.wait_for_timeout(3000)

        url = page.url
        print(f"[jobright_auth] Landed on: {url}")

        if "jobs" not in url and "recommend" not in url:
            print("[jobright_auth] Not logged in with saved profile")
            await context.close()
            return "", ""

        session_id, cookie_string = await extract_cookies(context)
        await context.close()
        return session_id, cookie_string


async def first_time_setup() -> bool:
    """
    Open VISIBLE browser. User logs in manually (Google or email).
    Session saved to PROFILE_DIR permanently.
    """
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    print("\n" + "="*60)
    print("FIRST TIME SETUP")
    print("="*60)
    print("A browser will open at jobright.ai")
    print("Log in using Google or email/password.")
    print("Wait until you see your job recommendations page.")
    print("Then come back here and press Enter.")
    print("="*60 + "\n")

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=False,
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/148.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = await context.new_page()

        # Go directly to the URL that triggers the login modal
        await page.goto(
            "https://jobright.ai/?from=homepage",
            wait_until="domcontentloaded",
            timeout=30000,
        )
        await page.wait_for_timeout(2000)

        print("[jobright_auth] Browser opened. Please log in now...")
        print("Press Enter here AFTER you can see your job recommendations.")

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, input, "")

        url = page.url
        print(f"[jobright_auth] Current URL: {url}")

        session_id, cookie_string = await extract_cookies(context)
        await context.close()

        if not session_id:
            print("[jobright_auth] No SESSION_ID found — did you complete login?")
            return False

        update_env_file(session_id, cookie_string)
        print(f"[jobright_auth] Profile saved to: {PROFILE_DIR}")
        print("[jobright_auth] First-time setup complete!")
        return True


async def refresh_session() -> bool:
    """
    Called by JobrightScraper on 401.
    Uses saved profile silently. Falls back to first_time_setup if needed.
    """
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    profile_files = list(PROFILE_DIR.glob("**/*"))
    has_profile = len(profile_files) > 2  # more than just empty dirs

    if has_profile:
        print("[jobright_auth] Trying headless refresh with saved profile...")
        session_id, cookie_string = await headless_refresh()
        if session_id:
            update_env_file(session_id, cookie_string)
            return True
        print("[jobright_auth] Headless refresh failed — profile may be stale")

    # Profile missing or stale — need manual login
    print("[jobright_auth] Running first-time setup...")
    return await first_time_setup()


async def refresh_session_headless_only() -> bool:
    """
    Non-interactive refresh for scheduled scrapes.
    Returns False if no saved profile exists or the saved profile is stale.
    """
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    profile_files = list(PROFILE_DIR.glob("**/*"))
    has_profile = len(profile_files) > 2
    if not has_profile:
        print("[jobright_auth] No saved Jobright browser profile; run first-time setup manually")
        return False
    session_id, cookie_string = await headless_refresh()
    if not session_id:
        return False
    update_env_file(session_id, cookie_string)
    return True


async def apply_stealth_fallback(page) -> None:
    try:
        from playwright_stealth import Stealth

        await Stealth().apply_stealth_async(page)
    except Exception:
        return


async def main() -> None:
    success = await refresh_session()
    if success:
        print("\n[jobright_auth] Ready — run your scraper now")
    else:
        print("\n[jobright_auth] Setup failed")


if __name__ == "__main__":
    asyncio.run(main())
