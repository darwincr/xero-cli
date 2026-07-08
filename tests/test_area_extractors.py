from xero_user_cli.actions.areas import _looks_like_chrome, _regex_rows


def test_regex_rows_extracts_purchase_orders():
    text = "Header PO-0001 Acme Supplies 10.00 Draft PO-0002 Example Co 20.00 Approved Showing items 1-2"

    rows = _regex_rows(text, r"(PO-\d+\s+.*?)(?=\s+PO-\d+|\s+Showing items|$)", limit=10)

    assert [row["text"] for row in rows] == ["PO-0001 Acme Supplies 10.00 Draft", "PO-0002 Example Co 20.00 Approved"]


def test_regex_rows_respects_limit():
    text = "PO-0001 A PO-0002 B"

    rows = _regex_rows(text, r"(PO-\d+\s+.*?)(?=\s+PO-\d+|$)", limit=1)

    assert len(rows) == 1


def test_navigation_chrome_is_filtered():
    assert _looks_like_chrome("Files Files", None)
    assert _looks_like_chrome("Organisation Add new organisation", None)
    assert _looks_like_chrome("Anything", "https://my.xero.com/app")


def test_leave_body_rows_parse():
    text = "AB Alex Brown Annual Leave 01 Jul 2026 Approved CD Casey Drew Personal (Sick/Carer's) Leave 02 Jul 2026 Requested"
    pattern = r"([A-Z]{2}\s+[A-Z][a-z]+\s+[A-Z][a-z]+\s+(?:Annual Leave|Personal \(Sick/Carer.s\) Leave).*?)(?=\s+[A-Z]{2}\s+[A-Z][a-z]+\s+[A-Z][a-z]+\s+(?:Annual Leave|Personal \(Sick/Carer.s\) Leave)|$)"

    rows = _regex_rows(text, pattern, limit=10)

    assert len(rows) == 2
    assert rows[0]["text"].startswith("AB Alex Brown Annual Leave")
