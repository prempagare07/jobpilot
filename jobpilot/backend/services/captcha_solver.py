from __future__ import annotations

from typing import Any


"""
CAPTCHA handling policy for JobPilot.

JobPilot does not bypass CAPTCHA challenges. When a job board presents a
CAPTCHA, the application workflow pauses in a visible browser so the user can
complete the challenge, review the form, and submit manually. These helpers are
kept as compatibility stubs for older call sites and always return "not solved".
"""


async def solve_recaptcha_v2_audio(page: Any, log: Any | None = None) -> bool:
    _log(log, "reCAPTCHA detected; automatic solving is disabled. Waiting for human completion.")
    return False


async def solve_hcaptcha_audio(page: Any, log: Any | None = None) -> bool:
    _log(log, "hCaptcha detected; automatic solving is disabled. Waiting for human completion.")
    return False


class CaptchaSolverDisabled:
    @property
    def enabled(self) -> bool:
        return False

    async def solve_recaptcha_v3(
        self,
        site_key: str,
        page_url: str,
        action: str = "submit",
        min_score: float = 0.7,
    ) -> str | None:
        return None


def get_solver() -> CaptchaSolverDisabled:
    return CaptchaSolverDisabled()


def get_2captcha_fallback() -> CaptchaSolverDisabled:
    return CaptchaSolverDisabled()


def _log(log: Any | None, message: str) -> None:
    if log is not None:
        try:
            log.info(message)
            return
        except Exception:
            pass
    print(f"[captcha] {message}")
