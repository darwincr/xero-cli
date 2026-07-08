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

Use these areas and URLs as the next implementation map after expenses. Add each
area incrementally: navigation/open first, then list/read-only commands, then
non-submit create/edit flows, and only then explicit submit/save commands.

### Sales Tab

| Area | URL |
|---|---|
| Invoices / Accounts receivable search | `https://go.xero.com/AccountsReceivable/Search.aspx` |
| Payment links | `$XERO_APP_BASE_URL/payment-links` |
| Payment services | `$XERO_APP_BASE_URL/payment-services` |
| Quotes | `$XERO_APP_BASE_URL/quotes-list?` |
| Products and services | `$XERO_APP_BASE_URL/products-and-services` |
| Customers | `$XERO_APP_BASE_URL/contacts/customers` |

### Purchases Tab

| Area | URL |
|---|---|
| Bills | `$XERO_APP_BASE_URL/bills/list/all` |
| Payments | `$XERO_APP_BASE_URL/payments` |
| Purchase orders | `$XERO_APP_BASE_URL/purchase-orders` |
| Suppliers | `$XERO_APP_BASE_URL/contacts/suppliers` |

### Payroll Tab

| Area | URL |
|---|---|
| Employees | `$XERO_APP_BASE_URL/payroll/employees` |
| Leave | `https://payroll.xero.com/Leave?CID=$XERO_ORG` |
| Timesheets | `https://payroll.xero.com/Timesheets?CID=$XERO_ORG` |

## Engineering Notes

- Keep changes minimal and selector-specific.
- Preserve the background worker behavior; it is required for MFA handoff and browser continuity.
- Do not rotate browser fingerprints per run. The current session uses a stable macOS Camoufox profile to help trusted-device persistence.
- Do not print or log `SECRET_XERO_PASSWORD` or MFA codes.
- Prefer structured JSON output for agent use.
- Prefer non-destructive defaults for create/edit workflows.

## Current Known Risk

Xero UI selectors may vary by account, feature flags, viewport, or rollout. The auth flow is now validated through MFA handoff, but the selectors still need live hardening against the actual Xero pages.
