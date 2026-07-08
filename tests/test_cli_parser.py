from xero_user_cli.cli import _parse_args, build_parser


def test_top_level_area_groups_exist():
    help_text = build_parser().format_help()

    assert "sales" in help_text
    assert "purchases" in help_text
    assert "payroll" in help_text


def test_sales_invoices_list_parser_values():
    args = _parse_args(["sales", "invoices", "list", "--json", "--limit", "5"])

    assert args.verb == "area-list"
    assert args.area_key == "sales-invoices"
    assert args.limit == 5
    assert args.json is True


def test_area_open_parser_values():
    args = _parse_args(["sales", "invoices", "open"])

    assert args.verb == "area-open"
    assert args.area_key == "sales-invoices"


def test_purchases_bills_list_parser_values():
    args = _parse_args(["purchases", "bills", "list"])

    assert args.verb == "area-list"
    assert args.area_key == "purchases-bills"


def test_payroll_leave_list_parser_values():
    args = _parse_args(["payroll", "leave", "list"])

    assert args.verb == "area-list"
    assert args.area_key == "payroll-leave"


def test_sales_invoices_create_parser_values():
    args = _parse_args(["sales", "invoices", "create", "--contact", "Demo Co", "--unit-price", "10.00"])

    assert args.verb == "sales-invoices-create"
    assert args.area_key == "sales-invoices"
    assert args.contact == "Demo Co"
    assert args.unit_price == "10.00"


def test_expense_create_force_create_spent_at_parser_value():
    args = _parse_args(["expenses", "create", "--spent-at", "Account", "--force-create-spent-at"])

    assert args.verb == "expenses-create"
    assert args.merchant == "Account"
    assert args.force_create_spent_at is True
