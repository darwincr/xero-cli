# AGENTS.md - xero-cli

## Project Goal

Build a Python CLI that drives Xero through a real Camoufox browser session. The CLI should support Xero workflows starting from the expenses funcionalities then expanding to other areas of the Xero web app.

The default organisation path is:

```text
https://go.xero.com/app/!M0777
```

Set `XERO_APP_BASE_URL` to target another organisation, such as the Xero demo
company. Commands should derive organisation-specific URLs from that setting
rather than hardcoding `!M0777`.

## Current Stack

- Python package managed with `uv`
- CLI entry point: `xero-cli`
- Browser automation: Camoufox + Playwright sync API
- Persistent browser profile: `~/.xero-user-cli/profiles/<session>`
- Background browser worker: UNIX socket worker in `src/xero_user_cli/worker.py`
- Environment config: `.env` resolved robustly (does not depend on the launch
  directory). Resolution order, highest priority first: `XERO_USER_CLI_ENV_FILE`
  (explicit path), the nearest `.env` walking up from the current working
  directory, then `~/.xero-user-cli/.env`. Empty/whitespace env vars are treated
  as unset so injected placeholders (e.g. docker-compose `${XERO_USER:-}`) can be
  filled from `.env`; real non-empty env vars always win.

Required environment variables:

```text
XERO_USER=...
SECRET_XERO_PASSWORD=...
```

## CLI Operations

Specific CLI commands, flags, auth flow details, build/dev commands, and expected JSON responses live in the `xero-cli` skill (`.agents/skills/xero-cli/SKILL.md`). Refer to that skill for operational command reference; this file intentionally does not duplicate CLI command syntax.


## Planned Xero Areas

Remaining URLs discovered from the live Settings page
(`$XERO_APP_BASE_URL/settings`). Already-shipped areas are omitted: expenses,
mileage, timesheets, invoice create, and the open/list flows for invoices,
payment-links, payment-services, quotes, products, customers, bills, payments,
purchase-orders, suppliers, employees, and leave.

Organized by URL host/path hierarchy so the structure doubles as a todo map.
Add each area incrementally: navigation/open first, then list/read-only
commands, then non-submit create/edit flows, and only then explicit
submit/save commands.

### `go.xero.com/app/!{org}` (main SPA)

| Area | URL |
|---|---|
| Sales overview | `$XERO_APP_BASE_URL/sales-overview` |
| Expense settings | `$XERO_APP_BASE_URL/expenses/settings` |
| All contacts | `$XERO_APP_BASE_URL/contacts/all` |
| All projects | `$XERO_APP_BASE_URL/projects` |
| Time entries | `$XERO_APP_BASE_URL/projects/time-entries` |
| Staff time overview | `$XERO_APP_BASE_URL/projects/staff-time-overview` |
| Staff cost rates | `$XERO_APP_BASE_URL/projects/settings/staff-rates` |
| Payroll history | `$XERO_APP_BASE_URL/activity-summary/payroll` |
| History and notes | `$XERO_APP_BASE_URL/activity-summary` |
| Assurance dashboard | `$XERO_APP_BASE_URL/assurance-dashboard` |
| Short-term cash flow | `$XERO_APP_BASE_URL/cashflow` |
| Business snapshot | `$XERO_APP_BASE_URL/business-snapshot/` |
| Taxable payments annual report | `$XERO_APP_BASE_URL/tpar` |
| Organisation details | `$XERO_APP_BASE_URL/organisation-details` |
| Users | `$XERO_APP_BASE_URL/settings/users` |
| Reporting preferences | `$XERO_APP_BASE_URL/globalsettings` |
| Activity Statement settings | `$XERO_APP_BASE_URL/bas/bas-settings` |
| Bank rules | `https://go.xero.com/app/bank-rules` |
| New payment link | `$XERO_APP_BASE_URL/payment-links/create` |
| Hubdoc connection | `$XERO_APP_BASE_URL/connect-hubdoc` |

### `go.xero.com/Accounts*` (legacy Receivable/Payable ASPX)

| Area | URL |
|---|---|
| New invoice | `https://go.xero.com/AccountsReceivable/Edit.aspx` |
| New quote | `https://go.xero.com/Accounts/Receivable/Quotes/New` |
| Purchases overview | `https://go.xero.com/Accounts/Payable/Dashboard/` |
| New bill | `https://go.xero.com/AccountsPayable/Edit.aspx` |
| New purchase order | `https://go.xero.com/Accounts/Payable/PurchaseOrders/New` |
| Find and recode | `https://go.xero.com/Accounts/Recoding` |

### `go.xero.com/Bank*` (Banking)

| Area | URL |
|---|---|
| Bank accounts | `https://go.xero.com/Bank/BankAccounts.aspx` |
| Transfer money | `https://go.xero.com/Bank/Transfer.aspx` |
| Spend money | `https://go.xero.com/Banking/Account/#select/spend` |
| Receive money | `https://go.xero.com/Banking/Account/#select/receive` |

### `go.xero.com/GeneralLedger` / `Journal`

| Area | URL |
|---|---|
| Chart of accounts | `https://go.xero.com/GeneralLedger/ChartOfAccounts.aspx` |
| Manual journals | `https://go.xero.com/Journal/Search.aspx` |
| New manual journal | `https://go.xero.com/Journal/Edit.aspx` |

### `go.xero.com/Setup` (financial / tax setup)

| Area | URL |
|---|---|
| Financial settings | `https://go.xero.com/Setup/FinancialSettings.aspx` |
| Currencies | `https://go.xero.com/Setup/CurrencyRates.aspx` |
| Tracking categories | `https://go.xero.com/Setup/Tracking.aspx` |
| Conversion balances | `https://go.xero.com/Setup/Welcome/EditConversionBalances.aspx` |
| Tax rates | `https://go.xero.com/Setup/TaxRates.aspx` |

### `go.xero.com/Settings` / `InvoiceSettings` / `Contacts`

| Area | URL |
|---|---|
| Connected apps | `https://go.xero.com/Settings/ConnectedApps/` |
| Email settings | `https://go.xero.com/Settings/Email/` |
| Xero to Xero | `https://go.xero.com/Settings/Xero2Xero/` |
| Invoice settings | `https://go.xero.com/InvoiceSettings/InvoiceSettings.aspx` |
| New contact | `https://go.xero.com/Contacts/Edit.aspx` |
| Custom contact links | `https://go.xero.com/Contacts/ContactLinks.aspx` |

### `go.xero.com/Docs` / `Reporting`

| Area | URL |
|---|---|
| Files | `https://go.xero.com/Docs/Folders` |
| Export accounting data | `https://go.xero.com/Reporting/GLExport/` |

### `reporting.xero.com/!{org}`

| Area | URL |
|---|---|
| All reports | `https://reporting.xero.com/$XERO_ORG` |
| Account Transactions | `https://reporting.xero.com/$XERO_ORG/v2/Run/New/1009` |
| Activity Statement | `https://reporting.xero.com/$XERO_ORG/v2/Run/New/690e802c-2842-4039-88a7-d07893312608` |
| Aged Payables Summary | `https://reporting.xero.com/$XERO_ORG/v2/Run/New/1400` |
| Aged Receivables Summary | `https://reporting.xero.com/$XERO_ORG/v2/Run/New/1001` |
| Balance Sheet | `https://reporting.xero.com/$XERO_ORG/v2/Run/New/1017` |
| Profit and Loss | `https://reporting.xero.com/$XERO_ORG/v2/Run/New/1016` |

### `payroll.xero.com` (CID=$XERO_ORG)

| Area | URL |
|---|---|
| Payroll overview | `https://payroll.xero.com/Home?CID=$XERO_ORG` |
| Pay employees | `https://payroll.xero.com/PayRun?CID=$XERO_ORG` |
| Superannuation | `https://payroll.xero.com/Superannuation?CID=$XERO_ORG` |
| Single Touch Payroll | `https://payroll.xero.com/SingleTouch?CID=$XERO_ORG` |
| Payroll settings | `https://payroll.xero.com/Settings?CID=$XERO_ORG` |

### `fixedassets.xero.com` (CID=$XERO_ORG)

| Area | URL |
|---|---|
| Fixed assets | `https://fixedassets.xero.com/?CID=$XERO_ORG&fromNav=true#assets` |
| Fixed asset settings | `https://fixedassets.xero.com/?fromNav=settings&CID=$XERO_ORG#settings` |

## Engineering Notes

- Keep changes minimal and selector-specific.
- Preserve the background worker behavior; it is required for MFA handoff and browser continuity.
- Do not rotate browser fingerprints per run. The current session uses a stable macOS Camoufox profile to help trusted-device persistence.
- Do not print or log `SECRET_XERO_PASSWORD` or MFA codes.
- Prefer structured JSON output for agent use.
- Prefer non-destructive defaults for create/edit workflows.

## Current Known Risk

Xero UI selectors may vary by account, feature flags, viewport, or rollout. The auth flow is now validated through MFA handoff, but the selectors still need live hardening against the actual Xero pages.
