from __future__ import annotations

from collections.abc import Callable

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeoutError

from xero_user_cli.conf import HUMAN_TYPE_DELAY_MS
from xero_user_cli.exceptions import ElementNotFoundError

LocatorFactory = Callable[[Page], Locator]


def first_visible(page: Page, factories: list[LocatorFactory], *, timeout_ms: int = 1200) -> Locator | None:
    for factory in factories:
        locator = factory(page).first
        try:
            locator.wait_for(state="visible", timeout=timeout_ms)
            return locator
        except (PlaywrightTimeoutError, PlaywrightError):
            continue
    return None


def goto_domcontentloaded(page: Page, url: str) -> None:
    try:
        page.goto(url, wait_until="domcontentloaded")
    except PlaywrightTimeoutError:
        if page.url == "about:blank":
            raise
    try:
        page.wait_for_load_state("domcontentloaded")
    except PlaywrightTimeoutError:
        if page.url == "about:blank":
            raise


def require_visible(page: Page, factories: list[LocatorFactory], *, label: str, timeout_ms: int = 2500) -> Locator:
    locator = first_visible(page, factories, timeout_ms=timeout_ms)
    if locator is None:
        raise ElementNotFoundError(f"Could not find visible {label}")
    return locator


def human_fill(locator: Locator, text: str) -> None:
    locator.click()
    try:
        locator.fill("")
    except PlaywrightError:
        locator.press("Meta+A")
        locator.press("Backspace")
    locator.press_sequentially(text, delay=HUMAN_TYPE_DELAY_MS)


def visible_text(locator: Locator | None) -> str:
    if locator is None:
        return ""
    try:
        return " ".join(locator.inner_text(timeout=1000).split())
    except PlaywrightError:
        return ""
