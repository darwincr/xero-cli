from __future__ import annotations

import re
import sys
import time

from playwright.sync_api import Error as PlaywrightError

from xero_user_cli.browser import first_visible, goto_domcontentloaded, human_fill, require_visible, visible_text
from xero_user_cli.conf import BROWSER_LOGIN_TIMEOUT_MS, XERO_APP_BASE_URL, XERO_HOME_URL, xero_credentials
from xero_user_cli.exceptions import AuthenticationError, InteractiveAuthenticationRequired, MfaRequired

LOGIN_URL_MARKERS = ("login.xero.com", "identity.xero.com", "/signin", "/login")

EMAIL_LOCATORS = [
    lambda p: p.get_by_label(re.compile("email|username", re.I)),
    lambda p: p.locator('input[type="email"]'),
    lambda p: p.locator('input[name*="email" i]'),
    lambda p: p.locator('input[name*="username" i]'),
    lambda p: p.locator('input[id*="email" i]'),
    lambda p: p.locator('input[id*="username" i]'),
]
PASSWORD_LOCATORS = [
    lambda p: p.get_by_label(re.compile("password", re.I)),
    lambda p: p.locator('input[type="password"]'),
    lambda p: p.locator('input[name*="password" i]'),
    lambda p: p.locator('input[id*="password" i]'),
]
SUBMIT_LOCATORS = [
    lambda p: p.get_by_role("button", name=re.compile("log in|login|sign in|continue|next", re.I)),
    lambda p: p.locator('button[type="submit"]'),
    lambda p: p.locator('input[type="submit"]'),
]
AUTHENTICATED_LOCATORS = [
    lambda p: p.locator(f'a[href*="{XERO_APP_BASE_URL}/"]'),
    lambda p: p.locator(f'a[href*="{XERO_APP_BASE_URL}/expenses"]'),
    lambda p: p.locator('[data-automationid*="navigation" i]'),
    lambda p: p.get_by_role("heading", name=re.compile("expenses", re.I)),
    lambda p: p.locator('a[href*="/expenses/detail/create-new"]'),
    lambda p: p.locator('a[href*="/expenses"]'),
    lambda p: p.locator('text=/Dashboard|Business|Accounting|Contacts|Expenses|New expense|Create expense|Mileage/i'),
]
MFA_LOCATORS = [
    lambda p: p.locator('input[name*="code" i]'),
    lambda p: p.locator('input[id*="code" i]'),
    lambda p: p.locator('text=/multi-factor|authenticator|verification code|enter code|approve/i'),
]
MFA_CODE_LOCATORS = [
    lambda p: p.get_by_label(re.compile("code|verification|authentication|authenticator", re.I)),
    lambda p: p.locator('input[autocomplete="one-time-code"]'),
    lambda p: p.locator('input[inputmode="numeric"]'),
    lambda p: p.locator('input[placeholder*="code" i]'),
    lambda p: p.locator('input[aria-label*="code" i]'),
    lambda p: p.locator('input[name*="code" i]'),
    lambda p: p.locator('input[id*="code" i]'),
    lambda p: p.locator('input[type="tel"]'),
]
TRUST_DEVICE_LOCATORS = [
    lambda p: p.get_by_label(re.compile("trust|remember|don't ask|do not ask", re.I)),
    lambda p: p.get_by_role("checkbox", name=re.compile("trust|remember|don't ask|do not ask", re.I)),
    lambda p: p.locator('input[type="checkbox"]').filter(has_text=re.compile("trust|remember", re.I)),
]
TRUST_BUTTON_LOCATORS = [
    lambda p: p.get_by_role("button", name=re.compile("trust|remember|yes|continue", re.I)),
    lambda p: p.locator('button:has-text("Trust")'),
    lambda p: p.locator('button:has-text("Remember")'),
]


def _is_login_url(url: str) -> bool:
    lower = url.lower()
    return any(marker in lower for marker in LOGIN_URL_MARKERS)


def _is_xero_app_url(url: str) -> bool:
    return url.lower().startswith(XERO_APP_BASE_URL.lower())


def _looks_authenticated(session, *, timeout_ms: int = 1000) -> bool:
    page = session.page
    if not _is_xero_app_url(page.url) or _is_login_url(page.url):
        return False
    if first_visible(page, AUTHENTICATED_LOCATORS, timeout_ms=timeout_ms) is not None:
        return True
    # Xero can land on /homepage after login before the expense nav is visible.
    return "/homepage" in page.url.lower()


def ensure_logged_in(session, *, wait_for_manual_seconds: int = 120) -> dict:
    page = session.page
    goto_domcontentloaded(page, XERO_HOME_URL)
    if _looks_authenticated(session):
        return {"ok": True, "authenticated": True, "url": page.url}

    if _is_login_url(page.url) or first_visible(page, EMAIL_LOCATORS + PASSWORD_LOCATORS, timeout_ms=1500) is not None:
        _login_with_credentials(session)

    deadline = time.monotonic() + wait_for_manual_seconds
    while time.monotonic() < deadline:
        if _looks_authenticated(session, timeout_ms=800):
            return {"ok": True, "authenticated": True, "url": page.url}
        if _mfa_code_input(page, timeout_ms=300) is not None:
            raise MfaRequired("Xero requires an MFA code. Run `uv run xero-cli auth mfa CODE` to continue.")
        if _handle_trust_device_prompt(page):
            time.sleep(1)
            continue
        if _is_login_url(page.url) and first_visible(page, PASSWORD_LOCATORS, timeout_ms=300) is not None:
            _login_with_credentials(session)
        time.sleep(1)

    if _mfa_code_input(page, timeout_ms=500) is not None:
        raise MfaRequired("Xero requires an MFA code. Run `uv run xero-cli auth mfa CODE` to continue.")
    if first_visible(page, MFA_LOCATORS, timeout_ms=500) is not None:
        raise InteractiveAuthenticationRequired("Xero is waiting for manual MFA approval or another verification step in the Camoufox window")
    raise AuthenticationError(f"Xero did not reach an authenticated expenses page; current URL: {page.url}")


def auth_status(session) -> dict:
    page = session.page
    goto_domcontentloaded(page, XERO_HOME_URL)
    authenticated = _looks_authenticated(session)
    if authenticated:
        state = "logged_in"
    elif _mfa_code_input(page, timeout_ms=500) is not None:
        state = "mfa_required"
    else:
        state = "login_required"
    return {"ok": True, "authenticated": authenticated, "url": page.url, "state": state}


def submit_mfa_code(session, code: str, *, trust_device: bool = True, timeout: int = 120) -> dict:
    page = session.page
    if _looks_authenticated(session, timeout_ms=500):
        return {"ok": True, "authenticated": True, "url": page.url, "state": "logged_in"}

    code_input = _mfa_code_input(page, timeout_ms=5000)
    if code_input is None:
        raise MfaRequired("No MFA code input is visible in the current Xero browser session. Run `uv run xero-cli login` first.")

    human_fill(code_input, code)
    if trust_device:
        _select_trust_device(page)
    _click_submit(page)
    _wait_short(page)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _handle_trust_device_prompt(page):
            _wait_short(page)
        if _looks_authenticated(session, timeout_ms=800):
            return {"ok": True, "authenticated": True, "url": page.url, "state": "logged_in"}
        if _mfa_code_input(page, timeout_ms=300) is not None and first_visible(page, MFA_LOCATORS, timeout_ms=100) is not None:
            raise MfaRequired("Xero still requires MFA. The code may be invalid or expired; run `uv run xero-cli auth mfa CODE` again.")
        time.sleep(1)

    raise AuthenticationError(f"Xero did not complete MFA within {timeout} seconds; current URL: {page.url}")


def interactive_login(session, *, timeout: int = 300) -> dict:
    page = session.page
    goto_domcontentloaded(page, XERO_HOME_URL)
    print(
        "Complete Xero login in the Camoufox browser, including MFA and 'Trust this device' if prompted. "
        f"Waiting up to {timeout} seconds...",
        file=sys.stderr,
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            page.wait_for_load_state("domcontentloaded", timeout=5000)
        except PlaywrightError:
            pass
        if _looks_authenticated(session, timeout_ms=800):
            return {"ok": True, "authenticated": True, "url": page.url}
        time.sleep(2)
    raise InteractiveAuthenticationRequired(f"Xero interactive login was not completed within {timeout} seconds")


def _login_with_credentials(session) -> None:
    page = session.page
    user, password = xero_credentials()

    email = first_visible(page, EMAIL_LOCATORS, timeout_ms=2500)
    if email is not None:
        human_fill(email, user)

    password_input = first_visible(page, PASSWORD_LOCATORS, timeout_ms=1200)
    if password_input is None and email is not None:
        _click_submit(page)
        _wait_short(page)
        password_input = first_visible(page, PASSWORD_LOCATORS, timeout_ms=BROWSER_LOGIN_TIMEOUT_MS)

    if password_input is None:
        password_input = require_visible(page, PASSWORD_LOCATORS, label="password input", timeout_ms=BROWSER_LOGIN_TIMEOUT_MS)
    human_fill(password_input, password)
    _click_submit(page)
    _wait_short(page)


def _click_submit(page) -> None:
    submit = require_visible(page, SUBMIT_LOCATORS, label="login submit button", timeout_ms=5000)
    try:
        submit.click()
    except PlaywrightError:
        submit.press("Enter")


def _mfa_code_input(page, *, timeout_ms: int = 1000):
    locator = first_visible(page, MFA_CODE_LOCATORS, timeout_ms=timeout_ms)
    if locator is None:
        return None
    try:
        input_type = (locator.get_attribute("type", timeout=500) or "").lower()
        if input_type in {"hidden", "password", "email", "submit", "checkbox", "radio"}:
            return None
    except PlaywrightError:
        pass
    return locator


def _select_trust_device(page) -> bool:
    checkbox = first_visible(page, TRUST_DEVICE_LOCATORS, timeout_ms=700)
    if checkbox is None:
        return False
    try:
        if hasattr(checkbox, "is_checked") and not checkbox.is_checked(timeout=500):
            checkbox.check(timeout=1000)
        else:
            checkbox.click(timeout=1000)
        return True
    except PlaywrightError:
        try:
            checkbox.click(timeout=1000)
            return True
        except PlaywrightError:
            return False


def _handle_trust_device_prompt(page) -> bool:
    text = visible_text(page.locator("body").first)
    if not re.search(r"trust|remember|don't ask|do not ask", text, re.I):
        return False
    selected = _select_trust_device(page)
    button = first_visible(page, TRUST_BUTTON_LOCATORS, timeout_ms=700)
    if button is None:
        return selected
    try:
        button.click(timeout=1500)
        return True
    except PlaywrightError:
        return selected


def _wait_short(page) -> None:
    try:
        page.wait_for_load_state("domcontentloaded", timeout=10_000)
    except PlaywrightError:
        pass
    page.wait_for_timeout(800)


def current_page_summary(session) -> dict:
    page = session.page
    body = visible_text(page.locator("body").first)
    return {"url": page.url, "title": page.title(), "text": body[:2000]}
