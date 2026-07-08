from __future__ import annotations

import re
from dataclasses import dataclass

from playwright.sync_api import Error as PlaywrightError

from xero_user_cli.actions.auth import current_page_summary, ensure_logged_in
from xero_user_cli.browser import goto_domcontentloaded, visible_text
from xero_user_cli.conf import (
    XERO_BILLS_URL,
    XERO_CUSTOMERS_URL,
    XERO_EMPLOYEES_URL,
    XERO_INVOICES_URL,
    XERO_LEAVE_URL,
    XERO_PAYMENTS_URL,
    XERO_PAYMENT_LINKS_URL,
    XERO_PAYMENT_SERVICES_URL,
    XERO_PRODUCTS_AND_SERVICES_URL,
    XERO_PURCHASE_ORDERS_URL,
    XERO_QUOTES_URL,
    XERO_SUPPLIERS_URL,
)
from xero_user_cli.exceptions import ValidationError


@dataclass(frozen=True)
class XeroArea:
    key: str
    label: str
    url: str


AREAS = {
    "sales-invoices": XeroArea("sales-invoices", "Invoices", XERO_INVOICES_URL),
    "sales-payment-links": XeroArea("sales-payment-links", "Payment links", XERO_PAYMENT_LINKS_URL),
    "sales-payment-services": XeroArea("sales-payment-services", "Payment services", XERO_PAYMENT_SERVICES_URL),
    "sales-quotes": XeroArea("sales-quotes", "Quotes", XERO_QUOTES_URL),
    "sales-products": XeroArea("sales-products", "Products and services", XERO_PRODUCTS_AND_SERVICES_URL),
    "sales-customers": XeroArea("sales-customers", "Customers", XERO_CUSTOMERS_URL),
    "purchases-bills": XeroArea("purchases-bills", "Bills", XERO_BILLS_URL),
    "purchases-payments": XeroArea("purchases-payments", "Payments", XERO_PAYMENTS_URL),
    "purchases-purchase-orders": XeroArea("purchases-purchase-orders", "Purchase orders", XERO_PURCHASE_ORDERS_URL),
    "purchases-suppliers": XeroArea("purchases-suppliers", "Suppliers", XERO_SUPPLIERS_URL),
    "payroll-employees": XeroArea("payroll-employees", "Employees", XERO_EMPLOYEES_URL),
    "payroll-leave": XeroArea("payroll-leave", "Leave", XERO_LEAVE_URL),
}


def open_area(session, *, area_key: str) -> dict:
    area = _area(area_key)
    ensure_logged_in(session)
    page = session.page
    goto_domcontentloaded(page, area.url)
    page.wait_for_timeout(2500)
    return {"ok": True, "area": area.key, "label": area.label, "url": page.url, "summary": current_page_summary(session)}


def list_area(session, *, area_key: str, limit: int = 25) -> dict:
    area = _area(area_key)
    ensure_logged_in(session)
    page = session.page
    goto_domcontentloaded(page, area.url)
    page.wait_for_timeout(2500)
    rows = _extract_table_rows(page, limit=limit)
    if not rows:
        rows = _extract_body_rows(page, area_key=area.key, limit=limit)
    return {
        "ok": True,
        "area": area.key,
        "label": area.label,
        "url": page.url,
        "items": rows,
        "summary": current_page_summary(session) if not rows else None,
    }


def _area(area_key: str) -> XeroArea:
    try:
        return AREAS[area_key]
    except KeyError as exc:
        raise ValidationError(f"Unknown Xero area: {area_key}") from exc


def _extract_table_rows(page, *, limit: int) -> list[dict]:
    try:
        page.wait_for_selector("table, [role='table'], [role='grid'], [role='row']", timeout=7000)
    except PlaywrightError:
        return []

    rows = []
    locators = page.locator("table tbody tr, [role='table'] [role='row'], [role='grid'] [role='row'], [data-automationid*='row' i]")
    for idx in range(min(locators.count(), limit * 4)):
        locator = locators.nth(idx)
        text = visible_text(locator)
        if not text or _looks_like_header(text) or _looks_like_chrome(text, _row_url(locator)):
            continue
        cells_locator = locator.locator("td, [role='cell'], [role='gridcell']")
        cells = [visible_text(cells_locator.nth(cell_idx)) for cell_idx in range(cells_locator.count())]
        if not any(cells):
            continue
        rows.append({"text": text, "cells": [cell for cell in cells if cell], "url": _row_url(locator)})
        if len(rows) >= limit:
            break
    return rows


def _looks_like_header(text: str) -> bool:
    lower = text.lower()
    if lower.startswith("start creating") or lower.startswith("no payment links here yet"):
        return True
    if lower in {"name", "date", "status", "amount", "contact select a checkbox"}:
        return True
    return lower.startswith(("name ", "date ", "status ", "number "))


def _extract_body_rows(page, *, area_key: str, limit: int) -> list[dict]:
    text = visible_text(page.locator("body").first)
    if not text:
        return []
    if area_key == "purchases-purchase-orders":
        return _regex_rows(text, r"(PO-\d+\s+.*?)(?=\s+PO-\d+|\s+Showing items|$)", limit=limit)
    if area_key == "payroll-leave":
        return _regex_rows(
            text,
            r"([A-Z]{2}\s+[A-Z][a-z]+\s+[A-Z][a-z]+\s+(?:Annual Leave|Personal \(Sick/Carer.s\) Leave).*?)(?=\s+[A-Z]{2}\s+[A-Z][a-z]+\s+[A-Z][a-z]+\s+(?:Annual Leave|Personal \(Sick/Carer.s\) Leave)|$)",
            limit=limit,
        )
    if area_key == "sales-payment-services":
        return _regex_rows(text, r"((?:PayPal|Stripe) \| .*? Popular globally On)", limit=limit)
    return []


def _regex_rows(text: str, pattern: str, *, limit: int) -> list[dict]:
    rows = []
    for match in re.finditer(pattern, text):
        row_text = " ".join(match.group(1).split())
        if row_text and not _looks_like_chrome(row_text, None):
            rows.append({"text": row_text, "url": None})
        if len(rows) >= limit:
            break
    return rows


def _looks_like_chrome(text: str, url: str | None) -> bool:
    compact = text.lower().replace(" ", "")
    if compact in {"filesfiles", "settingssettings"} or compact.endswith("democompany(au)"):
        return True
    if compact.startswith("organisation") or "addneworganisation" in compact:
        return True
    if "deepsynergysolutions" in compact or "myxero" in compact or "hubdoc" in compact or "xeroappstore" in compact:
        return True
    return bool(
        url
        and (
            "/docs/folders" in url.lower()
            or "organisationlogin" in url.lower()
            or "onboarding-ui" in url.lower()
            or "connect-hubdoc" in url.lower()
            or "my.xero.com" in url.lower()
            or "apps.xero.com" in url.lower()
            or url.lower().endswith("/settings")
        )
    )


def _row_url(locator) -> str | None:
    for candidate in (locator, locator.locator("a[href]").first):
        try:
            href = candidate.get_attribute("href", timeout=500)
        except PlaywrightError:
            href = None
        if href:
            return href
    return None
