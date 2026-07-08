from __future__ import annotations

from urllib.parse import urlencode

from playwright.sync_api import Error as PlaywrightError

from xero_user_cli.actions.auth import ensure_logged_in
from xero_user_cli.browser import goto_domcontentloaded, visible_text
from xero_user_cli.conf import XERO_CHART_OF_ACCOUNTS_URL


def list_accounts(
    session,
    *,
    page_number: int = 1,
    page_size: int = 100,
    order_by: str = "Code",
    direction: str = "ASC",
    account_class: str = "",
) -> dict:
    ensure_logged_in(session)
    page = session.page
    url = _accounts_url(
        page_number=page_number,
        page_size=page_size,
        order_by=order_by,
        direction=direction,
        account_class=account_class,
    )
    goto_domcontentloaded(page, url)
    page.wait_for_timeout(2500)
    accounts = _extract_accounts(page, limit=page_size)
    return {
        "ok": True,
        "area": "accounting-accounts",
        "label": "Chart of accounts",
        "url": page.url,
        "page": page_number,
        "page_size": page_size,
        "order_by": order_by,
        "direction": direction.upper(),
        "account_class": account_class,
        "accounts": accounts,
    }


def _accounts_url(*, page_number: int, page_size: int, order_by: str, direction: str, account_class: str) -> str:
    query = urlencode(
        {
            "accountClass": account_class,
            "page": page_number,
            "pageSize": page_size,
            "orderBy": order_by,
            "direction": direction.upper(),
        }
    )
    return f"{XERO_CHART_OF_ACCOUNTS_URL}?{query}"


def _extract_accounts(page, *, limit: int) -> list[dict]:
    try:
        page.wait_for_selector("table", timeout=7000)
    except PlaywrightError:
        return []

    accounts: list[dict] = []
    tables = page.locator("table")
    for table_idx in range(tables.count()):
        table = tables.nth(table_idx)
        headers = _table_headers(table)
        rows = table.locator("tbody tr, tr")
        for row_idx in range(rows.count()):
            row = rows.nth(row_idx)
            if row.locator("th").count():
                continue
            cells_locator = row.locator("td, [role='cell'], [role='gridcell']")
            cells = [visible_text(cells_locator.nth(cell_idx)) for cell_idx in range(cells_locator.count())]
            cells = [cell for cell in cells if cell]
            text = visible_text(row)
            if not cells or _looks_like_header(text) or not _looks_like_account_row(cells):
                continue
            accounts.append(_account_from_cells(cells, headers))
            if len(accounts) >= limit:
                return accounts
    return accounts


def _table_headers(table) -> list[str]:
    locators = table.locator("thead th, tr th")
    headers = [visible_text(locators.nth(idx)) for idx in range(locators.count())]
    return [_normalize_header(header) for header in headers if header]


def _account_from_cells(cells: list[str], headers: list[str]) -> dict:
    account = {
        "code": cells[0] if len(cells) > 0 else "",
        "name": cells[1] if len(cells) > 1 else "",
        "type": cells[2] if len(cells) > 2 else "",
        "tax_rate": cells[3] if len(cells) > 3 else "",
        "YTD": cells[4] if len(cells) > 4 else "",
    }
    columns = {}
    for idx, header in enumerate(headers[: len(cells)]):
        if header:
            columns[header] = cells[idx]
    if columns:
        account["columns"] = columns
        account["code"] = columns.get("code", account["code"])
        account["name"] = columns.get("name", account["name"])
        account["type"] = columns.get("type", account["type"])
        account["tax_rate"] = columns.get("tax_rate", account["tax_rate"])
        account["YTD"] = columns.get("ytd", account["YTD"])
    return account


def _normalize_header(value: str) -> str:
    normalized = "_".join(value.strip().lower().split())
    return normalized.replace("/", "_").replace("-", "_")


def _looks_like_header(text: str) -> bool:
    lower = text.lower()
    return lower.startswith("code name type") or lower in {"code", "name", "type", "tax rate"}


def _looks_like_account_row(cells: list[str]) -> bool:
    if len(cells) < 2:
        return False
    code = cells[0].strip()
    name = cells[1].strip()
    return bool(code and name and any(char.isdigit() for char in code))
