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
    force_create_spent_at: bool = False
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
    page.wait_for_timeout(4000)

    rows = _extract_table_rows(page, limit=limit)
    if not rows:
        rows = _extract_scoped_expense_cards(page, limit=limit)
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
        spent_at = field_state["spent_at"]
        known_values = spent_at.get("options", [])
        create_values = spent_at.get("create_options", [])
        return {
            "ok": False,
            "submitted": False,
            "error": {
                "type": "ambiguous_spent_at",
                "message": "Spent at value is ambiguous. Use one of the known_values or pass --force-create-spent-at to create a new contact anyway.",
            },
            "needs_spent_at_selection": True,
            "spent_at": {"status": spent_at.get("status", ""), "input": spent_at.get("input", form.merchant or "")},
            "known_values": known_values,
            "create_values": create_values,
            "next_actions": [
                "Retry with --spent-at set to one of known_values.",
                "Retry with --force-create-spent-at to create a new contact using the original input.",
            ],
            "url": page.url,
            "field_values": _field_values_output(_field_values(page), "expense"),
        }
    _upload_receipt(page, form.receipt_file)
    detail_url = ""
    if form.submit:
        _click_submit_expense(page)
        page.wait_for_timeout(3000)
        _raise_if_submit_blocked(page)
        detail_url = _find_created_expense_url(page, form)
    return {
        "ok": True,
        "submitted": form.submit,
        "url": page.url,
        "detail_url": detail_url,
        "field_values": _field_values_output(_field_values(page), "expense"),
        "field_state": field_state,
        "summary": current_page_summary(session),
    }


def create_mileage(session, form: ExpenseForm) -> dict:
    _validate_mileage_form(form)
    ensure_logged_in(session)
    page = session.page
    goto_domcontentloaded(page, XERO_CREATE_MILEAGE_URL)
    page.wait_for_timeout(1500)

    _fill_mileage_fields(page, form)
    _upload_receipt(page, form.receipt_file)
    detail_url = ""
    if form.submit:
        _click_submit_expense(page)
        page.wait_for_timeout(3000)
        _raise_if_submit_blocked(page)
        detail_url = _find_created_expense_url(page, form)
    return {
        "ok": True,
        "submitted": form.submit,
        "url": page.url,
        "detail_url": detail_url,
        "field_values": _field_values_output(_field_values(page), "mileage"),
        "summary": current_page_summary(session),
    }


def view_expense_detail(session, *, url: str, claim_type: str | None = None) -> dict:
    ensure_logged_in(session)
    page = session.page
    goto_domcontentloaded(page, _expense_target_url(url))
    _wait_for_expense_detail(page)
    fields = _detail_values(page)
    detail_type = claim_type or _infer_claim_type(fields)
    return {
        "ok": True,
        "type": detail_type,
        "url": page.url,
        "fields": _detail_fields_output(fields, detail_type),
    }


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
    date: str | None = None,
    description: str | None = None,
    amount: str | None = None,
    merchant: str | None = None,
    currency: str | None = None,
    category: str | None = None,
    assign_to: str | None = None,
    label: str | None = None,
    payment_due_date: str | None = None,
    tax_rate: str | None = None,
    items: list[ExpenseLineItem] | None = None,
    force_create_spent_at: bool = False,
    save: bool = False,
) -> dict:
    items = items or []
    form = ExpenseForm(
        date=date,
        description=description,
        amount=amount,
        merchant=merchant,
        currency=currency,
        category=category,
        assign_to=assign_to,
        label=label,
        payment_due_date=payment_due_date,
        tax_rate=tax_rate,
        force_create_spent_at=force_create_spent_at,
    )
    _validate_expense_form(form)
    if items and any([date, description, amount, merchant, currency, category, assign_to, label, payment_due_date, tax_rate]):
        raise ValidationError("--item cannot be combined with other edit fields")
    if not any([date, description, amount, merchant, currency, category, assign_to, label, payment_due_date, tax_rate, items]):
        raise ValidationError("At least one expense edit value is required")

    ensure_logged_in(session)
    page = session.page
    goto_domcontentloaded(page, _expense_target_url(url))
    _wait_for_expense_detail(page)

    _click_expense_edit(page)
    page.wait_for_timeout(1500)

    unapplied: list[str] = []
    if items:
        _fill_itemised_lines(page, items)
    else:
        if merchant:
            spent_at = _select_or_create_spent_at(page, merchant, force_create=form.force_create_spent_at)
            if spent_at.get("status") in {"ambiguous", "unresolved"}:
                unapplied.append("spent-at")
        unapplied.extend(_apply_shared_edit_fields(page, form))
        if currency and not _select_currency(page, currency):
            unapplied.append("currency")
        if amount and not _fill_targeted_or_by_label(page, [lambda p: p.locator("#expense-detail-subtotal")], ["purchase amount", "amount"], amount):
            unapplied.append("amount")
        if tax_rate and not _select_tax_rate(page, tax_rate):
            unapplied.append("tax-rate")
    if unapplied:
        raise ElementNotFoundError(
            "Could not apply requested change(s): "
            + ", ".join(unapplied)
            + ". The item may be locked/approved or the form layout changed; nothing was saved."
        )

    if save:
        _click_save_expense(page)
    return {
        "ok": True,
        "saved": save,
        "changed": _changed_fields_output(
            {
                "date": date,
                "description": description,
                "amount": amount,
                "spent_at": merchant,
                "currency": currency,
                "account": category,
                "assign_to": assign_to,
                "label": label,
                "payment_due_date": payment_due_date,
                "tax_rate": tax_rate,
                "items": items,
            }
        ),
        "url": page.url,
        "field_values": _field_values_output(_field_values(page), "expense"),
        "summary": current_page_summary(session),
    }


def edit_mileage_detail(
    session,
    *,
    url: str,
    date: str | None = None,
    description: str | None = None,
    category: str | None = None,
    assign_to: str | None = None,
    label: str | None = None,
    payment_due_date: str | None = None,
    distance: str | None = None,
    rate: str | None = None,
    save: bool = False,
) -> dict:
    form = ExpenseForm(
        date=date,
        description=description,
        category=category,
        assign_to=assign_to,
        label=label,
        payment_due_date=payment_due_date,
        distance=distance,
        rate=rate,
    )
    _validate_mileage_form(form)
    if not any([date, description, category, assign_to, label, payment_due_date, distance, rate]):
        raise ValidationError("At least one mileage edit value is required")

    ensure_logged_in(session)
    page = session.page
    goto_domcontentloaded(page, _expense_target_url(url))
    _wait_for_expense_detail(page)
    _click_expense_edit(page)
    page.wait_for_timeout(1500)

    unapplied = _apply_shared_edit_fields(page, form)
    if distance and not _fill_targeted_or_by_label(
        page,
        [lambda p: p.locator("#expense-detail-distance-input")],
        ["mileage to claim", "distance", "kilometres", "kilometers", "km", "miles"],
        distance,
    ):
        unapplied.append("distance")
    if rate and not _fill_targeted_or_by_label(page, [lambda p: p.locator("#distance-rate-input")], ["rate", "rate per km", "aud per km"], rate):
        unapplied.append("rate")
    if unapplied:
        raise ElementNotFoundError(
            "Could not apply requested change(s): "
            + ", ".join(unapplied)
            + ". The item may be locked/approved or the form layout changed; nothing was saved."
        )
    if save:
        _click_save_expense(page)
    return {
        "ok": True,
        "saved": save,
        "changed": _changed_fields_output(
            {
                "date": date,
                "description": description,
                "account": category,
                "assign_to": assign_to,
                "label": label,
                "payment_due_date": payment_due_date,
                "distance": distance,
                "rate": rate,
            }
        ),
        "url": page.url,
        "field_values": _field_values_output(_field_values(page), "mileage"),
        "summary": current_page_summary(session),
    }


def delete_expense_detail(session, *, url: str, claim_type: str | None = None, confirm: bool = False) -> dict:
    ensure_logged_in(session)
    page = session.page
    goto_domcontentloaded(page, _expense_target_url(url))
    _wait_for_expense_detail(page)
    fields = _detail_values(page)
    detail_type = claim_type or _infer_claim_type(fields)
    if not confirm:
        return {
            "ok": True,
            "deleted": False,
            "requires_confirm": True,
            "type": detail_type,
            "url": page.url,
            "fields": _detail_fields_output(fields, detail_type),
            "summary": current_page_summary(session),
        }

    _click_expense_delete(page)
    page.wait_for_timeout(500)
    confirm_control = first_visible(
        page,
        [
            lambda p: p.get_by_role("button", name=re.compile(r"delete|yes|ok|confirm", re.I)),
            lambda p: p.get_by_role("link", name=re.compile(r"delete|yes|ok|confirm", re.I)),
        ],
        timeout_ms=3000,
    )
    if confirm_control is not None:
        confirm_control.click()
    page.wait_for_timeout(2500)
    return {
        "ok": True,
        "deleted": True,
        "type": detail_type,
        "url": page.url,
        "fields": _detail_fields_output(fields, detail_type),
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


def _field_values_output(fields: dict, claim_type: str) -> dict:
    if claim_type == "mileage":
        keys = ("description", "account", "assign_to", "label", "distance", "rate")
    else:
        keys = ("description", "spent_at", "account", "assign_to", "label", "amount")
    return {key: fields.get(key, "") for key in keys if fields.get(key, "") != ""}


def _changed_fields_output(changed: dict) -> dict:
    return {key: value for key, value in changed.items() if value not in (None, "", [])}


def _wait_for_expense_detail(page, *, timeout_ms: int = 10000) -> None:
    """Wait for the expense detail SPA to render claim content."""
    for sel in (
        'text=/claim/i',
        "#description-input",
        "#expense-detail-subtotal",
        "#expense-detail-distance-input",
    ):
        try:
            page.wait_for_selector(sel, timeout=timeout_ms)
            page.wait_for_timeout(500)
            return
        except PlaywrightError:
            continue
    page.wait_for_timeout(5000)


def _detail_values(page) -> dict:
    fields = _field_values(page)
    text = visible_text(page.locator("body").first)
    parsed = _parse_detail_page(text)
    if not parsed:
        parsed = _parse_expense_text(text)
    for key, value in parsed.items():
        if value and not fields.get(key):
            fields[key] = value
    if fields.get("merchant") and not fields.get("spent_at"):
        fields["spent_at"] = fields["merchant"]
    fields.pop("merchant", None)
    fields["has_attachment"] = _detail_has_attachment(page)
    fields["text"] = text
    return fields


def _detail_fields_output(fields: dict, claim_type: str) -> dict:
    if claim_type == "mileage":
        keys = ("type", "status", "description", "date", "account", "payment_due_date", "distance", "rate", "amount", "has_attachment")
    else:
        keys = ("type", "status", "description", "spent_at", "date", "payment_source", "payment_due_date", "account", "amount", "has_attachment")
    output = {key: fields.get(key, "") for key in keys if key == "has_attachment" or fields.get(key, "") != ""}
    for key in ("date", "payment_due_date"):
        if output.get(key):
            output[key] = _normalize_date(output[key])
    return output


def _infer_claim_type(fields: dict) -> str:
    if fields.get("distance") or re.search(r"\bmileage\b", fields.get("text", ""), re.I):
        return "mileage"
    return "expense"


def _expense_target_url(url: str) -> str:
    return url if url.startswith("http") else f"https://go.xero.com{url}"


def _extract_table_rows(page, *, limit: int) -> list[dict]:
    try:
        page.wait_for_selector("table, [role='table'], [role='row']", timeout=7000)
    except PlaywrightError:
        return []

    section_header_re = re.compile(
        r"\b(?:Drafts\s+\d+|Awaiting\s+approval\s+\d+|Awaiting\s+payment\s+\d+"
        r"|To\s+review\s*\(\d+\)|To\s+pay\s*\(\d+\)|All\s+expenses|Expense\s+details"
        r"|Quick\s+action|Claim\s+Status)\b",
        re.I,
    )
    rows = []
    attachment_flags = _list_attachment_flags(page, limit * 4)
    row_locators = page.locator("table tbody tr, [role='table'] [role='row'], [data-automationid*='row' i]")
    count = min(row_locators.count(), limit * 4)
    for idx in range(count):
        row = row_locators.nth(idx)
        text = visible_text(row)
        if not text or re.search(r"date\s+description\s+amount", text, re.I):
            continue
        if section_header_re.search(text):
            continue
        url = _row_url(row)
        if not url:
            continue
        cells = [visible_text(row.locator("td, [role='cell'], [role='gridcell']").nth(cell_idx)) for cell_idx in range(row.locator("td, [role='cell'], [role='gridcell']").count())]
        cells = [cell for cell in cells if cell]
        parsed = _parse_expense_text(text)
        row_index = len(rows)
        has_attachment = attachment_flags[row_index] if row_index < len(attachment_flags) else _row_has_attachment(row, url)
        parsed.update({"text": text, "cells": cells, "url": url, "has_attachment": has_attachment})
        rows.append(_normalize_expense_row(parsed))
        if len(rows) >= limit:
            break
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
    text = re.sub(
        r"\s*(?:Drafts\s+\d+|Awaiting\s+approval\s+\d+|Awaiting\s+payment\s+\d+"
        r"|To\s+review\s*\(\d+\)|To\s+pay\s*\(\d+\)|All\s+expenses)\s*",
        " ",
        text,
    )
    urls = _expense_detail_urls(page, limit=limit)
    pattern = re.compile(
        r"(?P<leading_status>Awaiting payment|Submitted|Approved|Declined|Draft)?\s*"
        r"(?P<description>.+?)\s+DC\s+.*?\(You\)"
        r"\s*•\s*(?:Spent on|Travelled on)\s+(?P<date>[^•]+)"
        r"(?:\s*•\s*(?P<category>[^•]+?))?"
        r"(?:\s*•\s*(?P<tax_rate>[^•]+?))?"
        r"(?:\s*•\s*(?P<claim_type>.+?))?\s+"
        r"(?P<status>To be paid|Submitted|Approved|Declined|Draft|Paid)\s+"
        r"(?P<amount>[\d,]+\.\d{2})\b",
        re.I,
    )
    rows = []
    attachment_flags = _list_attachment_flags(page, limit)
    for idx, match in enumerate(pattern.finditer(text)):
        row = {key: " ".join(value.split()) for key, value in match.groupdict(default="").items()}
        row["description"] = re.sub(r"^(?:Awaiting payment\s+\d+\s+|\d+\s+|Pay\s+)", "", row["description"]).strip()
        row["url"] = urls[idx] if idx < len(urls) else _first_link_url(page, row["description"])
        row["has_attachment"] = attachment_flags[idx] if idx < len(attachment_flags) else _has_attachment_for_url(page, row["url"])
        rows.append(_normalize_expense_row(row))
        if len(rows) >= limit:
            break
    return rows


def _extract_scoped_expense_cards(page, *, limit: int) -> list[dict]:
    section_header_re = re.compile(
        r"\b(?:Drafts\s+\d+|Awaiting\s+approval\s+\d+|Awaiting\s+payment\s+\d+"
        r"|To\s+review|To\s+pay|All\s+expenses|Expense\s+details|Quick\s+action|Claim\s+Status)\b",
        re.I,
    )
    rows = []
    attachment_flags = _list_attachment_flags(page, limit * 4)
    candidates = page.locator('li, [role="listitem"], [data-automationid*="card" i], [data-automationid*="expense" i]')
    for idx in range(min(candidates.count(), limit * 4)):
        candidate = candidates.nth(idx)
        text = visible_text(candidate)
        if not text or section_header_re.search(text):
            continue
        if not re.search(r"expense|mileage|spent|submitted|approved|draft|paid|\d+\.\d{2}", text, re.I):
            continue
        url = _row_url(candidate)
        if not url and "/expenses/detail/" not in text:
            continue
        row = _parse_expense_text(text)
        row_index = len(rows)
        has_attachment = attachment_flags[row_index] if row_index < len(attachment_flags) else _row_has_attachment(candidate, url)
        row.update({"text": text, "url": url, "has_attachment": has_attachment})
        rows.append(_normalize_expense_row(row))
        if len(rows) >= limit:
            break
    return rows


def _parse_expense_text(text: str) -> dict:
    compact = " ".join((text or "").split())
    if not compact:
        return {}
    result: dict[str, str] = {}
    amount = re.search(r"(?P<currency>[$A-Z]{0,3})\s*(?P<amount>[\d,]+\.\d{2})\b", compact)
    if amount:
        result["currency"] = amount.group("currency").replace("$", "").strip()
        result["amount"] = amount.group("amount")
    status = re.search(r"\b(To be paid|Awaiting payment|Submitted|Approved|Declined|Draft|Paid)\b", compact, re.I)
    if status:
        result["status"] = status.group(1)
    date_match = re.search(r"\b(?:Spent on|Travelled on|Date)\s+([^•|]+?)(?:\s+•|\s{2,}|$)", compact, re.I)
    if date_match:
        result["date"] = date_match.group(1).strip()
    merchant = re.search(r"\b(?:Spent at|Merchant|Supplier)\s+([^•|]+?)(?:\s+•|\s{2,}|$)", compact, re.I)
    if merchant:
        result["spent_at"] = merchant.group(1).strip()
    distance = re.search(r"\b(?P<distance>[\d,.]+)\s*(?:km|kilometres|kilometers|miles)\b", compact, re.I)
    if distance:
        result["distance"] = distance.group("distance")
    rate = re.search(r"\b(?:rate|rate per km)\s+[$A-Z]*\s*(?P<rate>[\d,.]+)", compact, re.I)
    if rate:
        result["rate"] = rate.group("rate")
    claim_type = re.search(r"\b(Mileage|Expense)\b", compact, re.I)
    if claim_type:
        result["claim_type"] = claim_type.group(1)
        result["type"] = claim_type.group(1).lower()
    bullet_parts = [part.strip() for part in re.split(r"\s*•\s*", compact) if part.strip()]
    if len(bullet_parts) >= 2 and not result.get("category"):
        result["category"] = bullet_parts[1] if bullet_parts[0].lower().startswith(("spent on", "travelled on")) else ""
    if len(bullet_parts) >= 3 and not result.get("tax_rate"):
        result["tax_rate"] = bullet_parts[2]
    return result


def _description_and_spent_at_from_title(title: str) -> tuple[str, str]:
    compact = " ".join((title or "").split())
    quoted = re.search(r'"([^"]+)"', compact)
    if not quoted:
        return compact, ""
    description = quoted.group(1).strip()
    prefix = compact[: quoted.start()].strip()
    prefix = re.sub(r"^(?:Approve|Decline|Pay|Submit|Edit|Completed)\s+", "", prefix, flags=re.I).strip()
    if re.fullmatch(r"[\d,.]+\s*(?:km|kilometres|kilometers|miles)?", prefix, re.I):
        prefix = ""
    return description, prefix


def _has_attachment(locator) -> bool:
    selectors = (
        '.xec-claims-list-view__attachment-col',
        '.xec-claim-file-icon',
        'svg.xec-claim-file-icon',
        '[aria-label*="attachment" i]',
        '[aria-label*="receipt" i]',
        '[title*="attachment" i]',
        '[title*="receipt" i]',
        '[data-automationid*="attachment" i]',
        '[data-automationid*="receipt" i]',
        'svg[class*="paperclip" i]',
        'svg[data-icon*="paperclip" i]',
        'use[href*="paperclip" i]',
    )
    exclude_re = re.compile(r"upload|attach\s+(?:file|receipt)|add\s+(?:file|receipt)|drag|drop", re.I)
    for selector in selectors:
        matches = locator.locator(selector)
        try:
            count = min(matches.count(), 20)
        except PlaywrightError:
            continue
        for idx in range(count):
            match = matches.nth(idx)
            try:
                if not match.is_visible(timeout=200):
                    continue
                label = " ".join(
                    str(value or "")
                    for value in (
                        match.get_attribute("aria-label", timeout=200),
                        match.get_attribute("title", timeout=200),
                        match.get_attribute("data-automationid", timeout=200),
                        visible_text(match),
                        _attachment_control_context(match),
                    )
                )
            except PlaywrightError:
                continue
            if not exclude_re.search(label):
                return True
    return False


def _list_attachment_flags(page, limit: int) -> list[bool]:
    flags = []
    columns = page.locator(".xec-claims-list-view__attachment-col")
    try:
        count = min(columns.count(), limit)
    except PlaywrightError:
        return flags
    for idx in range(count):
        column = columns.nth(idx)
        try:
            flags.append(column.locator(".xec-claim-file-icon").count() > 0)
        except PlaywrightError:
            flags.append(False)
    return flags


def _row_has_attachment(row, url: str = "") -> bool:
    scoped = _scoped_claim_row(row, url)
    return _has_attachment(scoped)


def _detail_has_attachment(page) -> bool:
    try:
        if page.locator('[aria-label="Expense receipt"], [aria-label="Mileage receipt"]').count() > 0:
            return True
    except PlaywrightError:
        pass
    selectors = (
        'a[href*="/Files/"]',
        'a[href*="/files/"]',
        'a[href*="attachment" i]',
        'a[href*="receipt" i]',
        '[data-automationid*="attachment-list" i]',
        '[data-automationid*="attachment-preview" i]',
        '[data-automationid*="receipt-list" i]',
        '[data-automationid*="receipt-preview" i]',
        'img[alt*="receipt" i]',
        'img[alt*="attachment" i]',
    )
    exclude_re = re.compile(r"upload|attach\s+(?:file|receipt)|add\s+(?:file|receipt)|drag|drop", re.I)
    for selector in selectors:
        matches = page.locator(selector)
        try:
            count = min(matches.count(), 20)
        except PlaywrightError:
            continue
        for idx in range(count):
            match = matches.nth(idx)
            try:
                if not match.is_visible(timeout=200):
                    continue
                label = " ".join(
                    str(value or "")
                    for value in (
                        match.get_attribute("href", timeout=200),
                        match.get_attribute("aria-label", timeout=200),
                        match.get_attribute("title", timeout=200),
                        match.get_attribute("data-automationid", timeout=200),
                        visible_text(match),
                        _attachment_control_context(match),
                    )
                )
            except PlaywrightError:
                continue
            if not exclude_re.search(label):
                return True
    return False


def _has_attachment_for_url(page, url: str) -> bool:
    if not url:
        return False
    path = url.split("?", 1)[0]
    links = page.locator(f'a[href*="{path}"]')
    try:
        for idx in range(min(links.count(), 10)):
            link = links.nth(idx)
            if _has_attachment(link):
                return True
            for container in _expense_row_containers(link, prefer_claim_list=True):
                if _has_attachment(container):
                    return True
    except PlaywrightError:
        return False
    return False


def _expense_row_container(link):
    containers = _expense_row_containers(link)
    return containers[0] if containers else link


def _expense_row_containers(link, *, prefer_claim_list: bool = False) -> list:
    containers = []
    if prefer_claim_list:
        for xpath in (
            "xpath=ancestor::*[contains(@class, 'xec-claims-list-view__claim-row')][1]",
            "xpath=ancestor::*[contains(@class, 'xec-claims-list-view__claim')][1]",
            "xpath=ancestor::*[contains(@class, 'xec-claims-list-view')][1]",
        ):
            container = link.locator(xpath)
            try:
                if container.count() > 0:
                    return [container.first]
            except PlaywrightError:
                continue
    for xpath in (
        "xpath=ancestor::tr[1]",
        "xpath=ancestor::*[@role='row'][1]",
        "xpath=ancestor::li[1]",
        "xpath=ancestor::article[1]",
        "xpath=ancestor::div[1]",
        "xpath=ancestor::div[2]",
        "xpath=ancestor::div[3]",
        "xpath=ancestor::div[4]",
        "xpath=ancestor::div[5]",
        "xpath=ancestor::div[6]",
    ):
        container = link.locator(xpath)
        try:
            if container.count() > 0:
                containers.append(container.first)
        except PlaywrightError:
            continue
    return containers


def _scoped_claim_row(row, url: str):
    if not url:
        return row
    path = url.split("?", 1)[0]
    links = row.locator(f'a[href*="{path}"]')
    try:
        if links.count() == 0:
            return row
        containers = _expense_row_containers(links.first, prefer_claim_list=True)
        return containers[0] if containers else row
    except PlaywrightError:
        return row


def _attachment_control_context(locator) -> str:
    values = []
    for xpath in ("xpath=ancestor::button[1]", "xpath=ancestor::a[1]", "xpath=ancestor::*[@role='button'][1]", "xpath=ancestor::*[@role='link'][1]"):
        control = locator.locator(xpath)
        try:
            if control.count() == 0:
                continue
            first = control.first
            values.extend(
                str(value or "")
                for value in (
                    first.get_attribute("aria-label", timeout=200),
                    first.get_attribute("title", timeout=200),
                    first.get_attribute("data-automationid", timeout=200),
                    visible_text(first),
                )
            )
        except PlaywrightError:
            continue
    return " ".join(values)


def _parse_detail_page(text: str) -> dict:
    """Parse the read-only submitted/draft expense detail page body text.

    The detail page renders label:value pairs as static text, e.g.:
        Submitted expense claim Edit Decline Approve 42.50
        "CLI test expense" Spent at Test Merchant CLI Spent on
        8 July 2026 Account 400 - Advertising Subtotal including
        tax AUD42.50 Total AUD 42.50
    """
    compact = " ".join((text or "").split())
    if not compact or not re.search(r"\bclaim\b", compact, re.I):
        return {}
    result: dict[str, str] = {}

    if re.search(r"\bmileage claim\b", compact, re.I):
        result["type"] = "mileage"
    elif re.search(r"\bexpense claim\b", compact, re.I):
        result["type"] = "expense"

    status = re.search(r"\b(Draft|Submitted|Approved|Declined)\s+(?:expense|mileage)\s+claim\b", compact, re.I)
    if status:
        result["status"] = status.group(1).capitalize()

    date = re.search(r"\b(?:Spent on|Travelled on)\s+(\d{1,2}\s+\w+\s+\d{4})", compact, re.I)
    if date:
        result["date"] = date.group(1)

    merchant = re.search(r"\bSpent at\s+(.+?)(?:\s+Spent on|\s+How did you pay|\s+Account\b)", compact, re.I)
    if merchant:
        result["spent_at"] = merchant.group(1).strip()

    account = re.search(
        r"\bAccount\s+(\d.+?)(?:\s+Subtotal|\s+Payment due date|\s+Region|\s+Purchase amount|\s+Mileage to claim|\s+Label|\s+Total\b|\s+How did you pay)",
        compact,
    )
    if account:
        result["account"] = account.group(1).strip()

    payment_source = re.search(
        r"\bHow did you pay\?.{0,80}?\b(Company money \(non-reimbursable\)|Personal money \(reimbursable\))",
        compact,
        re.I,
    )
    if payment_source:
        result["payment_source"] = payment_source.group(1).strip()

    payment_due_date = re.search(
        r"\bPayment due date\s+(\d{1,2}\s+\w+\s+\d{4}|\d{1,2}\s+\w{3}\s+\d{4}|\d{1,2}\s+\w{3})(?=\s+(?:Mileage to claim|Subtotal|Total|History)\b)",
        compact,
        re.I,
    )
    if payment_due_date and (result.get("type") == "mileage" or result.get("payment_source") == "Personal money (reimbursable)"):
        result["payment_due_date"] = payment_due_date.group(1).strip()

    total = re.search(r"\bTotal\s+(?:AUD|USD|\$)?\s*([\d,]+\.\d{2})", compact, re.I)
    if total:
        result["amount"] = total.group(1)

    distance = re.search(r"\bMileage to claim\s*\(km\)\s*([\d.]+)", compact, re.I)
    if distance:
        result["distance"] = distance.group(1)
    else:
        dist_prefix = re.search(r"\b(\d+(?:\.\d+)?)km\b", compact, re.I)
        if dist_prefix:
            result["distance"] = dist_prefix.group(1)

    rate = re.search(r"\bRate\s*\([^)]*per km\)\s*([\d.]+)", compact, re.I)
    if rate:
        result["rate"] = rate.group(1)

    desc = re.search(r'"([^"]+)"', compact)
    if desc:
        result["description"] = desc.group(1)

    return result


def _normalize_expense_row(row: dict) -> dict:
    known = {
        "type": "",
        "url": "",
        "status": "",
        "description": "",
        "spent_at": "",
        "date": "",
        "account": "",
        "tax_rate": "",
        "distance": "",
        "rate": "",
        "amount": "",
        "currency": "",
        "reimbursement_type": "",
        "text": "",
        "has_attachment": False,
    }
    known.update({key: value for key, value in row.items() if value is not None})
    if row.get("category") and not known["account"]:
        known["account"] = row["category"]
    if row.get("claim_type") and not known["reimbursement_type"]:
        known["reimbursement_type"] = row["claim_type"]
    if known["date"]:
        known["date"] = _normalize_date(known["date"])
    raw_description = known["description"]
    description, spent_at = _description_and_spent_at_from_title(known["description"])
    if description:
        known["description"] = description
    if spent_at and not known["spent_at"]:
        known["spent_at"] = spent_at
    if not known["type"]:
        known["type"] = "mileage" if re.search(r"\bmileage\b", f"{known['reimbursement_type']} {known['text']}", re.I) else "expense"
    if re.search(r"\b(?:Reimbursable|Non-reimbursable)\b", known["account"], re.I):
        known["reimbursement_type"] = known["reimbursement_type"] or known["account"]
        known["account"] = ""
    if re.search(r"\b(?:Reimbursable|Non-reimbursable)\b", known["tax_rate"], re.I):
        known["reimbursement_type"] = known["reimbursement_type"] or known["tax_rate"]
        known["tax_rate"] = ""
    if known["type"] != "mileage" and re.search(r"\b\d+(?:\.\d+)?\s*(?:km|kilometres|kilometers|miles)\b", raw_description, re.I):
        known["type"] = "mileage"
    if known["type"] == "mileage" and not known["distance"]:
        distance = re.search(r"\b(?P<distance>\d+(?:\.\d+)?)\s*(?:km|kilometres|kilometers|miles)\b", f"{raw_description} {known['text']}", re.I)
        if distance:
            known["distance"] = distance.group("distance")
    return _expense_row_output(known)


def _expense_row_output(row: dict) -> dict:
    if row.get("type") == "mileage":
        keys = ("type", "url", "status", "description", "date", "account", "tax_rate", "distance", "rate", "amount", "reimbursement_type", "has_attachment")
    else:
        keys = ("type", "url", "status", "description", "spent_at", "date", "account", "tax_rate", "amount", "reimbursement_type", "has_attachment")
    return {key: row.get(key, "") for key in keys if key == "has_attachment" or row.get(key, "") != ""}


def _normalize_date(value: str) -> str:
    return re.sub(
        r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\b",
        lambda match: match.group(1)[:3],
        " ".join((value or "").split()),
    )


def _row_url(row) -> str:
    links = row.locator('a[href*="/expenses/detail/"]')
    try:
        if links.count() == 0:
            return ""
        return links.first.get_attribute("href", timeout=500) or ""
    except PlaywrightError:
        return ""


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


def _apply_shared_edit_fields(page, form: ExpenseForm) -> list[str]:
    unapplied: list[str] = []
    if form.date and not _fill_date(page, form.date):
        unapplied.append("date")
    if form.description and not _fill_targeted_or_by_label(
        page,
        [lambda p: p.locator("#description-input")],
        ["description", "what was this for", "purpose", "reference"],
        form.description,
    ):
        unapplied.append("description")
    if form.category and not _select_account(page, form.category):
        unapplied.append("category")
    if form.assign_to and not _select_assign_to(page, form.assign_to):
        unapplied.append("assign-to")
    if form.label and not _select_label(page, form.label):
        unapplied.append("label")
    if form.payment_due_date and not _fill_date(page, form.payment_due_date, due=True):
        unapplied.append("payment-due-date")
    return unapplied


def _fill_expense_fields(page, form: ExpenseForm) -> dict:
    state: dict = {}
    if form.merchant:
        state["spent_at"] = _select_or_create_spent_at(page, form.merchant, force_create=form.force_create_spent_at)
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


def _click_save_expense(page) -> None:
    save_button = require_visible(page, [lambda p: p.get_by_role("button", name=re.compile(r"^save$", re.I))], label="expense save button", timeout_ms=5000)
    save_button.click()
    page.wait_for_timeout(3000)


def _click_expense_delete(page) -> None:
    delete_button = first_visible(page, [lambda p: p.get_by_role("button", name=re.compile(r"^delete$", re.I))], timeout_ms=1200)
    if delete_button is not None:
        delete_button.click()
        return

    delete_item_factories = [
        lambda p: p.locator('button.xui-pickitem--body:has-text("Delete")'),
        lambda p: p.locator('.xui-pickitem--body:has-text("Delete")'),
        lambda p: p.get_by_role("button", name=re.compile(r"^delete$", re.I)),
        lambda p: p.get_by_text(re.compile(r"^delete$", re.I)),
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
            delete_item = first_visible(page, delete_item_factories, timeout_ms=800)
            if delete_item is not None:
                delete_item.click()
                return
            try:
                page.keyboard.press("Escape")
            except PlaywrightError:
                pass
    raise ElementNotFoundError("Could not find expense Delete action, including under the actions/three-dots menu")


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


def _select_or_create_spent_at(page, value: str, *, force_create: bool = False) -> dict:
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
    if not options:
        page.wait_for_timeout(800)
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

    if len(contact_options) > 1 and not force_create:
        return {"status": "ambiguous", "input": value, "options": _option_texts(contact_options), "create_options": _option_texts(create_options)}

    if create_options:
        create_options[0]["locator"].click()
        page.wait_for_timeout(800)
        _confirm_spent_at_creation(page)
        return _spent_at_result(page, "created", value, options)

    page.keyboard.press("Enter")
    page.wait_for_timeout(800)
    _confirm_spent_at_creation(page)
    return _spent_at_result(page, "created", value, [])


def _spent_at_result(page, status: str, value: str, options: list[dict]) -> dict:
    selected = ""
    for _ in range(6):
        selected = _field_values(page).get("spent_at", "").strip()
        if selected:
            break
        page.wait_for_timeout(400)
    if not selected:
        return {"status": "unresolved", "input": value, "options": _option_texts(options)}
    return {"status": status, "value": selected, "options": _option_texts(options)}


def _visible_spent_at_options(page) -> list[dict]:
    option_locators = [
        lambda p: p.locator('[data-automationid="new-contact-pick-item"]'),
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


def _confirm_spent_at_creation(page) -> None:
    try:
        dialog = page.get_by_role("dialog")
        if dialog.count() == 0 or not dialog.first.is_visible(timeout=300):
            return
    except PlaywrightError:
        return
    save = first_visible(
        page,
        [
            lambda p: p.get_by_role("dialog").get_by_role("button", name=re.compile(r"^(save|add|create)$", re.I)),
            lambda p: p.get_by_role("dialog").locator('button[type="submit"]'),
            lambda p: p.locator('[data-automationid*="modal" i]').get_by_role("button", name=re.compile(r"^(save|add|create)$", re.I)),
        ],
        timeout_ms=1500,
    )
    if save is not None:
        save.click()
        page.wait_for_timeout(1000)


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


def _find_created_expense_url(page, form: ExpenseForm) -> str:
    """After submit, try to find the newly created expense's detail URL.

    After clicking Submit on the create form, Xero navigates to the expenses
    list. We search the list for a row matching the merchant/description/amount
    and return its detail URL.
    """
    page.wait_for_timeout(1000)
    if "/expenses/detail/" in page.url:
        return page.url
    goto_domcontentloaded(page, XERO_EXPENSES_URL)
    page.wait_for_timeout(4000)
    rows = _extract_table_rows(page, limit=25)
    if not rows:
        rows = _extract_scoped_expense_cards(page, limit=25)
    if not rows:
        rows = _extract_expense_cards(page, limit=25)
    for row in rows:
        url = row.get("url", "")
        if not url:
            continue
        blob = " ".join(str(v) for v in row.values()).lower()
        if form.merchant and form.merchant.lower() in blob:
            return url
        if form.description and form.description.lower() in blob:
            return url
        if form.distance and f"{form.distance}km" in blob:
            return url
    return ""
