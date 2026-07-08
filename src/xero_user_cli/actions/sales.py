from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from playwright.sync_api import Error as PlaywrightError

from xero_user_cli.actions.auth import current_page_summary, ensure_logged_in
from xero_user_cli.browser import first_visible, goto_domcontentloaded, human_fill
from xero_user_cli.conf import HUMAN_TYPE_DELAY_MS, XERO_CREATE_INVOICE_URL
from xero_user_cli.exceptions import ElementNotFoundError


@dataclass
class InvoiceForm:
    contact: str | None = None
    date: str | None = None
    due_date: str | None = None
    invoice_number: str | None = None
    reference: str | None = None
    line_description: str | None = None
    quantity: str | None = None
    unit_price: str | None = None
    account: str | None = None
    tax_rate: str | None = None


def create_invoice(session, form: InvoiceForm) -> dict:
    ensure_logged_in(session)
    page = session.page
    goto_domcontentloaded(page, XERO_CREATE_INVOICE_URL)
    page.wait_for_timeout(2000)
    if not _looks_like_invoice_form(page):
        _open_new_invoice_form(page)
        page.wait_for_timeout(2000)

    changed = _fill_invoice_fields(page, form)
    return {
        "ok": True,
        "submitted": False,
        "url": page.url,
        "field_values": _field_values(page),
        "changed": changed,
        "summary": current_page_summary(session),
    }


def _open_new_invoice_form(page) -> None:
    button = first_visible(
        page,
        [
            lambda p: p.get_by_role("link", name=re.compile(r"new invoice|add invoice|\+\s*new", re.I)),
            lambda p: p.get_by_role("button", name=re.compile(r"new invoice|add invoice|\+\s*new", re.I)),
            lambda p: p.locator('a[href*="/invoicing"]'),
            lambda p: p.locator('a[href*="AccountsReceivable"][href*="Edit"]'),
            lambda p: p.locator('[data-automationid*="new" i]').get_by_text(re.compile(r"invoice|new", re.I)),
        ],
        timeout_ms=2500,
    )
    if button is None:
        raise ElementNotFoundError("Could not find New invoice control")
    button.click()


def _looks_like_invoice_form(page) -> bool:
    try:
        return first_visible(page, [lambda p: p.locator("#InvoiceDateInput")], timeout_ms=1500) is not None
    except PlaywrightError:
        return False


def _fill_invoice_fields(page, form: InvoiceForm) -> dict:
    changed = {}
    for name, value in {
        "contact": form.contact,
        "date": form.date,
        "due_date": form.due_date,
        "invoice_number": form.invoice_number,
        "reference": form.reference,
        "line_description": form.line_description,
        "quantity": form.quantity,
        "unit_price": form.unit_price,
        "account": form.account,
        "tax_rate": form.tax_rate,
    }.items():
        if value is None:
            continue
        if _fill_field(page, name, value):
            changed[name] = value
    return changed


def _fill_field(page, name: str, value: str) -> bool:
    if name in {"account", "tax_rate"}:
        return _fill_grid_combobox(page, name, value)
    if name in {"date", "due_date"}:
        value = _to_xero_date(value)
    locator = first_visible(page, _field_locators(name), timeout_ms=1500)
    if locator is None:
        return False
    human_fill(locator, value)
    if name in {"quantity", "unit_price"}:
        # Numeric grid fields revert to their formatted default unless committed
        # via blur before a later cell is activated.
        page.keyboard.press("Tab")
        page.wait_for_timeout(200)
    if name == "contact":
        _select_contact(page, value)
    return True


def _select_contact(page, value: str) -> None:
    """Resolve the contact autocompleter and dismiss its portal dropdown.

    The contact picker is a separate "contacts MFE" whose dropdown renders in a
    portal that intercepts pointer events across the whole form until closed, so
    it must be resolved/dismissed before any later field is touched.
    """
    # Wait for the async contacts search to stop loading.
    try:
        page.locator(".contacts-mfe-loader").wait_for(state="hidden", timeout=4000)
    except PlaywrightError:
        pass
    partial = re.compile(re.escape(value), re.I)
    contact_option = first_visible(
        page,
        [
            lambda p: p.locator('.contacts-mfe-dropdown-layout [role="option"]').filter(has_text=partial).first,
            lambda p: p.locator('[role="option"]').filter(has_text=partial).first,
            lambda p: p.locator(".contacts-mfe-dropdown-layout").get_by_text(partial).first,
        ],
        timeout_ms=2500,
    )
    if contact_option is not None:
        contact_option.click()
    else:
        page.keyboard.press("Enter")
    # Ensure the portal dropdown is closed so it does not intercept later clicks.
    page.keyboard.press("Escape")


_GRID_COMBOBOX_KEYS = {"account": "account", "tax_rate": "taxRate"}


def _fill_grid_combobox(page, name: str, value: str) -> bool:
    col = _GRID_COMBOBOX_KEYS[name]
    base = f"InvoiceTable--line-item-grid--{col}"
    wrapper = page.locator(f'[data-automationid="{base}--search-field"]').first
    inp = page.locator(f'[data-automationid="{base}--search-field--input"]').first
    try:
        wrapper.wait_for(state="visible", timeout=2000)
    except PlaywrightError:
        return False
    # Activate the cell into edit mode by clicking the visible overlay/wrapper.
    activated = False
    for target in (
        page.locator(f'[data-automationid="{base}--value-display"]').first,
        wrapper,
    ):
        try:
            target.click(timeout=1500)
            activated = True
            break
        except PlaywrightError:
            continue
    if not activated:
        return False
    page.wait_for_timeout(400)
    try:
        inp.fill("")
    except PlaywrightError:
        page.keyboard.press("Meta+A")
        page.keyboard.press("Backspace")
    try:
        inp.press_sequentially(value, delay=HUMAN_TYPE_DELAY_MS)
    except PlaywrightError:
        return False
    page.wait_for_timeout(800)
    option = first_visible(page, _option_locators(value), timeout_ms=1500)
    if option is not None:
        option.click()
    else:
        # Fall back to the first suggestion: typing a partial query already
        # filters the Xero autocompleter, so the first option is the best match.
        first_option = first_visible(page, _first_option_locators(), timeout_ms=800)
        if first_option is not None:
            first_option.click()
        else:
            page.keyboard.press("Enter")
    page.keyboard.press("Escape")
    return True


def _first_option_locators():
    return [
        lambda p: p.locator('[role="option"]').first,
        lambda p: p.locator('[data-automationid*="autocompleter" i] [data-automationid*="option" i]').first,
        lambda p: p.locator('[data-automationid*="option" i], [data-automationid*="suggestion" i]').first,
    ]


def _to_xero_date(value: str) -> str:
    """Convert an ISO date (YYYY-MM-DD) to Xero's display format (e.g. 8 Jul 2026)."""
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").strftime("%-d %b %Y")
    except ValueError:
        return value


def _field_locators(name: str):
    locators = {
        "contact": [
            lambda p: p.locator('[data-automationid="contacts-picker-search-field--input"]'),
            lambda p: p.get_by_label("Contact", exact=True),
            lambda p: p.locator('[data-automationid*="contact" i] input'),
        ],
        "date": [
            lambda p: p.locator("#InvoiceDateInput"),
            lambda p: p.get_by_label(re.compile(r"^issue date$|^date$|invoice date", re.I)),
            lambda p: p.locator('[data-automationid*="date" i] input').first,
        ],
        "due_date": [
            lambda p: p.locator("#DueDateInput"),
            lambda p: p.get_by_label(re.compile(r"^due date$", re.I)),
            lambda p: p.locator('[data-automationid*="due" i] input'),
        ],
        "invoice_number": [
            lambda p: p.get_by_label(re.compile(r"^invoice number$", re.I)),
            lambda p: p.locator('[data-automationid*="invoice-number" i] input'),
            lambda p: p.get_by_label(re.compile(r"invoice\s*(no|number|#)", re.I)),
        ],
        "reference": [
            lambda p: p.get_by_label("Reference", exact=True),
            lambda p: p.locator('[data-automationid*="reference" i] input'),
        ],
        "line_description": [
            lambda p: p.locator('[data-automationid="InvoiceTable--line-item-grid--description--input"]').first,
            lambda p: p.locator('textarea[id^="description-input-"]').first,
            lambda p: p.get_by_label(re.compile(r"^description$", re.I)).first,
        ],
        "quantity": [
            lambda p: p.locator('[data-automationid="InvoiceTable--line-item-grid--quantity--input"]').first,
            lambda p: p.get_by_label(re.compile(r"^qty\.?$", re.I)).first,
        ],
        "unit_price": [
            lambda p: p.locator('[data-automationid="InvoiceTable--line-item-grid--unitAmount--input"]').first,
            lambda p: p.get_by_label(re.compile(r"^price$", re.I)).first,
        ],
        "account": [
            lambda p: p.locator('[data-automationid="InvoiceTable--line-item-grid--account--search-field--input"]').first,
            lambda p: p.get_by_label("Account", exact=True),
        ],
        "tax_rate": [
            lambda p: p.locator('[data-automationid="InvoiceTable--line-item-grid--taxRate--search-field--input"]').first,
            lambda p: p.get_by_label(re.compile(r"^tax rate$", re.I)).first,
        ],
    }
    return locators[name]


def _option_locators(value: str):
    partial = re.compile(re.escape(value), re.I)
    exact = re.compile(rf"^{re.escape(value)}$", re.I)
    return [
        lambda p: p.locator('[role="option"]').filter(has_text=partial).first,
        lambda p: p.locator('[data-automationid*="autocompleter" i] [data-automationid*="option" i]').filter(has_text=partial).first,
        lambda p: p.locator('[data-automationid*="option" i], [data-automationid*="suggestion" i]').filter(has_text=partial).first,
        lambda p: p.get_by_role("option", name=partial).first,
        lambda p: p.locator('[role="listbox"], [role="menu"]').get_by_text(partial).first,
        lambda p: p.get_by_role("option", name=exact).first,
        lambda p: p.get_by_text(exact).first,
    ]


def _field_values(page) -> dict:
    return {
        "contact": _value(page, _field_locators("contact")),
        "date": _value(page, _field_locators("date")),
        "due_date": _value(page, _field_locators("due_date")),
        "invoice_number": _value(page, _field_locators("invoice_number")),
        "reference": _value(page, _field_locators("reference")),
        "line_description": _value(page, _field_locators("line_description")),
        "quantity": _value(page, _field_locators("quantity")),
        "unit_price": _value(page, _field_locators("unit_price")),
        "account": _value(page, _field_locators("account")),
        "tax_rate": _value(page, _field_locators("tax_rate")),
    }


def _value(page, locators) -> str:
    locator = first_visible(page, locators, timeout_ms=300)
    if locator is None:
        return ""
    try:
        return locator.evaluate("el => el.value || el.textContent || ''", timeout=400) or ""
    except PlaywrightError:
        return ""
