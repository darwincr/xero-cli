from __future__ import annotations

import re

from xero_user_cli.actions.auth import ensure_logged_in
from xero_user_cli.browser import goto_domcontentloaded, visible_text
from xero_user_cli.conf import XERO_HOME_URL


def page_summary(session, *, url: str | None = None, limit: int = 80, click_buttons: list[str] | None = None) -> dict:
    ensure_logged_in(session)
    page = session.page
    if url:
        goto_domcontentloaded(page, url)
        page.wait_for_timeout(5000)
    for click_button in click_buttons or []:
        pattern = re.compile(re.escape(click_button), re.I)
        control = page.get_by_role("button", name=pattern).or_(page.get_by_role("link", name=pattern)).first
        control.click()
        page.wait_for_timeout(2500)
    return {
        "ok": True,
        "url": page.url,
        "title": page.title(),
        "headings": _texts(page, "h1, h2, h3", limit=20),
        "buttons": _texts(page, "button, [role='button']", limit=limit),
        "links": _texts(page, "a, [role='link']", limit=limit),
        "link_targets": _link_targets(page, limit=limit),
        "inputs": _inputs(page, limit=limit),
        "labels": _texts(page, "label", limit=limit),
        "body_text": visible_text(page.locator("body").first),
    }


def _texts(page, selector: str, *, limit: int) -> list[str]:
    values = []
    locators = page.locator(selector)
    for idx in range(min(locators.count(), limit)):
        text = visible_text(locators.nth(idx))
        if text:
            values.append(text)
    return values


def _link_targets(page, *, limit: int) -> list[dict]:
    values = []
    locators = page.locator("a, [role='link']")
    for idx in range(min(locators.count(), limit)):
        locator = locators.nth(idx)
        text = visible_text(locator)
        href = _attr(locator, "href")
        if text or href:
            values.append({"text": text, "href": href})
    return values


def _inputs(page, *, limit: int) -> list[dict]:
    values = []
    locators = page.locator("input, textarea, select, [contenteditable='true'], [role='textbox'], [role='combobox'], [role='spinbutton']")
    for idx in range(min(locators.count(), limit)):
        locator = locators.nth(idx)
        entry = {
            "tag": _attr(locator, "tagName"),
            "type": _attr(locator, "type"),
            "name": _attr(locator, "name"),
            "id": _attr(locator, "id"),
            "placeholder": _attr(locator, "placeholder"),
            "aria_label": _attr(locator, "aria-label"),
            "role": _attr(locator, "role"),
            "text": visible_text(locator),
        }
        if not _looks_sensitive(entry):
            entry["value"] = _input_value(locator)
        values.append(entry)
    return values


def _looks_sensitive(entry: dict) -> bool:
    blob = f"{entry.get('id','')} {entry.get('name','')} {entry.get('type','')} {entry.get('aria_label','')}".lower()
    return any(word in blob for word in ("password", "passwd", "secret", "mfa", "2fa", "code", "otp", "token"))


def _input_value(locator) -> str:
    for expr in ("el => el.value", "el => el.textContent", "el => el.innerText"):
        try:
            value = locator.evaluate(expr, timeout=400)
            if value:
                return value
        except Exception:  # noqa: BLE001
            continue
    return ""


def _attr(locator, name: str) -> str:
    if name == "tagName":
        try:
            return locator.evaluate("el => el.tagName.toLowerCase()", timeout=500) or ""
        except Exception:  # noqa: BLE001
            return ""
    try:
        return locator.get_attribute(name, timeout=500) or ""
    except Exception:  # noqa: BLE001
        return ""
