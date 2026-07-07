from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError

from xero_user_cli.actions.auth import current_page_summary, ensure_logged_in
from xero_user_cli.browser import first_visible, goto_domcontentloaded, human_fill, require_visible, visible_text
from xero_user_cli.conf import XERO_CREATE_EXPENSE_URL, XERO_CREATE_MILEAGE_URL, XERO_EXPENSES_URL
from xero_user_cli.exceptions import ElementNotFoundError, ValidationError


@dataclass
class ExpenseForm:
    # Shared by expense and mileage claims.
    date: str | None = None
    description: str | None = None
    category: str | None = None
    assign_to: str | None = None
    label: str | None = None
    payment_due_date: str | None = None
    receipt_file: str | None = None
    submit: bool = False
    # Expense-only.
    amount: str | None = None
    merchant: str | None = None
    currency: str | None = None
    tax_rate: str | None = None
    # Mileage-only.
    distance: str | None = None
    rate: str | None = None


@dataclass
class ExpenseLineItem:
    description: str
    account: str
    tax_rate: str
    amount: str


def list_expenses(session, *, limit: int = 25) -> dict:
    ensure_logged_in(session)
    page = session.page
    goto_domcontentloaded(page, XERO_EXPENSES_URL)
    page.wait_for_timeout(1500)

    rows = _extract_table_rows(page, limit=limit)
    if not rows:
        rows = _extract_expense_cards(page, limit=limit)
    return {"ok": True, "url": page.url, "expenses": rows, "summary": current_page_summary(session) if not rows else None}


def create_expense(session, form: ExpenseForm) -> dict:
    _validate_expense_form(form)
    ensure_logged_in(session)
    page = session.page
    goto_domcontentloaded(page, XERO_CREATE_EXPENSE_URL)
    page.wait_for_timeout(1500)

    field_state = _fill_expense_fields(page, form)
    if field_state.get("spent_at", {}).get("status") in {"ambiguous", "unresolved"}:
        return {"ok": False, "submitted": False, "needs_spent_at_selection": True, "spent_at": field_state, "url": page.url, "field_values": _field_values(page)}
    _upload_receipt(page, form.receipt_file)
    if form.submit:
        _click_submit_expense(page)
        page.wait_for_timeout(1500)
        _raise_if_submit_blocked(page)
    return {"ok": True, "submitted": form.submit, "url": page.url, "field_values": _field_values(page), "field_state": field_state, "summary": current_page_summary(session)}


def create_mileage(session, form: ExpenseForm) -> dict:
    _validate_mileage_form(form)
    ensure_logged_in(session)
    page = session.page
    goto_domcontentloaded(page, XERO_CREATE_MILEAGE_URL)
    page.wait_for_timeout(1500)

    _fill_mileage_fields(page, form)
    _upload_receipt(page, form.receipt_file)
    if form.submit:
        _click_submit_expense(page)
        page.wait_for_timeout(1500)
        _raise_if_submit_blocked(page)
    return {"ok": True, "submitted": form.submit, "url": page.url, "field_values": _field_values(page), "summary": current_page_summary(session)}


def parse_line_items(values: list[str]) -> list[ExpenseLineItem]:
    items = []
    for idx, raw in enumerate(values, start=1):
        parts = [part.strip() for part in raw.split("|")]
        if len(parts) != 4 or any(not part for part in parts):
            raise ValidationError(f"--item #{idx} must be 'description|account|tax-rate|amount'")
        _validate_positive_decimal(f"item #{idx} amount", parts[3])
        items.append(ExpenseLineItem(description=parts[0], account=parts[1], tax_rate=parts[2], amount=parts[3]))
    return items


def edit_expense_detail(
    session,
    *,
    url: str,
    amount: str | None = None,
    category: str | None = None,
    tax_rate: str | None = None,
    items: list[ExpenseLineItem] | None = None,
) -> dict:
    items = items or []
    if amount is not None:
        _validate_positive_decimal("amount", amount)
    if items and any([amount, category, tax_rate]):
        raise ValidationError("--item cannot be combined with --amount, --category, or --tax-rate")
    if not any([amount, category, tax_rate, items]):
        raise ValidationError("At least one of --amount, --category, --tax-rate, or --item is required")

    ensure_logged_in(session)
    page = session.page
    target_url = url if url.startswith("http") else f"https://go.xero.com{url}"
    goto_domcontentloaded(page, target_url)
    page.wait_for_timeout(1500)

    _click_expense_edit(page)
    page.wait_for_timeout(1500)

    unapplied: list[str] = []
    if items:
        _fill_itemised_lines(page, items)
    elif amount and not _fill_targeted_or_by_label(page, [lambda p: p.locator("#expense-detail-subtotal")], ["purchase amount", "amount"], amount):
        unapplied.append("amount")
    if category and not _select_account(page, category):
        unapplied.append("category")
    if tax_rate and not _select_tax_rate(page, tax_rate):
        unapplied.append("tax-rate")
    if unapplied:
        raise ElementNotFoundError(
            "Could not apply requested change(s): "
            + ", ".join(unapplied)
            + ". The item may be locked/approved or the form layout changed; nothing was saved."
        )

    save_button = require_visible(page, [lambda p: p.get_by_role("button", name=re.compile(r"^save$", re.I))], label="expense save button", timeout_ms=5000)
    save_button.click()
    page.wait_for_timeout(3000)
    return {
        "ok": True,
        "changed": {"amount": amount, "category": category, "tax_rate": tax_rate, "items": items},
        "url": page.url,
        "summary": current_page_summary(session),
    }


def _validate_expense_form(form: ExpenseForm) -> None:
    _validate_common_fields(form)
    if form.amount is not None:
        _validate_positive_decimal("amount", form.amount)
    if not form.submit:
        return
    _require_fields(
        form,
        {
            "date": "--date is required when using --submit",
            "description": "--description is required when using --submit",
            "amount": "--amount is required when using --submit",
            "category": "--category is required when using --submit",
        },
    )


def _validate_mileage_form(form: ExpenseForm) -> None:
    _validate_common_fields(form)
    if form.distance is not None:
        _validate_positive_decimal("distance", form.distance)
    if form.rate is not None:
        _validate_positive_decimal("rate", form.rate)
    if not form.submit:
        return
    _require_fields(
        form,
        {
            "date": "--date is required when using --submit",
            "description": "--description is required when using --submit",
            "distance": "--distance is required when using --submit",
            "category": "--category is required when using --submit",
        },
    )


def _validate_common_fields(form: ExpenseForm) -> None:
    if form.date is not None:
        _validate_iso_date("date", form.date)
    if form.payment_due_date is not None:
        _validate_iso_date("payment-due-date", form.payment_due_date)
    if form.receipt_file is not None:
        _validate_receipt_file(form.receipt_file)
    for field in ("description", "category", "assign_to", "label", "merchant", "currency", "tax_rate", "distance", "rate"):
        value = getattr(form, field)
        if value is not None and not value.strip():
            raise ValidationError(f"--{field.replace('_', '-')} cannot be empty")


def _require_fields(form: ExpenseForm, messages: dict[str, str]) -> None:
    missing = [message for field, message in messages.items() if not (getattr(form, field) or "").strip()]
    if missing:
        raise ValidationError("; ".join(missing))


def _validate_iso_date(name: str, value: str) -> None:
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError(f"--{name} must be a valid date in YYYY-MM-DD format") from exc


def _validate_positive_decimal(name: str, value: str) -> None:
    try:
        number = Decimal(value.replace(",", ""))
    except (InvalidOperation, AttributeError) as exc:
        raise ValidationError(f"--{name} must be a positive decimal number") from exc
    if number <= 0:
        raise ValidationError(f"--{name} must be greater than zero")


def _validate_receipt_file(receipt_file: str) -> None:
    path = Path(receipt_file).expanduser()
    if not path.exists() or not path.is_file():
        raise ValidationError(f"--receipt-file does not exist or is not a file: {path}")


def _field_values(page) -> dict:
    """Read the current value of each known expense/mileage form field for verification.

    Safe to call on either the expense or mileage form: missing fields resolve to "".
    """
    selectors = {
        "description": "#description-input",
        "amount": "#expense-detail-subtotal",
        "spent_at": '#expense-detail-vendor-input, input[placeholder="Where was the money spent?"], input[placeholder="Select contact"]',
        "distance": "#expense-detail-distance-input",
        "rate": "#distance-rate-input",
        "account": 'input[placeholder="Select account"]',
        "assign_to": 'input[id^="billable-autocompleter"]',
        "label": "#expense-detail-label-autocompleter-input",
    }
    values: dict[str, str] = {}
    for name, selector in selectors.items():
        try:
            locator = page.locator(selector).first
            if locator.count() == 0:
                values[name] = ""
                continue
            raw = locator.evaluate("el => el.value", timeout=400)
            values[name] = raw or ""
        except PlaywrightError:
            values[name] = ""
    return values


def _extract_table_rows(page, *, limit: int) -> list[dict]:
    try:
        page.wait_for_selector("table, [role='table'], [role='row']", timeout=7000)
    except PlaywrightError:
        return []

    rows = []
    row_locators = page.locator("table tbody tr, [role='table'] [role='row'], [data-automationid*='row' i]")
    count = min(row_locators.count(), limit)
    for idx in range(count):
        row = row_locators.nth(idx)
        text = visible_text(row)
        if not text or re.search(r"date\s+description\s+amount", text, re.I):
            continue
        cells = [visible_text(row.locator("td, [role='cell'], [role='gridcell']").nth(cell_idx)) for cell_idx in range(row.locator("td, [role='cell'], [role='gridcell']").count())]
        cells = [cell for cell in cells if cell]
        rows.append({"text": text, "cells": cells})
    return rows[:limit]


def _extract_expense_cards(page, *, limit: int) -> list[dict]:
    text = visible_text(page.locator("body").first)
    if not text:
        return []

    start = re.search(r"\bQuick action\b", text, re.I)
    if start is None:
        start = re.search(r"\b(Awaiting payment|Submitted|Approved|Declined|Draft)\b.*?\bExpense details\b", text, re.I)
    if start is not None:
        text = text[start.end() :]
    urls = _expense_detail_urls(page, limit=limit)
    pattern = re.compile(
        r"(?P<leading_status>Awaiting payment|Submitted|Approved|Declined|Draft)?\s*"
        r"(?P<description>.+?)\s+DC\s+.*?\(You\)"
        r"\s*•\s*Spent on\s+(?P<date>[^•]+)"
        r"\s*•\s*(?P<category>[^•]+)"
        r"\s*•\s*(?P<tax_rate>[^•]+)"
        r"\s*•\s*(?P<claim_type>.*?)\s+"
        r"(?P<status>To be paid|Submitted|Approved|Declined|Draft|Paid)\s+"
        r"(?P<amount>[\d,]+\.\d{2})\b",
        re.I,
    )
    rows = []
    for idx, match in enumerate(pattern.finditer(text)):
        row = {key: " ".join(value.split()) for key, value in match.groupdict(default="").items()}
        row["description"] = re.sub(r"^(?:Awaiting payment\s+\d+\s+|\d+\s+|Pay\s+)", "", row["description"]).strip()
        row["merchant"] = ""
        row["url"] = urls[idx] if idx < len(urls) else _first_link_url(page, row["description"])
        rows.append(row)
        if len(rows) >= limit:
            break
    return rows


def _expense_detail_urls(page, *, limit: int) -> list[str]:
    urls = []
    links = page.locator('a[href*="/expenses/detail/"]')
    for idx in range(links.count()):
        try:
            href = links.nth(idx).get_attribute("href", timeout=500) or ""
        except PlaywrightError:
            continue
        if not href or href in urls:
            continue
        urls.append(href)
        if len(urls) >= limit:
            break
    return urls


def _fill_shared_expense_fields(page, form: ExpenseForm) -> None:
    if form.date:
        _fill_date(page, form.date)
    if form.description:
        _fill_targeted_or_by_label(
            page,
            [lambda p: p.locator("#description-input")],
            ["description", "what was this for", "purpose", "reference"],
            form.description,
        )
    if form.category:
        _select_account(page, form.category)
    if form.assign_to:
        _select_assign_to(page, form.assign_to)
    if form.label:
        _select_label(page, form.label)
    if form.payment_due_date:
        _fill_date(page, form.payment_due_date, due=True)


def _fill_expense_fields(page, form: ExpenseForm) -> dict:
    state: dict = {}
    if form.merchant:
        state["spent_at"] = _select_or_create_spent_at(page, form.merchant)
        if state["spent_at"].get("status") in {"ambiguous", "unresolved"}:
            return state
    _fill_shared_expense_fields(page, form)
    if form.currency:
        _select_currency(page, form.currency)
    if form.amount:
        _fill_targeted_or_by_label(
            page,
            [lambda p: p.locator("#expense-detail-subtotal")],
            ["purchase amount", "amount", "total", "value"],
            form.amount,
        )
    if form.tax_rate:
        _select_tax_rate(page, form.tax_rate)
    return state


def _fill_mileage_fields(page, form: ExpenseForm) -> None:
    _fill_shared_expense_fields(page, form)
    if form.distance:
        _fill_targeted_or_by_label(
            page,
            [lambda p: p.locator("#expense-detail-distance-input")],
            ["mileage to claim", "distance", "kilometres", "kilometers", "km", "miles"],
            form.distance,
        )
    if form.rate:
        _fill_targeted_or_by_label(
            page,
            [lambda p: p.locator("#distance-rate-input")],
            ["rate", "rate per km", "aud per km"],
            form.rate,
        )


def _upload_receipt(page, receipt_file: str | None) -> bool:
    if not receipt_file:
        return False
    path = Path(receipt_file).expanduser()
    if not path.exists() or not path.is_file():
        raise ElementNotFoundError(f"Receipt file does not exist: {path}")
    file_input = page.locator('input[type="file"], input[accept*="image" i], input[accept*="pdf" i]').first
    try:
        if file_input.count() == 0:
            file_input = None
    except PlaywrightError:
        file_input = None
    if file_input is None:
        upload_button = first_visible(
            page,
            [
                lambda p: p.get_by_role("button", name=re.compile("upload|attach|receipt|file", re.I)),
                lambda p: p.get_by_text(re.compile("upload|attach|receipt|file", re.I)),
            ],
            timeout_ms=1200,
        )
        if upload_button is not None:
            try:
                upload_button.click()
                page.wait_for_timeout(800)
            except PlaywrightError:
                pass
        file_input = page.locator('input[type="file"]').first
    try:
        file_input.set_input_files(str(path))
        page.wait_for_timeout(800)
        return True
    except PlaywrightError as exc:
        raise ElementNotFoundError("Could not find a receipt upload input on the expense form") from exc


def _first_link_url(page, text: str) -> str:
    if not text:
        return ""
    link = first_visible(page, [lambda p, text=text: p.get_by_role("link", name=re.compile(re.escape(text[:40]), re.I))], timeout_ms=400)
    if link is None:
        return ""
    try:
        return link.get_attribute("href", timeout=500) or ""
    except PlaywrightError:
        return ""


def _click_expense_edit(page) -> None:
    edit_button = first_visible(page, [lambda p: p.get_by_role("button", name=re.compile(r"^edit$", re.I))], timeout_ms=1200)
    if edit_button is not None:
        edit_button.click()
        return

    edit_item_factories = [
        lambda p: p.locator('button.xui-pickitem--body:has-text("Edit")'),
        lambda p: p.locator('.xui-pickitem--body:has-text("Edit")'),
        lambda p: p.get_by_role("button", name=re.compile(r"^edit$", re.I)),
        lambda p: p.get_by_text(re.compile(r"^edit$", re.I)),
    ]
    menu_factories = [
        lambda p: p.locator('button:has(.xui-touchtarget), [role="button"]:has(.xui-touchtarget)'),
        lambda p: p.locator('button[aria-haspopup], [role="button"][aria-haspopup]'),
        lambda p: p.locator('button[aria-label*="more" i], button[aria-label*="options" i], button[aria-label*="actions" i]'),
    ]
    for factory in menu_factories:
        menus = factory(page)
        for idx in reversed(range(min(menus.count(), 20))):
            menu = menus.nth(idx)
            try:
                if not menu.is_visible(timeout=300):
                    continue
                menu.click(timeout=1000)
                page.wait_for_timeout(500)
            except PlaywrightError:
                continue
            edit_item = first_visible(page, edit_item_factories, timeout_ms=800)
            if edit_item is not None:
                edit_item.click()
                return
            try:
                page.keyboard.press("Escape")
            except PlaywrightError:
                pass
    raise ElementNotFoundError("Could not find expense Edit action, including under the actions/three-dots menu")


def _fill_itemised_lines(page, items: list[ExpenseLineItem]) -> None:
    if not items:
        return
    itemise_button = first_visible(
        page,
        [
            lambda p: p.locator("#expense-detail--itemise"),
            lambda p: p.get_by_role("button", name=re.compile("itemise|itemize", re.I)),
        ],
        timeout_ms=1500,
    )
    if itemise_button is not None:
        itemise_button.click()
        page.wait_for_timeout(1200)

    _ensure_line_count(page, len(items))
    _trim_line_count(page, len(items))

    for idx, item in enumerate(items):
        _fill_line_item(page, idx, item)


def _ensure_line_count(page, count: int) -> None:
    while _line_descriptions(page).count() < count:
        add = first_visible(page, [lambda p: p.locator('input[placeholder="Add another item"]')], timeout_ms=1500)
        if add is None:
            raise ElementNotFoundError("Could not find Add another item field on itemised expense form")
        human_fill(add, "New item")
        page.keyboard.press("Enter")
        page.wait_for_timeout(1000)


def _trim_line_count(page, count: int) -> None:
    while _line_descriptions(page).count() > count:
        remove = page.locator('button[aria-label="Remove item"]').last
        try:
            remove.click(timeout=1000)
        except PlaywrightError as exc:
            raise ElementNotFoundError("Could not remove extra itemised expense line") from exc
        page.wait_for_timeout(800)


def _fill_line_item(page, idx: int, item: ExpenseLineItem) -> None:
    _fill_nth(_line_descriptions(page), idx, item.description, label="line description")
    _select_nth_autocomplete(page, _line_accounts(page), idx, item.account, label="line account")
    _select_nth_autocomplete(page, _line_tax_rates(page), idx, item.tax_rate, label="line tax rate")
    _fill_nth(_line_amounts(page), idx, item.amount, label="line amount")
    page.wait_for_timeout(500)


def _line_descriptions(page):
    return page.locator('input[placeholder="What was it for?"]')


def _line_accounts(page):
    return page.locator('input[placeholder="Select account"]')


def _line_tax_rates(page):
    return page.locator('input[placeholder="Select tax rate"]')


def _line_amounts(page):
    return page.locator('input[placeholder="0.00"]')


def _fill_nth(locator, idx: int, value: str, *, label: str) -> None:
    if locator.count() <= idx:
        raise ElementNotFoundError(f"Could not find {label} #{idx + 1}")
    human_fill(locator.nth(idx), value)


def _select_nth_autocomplete(page, locator, idx: int, value: str, *, label: str) -> None:
    if locator.count() <= idx:
        raise ElementNotFoundError(f"Could not find {label} #{idx + 1}")
    human_fill(locator.nth(idx), value)
    page.wait_for_timeout(800)
    option = first_visible(page, _autocomplete_option_locators(value), timeout_ms=2000)
    if option is None:
        raise ElementNotFoundError(f"Could not select {label} option: {value}")
    option.click()
    page.wait_for_timeout(600)


def _autocomplete_option_locators(value: str):
    pattern = re.compile(rf"^{re.escape(value)}$", re.I)
    partial = re.compile(re.escape(value), re.I)
    return [
        lambda p: p.get_by_role("option", name=pattern),
        lambda p: p.get_by_role("button", name=pattern),
        lambda p: p.locator(".xui-pickitem--body").get_by_text(pattern),
        lambda p: p.locator('[role="listbox"], [role="menu"], [data-automationid*="dropdown" i]').get_by_text(partial),
        lambda p: p.get_by_text(pattern),
    ]


def _fill_by_label(page, labels: list[str], value: str, *, required: bool) -> bool:
    locators = []
    for label in labels:
        pattern = re.compile(re.escape(label), re.I)
        locators.extend(
            [
                lambda p, pattern=pattern: p.get_by_label(pattern),
                lambda p, label=label: p.locator(f'input[placeholder*="{label}" i]'),
                lambda p, label=label: p.locator(f'textarea[placeholder*="{label}" i]'),
                lambda p, label=label: p.locator(f'input[name*="{label}" i]'),
                lambda p, label=label: p.locator(f'textarea[name*="{label}" i]'),
                lambda p, label=label: p.locator(f'[aria-label*="{label}" i]'),
            ]
        )
    locator = first_visible(page, locators, timeout_ms=1200)
    if locator is None:
        if required:
            raise ElementNotFoundError(f"Could not find field matching: {', '.join(labels)}")
        return False
    human_fill(locator, value)
    return True


def _fill_targeted_or_by_label(page, locators, labels: list[str], value: str) -> bool:
    locator = first_visible(page, locators, timeout_ms=1200)
    if locator is not None:
        human_fill(locator, value)
        return True
    return _fill_by_label(page, labels, value, required=False)


def _select_or_create_spent_at(page, value: str) -> dict:
    field = first_visible(
        page,
        [
            lambda p: p.locator("#expense-detail-vendor-input"),
            lambda p: p.locator('input[placeholder="Select contact"]'),
            lambda p: p.locator('input[placeholder="Where was the money spent?"]'),
            lambda p: p.get_by_role("textbox", name=re.compile("spent at|select contact", re.I)),
        ],
        timeout_ms=2000,
    )
    if field is None:
        raise ElementNotFoundError("Could not find Spent at field")

    human_fill(field, value)
    page.wait_for_timeout(1200)
    options = _visible_spent_at_options(page)
    contact_options = [option for option in options if not option.get("create")]
    create_options = [option for option in options if option.get("create")]

    exact = [option for option in contact_options if option["text"].casefold() == value.casefold()]
    if len(exact) == 1:
        exact[0]["locator"].click()
        page.wait_for_timeout(600)
        return _spent_at_result(page, "selected", exact[0]["text"], options)

    if len(contact_options) == 1:
        contact_options[0]["locator"].click()
        page.wait_for_timeout(600)
        return _spent_at_result(page, "selected", contact_options[0]["text"], options)

    if len(contact_options) > 1:
        return {"status": "ambiguous", "input": value, "options": _option_texts(contact_options), "create_options": _option_texts(create_options)}

    if create_options:
        create_options[0]["locator"].click()
        page.wait_for_timeout(800)
        return _spent_at_result(page, "created", value, options)

    page.keyboard.press("Enter")
    page.wait_for_timeout(800)
    return _spent_at_result(page, "created", value, [])


def _spent_at_result(page, status: str, value: str, options: list[dict]) -> dict:
    selected = _field_values(page).get("spent_at", "").strip()
    if not selected:
        return {"status": "unresolved", "input": value, "options": _option_texts(options)}
    return {"status": status, "value": selected, "options": _option_texts(options)}


def _visible_spent_at_options(page) -> list[dict]:
    option_locators = [
        lambda p: p.get_by_role("option"),
        lambda p: p.locator('.xui-pickitem--body, [role="listbox"] [role="button"], [data-automationid*="dropdown" i] [role="button"]'),
        lambda p: p.locator('[role="menu"] [role="menuitem"], [role="menu"] [role="button"]'),
    ]
    seen: set[str] = set()
    options: list[dict] = []
    for factory in option_locators:
        locators = factory(page)
        try:
            count = min(locators.count(), 20)
        except PlaywrightError:
            continue
        for idx in range(count):
            locator = locators.nth(idx)
            try:
                if not locator.is_visible(timeout=200):
                    continue
            except PlaywrightError:
                continue
            text = visible_text(locator)
            if not text or text in seen:
                continue
            seen.add(text)
            options.append({"text": text, "create": bool(re.search(r"add|create|new contact", text, re.I)), "locator": locator})
    return options


def _option_texts(options: list[dict]) -> list[str]:
    return [option["text"] for option in options]


def _select_or_fill(page, labels: list[str], value: str) -> bool:
    if _fill_by_label(page, labels, value, required=False):
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")
        return True
    for label in labels:
        locator = first_visible(page, [lambda p, label=label: p.get_by_text(re.compile(re.escape(label), re.I))], timeout_ms=700)
        if locator is None:
            continue
        try:
            locator.click()
            page.keyboard.insert_text(value)
            page.keyboard.press("ArrowDown")
            page.keyboard.press("Enter")
            return True
        except PlaywrightError:
            continue
    return False


def _fill_date(page, value: str, *, due: bool = False) -> bool:
    target_id = "#expense-detail-due-date-picker" if due else "#expense-detail-purchase-date-picker"
    button = first_visible(page, [lambda p: p.locator(target_id)], timeout_ms=1500)
    if button is None:
        date_re = re.compile(r"^\d{1,2}\s+[A-Z][a-z]{2}\s+\d{4}$")
        pickers = page.get_by_role("button", name=date_re)
        idx = 1 if due else 0
        try:
            if pickers.count() > idx:
                button = pickers.nth(idx)
        except PlaywrightError:
            button = None
    if button is None:
        labels = ["payment due", "due date"] if due else ["date", "spent on", "travelled on"]
        return _fill_by_label(page, labels, value, required=False)

    target_date = datetime.fromisoformat(value).date()
    current_text = visible_text(button)
    try:
        current_date = datetime.strptime(current_text, "%d %b %Y").date()
    except ValueError:
        current_date = date.today()

    button.click()
    page.wait_for_timeout(300)
    month_delta = (target_date.year - current_date.year) * 12 + target_date.month - current_date.month
    arrows = _date_picker_arrows(page)
    if arrows is None:
        return _fill_by_label(page, ["date", "spent on", "travelled on"], value, required=False)
    arrow_idx = 1 if month_delta > 0 else 0
    for _ in range(abs(month_delta)):
        arrows.nth(arrow_idx).click()
        page.wait_for_timeout(200)

    day = _enabled_date_cell(page, value)
    # Xero can render target dates from adjacent months as disabled cells. If
    # that happens, move one more month toward the target and try again.
    attempts = 0
    while day is None and attempts < 2:
        arrows.nth(arrow_idx).click()
        page.wait_for_timeout(200)
        day = _enabled_date_cell(page, value)
        attempts += 1
    if day is None:
        raise ElementNotFoundError(f"Could not select enabled date: {value}")
    day.click(timeout=5000)
    page.wait_for_timeout(500)
    return True


def _date_picker_arrows(page):
    arrow_factories = [
        lambda p: p.locator(".xui-datepicker--arrow-icon"),
        lambda p: p.get_by_role("button", name=re.compile("previous|next", re.I)),
        lambda p: p.locator('button[aria-label*="Previous" i], button[aria-label*="Next" i]'),
    ]
    for factory in arrow_factories:
        candidate = factory(page)
        try:
            if candidate.count() >= 2:
                return candidate
        except PlaywrightError:
            continue
    return None


def _enabled_date_cell(page, value: str):
    cells = page.locator(f'time[datetime="{value}"]').locator("..")
    try:
        count = cells.count()
    except PlaywrightError:
        return None
    for idx in range(count):
        cell = cells.nth(idx)
        try:
            disabled = (cell.get_attribute("aria-disabled", timeout=300) or "").lower() == "true"
            if not disabled and cell.is_visible(timeout=300):
                return cell
        except PlaywrightError:
            continue
    return None


def _select_currency(page, value: str) -> bool:
    trigger = first_visible(
        page,
        [
            lambda p: p.locator("#expense-detail-currency-selector--trigger"),
            lambda p: p.locator('button[aria-label*="currency" i]'),
            lambda p: p.locator("#expense-detail-subtotal").locator("xpath=ancestor-or-self::*[1]/preceding-sibling::*//button").first,
        ],
        timeout_ms=1500,
    )
    if trigger is None:
        return False
    trigger.click()
    page.wait_for_timeout(500)
    option = first_visible(
        page,
        [
            lambda p: p.get_by_role("button", name=re.compile(rf"^{re.escape(value)}$", re.I)),
            lambda p: p.get_by_role("option", name=re.compile(re.escape(value), re.I)),
            lambda p: p.get_by_text(re.compile(rf"^{re.escape(value)}$", re.I)),
        ],
        timeout_ms=1500,
    )
    if option is None:
        try:
            page.keyboard.press("Escape")
        except PlaywrightError:
            pass
        return False
    option.click()
    return True


def _select_assign_to(page, value: str) -> bool:
    return _select_combobox(
        page,
        [
            lambda p: p.locator('input[id^="billable-autocompleter"]'),
            lambda p: p.get_by_role("combobox", name=re.compile("assign to customer", re.I)),
        ],
        value,
        label="assign to customer",
    )


def _select_label(page, value: str) -> bool:
    return _select_combobox(
        page,
        [
            lambda p: p.locator("#expense-detail-label-autocompleter-input"),
            lambda p: p.get_by_role("combobox", name=re.compile(r"^label", re.I)),
        ],
        value,
        label="label",
    )


def _select_combobox(page, field_locators, value: str, *, label: str) -> bool:
    field = first_visible(page, field_locators, timeout_ms=1500)
    if field is None:
        return False
    human_fill(field, value)
    page.wait_for_timeout(600)
    option = first_visible(page, _autocomplete_option_locators(value), timeout_ms=1500)
    if option is None:
        # Dismiss the autocomplete dropdown so its overlay does not intercept
        # subsequent clicks on later fields. Clear the typed text so an invalid
        # optional value is not left behind in the field.
        try:
            page.keyboard.press("Escape")
        except PlaywrightError:
            pass
        page.wait_for_timeout(300)
        try:
            field.fill("")
        except PlaywrightError:
            pass
        return False
    option.click()
    page.wait_for_timeout(600)
    return True


def _select_account(page, value: str) -> bool:
    locator = first_visible(
        page,
        [
            lambda p: p.locator('input[placeholder="Select account"]'),
            lambda p: p.locator('input[aria-label="Select account"]'),
        ],
        timeout_ms=1500,
    )
    if locator is None:
        return _select_or_fill(page, ["account", "category", "expense category"], value)
    human_fill(locator, value)
    page.wait_for_timeout(500)
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")
    return True


def _tax_rate_option_locators(value: str):
    return [
        lambda p: p.get_by_role("button", name=re.compile(rf"^{re.escape(value)}$", re.I)),
        lambda p: p.get_by_role("option", name=re.compile(re.escape(value), re.I)),
        lambda p: p.get_by_role("menuitem", name=re.compile(re.escape(value), re.I)),
        lambda p: p.locator('[role="listbox"], [role="menu"], [data-automationid*="dropdown" i]').get_by_text(re.compile(re.escape(value), re.I)),
    ]


def _select_tax_rate(page, value: str) -> bool:
    """Select a tax rate. Returns True only when a concrete matching option was clicked.

    Xero renders the tax-rate control either as a button that opens a dropdown or as a
    searchable combobox/input. Both are tried. A blind "type and press Enter" is never
    treated as success, so callers can trust the return value and avoid saving a change
    that did not actually take.
    """
    # Strategy 1: a button that opens the tax-rate dropdown.
    trigger = first_visible(
        page,
        [
            lambda p: p.locator("#expense-detail-tax-selector--trigger"),
            lambda p: p.get_by_role("button", name=re.compile(r"select tax rate|tax rate|gst on expenses|bas excluded|no tax", re.I)),
        ],
        timeout_ms=1500,
    )
    if trigger is not None:
        trigger.click()
        page.wait_for_timeout(500)
        option = first_visible(page, _tax_rate_option_locators(value), timeout_ms=1500)
        if option is not None:
            option.click()
            return True
        # Close this dropdown before trying the combobox strategy.
        try:
            page.keyboard.press("Escape")
        except PlaywrightError:
            pass

    # Strategy 2: a searchable combobox/input (mirrors the account selector).
    field = first_visible(
        page,
        [
            lambda p: p.locator('input[placeholder*="tax rate" i]'),
            lambda p: p.locator('input[aria-label*="tax rate" i]'),
            lambda p: p.get_by_role("combobox", name=re.compile("tax", re.I)),
        ],
        timeout_ms=1200,
    )
    if field is not None:
        human_fill(field, value)
        page.wait_for_timeout(600)
        option = first_visible(page, _tax_rate_option_locators(value), timeout_ms=1500)
        if option is not None:
            option.click()
            return True

    # No concrete tax-rate option matching `value` could be selected.
    return False


def _click_submit_expense(page) -> None:
    button = require_visible(
        page,
        [
            lambda p: p.get_by_role("button", name=re.compile(r"^submit$", re.I)),
            lambda p: p.get_by_role("button", name=re.compile(r"submit|create|claim", re.I)),
            lambda p: p.locator('button[type="submit"]'),
            lambda p: p.get_by_role("button", name=re.compile(r"save", re.I)),
        ],
        label="expense submit button",
        timeout_ms=5000,
    )
    button.click()


def _raise_if_submit_blocked(page) -> None:
    text = visible_text(page.locator("body").first)
    blocked_messages = [
        "There was a problem",
        "Spent at is missing",
        "Must be greater than zero",
        "is missing",
    ]
    if any(message in text for message in blocked_messages):
        raise ValidationError("Xero blocked expense submission: " + text[:500])
