from __future__ import annotations

import re
from dataclasses import dataclass

from playwright.sync_api import Error as PlaywrightError

from xero_user_cli.actions.auth import current_page_summary, ensure_logged_in
from xero_user_cli.browser import first_visible, goto_domcontentloaded, human_fill, require_visible, visible_text
from xero_user_cli.conf import XERO_TIMESHEETS_URL
from xero_user_cli.exceptions import ElementNotFoundError, ValidationError


_VISIBLE_BOUNDLIST_JS = """
() => {
  const lists = Array.from(document.querySelectorAll('.x-boundlist'));
  const visible = lists.filter(bl => bl.getClientRects().length > 0);
  const out = [];
  visible.forEach(bl => {
    Array.from(bl.querySelectorAll('.x-boundlist-item')).forEach(item => {
      const text = (item.textContent || '').replace(/\\s+/g, ' ').trim();
      if (text) out.push(text);
    });
  });
  return out;
}
"""


@dataclass
class TimesheetForm:
    employee: str | None = None
    period: str | None = None
    save: bool = False


def open_timesheets(session) -> dict:
    ensure_logged_in(session)
    page = session.page
    goto_domcontentloaded(page, XERO_TIMESHEETS_URL)
    page.wait_for_timeout(1500)
    return {"ok": True, "url": page.url, "summary": current_page_summary(session)}


def list_timesheets(session, *, limit: int = 25) -> dict:
    ensure_logged_in(session)
    page = session.page
    goto_domcontentloaded(page, XERO_TIMESHEETS_URL)
    page.wait_for_timeout(1500)

    rows = _extract_table_rows(page, limit=limit)
    if not rows:
        rows = _extract_list_items(page, limit=limit)
    if not rows:
        rows = _extract_body_rows(page, limit=limit)
    return {"ok": True, "url": page.url, "timesheets": rows, "summary": current_page_summary(session) if not rows else None}


def list_timesheet_periods(session, *, employee: str) -> dict:
    if not employee:
        raise ValidationError("--employee is required to list valid timesheet periods")
    ensure_logged_in(session)
    page = session.page
    goto_domcontentloaded(page, XERO_TIMESHEETS_URL)
    page.wait_for_timeout(1500)
    _click_named_control(page, "+ Add Timesheet")
    page.wait_for_timeout(1000)
    _fill_employee(page, employee)
    page.wait_for_timeout(1500)
    periods = _extract_period_options(page, employee=employee)
    _dismiss_add_timesheet_modal(page)
    return {"ok": True, "employee": employee, "count": len(periods), "periods": periods, "url": page.url}


def create_timesheet(session, form: TimesheetForm) -> dict:
    ensure_logged_in(session)
    page = session.page
    goto_domcontentloaded(page, XERO_TIMESHEETS_URL)
    page.wait_for_timeout(1500)
    _click_named_control(page, "+ Add Timesheet")
    page.wait_for_timeout(1000)

    selected_period = None
    if form.employee:
        _fill_employee(page, form.employee)
    if form.period:
        page.wait_for_timeout(800)
        selected_period = _select_period(page, form.period)
    if form.save:
        if not form.employee:
            raise ValidationError("--employee is required when using --save")
        _click_named_control(page, "Save")
        page.wait_for_timeout(2500)
        _raise_xero_validation(page)
    return {"ok": True, "saved": form.save, "period": selected_period, "url": page.url, "field_values": _field_values(page), "summary": current_page_summary(session)}


def view_timesheet(session, *, employee: str | None = None, period: str | None = None, status: str | None = None) -> dict:
    ensure_logged_in(session)
    page = session.page
    goto_domcontentloaded(page, XERO_TIMESHEETS_URL)
    page.wait_for_timeout(1500)
    row = _matching_row_from_body(page, employee=employee, period=period, status=status)
    _click_timesheet_row(page, row)
    page.wait_for_timeout(1500)
    return {"ok": True, "matched": row, "detail": _extract_detail(page), "url": page.url, "summary": current_page_summary(session)}


def edit_timesheet(
    session,
    *,
    employee: str | None = None,
    period: str | None = None,
    status: str | None = None,
    hours: str | None = None,
    save: bool = False,
) -> dict:
    ensure_logged_in(session)
    page = session.page
    goto_domcontentloaded(page, XERO_TIMESHEETS_URL)
    page.wait_for_timeout(1500)
    row = _matching_row_from_body(page, employee=employee, period=period, status=status)
    _click_timesheet_row(page, row)
    page.wait_for_timeout(1500)

    changed = {}
    if hours is not None:
        _fill_first_hours_input(page, hours)
        changed["hours"] = hours
    if save:
        if not changed:
            raise ValidationError("At least one edit value is required when using --save")
        _click_named_control(page, "Save")
        page.wait_for_timeout(2500)
    return {"ok": True, "saved": save, "changed": changed, "matched": row, "detail": _extract_detail(page), "url": page.url, "summary": current_page_summary(session)}


def revert_timesheet_to_draft(
    session,
    *,
    employee: str | None = None,
    period: str | None = None,
    status: str | None = None,
    confirm: bool = False,
) -> dict:
    if not confirm:
        raise ValidationError("Reverting a timesheet to draft requires --confirm")
    _require_match_filter(employee=employee, period=period, status=status)
    ensure_logged_in(session)
    page = session.page
    goto_domcontentloaded(page, XERO_TIMESHEETS_URL)
    page.wait_for_timeout(1500)
    row = _matching_row_from_body(page, employee=employee, period=period, status=status)
    _click_timesheet_row(page, row)
    page.wait_for_timeout(1500)
    _click_named_control(page, "Revert to Draft")
    page.wait_for_timeout(500)
    confirm_control = first_visible(
        page,
        [lambda p: p.get_by_role("button", name=re.compile(r"revert|yes|ok|confirm", re.I)), lambda p: p.get_by_role("link", name=re.compile(r"revert|yes|ok|confirm", re.I))],
        timeout_ms=3000,
    )
    if confirm_control is not None:
        confirm_control.click()
    page.wait_for_timeout(2500)
    return {"ok": True, "reverted": True, "matched": row, "detail": _extract_detail(page), "url": page.url, "summary": current_page_summary(session)}


def approve_timesheet(
    session,
    *,
    employee: str | None = None,
    period: str | None = None,
    status: str | None = None,
    confirm: bool = False,
) -> dict:
    if not confirm:
        raise ValidationError("Approving a timesheet requires --confirm")
    _require_match_filter(employee=employee, period=period, status=status)
    ensure_logged_in(session)
    page = session.page
    goto_domcontentloaded(page, XERO_TIMESHEETS_URL)
    page.wait_for_timeout(1500)
    row = _matching_row_from_body(page, employee=employee, period=period, status=status)
    _click_timesheet_row(page, row)
    page.wait_for_timeout(1500)
    _click_named_control(page, "Approve")
    page.wait_for_timeout(500)
    confirm_control = first_visible(
        page,
        [lambda p: p.get_by_role("button", name=re.compile(r"approve|yes|ok|confirm", re.I)), lambda p: p.get_by_role("link", name=re.compile(r"approve|yes|ok|confirm", re.I))],
        timeout_ms=3000,
    )
    if confirm_control is not None:
        confirm_control.click()
    page.wait_for_timeout(2500)
    return {"ok": True, "approved": True, "matched": row, "detail": _extract_detail(page), "url": page.url, "summary": current_page_summary(session)}


def delete_timesheet(session, *, employee: str | None = None, period: str | None = None, status: str | None = None, confirm: bool = False) -> dict:
    if not confirm:
        raise ValidationError("Deleting a timesheet requires --confirm")
    _require_match_filter(employee=employee, period=period, status=status)
    ensure_logged_in(session)
    page = session.page
    goto_domcontentloaded(page, XERO_TIMESHEETS_URL)
    page.wait_for_timeout(1500)
    row = _matching_row_from_body(page, employee=employee, period=period, status=status)
    _click_timesheet_row(page, row)
    page.wait_for_timeout(1500)
    _click_named_control(page, "Delete")
    page.wait_for_timeout(500)
    confirm_control = first_visible(page, [lambda p: p.get_by_role("button", name=re.compile(r"delete|yes|ok|confirm", re.I)), lambda p: p.get_by_role("link", name=re.compile(r"delete|yes|ok|confirm", re.I))], timeout_ms=3000)
    if confirm_control is not None:
        confirm_control.click()
    page.wait_for_timeout(2500)
    return {"ok": True, "deleted": True, "matched": row, "url": page.url, "summary": current_page_summary(session)}


def _extract_table_rows(page, *, limit: int) -> list[dict]:
    try:
        page.wait_for_selector("table, [role='table'], [role='grid'], [role='row']", timeout=7000)
    except PlaywrightError:
        return []

    rows = []
    row_locators = page.locator("table tbody tr, [role='table'] [role='row'], [role='grid'] [role='row'], [data-automationid*='row' i]")
    count = min(row_locators.count(), limit)
    for idx in range(count):
        row = row_locators.nth(idx)
        text = visible_text(row)
        if not text or re.search(r"employee\s+period|date\s+status|period\s+status", text, re.I):
            continue
        cells = [visible_text(row.locator("td, [role='cell'], [role='gridcell']").nth(cell_idx)) for cell_idx in range(row.locator("td, [role='cell'], [role='gridcell']").count())]
        cells = [cell for cell in cells if cell]
        rows.append({"text": text, "cells": cells, "url": _row_url(row)})
    return rows[:limit]


def _extract_list_items(page, *, limit: int) -> list[dict]:
    items = []
    locators = page.locator("li, [role='listitem'], [data-automationid*='card' i]")
    for idx in range(min(locators.count(), limit)):
        locator = locators.nth(idx)
        text = visible_text(locator)
        if not text or not re.search(r"timesheet|approved|draft|submitted|hours|employee|week|fortnight|month", text, re.I):
            continue
        items.append({"text": text, "url": _row_url(locator)})
    return items[:limit]


def _extract_body_rows(page, *, limit: int) -> list[dict]:
    text = visible_text(page.locator("body").first)
    if not text:
        return []
    start = re.search(r"\bFirst Name\s+Surname\s+Period\s+Status\s+Last edited\s+Hours\b", text, re.I)
    if start is None:
        return []
    text = text[start.end() :]
    pattern = re.compile(
        r"(?P<first_name>\S+)\s+"
        r"(?P<surname>.+?)\s+"
        r"(?P<period>(?:Week|Fortnight|Month)\s+ending\s+\d{2}\s+\w{3}\s+\d{4})\s+"
        r"(?P<status>Draft|Submitted|Approved|Rejected)\s+"
        r"(?P<last_edited>\d{2}\s+\w{3}\s+\d{4}\s+\d{2}:\d{2})\s+"
        r"(?P<hours>\d+(?:\.\d+)?)\b",
        re.I,
    )
    rows = []
    for match in pattern.finditer(text):
        row = {key: " ".join(value.split()) for key, value in match.groupdict().items()}
        row["employee"] = f"{row['first_name']} {row['surname']}"
        row["text"] = " ".join(match.group(0).split())
        row["cells"] = [row["first_name"], row["surname"], row["period"], row["status"], row["last_edited"], row["hours"]]
        rows.append(row)
        if len(rows) >= limit:
            break
    return rows


def _extract_period_options(page, *, employee: str | None = None, attempts: int = 10) -> list[dict]:
    _open_period_dropdown(page)
    for attempt in range(attempts):
        options = _period_options_from_texts(_read_visible_boundlist_items(page), employee=employee)
        if options:
            return options
        page.wait_for_timeout(500)
        if attempt % 3 == 2:
            _open_period_dropdown(page)
    return []


def _open_period_dropdown(page) -> bool:
    for factory in (
        lambda p: p.locator("#PeriodID-triggerWrap .x-form-arrow-trigger"),
        lambda p: p.locator("#PeriodID-triggerWrap .x-form-trigger"),
        lambda p: p.locator("#PeriodID-triggerWrap div[class*='trigger']"),
    ):
        locator = factory(page).first
        try:
            locator.click(timeout=1000)
            page.wait_for_timeout(600)
            return True
        except PlaywrightError:
            continue
    try:
        input_locator = page.locator("#PeriodID-inputEl").first
        input_locator.click(timeout=1000)
        input_locator.press("Alt+ArrowDown")
        page.wait_for_timeout(600)
        return True
    except PlaywrightError:
        return False


def _read_visible_boundlist_items(page) -> list[str]:
    try:
        return page.evaluate(_VISIBLE_BOUNDLIST_JS) or []
    except PlaywrightError:
        return []


def _period_options_from_texts(texts: list[str], *, employee: str | None = None) -> list[dict]:
    employee_key = " ".join((employee or "").split()).lower()
    options = []
    seen = set()
    for text in texts:
        clean = " ".join(str(text).split())
        if not clean or clean in seen or clean.lower() == employee_key:
            continue
        seen.add(clean)
        options.append({"period": clean, "value": clean})
    period_like = [option for option in options if re.search(r"ending\s+\d{1,2}\s+\w{3}\s+\d{4}", option["period"], re.I)]
    return period_like or options


def _dismiss_add_timesheet_modal(page) -> None:
    control = first_visible(
        page,
        [
            lambda p: p.get_by_role("button", name=re.compile(r"^\s*cancel\s*$", re.I)),
            lambda p: p.get_by_text(re.compile(r"^\s*cancel\s*$", re.I)),
        ],
        timeout_ms=1500,
    )
    if control is None:
        try:
            page.keyboard.press("Escape")
        except PlaywrightError:
            pass
        return
    try:
        control.click()
    except PlaywrightError:
        pass


def _matching_row_from_body(page, *, employee: str | None, period: str | None, status: str | None) -> dict:
    rows = _extract_body_rows(page, limit=100)
    for row in rows:
        if employee and employee.lower() not in row.get("employee", "").lower():
            continue
        if period and period.lower() not in row.get("period", "").lower():
            continue
        if status and status.lower() != row.get("status", "").lower():
            continue
        return row
    raise ElementNotFoundError("Could not find a visible timesheet matching the supplied filters")


def _require_match_filter(*, employee: str | None, period: str | None, status: str | None) -> None:
    if not any([employee, period, status]):
        raise ValidationError("At least one of --employee, --period, or --status is required for this action")


def _click_timesheet_row(page, row: dict) -> None:
    employee = row.get("employee", "")
    period = row.get("period", "")
    candidates = [
        lambda p: p.get_by_text(re.compile(re.escape(row.get("text", "")), re.I)),
        lambda p: p.get_by_text(re.compile(re.escape(employee), re.I)),
        lambda p: p.get_by_text(re.compile(re.escape(period), re.I)),
    ]
    locator = first_visible(page, candidates, timeout_ms=2000)
    if locator is None:
        raise ElementNotFoundError("Could not find a clickable timesheet row")
    locator.click()


def _click_named_control(page, name: str) -> None:
    pattern = re.compile(re.escape(name), re.I)
    control = require_visible(
        page,
        [
            lambda p: p.get_by_role("button", name=pattern),
            lambda p: p.get_by_role("link", name=pattern),
            lambda p: p.get_by_text(pattern),
        ],
        label=f"{name} control",
        timeout_ms=5000,
    )
    control.click()


def _fill_employee(page, employee: str) -> None:
    input_locator = require_visible(
        page,
        [lambda p: p.locator("#PayeeID-inputEl"), lambda p: p.locator('input[name="payeeID"]'), lambda p: p.get_by_label(re.compile("employee", re.I))],
        label="timesheet employee input",
        timeout_ms=5000,
    )
    _select_combo_option(page, input_locator, employee)


def _select_period(page, period: str) -> str:
    """Open the period dropdown and click the option matching `period`.

    The Add Timesheet period combo is an ExtJS combobox whose store is not
    reachable via window.Ext in this browser context, so we resolve and select
    the option purely through the rendered dropdown list.
    """
    options = _extract_period_options(page)
    match = _resolve_period_text(options, period)
    if match is None:
        available = "; ".join(option["period"] for option in options) or "none found"
        raise ValidationError(
            f"Period {period!r} is not available for this employee. "
            f"Available periods: {available}. Use `timesheets periods --employee ...` to list them."
        )
    _click_period_item(page, match)
    return match


def _resolve_period_text(options: list[dict], period: str) -> str | None:
    needle = " ".join(period.split()).lower()
    texts = [option["period"] for option in options]
    for text in texts:
        if text.lower() == needle:
            return text
    for text in texts:
        lowered = text.lower()
        if needle and (needle in lowered or lowered in needle):
            return text
    return None


def _click_period_item(page, text: str) -> None:
    pattern = re.compile(re.escape(text), re.I)
    for _ in range(3):
        item = first_visible(
            page,
            [
                lambda p: p.locator(".x-boundlist-item", has_text=pattern),
                lambda p: p.locator("[role='option']", has_text=pattern),
            ],
            timeout_ms=1500,
        )
        if item is not None:
            item.click(force=True)
            page.wait_for_timeout(600)
            return
        _open_period_dropdown(page)
    raise ElementNotFoundError(f"Could not select period option {text!r} from the dropdown")



def _select_combo_option(page, input_locator, text: str) -> None:
    human_fill(input_locator, text)
    page.wait_for_timeout(1200)
    if _select_ext_combo_record(input_locator, text):
        page.wait_for_timeout(800)
        return
    pattern = re.compile(re.escape(text), re.I)
    option = first_visible(
        page,
        [
            lambda p: p.locator(".x-boundlist-item", has_text=pattern),
            lambda p: p.locator(".x-combo-list-item", has_text=pattern),
            lambda p: p.locator("[role='option']", has_text=pattern),
        ],
        timeout_ms=1500,
    )
    if option is not None:
        option.click(force=True)
    else:
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")
    page.wait_for_timeout(800)


def _select_ext_combo_record(input_locator, text: str) -> bool:
    try:
        result = input_locator.evaluate(
            """
            (el, text) => {
              const Ext = window.Ext;
              if (!Ext) return false;
              const componentId = (el.id || '').replace(/-inputEl$/, '');
              let combo = componentId ? Ext.getCmp(componentId) : null;
              if (!combo && Ext.ComponentQuery) {
                combo = Ext.ComponentQuery.query('combo').find(c => c.inputEl && c.inputEl.dom === el);
              }
              if (!combo || !combo.getStore) return false;
              const store = combo.getStore();
              if (!store || !store.each) return false;
              const needle = String(text).toLowerCase();
              let match = null;
              store.each(record => {
                if (match) return;
                const data = record && record.data ? record.data : {};
                const values = Object.keys(data).map(key => String(data[key] ?? '').toLowerCase());
                if (values.some(value => value && (value.includes(needle) || needle.includes(value)))) match = record;
              });
              if (!match) return false;
              combo.select(match);
              combo.setValue(match);
              if (combo.setRawValue && combo.displayField) combo.setRawValue(match.get(combo.displayField));
              if (combo.fireEvent) combo.fireEvent('select', combo, [match]);
              return true;
            }
            """,
            text,
            timeout=1000,
        )
        return bool(result)
    except PlaywrightError:
        return False


def _fill_first_hours_input(page, hours: str) -> None:
    if not re.fullmatch(r"\d+(?:\.\d+)?", hours):
        raise ValidationError("--hours must be a positive decimal number")
    input_locator = first_visible(
        page,
        [
            lambda p: p.locator('input[name*="hour" i]'),
            lambda p: p.locator('input[id*="hour" i]'),
            lambda p: p.locator('input[type="text"]'),
            lambda p: p.get_by_role("textbox"),
        ],
        timeout_ms=2500,
    )
    if input_locator is None:
        raise ElementNotFoundError("Could not find an editable hours input. The timesheet may be approved or locked; nothing was saved.")
    human_fill(input_locator, hours)


def _field_values(page) -> dict:
    values = {}
    for name, selector in {
        "employee": "#PayeeID-inputEl",
        "employee_id": 'input[name="payeeID"]',
        "period": "#PeriodID-inputEl",
        "period_sequence": 'input[name="sequenceNumber"]',
    }.items():
        try:
            values[name] = page.locator(selector).first.evaluate("el => el.value", timeout=400) or ""
        except PlaywrightError:
            values[name] = ""
    try:
        values["ext_combos"] = page.evaluate(
            """
            () => {
              const Ext = window.Ext;
              if (!Ext || !Ext.ComponentQuery) return [];
              return Ext.ComponentQuery.query('combo').map(combo => ({
                id: combo.id || '',
                name: combo.name || '',
                value: String(combo.getValue ? combo.getValue() ?? '' : ''),
                raw: String(combo.getRawValue ? combo.getRawValue() ?? '' : ''),
                displayField: combo.displayField || '',
                valueField: combo.valueField || ''
              }));
            }
            """
        )
    except PlaywrightError:
        values["ext_combos"] = []
    return values


def _raise_xero_validation(page) -> None:
    text = visible_text(page.locator("body").first)
    patterns = [
        r"You must assign this employee to a pay frequency before creating a timesheet\.",
        r"The selected employee already has a timesheet for this period\.",
        r"Employee is required\.",
        r"Period is required\.",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            raise ValidationError(match.group(0))


def _extract_detail(page) -> dict:
    text = visible_text(page.locator("body").first)
    detail: dict[str, str | list[dict]] = {}
    title = page.title()
    title_match = re.search(r"Timesheet for the (.+)$", title, re.I)
    if title_match:
        detail["period"] = title_match.group(1).strip()
    for key, pattern in {
        "employee": r"\bEmployee\s+(.+?)\s+Status\b",
        "status": r"\bStatus\s+(Draft|Approved|Submitted|Rejected)\b",
        "week_ending": r"\bWeek ending\s+(\d{2}\s+\w{3}\s+\d{4})\b",
        "monthly_total": r"\bMonthly Total\s+(\d+(?:\.\d+)?)\b",
    }.items():
        match = re.search(pattern, text, re.I)
        if match:
            detail[key] = " ".join(match.group(1).split())
    rows = []
    row_pattern = re.compile(
        r"(?P<rate>[A-Za-z][A-Za-z\s]+?)\s+"
        r"(?P<sun>\d+(?:\.\d+)?)\s+"
        r"(?P<mon>\d+(?:\.\d+)?)\s+"
        r"(?P<tue>\d+(?:\.\d+)?)\s+"
        r"(?P<wed>\d+(?:\.\d+)?)\s+"
        r"(?P<thu>\d+(?:\.\d+)?)\s+"
        r"(?P<fri>\d+(?:\.\d+)?)\s+"
        r"(?P<sat>\d+(?:\.\d+)?)\s+"
        r"(?P<total>\d+(?:\.\d+)?)\b"
    )
    for match in row_pattern.finditer(text):
        rate = " ".join(match.group("rate").split())
        if rate.lower().endswith("hours") and rate.lower() != "ordinary hours":
            rate = rate.rsplit(" ", 1)[0]
        if rate.lower() in {"jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"}:
            continue
        row = {key: " ".join(value.split()) for key, value in match.groupdict().items()}
        row["rate"] = rate
        rows.append(row)
    if rows:
        detail["earnings"] = rows
    return detail


def _row_url(locator) -> str:
    try:
        return locator.locator("a[href]").first.get_attribute("href", timeout=500) or ""
    except PlaywrightError:
        return ""
