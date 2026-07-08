from xero_user_cli.actions.areas import AREAS


CLI_AREAS = {
    "sales-invoices",
    "sales-payment-links",
    "sales-payment-services",
    "sales-quotes",
    "sales-products",
    "sales-customers",
    "purchases-bills",
    "purchases-payments",
    "purchases-purchase-orders",
    "purchases-suppliers",
    "payroll-employees",
    "payroll-leave",
}


def test_every_cli_area_has_mapping():
    assert CLI_AREAS == set(AREAS)


def test_every_area_url_is_non_empty():
    for area in AREAS.values():
        assert area.url


def test_area_urls_are_org_specific_or_known_legacy_routes():
    known_legacy = {"sales-invoices"}
    for key, area in AREAS.items():
        if key in known_legacy:
            assert area.url == "https://go.xero.com/AccountsReceivable/Search.aspx"
        elif key == "payroll-leave":
            assert area.url.startswith("https://payroll.xero.com/Leave?CID=!")
        else:
            assert area.url.startswith("https://go.xero.com/app/!")
