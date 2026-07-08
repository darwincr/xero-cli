# xero-cli

CLI for driving Xero through a persistent Camoufox browser profile.

## Setup

```bash
uv sync
```

The CLI loads credentials from `.env` regardless of which directory you launch
it from. It checks, in priority order: `XERO_USER_CLI_ENV_FILE` (an explicit
path), the nearest `.env` found walking up from the current directory, then
`~/.xero-user-cli/.env`. Real (non-empty) environment variables always win;
empty/whitespace values are treated as unset so a `.env` value can fill them:

```bash
XERO_USER=your-xero-email@example.com
SECRET_XERO_PASSWORD=...
XERO_APP_BASE_URL=https://go.xero.com/app/!yj48m
```

Set `XERO_APP_BASE_URL` to choose the Xero organisation the CLI opens, for
example `https://go.xero.com/app/!yj48m` for the demo company. `XERO_ORG=!yj48m`
is also accepted if you prefer storing only the organisation id. If neither is
set, the CLI defaults to `https://go.xero.com/app/!M0777`. Stop any running
worker with `uv run xero-cli session stop` after changing these values so the
background browser process reloads the environment.

Tip: for tools that invoke `xero-cli` from an unrelated working directory (for
example a browser-agent workspace), either set
`XERO_USER_CLI_ENV_FILE=/abs/path/to/.env` or place the credentials in
`~/.xero-user-cli/.env` so the background worker always finds them.

## Commands

Run `uv run xero-cli --help` (or any subcommand's `--help`) for the canonical
list. Summary of the current surface:

### Session management

```bash
uv run xero-cli session stop      # stop background worker, keep profile
uv run xero-cli session clear     # delete the local browser profile
```

### Authentication

```bash
uv run xero-cli auth status --json
uv run xero-cli login --json
uv run xero-cli login --interactive --manual-timeout 300
uv run xero-cli auth mfa CODE --json
uv run xero-cli auth mfa CODE --no-trust-device --timeout 60 --json
```

`login` is non-interactive by default. If Xero requires MFA it returns a
structured `mfa_required` response and leaves the browser open in the worker so
`auth mfa CODE` can continue the same session. Use `--interactive` only when the
automated flow cannot handle a new Xero authentication/checkpoint variant.

### Expenses

```bash
uv run xero-cli expenses list --json --limit 25
uv run xero-cli expenses create \
  --date 2026-07-04 --description "Lunch" --amount 25.50 --spent-at "Cafe" \
  --currency AUD --category "200 - Sales" --tax-rate "GST on Income" \
  --label "team" --payment-due-date 2026-07-04 --receipt-file ./receipt.pdf --json
uv run xero-cli expenses mileage \
  --date 2026-07-04 --description "Client visit" --distance 42 --rate 2.50 \
  --category "449 - Motor Vehicle Expenses" --json
uv run xero-cli expenses edit-detail \
  --url "$XERO_APP_BASE_URL/expenses/detail/123" \
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

### Sales, purchases, and payroll areas

The planned non-expense areas currently support safe navigation and read-only
listing of visible rows/items:

```bash
uv run xero-cli sales invoices list --json --limit 25
uv run xero-cli sales payment-links open --json
uv run xero-cli sales payment-services list --json
uv run xero-cli sales quotes list --json
uv run xero-cli sales products list --json
uv run xero-cli sales customers list --json

uv run xero-cli purchases bills list --json
uv run xero-cli purchases payments open --json
uv run xero-cli purchases purchase-orders list --json
uv run xero-cli purchases suppliers list --json

uv run xero-cli payroll employees list --json
uv run xero-cli payroll leave list --json
```

Each area supports `open` and `list`. Listing is read-only; pages without visible
records return `items: []` plus a page summary for selector discovery.

### Debug

```bash
uv run xero-cli debug page --json
uv run xero-cli debug page --url "$XERO_APP_BASE_URL/expenses" --json
uv run xero-cli debug page --click-button "Save" --click-button "OK" --json --limit 80
```

Emits visible page controls (headings, buttons, inputs, labels, links) and body
text as JSON. Useful for selector discovery against the live Xero DOM. Never
prints credentials or MFA codes.

## Notes

If Xero requires MFA or an unrecognized verification step, the CLI keeps the
Camoufox window open long enough for manual completion and then rechecks the
session.
