# xero-user-cli

CLI for driving Xero through a persistent Camoufox browser profile.

## Setup

```bash
uv sync
```

The CLI reads `.env` from the current directory without overriding existing
environment variables:

```bash
XERO_USER=your-xero-email@example.com
SECRET_XERO_PASSWORD=...
```

## Commands

Run `uv run xero-user --help` (or any subcommand's `--help`) for the canonical
list. Summary of the current surface:

### Session management

```bash
uv run xero-user session stop      # stop background worker, keep profile
uv run xero-user session clear     # delete the local browser profile
```

### Authentication

```bash
uv run xero-user auth status --json
uv run xero-user login --json
uv run xero-user login --interactive --manual-timeout 300
uv run xero-user auth mfa CODE --json
uv run xero-user auth mfa CODE --no-trust-device --timeout 60 --json
```

`login` is non-interactive by default. If Xero requires MFA it returns a
structured `mfa_required` response and leaves the browser open in the worker so
`auth mfa CODE` can continue the same session. Use `--interactive` only when the
automated flow cannot handle a new Xero authentication/checkpoint variant.

### Expenses

```bash
uv run xero-user expenses list --json --limit 25
uv run xero-user expenses create \
  --date 2026-07-04 --description "Lunch" --amount 25.50 --spent-at "Cafe" \
  --currency AUD --category "200 - Sales" --tax-rate "GST on Income" \
  --label "team" --payment-due-date 2026-07-04 --receipt-file ./receipt.pdf --json
uv run xero-user expenses mileage \
  --date 2026-07-04 --description "Client visit" --distance 42 --rate 2.50 \
  --category "449 - Motor Vehicle Expenses" --json
uv run xero-user expenses edit-detail \
  --url "https://go.xero.com/app/!M0777/expenses/detail/123" \
  --amount 30.00 --category "Travel" --tax-rate "GST on Income" \
  --item "Parking|Travel|GST on Income|15.00" --json
```

Create/mileage/edit flows are non-destructive by default: they fill the form and
return a summary. Add `--submit` to click Xero's final submit/save/create button.

Expense claim fields: date (Spent on), description, amount (Purchase amount),
spent-at (Spent at; `--merchant` is also accepted), currency, category (Account),
tax-rate, assign-to (customer), label, payment-due-date, receipt-file.

Mileage claim fields: date (Travelled on), description, distance (Mileage to claim
km), rate (Rate per km), category (Account), assign-to (customer), label,
payment-due-date, receipt-file. Currency (AUD) is implied in the rate label and
Xero computes the total (distance x rate).

`edit-detail --item` accepts `description|account|tax-rate|amount` lines and may
be repeated for multiple itemised lines.

### Debug

```bash
uv run xero-user debug page --json
uv run xero-user debug page --url "https://go.xero.com/app/!M0777/expenses" --json
uv run xero-user debug page --click-button "Save" --click-button "OK" --json --limit 80
```

Emits visible page controls (headings, buttons, inputs, labels, links) and body
text as JSON. Useful for selector discovery against the live Xero DOM. Never
prints credentials or MFA codes.

## Notes

If Xero requires MFA or an unrecognized verification step, the CLI keeps the
Camoufox window open long enough for manual completion and then rechecks the
session.
