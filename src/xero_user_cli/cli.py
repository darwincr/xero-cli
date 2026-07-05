from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from xero_user_cli.conf import load_dotenv_file
from xero_user_cli.exceptions import AuthenticationError, ElementNotFoundError, InteractiveAuthenticationRequired, MfaRequired, ScreenshotError, ValidationError
from xero_user_cli.session import XeroSession, clear_profile, session_lock

logger = logging.getLogger("xero_user_cli")

_ERROR_TYPES = [
    (MfaRequired, "mfa_required"),
    (InteractiveAuthenticationRequired, "interactive_authentication_required"),
    (AuthenticationError, "authentication"),
    (ElementNotFoundError, "element_not_found"),
    (ValidationError, "validation"),
    (ScreenshotError, "screenshot"),
    (RuntimeError, "configuration"),
]


def _out(text: str) -> None:
    sys.stdout.write(f"{text}\n")
    sys.stdout.flush()


def _err(text: str) -> None:
    print(text, file=sys.stderr)


def _error_type(exc: Exception) -> str | None:
    for cls, name in _ERROR_TYPES:
        if isinstance(exc, cls):
            return name
    return None


def _render(command: str, result: dict, as_json: bool) -> None:
    if as_json:
        _out(json.dumps(result, ensure_ascii=False, default=str))
        return
    if command in {"login", "auth-status"}:
        _out(f"authenticated: {result.get('authenticated')} ({result.get('url')})")
    elif command == "auth-mfa":
        _out(f"authenticated: {result.get('authenticated')} ({result.get('url')})")
    elif command == "expenses-list":
        expenses = result.get("expenses") or []
        _out("(no expenses found)" if not expenses else "\n".join(expense.get("text", "") for expense in expenses))
    elif command == "timesheets-list":
        timesheets = result.get("timesheets") or []
        _out("(no timesheets found)" if not timesheets else "\n".join(timesheet.get("text", "") for timesheet in timesheets))
    elif command == "timesheets-periods":
        periods = result.get("periods") or []
        _out("(no periods found)" if not periods else "\n".join(period.get("period", "") for period in periods))
    elif command == "timesheets-open":
        _out(f"opened: {result.get('url')}")
    elif command == "timesheets-create":
        _out(f"saved: {result.get('saved')} ({result.get('url')})")
    elif command == "timesheets-view":
        _out(f"opened: {result.get('url')}")
    elif command == "timesheets-edit":
        _out(f"saved: {result.get('saved')} ({result.get('url')})")
    elif command == "timesheets-revert-to-draft":
        _out(f"reverted: {result.get('reverted')} ({result.get('url')})")
    elif command == "timesheets-approve":
        _out(f"approved: {result.get('approved')} ({result.get('url')})")
    elif command == "timesheets-delete":
        _out(f"deleted: {result.get('deleted')} ({result.get('url')})")
    elif command in {"expenses-create", "expenses-mileage"}:
        _out(f"submitted: {result.get('submitted')} ({result.get('url')})")
    elif command == "expenses-edit-detail":
        _out(f"saved: {result.get('url')}")
    elif command == "debug-page":
        _out(json.dumps(result, ensure_ascii=False, default=str))
    elif command == "screenshot":
        _out(str(result.get("path") or ""))
    elif command == "session-clear":
        _out(f"cleared {result.get('name')}")
    else:
        _out("\n".join(f"{key}: {value}" for key, value in result.items()))


def _verb_login(session, args) -> dict:
    from xero_user_cli.actions.auth import ensure_logged_in, interactive_login

    if args.interactive:
        return interactive_login(session, timeout=args.manual_timeout)
    return ensure_logged_in(session, wait_for_manual_seconds=args.manual_timeout)


def _verb_auth_status(session, args) -> dict:
    from xero_user_cli.actions.auth import auth_status

    return auth_status(session)


def _verb_auth_mfa(session, args) -> dict:
    from xero_user_cli.actions.auth import submit_mfa_code

    return submit_mfa_code(session, args.code, trust_device=not args.no_trust_device, timeout=args.timeout)


def _verb_expenses_list(session, args) -> dict:
    from xero_user_cli.actions.expenses import list_expenses

    return list_expenses(session, limit=args.limit)


def _expense_form_from_args(args):
    from xero_user_cli.actions.expenses import ExpenseForm

    return ExpenseForm(
        date=args.date,
        description=args.description,
        category=args.category,
        assign_to=getattr(args, "assign_to", None),
        label=getattr(args, "label", None),
        payment_due_date=getattr(args, "payment_due_date", None),
        receipt_file=args.receipt_file,
        submit=args.submit,
        amount=getattr(args, "amount", None),
        merchant=getattr(args, "merchant", None),
        currency=getattr(args, "currency", None),
        tax_rate=getattr(args, "tax_rate", None),
        distance=getattr(args, "distance", None),
        rate=getattr(args, "rate", None),
    )


def _verb_expenses_create(session, args) -> dict:
    from xero_user_cli.actions.expenses import create_expense

    return create_expense(session, _expense_form_from_args(args))


def _verb_expenses_mileage(session, args) -> dict:
    from xero_user_cli.actions.expenses import create_mileage

    return create_mileage(session, _expense_form_from_args(args))


def _verb_expenses_edit_detail(session, args) -> dict:
    from xero_user_cli.actions.expenses import edit_expense_detail, parse_line_items

    return edit_expense_detail(session, url=args.url, amount=args.amount, category=args.category, tax_rate=args.tax_rate, items=parse_line_items(args.item or []))


def _verb_timesheets_open(session, args) -> dict:
    from xero_user_cli.actions.timesheets import open_timesheets

    return open_timesheets(session)


def _verb_timesheets_list(session, args) -> dict:
    from xero_user_cli.actions.timesheets import list_timesheets

    return list_timesheets(session, limit=args.limit)


def _verb_timesheets_periods(session, args) -> dict:
    from xero_user_cli.actions.timesheets import list_timesheet_periods

    return list_timesheet_periods(session, employee=args.employee)


def _verb_timesheets_create(session, args) -> dict:
    from xero_user_cli.actions.timesheets import TimesheetForm, create_timesheet

    return create_timesheet(session, TimesheetForm(employee=args.employee, period=args.period, save=args.save))


def _verb_timesheets_view(session, args) -> dict:
    from xero_user_cli.actions.timesheets import view_timesheet

    return view_timesheet(session, employee=args.employee, period=args.period, status=args.status)


def _verb_timesheets_edit(session, args) -> dict:
    from xero_user_cli.actions.timesheets import edit_timesheet

    return edit_timesheet(session, employee=args.employee, period=args.period, status=args.status, hours=args.hours, save=args.save)


def _verb_timesheets_revert_to_draft(session, args) -> dict:
    from xero_user_cli.actions.timesheets import revert_timesheet_to_draft

    return revert_timesheet_to_draft(session, employee=args.employee, period=args.period, status=args.status, confirm=args.confirm)


def _verb_timesheets_approve(session, args) -> dict:
    from xero_user_cli.actions.timesheets import approve_timesheet

    return approve_timesheet(session, employee=args.employee, period=args.period, status=args.status, confirm=args.confirm)


def _verb_timesheets_delete(session, args) -> dict:
    from xero_user_cli.actions.timesheets import delete_timesheet

    return delete_timesheet(session, employee=args.employee, period=args.period, status=args.status, confirm=args.confirm)


def _verb_debug_page(session, args) -> dict:
    from xero_user_cli.actions.debug import page_summary

    return page_summary(session, url=args.url, limit=args.limit, click_buttons=args.click_button)


def _verb_screenshot(session, args) -> dict:
    from xero_user_cli.actions.screenshot import take_screenshot

    return take_screenshot(session, output=args.output)


_VERBS = {
    "login": _verb_login,
    "auth-status": _verb_auth_status,
    "auth-mfa": _verb_auth_mfa,
    "expenses-list": _verb_expenses_list,
    "expenses-create": _verb_expenses_create,
    "expenses-mileage": _verb_expenses_mileage,
    "expenses-edit-detail": _verb_expenses_edit_detail,
    "timesheets-open": _verb_timesheets_open,
    "timesheets-list": _verb_timesheets_list,
    "timesheets-periods": _verb_timesheets_periods,
    "timesheets-create": _verb_timesheets_create,
    "timesheets-view": _verb_timesheets_view,
    "timesheets-edit": _verb_timesheets_edit,
    "timesheets-revert-to-draft": _verb_timesheets_revert_to_draft,
    "timesheets-approve": _verb_timesheets_approve,
    "timesheets-delete": _verb_timesheets_delete,
    "debug-page": _verb_debug_page,
    "screenshot": _verb_screenshot,
}


def _error_payload(exc: Exception, error_type: str) -> dict:
    payload = {"ok": False, "error": {"type": error_type, "message": str(exc)}}
    if error_type == "mfa_required":
        payload["state"] = "mfa_required"
        payload["next_command"] = "uv run xero-user auth mfa CODE"
    elif error_type == "interactive_authentication_required":
        payload["next_command"] = "uv run xero-user login --manual-timeout 300"
    return payload


def _execute_verb(args, session) -> int:
    try:
        _render(args.verb, _VERBS[args.verb](session, args), args.json)
        return 0
    except Exception as exc:  # noqa: BLE001
        error_type = _error_type(exc)
        if error_type is None:
            raise
        if args.json:
            _out(json.dumps(_error_payload(exc, error_type), ensure_ascii=False, default=str))
            return 1
        _err(f"error: {error_type}: {exc}")
        return 1


def _run_verb_local(args) -> int:
    with session_lock(args.name):
        session = XeroSession(args.name)
        with session:
            return _execute_verb(args, session)


def _run_verb(args, argv: list[str]) -> int:
    if os.environ.get("XERO_USER_CLI_WORKER") == "1":
        return _run_verb_local(args)
    from xero_user_cli.worker import run_via_worker

    return run_via_worker(args.name, _argv_with_output_path(args, argv))


def _argv_with_output_path(args, argv: list[str]) -> list[str]:
    output = getattr(args, "output", None)
    if output is None:
        return argv
    rewritten = []
    skip_next = False
    for item in argv:
        if skip_next:
            skip_next = False
            continue
        if item == "--output":
            skip_next = True
            continue
        if item.startswith("--output="):
            continue
        rewritten.append(item)
    rewritten.extend(["--output", str(output.expanduser().resolve())])
    return rewritten


def _cmd_session_clear(args) -> int:
    from xero_user_cli.worker import stop_worker

    stop_worker(args.name)
    with session_lock(args.name):
        clear_profile(args.name)
    _render("session-clear", {"name": args.name, "cleared": True}, args.json)
    return 0


def _cmd_session_stop(args) -> int:
    from xero_user_cli.worker import stop_worker

    stop_worker(args.name)
    _render("session-stop", {"name": args.name, "stopped": True}, args.json)
    return 0


def _add_shared_expense_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--date", help="Date spent/travelled (YYYY-MM-DD)")
    parser.add_argument("--description", help="Description / purpose")
    parser.add_argument("--category", help="Account to code the claim to (account name or code)")
    parser.add_argument("--assign-to", dest="assign_to", help="Customer to bill the claim back to (optional)")
    parser.add_argument("--label", help="Label to tag the claim with (optional)")
    parser.add_argument("--payment-due-date", dest="payment_due_date", help="Payment due date (YYYY-MM-DD)")
    parser.add_argument("--receipt-file", help="Path to a receipt file to upload")
    parser.add_argument("--submit", action="store_true", help="Click Xero's submit/save/create button after filling fields")


def _add_expense_form_args(parser: argparse.ArgumentParser) -> None:
    _add_shared_expense_args(parser)
    parser.add_argument("--amount", help="Purchase amount")
    parser.add_argument("--spent-at", "--merchant", dest="merchant", help="Spent at / merchant name")
    parser.add_argument("--currency", help="Currency code, e.g. AUD")
    parser.add_argument("--tax-rate", dest="tax_rate", help="Tax/GST rate to select")


def _add_mileage_form_args(parser: argparse.ArgumentParser) -> None:
    _add_shared_expense_args(parser)
    parser.add_argument("--distance", help="Mileage to claim (km)")
    parser.add_argument("--rate", help="Reimbursement rate per km")


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--session", "--name", dest="name", default=os.environ.get("XERO_USER_CLI_SESSION", "default"), help="Session/profile name")
    common.add_argument("--json", action="store_true", help="Emit full JSON instead of a short summary")

    parser = argparse.ArgumentParser(prog="xero-user", description="Drive Xero through Camoufox")
    sub = parser.add_subparsers(dest="cmd", required=True)

    session_cmd = sub.add_parser("session", help="Manage local browser session state")
    session_sub = session_cmd.add_subparsers(dest="subcmd", required=True)
    session_sub.add_parser("clear", parents=[common], help="Delete the local browser profile for a session")
    session_sub.add_parser("stop", parents=[common], help="Stop the background browser worker without deleting the profile")

    p_login = sub.add_parser("login", parents=[common], help="Log in or verify the current Xero session")
    p_login.add_argument("--interactive", action="store_true", help="Open Xero and wait while you complete login manually, including Trust this device")
    p_login.add_argument("--manual-timeout", type=int, default=120, help="Seconds to wait for MFA/manual verification (default: 120)")

    p_screenshot = sub.add_parser("screenshot", parents=[common], help="Save a screenshot of the current browser page")
    p_screenshot.add_argument("--output", type=Path, default=Path("screenshot.png"), help="Screenshot file path (default: screenshot.png)")

    auth_cmd = sub.add_parser("auth", help="Inspect authentication state")
    auth_sub = auth_cmd.add_subparsers(dest="auth_cmd", required=True)
    auth_sub.add_parser("status", parents=[common], help="Report the current authentication state")
    p_auth_mfa = auth_sub.add_parser("mfa", parents=[common], help="Submit an MFA code into the active Xero login session")
    p_auth_mfa.add_argument("code", help="MFA verification code")
    p_auth_mfa.add_argument("--no-trust-device", action="store_true", help="Do not select Trust/Remember this device if Xero offers it")
    p_auth_mfa.add_argument("--timeout", type=int, default=120, help="Seconds to wait for Xero to finish after MFA submission")

    expenses_cmd = sub.add_parser("expenses", help="List and create Xero expenses")
    expenses_sub = expenses_cmd.add_subparsers(dest="expenses_cmd", required=True)

    p_list = expenses_sub.add_parser("list", parents=[common], help="List visible expenses")
    p_list.add_argument("--limit", type=int, default=25, help="Maximum rows to return (default: 25)")

    p_create = expenses_sub.add_parser("create", parents=[common], help="Open and optionally submit a new expense")
    _add_expense_form_args(p_create)

    p_mileage = expenses_sub.add_parser("mileage", parents=[common], help="Open and optionally submit a new mileage claim")
    _add_mileage_form_args(p_mileage)

    p_edit_detail = expenses_sub.add_parser("edit-detail", parents=[common], help="Edit an existing expense detail by URL")
    p_edit_detail.add_argument("--url", required=True, help="Expense detail URL or path")
    p_edit_detail.add_argument("--amount", help="Replacement purchase amount")
    p_edit_detail.add_argument("--category", help="Replacement expense category/account text to select")
    p_edit_detail.add_argument("--tax-rate", help="Replacement tax/GST rate text to select")
    p_edit_detail.add_argument(
        "--item",
        action="append",
        help="Itemised line as 'description|account|tax-rate|amount'. Repeat for multiple lines.",
    )

    timesheets_cmd = sub.add_parser("timesheets", help="Open and inspect Xero Payroll timesheets")
    timesheets_sub = timesheets_cmd.add_subparsers(dest="timesheets_cmd", required=True)
    timesheets_sub.add_parser("open", parents=[common], help="Open the Xero Payroll timesheets page")
    p_timesheets_list = timesheets_sub.add_parser("list", parents=[common], help="List visible timesheets")
    p_timesheets_list.add_argument("--limit", type=int, default=25, help="Maximum rows to return (default: 25)")
    p_timesheets_periods = timesheets_sub.add_parser("periods", parents=[common], help="List the valid pay periods that can be used to create a timesheet for an employee")
    p_timesheets_periods.add_argument("--employee", required=True, help="Employee name to look up available periods for")
    p_timesheets_create = timesheets_sub.add_parser("create", parents=[common], help="Open and optionally save a new timesheet")
    p_timesheets_create.add_argument("--employee", help="Employee name to select")
    p_timesheets_create.add_argument("--period", help="Timesheet period text to select")
    p_timesheets_create.add_argument("--save", action="store_true", help="Click Save after filling fields")
    p_timesheets_view = timesheets_sub.add_parser("view", parents=[common], help="Open a visible timesheet matching filters")
    p_timesheets_view.add_argument("--employee", help="Employee name contains this text")
    p_timesheets_view.add_argument("--period", help="Period contains this text, e.g. '04 Aug 2026'")
    p_timesheets_view.add_argument("--status", help="Status equals this text")
    p_timesheets_edit = timesheets_sub.add_parser("edit", parents=[common], help="Open and optionally save edits to a visible timesheet")
    p_timesheets_edit.add_argument("--employee", help="Employee name contains this text")
    p_timesheets_edit.add_argument("--period", help="Period contains this text, e.g. '04 Aug 2026'")
    p_timesheets_edit.add_argument("--status", help="Status equals this text")
    p_timesheets_edit.add_argument("--hours", help="Replacement hours for the first editable hours field")
    p_timesheets_edit.add_argument("--save", action="store_true", help="Click Save after applying edits")
    p_timesheets_revert = timesheets_sub.add_parser("revert-to-draft", parents=[common], help="Revert an approved/submitted timesheet to draft")
    p_timesheets_revert.add_argument("--employee", help="Employee name contains this text")
    p_timesheets_revert.add_argument("--period", help="Period contains this text, e.g. '04 Aug 2026'")
    p_timesheets_revert.add_argument("--status", help="Status equals this text")
    p_timesheets_revert.add_argument("--confirm", action="store_true", help="Actually revert the matched timesheet to draft")
    p_timesheets_approve = timesheets_sub.add_parser("approve", parents=[common], help="Approve a draft timesheet matching filters")
    p_timesheets_approve.add_argument("--employee", help="Employee name contains this text")
    p_timesheets_approve.add_argument("--period", help="Period contains this text, e.g. '04 Aug 2026'")
    p_timesheets_approve.add_argument("--status", help="Status equals this text")
    p_timesheets_approve.add_argument("--confirm", action="store_true", help="Actually approve the matched timesheet")
    p_timesheets_delete = timesheets_sub.add_parser("delete", parents=[common], help="Delete a visible timesheet matching filters")
    p_timesheets_delete.add_argument("--employee", help="Employee name contains this text")
    p_timesheets_delete.add_argument("--period", help="Period contains this text, e.g. '04 Aug 2026'")
    p_timesheets_delete.add_argument("--status", help="Status equals this text")
    p_timesheets_delete.add_argument("--confirm", action="store_true", help="Actually delete the matched timesheet")

    debug_cmd = sub.add_parser("debug", help="Inspect the current Xero page without printing secrets")
    debug_sub = debug_cmd.add_subparsers(dest="debug_cmd", required=True)
    p_debug_page = debug_sub.add_parser("page", parents=[common], help="Emit visible page controls and text as JSON")
    p_debug_page.add_argument("--url", help="URL to open before inspection")
    p_debug_page.add_argument("--click-button", action="append", help="Click a visible button by text before inspection. Repeat to click multiple buttons in order")
    p_debug_page.add_argument("--limit", type=int, default=80, help="Maximum controls per section (default: 80)")
    return parser


def _configure_logging() -> None:
    level = os.environ.get("XERO_USER_CLI_LOG", "INFO").upper()
    logging.basicConfig(level=level, stream=sys.stderr, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _parse_args(argv=None):
    args = build_parser().parse_args(argv)
    if args.cmd == "auth":
        args.verb = f"auth-{args.auth_cmd}"
    elif args.cmd == "expenses":
        args.verb = f"expenses-{args.expenses_cmd}"
    elif args.cmd == "timesheets":
        args.verb = f"timesheets-{args.timesheets_cmd}"
    elif args.cmd == "debug":
        args.verb = f"debug-{args.debug_cmd}"
    else:
        args.verb = args.cmd
    return args


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    load_dotenv_file()
    args = _parse_args(argv)
    _configure_logging()
    if args.cmd == "session":
        if args.subcmd == "stop":
            return _cmd_session_stop(args)
        return _cmd_session_clear(args)
    return _run_verb(args, argv)


if __name__ == "__main__":
    raise SystemExit(main())
