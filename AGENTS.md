# AGENTS.md - xero-cli

## Project Goal

Build a Python CLI that drives Xero through a real Camoufox browser session. The CLI should support Xero workflows starting from the expenses funcionalities then expanding to other areas of the Xero web app.

The current organisation path is:

```text
https://go.xero.com/app/!M0777
```

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

Use these areas and URLs as the next implementation map after expenses. Add each
area incrementally: navigation/open first, then list/read-only commands, then
non-submit create/edit flows, and only then explicit submit/save commands.

### Sales Tab

| Area | URL |
|---|---|
| Invoices / Accounts receivable search | `https://go.xero.com/AccountsReceivable/Search.aspx` |
| Payment links | `https://go.xero.com/app/!M0777/payment-links` |
| Payment services | `https://go.xero.com/app/!M0777/payment-services` |
| Quotes | `https://go.xero.com/app/!M0777/quotes-list?` |
| Products and services | `https://go.xero.com/app/!M0777/products-and-services` |
| Customers | `https://go.xero.com/app/!M0777/contacts/customers` |

### Purchases Tab

| Area | URL |
|---|---|
| Bills | `https://go.xero.com/app/!M0777/bills/list/all` |
| Payments | `https://go.xero.com/app/!M0777/payments` |
| Purchase orders | `https://go.xero.com/app/!M0777/purchase-orders` |
| Suppliers | `https://go.xero.com/app/!M0777/contacts/suppliers` |

### Payroll Tab

| Area | URL |
|---|---|
| Employees | `https://go.xero.com/app/!M0777/payroll/employees` |
| Leave | `https://payroll.xero.com/Leave?CID=!M0777` |
| Timesheets | `https://payroll.xero.com/Timesheets?CID=!M0777` |

## Engineering Notes

- Keep changes minimal and selector-specific.
- Preserve the background worker behavior; it is required for MFA handoff and browser continuity.
- Do not rotate browser fingerprints per run. The current session uses a stable macOS Camoufox profile to help trusted-device persistence.
- Do not print or log `SECRET_XERO_PASSWORD` or MFA codes.
- Prefer structured JSON output for agent use.
- Prefer non-destructive defaults for create/edit workflows.

## Current Known Risk

Xero UI selectors may vary by account, feature flags, viewport, or rollout. The auth flow is now validated through MFA handoff, but the selectors still need live hardening against the actual Xero pages.
