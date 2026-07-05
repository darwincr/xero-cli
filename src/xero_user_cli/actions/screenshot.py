from __future__ import annotations

from pathlib import Path

from playwright.sync_api import Error as PlaywrightError

from xero_user_cli.exceptions import ScreenshotError


def take_screenshot(session, *, output: Path) -> dict:
    page = session.page
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        page.screenshot(path=str(output))
    except PlaywrightError as exc:
        raise ScreenshotError(f"Could not take browser screenshot: {exc}") from exc
    return {
        "ok": True,
        "path": str(output),
        "url": page.url,
        "bytes": output.stat().st_size,
    }
