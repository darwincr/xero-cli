from xero_user_cli.actions.expenses import _changed_fields_output, _detail_fields_output, _field_values_output, _normalize_expense_row


def test_expense_row_splits_action_title_description_and_spent_at():
    row = _normalize_expense_row(
        {
            "type": "expense",
            "url": "/app/!yj48m/expenses/detail/854fd149-9598-4097-9912-fed2e5a8bf2d?returnToUrl=%2F",
            "status": "Submitted",
            "description": 'Approve Xero Limited "Xero Subscription first month"',
            "date": "3 Jul 2026",
            "category": "400 - Advertising",
            "tax_rate": "10% Tax",
            "amount": "15.60",
        }
    )

    assert row == {
        "type": "expense",
        "url": "/app/!yj48m/expenses/detail/854fd149-9598-4097-9912-fed2e5a8bf2d?returnToUrl=%2F",
        "status": "Submitted",
        "description": "Xero Subscription first month",
        "spent_at": "Xero Limited",
        "date": "3 Jul 2026",
        "account": "400 - Advertising",
        "tax_rate": "10% Tax",
        "amount": "15.60",
        "has_attachment": False,
    }


def test_mileage_row_uses_distance_and_claim_type():
    row = _normalize_expense_row(
        {
            "type": "expense",
            "url": "/app/!yj48m/expenses/detail/a5b17123-ac7e-4cb4-aa66-d59c0e19ecce?returnToUrl=%2F",
            "status": "Submitted",
            "description": '33km "Travel to client meeting"',
            "date": "4 Jul 2026",
            "category": "400 - Advertising",
            "tax_rate": "Reimbursable",
            "amount": "23.10",
        }
    )

    assert row == {
        "type": "mileage",
        "url": "/app/!yj48m/expenses/detail/a5b17123-ac7e-4cb4-aa66-d59c0e19ecce?returnToUrl=%2F",
        "status": "Submitted",
        "description": "Travel to client meeting",
        "date": "4 Jul 2026",
        "account": "400 - Advertising",
        "distance": "33",
        "amount": "23.10",
        "reimbursement_type": "Reimbursable",
        "has_attachment": False,
    }


def test_expense_field_values_use_list_detail_names():
    assert _field_values_output(
        {
            "description": "Taxi",
            "spent_at": "Taxi Co",
            "account": "400 - Advertising",
            "amount": "42.50",
            "distance": "",
            "rate": "",
        },
        "expense",
    ) == {
        "description": "Taxi",
        "spent_at": "Taxi Co",
        "account": "400 - Advertising",
        "amount": "42.50",
    }


def test_mileage_field_values_use_list_detail_names():
    assert _field_values_output(
        {
            "description": "Client visit",
            "spent_at": "",
            "account": "400 - Advertising",
            "amount": "",
            "distance": "33",
            "rate": "0.85",
        },
        "mileage",
    ) == {
        "description": "Client visit",
        "account": "400 - Advertising",
        "distance": "33",
        "rate": "0.85",
    }


def test_changed_fields_drop_internal_and_empty_names():
    assert _changed_fields_output(
        {
            "date": None,
            "description": "Taxi",
            "spent_at": "Taxi Co",
            "account": "400 - Advertising",
            "items": [],
        }
    ) == {
        "description": "Taxi",
        "spent_at": "Taxi Co",
        "account": "400 - Advertising",
    }


def test_delete_fields_match_view_detail_shape():
    fields = {
        "type": "expense",
        "status": "Submitted",
        "description": "Taxi",
        "spent_at": "Taxi Co",
        "date": "8 July 2026",
        "account": "400 - Advertising",
        "amount": "42.50",
        "text": "raw page text",
        "has_attachment": False,
    }

    assert _detail_fields_output(fields, "expense") == {
        "type": "expense",
        "status": "Submitted",
        "description": "Taxi",
        "spent_at": "Taxi Co",
        "date": "8 Jul 2026",
        "account": "400 - Advertising",
        "amount": "42.50",
        "has_attachment": False,
    }
