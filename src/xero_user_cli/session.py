from __future__ import annotations

import contextlib
import fcntl
import logging
import shutil
from pathlib import Path

from browserforge.fingerprints.generator import Screen
from playwright.sync_api import Error as PlaywrightError

from xero_user_cli.conf import BROWSER_DEFAULT_TIMEOUT_MS, BROWSER_HEIGHT, BROWSER_WIDTH, HUMAN_MOUSE_MAX_TIME_S, browser_headless, xero_cli_home
from xero_user_cli.profile_locks import remove_stale_chromium_locks

logger = logging.getLogger(__name__)


def profile_dir(name: str) -> Path:
    return xero_cli_home() / "profiles" / name


def clear_profile(name: str) -> None:
    shutil.rmtree(profile_dir(name), ignore_errors=True)


def _locks_dir() -> Path:
    return xero_cli_home() / "locks"


@contextlib.contextmanager
def session_lock(name: str):
    path = _locks_dir() / f"{name}.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


class XeroSession:
    """Camoufox-backed browser session with a persistent local profile."""

    def __init__(self, name: str):
        self.name = name
        self.context = None
        self.page = None
        self._browser_cm = None

    def __enter__(self) -> "XeroSession":
        self.ensure_browser()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def ensure_browser(self) -> None:
        if self.page is not None:
            try:
                if not self.page.is_closed() and self.context is not None and self.context.browser is not None and self.context.browser.is_connected():
                    return
            except PlaywrightError:
                pass
            self.close()

        from camoufox.sync_api import Camoufox

        path = profile_dir(self.name)
        path.mkdir(parents=True, exist_ok=True)
        remove_stale_chromium_locks(path)
        self._browser_cm = Camoufox(
            persistent_context=True,
            user_data_dir=str(path),
            headless=browser_headless(),
            humanize=HUMAN_MOUSE_MAX_TIME_S,
            screen=Screen(min_width=BROWSER_WIDTH, max_width=BROWSER_WIDTH, min_height=BROWSER_HEIGHT, max_height=BROWSER_HEIGHT),
            window=(BROWSER_WIDTH, BROWSER_HEIGHT),
            os="macos",
            locale="en-AU",
        )
        self.context = self._browser_cm.__enter__()
        self.context.set_default_timeout(BROWSER_DEFAULT_TIMEOUT_MS)
        self.context.set_default_navigation_timeout(BROWSER_DEFAULT_TIMEOUT_MS)
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        self.page.set_viewport_size({"width": BROWSER_WIDTH, "height": BROWSER_HEIGHT})
        logger.debug("Opened Camoufox profile %s", path)

    def close(self) -> None:
        try:
            if self.context:
                try:
                    self.context.close()
                except PlaywrightError:
                    pass
            if self._browser_cm:
                try:
                    self._browser_cm.__exit__(None, None, None)
                except PlaywrightError:
                    pass
        finally:
            self.context = None
            self.page = None
            self._browser_cm = None
