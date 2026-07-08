---
name: xero-cli
description: Operate and validate the xero-cli for browser-driven Xero workflows across expenses, mileage, timesheets, sales, purchases, payroll, auth, and live page debugging. Use when an agent needs to run any xero-cli command or discover the current command surface through --help while the CLI is still evolving.
---

# xero-cli

Use this skill when working with the `xero-cli` CLI in this repository. The CLI is under active development, so do not rely on hardcoded command examples beyond help discovery. Always inspect the relevant `--help` output before choosing flags for an operation.

## Project Context

- Package manager: `uv`
- CLI entry point: `xero-cli`
- Browser automation: Camoufox with Playwright sync API
- Persistent profile: `~/.xero-user-cli/profiles/<session>`
- Background browser worker: UNIX socket worker
- Environment config: `.env` resolved independent of the launch directory.
  Priority: `XERO_USER_CLI_ENV_FILE` (explicit path) → nearest `.env` walking up
  from the current working directory → `~/.xero-user-cli/.env`.
- Required environment variables (`XERO_USER`, `SECRET_XERO_PASSWORD`) are loaded
  from those `.env` locations. Empty/whitespace env vars are treated as unset so
  injected placeholders can be filled from `.env`; real non-empty env vars win.

## Operating Principles

- Prefer JSON output for agent workflows when the command supports it.
- Keep create/edit workflows non-destructive unless the user explicitly asks to submit/save/approve.
- Do not use submit/save/approve flags unless the user explicitly requested a real submission.
- Do not clear the browser profile unless the user explicitly asks, because it can remove trusted-device/session state.
- Stopping the worker (`xero-cli session stop`) is safe when needed to reload code; it does not delete the profile and is different from clearing the session.
- If source code changes are made while a worker is running, stop the worker so the next CLI invocation reloads the updated code.
- Do not clear the session as a first response to auth issues; preserving trusted-device state is valuable.
- Never print secrets, passwords, MFA codes, or `.env` contents.
- Run the relevant `--help` command before using a command area, because flags may change while this CLI is being developed.

## Build And Dev Commands

Install/sync dependencies:

```bash
uv sync
```

Compile check (run after Python source changes):

```bash
uv run python -m compileall src
```

## Help Discovery Map

Run these help commands to learn the current supported syntax for each functional area. The CLI is under active development, so always inspect the relevant `--help` output before choosing flags.

### Top-Level Capability Discovery

```bash
uv run xero-cli --help
```

Use this first when unsure what command groups exist. Current groups: `session`, `login`, `screenshot`, `auth`, `expenses`, `timesheets`, `sales`, `purchases`, `payroll`, `accounting`, `debug`.

### Session Management

```bash
uv run xero-cli session --help
uv run xero-cli session clear --help
uv run xero-cli session stop --help
```

`clear` deletes the local browser profile for a session; `stop` stops the background browser worker without deleting the profile.

### Authentication And MFA

```bash
uv run xero-cli login --help
uv run xero-cli auth --help
uv run xero-cli auth status --help
uv run xero-cli auth mfa --help
```

### Screenshots

```bash
uv run xero-cli screenshot --help
```

### Expenses And Mileage

```bash
uv run xero-cli expenses --help
uv run xero-cli expenses list --help
uv run xero-cli expenses create --help
uv run xero-cli expenses view-detail --help
uv run xero-cli expenses edit-detail --help
uv run xero-cli expenses delete-detail --help
uv run xero-cli expenses mileage --help
uv run xero-cli expenses mileage create --help
uv run xero-cli expenses mileage view-detail --help
uv run xero-cli expenses mileage edit-detail --help
uv run xero-cli expenses mileage delete-detail --help
```

### Timesheets

```bash
uv run xero-cli timesheets --help
uv run xero-cli timesheets open --help
uv run xero-cli timesheets list --help
uv run xero-cli timesheets periods --help
uv run xero-cli timesheets create --help
uv run xero-cli timesheets view --help
uv run xero-cli timesheets edit --help
uv run xero-cli timesheets revert-to-draft --help
uv run xero-cli timesheets approve --help
uv run xero-cli timesheets delete --help
```

### Sales

```bash
uv run xero-cli sales --help
uv run xero-cli sales invoices --help
uv run xero-cli sales invoices open --help
uv run xero-cli sales invoices list --help
uv run xero-cli sales invoices create --help
uv run xero-cli sales payment-links --help
uv run xero-cli sales payment-links open --help
uv run xero-cli sales payment-links list --help
uv run xero-cli sales payment-services --help
uv run xero-cli sales payment-services open --help
uv run xero-cli sales payment-services list --help
uv run xero-cli sales quotes --help
uv run xero-cli sales quotes open --help
uv run xero-cli sales quotes list --help
uv run xero-cli sales products --help
uv run xero-cli sales products open --help
uv run xero-cli sales products list --help
uv run xero-cli sales customers --help
uv run xero-cli sales customers open --help
uv run xero-cli sales customers list --help
```

### Purchases

```bash
uv run xero-cli purchases --help
uv run xero-cli purchases bills --help
uv run xero-cli purchases bills open --help
uv run xero-cli purchases bills list --help
uv run xero-cli purchases payments --help
uv run xero-cli purchases payments open --help
uv run xero-cli purchases payments list --help
uv run xero-cli purchases purchase-orders --help
uv run xero-cli purchases purchase-orders open --help
uv run xero-cli purchases purchase-orders list --help
uv run xero-cli purchases suppliers --help
uv run xero-cli purchases suppliers open --help
uv run xero-cli purchases suppliers list --help
```

### Payroll

```bash
uv run xero-cli payroll --help
uv run xero-cli payroll employees --help
uv run xero-cli payroll employees open --help
uv run xero-cli payroll employees list --help
uv run xero-cli payroll leave --help
uv run xero-cli payroll leave open --help
uv run xero-cli payroll leave list --help
```

### Accounting

```bash
uv run xero-cli accounting --help
uv run xero-cli accounting accounts --help
uv run xero-cli accounting accounts list --help
```

### Live Page Debugging

```bash
uv run xero-cli debug --help
uv run xero-cli debug page --help
```

The debug output is intended to expose visible page structure such as headings, buttons, labels, inputs, links, and body text. Do not add logging that exposes credentials, MFA codes, or other secrets.

## Authentication Command Reference

These are the validated stable auth flows. For evolving flags, still cross-check the relevant `--help` output above.

### Login (non-interactive primary flow)

```bash
uv run xero-cli login --json
```

Expected behavior:

- Opens the neutral Xero homepage URL for the configured organisation
  (`$XERO_APP_BASE_URL/homepage`, defaulting to `https://go.xero.com/app/!M0777/homepage`)
- Fills username from `XERO_USER`
- Fills password from `SECRET_XERO_PASSWORD`
- Detects whether the user is authenticated
- Detects MFA and returns a structured `mfa_required` response
- Keeps the browser open in the background worker if MFA is required

### MFA continuation

```bash
uv run xero-cli auth mfa CODE [--no-trust-device] [--timeout SECONDS] --json
```

Expected behavior:

- Reuses the existing worker browser session
- Inserts the MFA code into the current MFA page
- Selects the trust/remember device option if Xero offers it (unless `--no-trust-device`)
- Waits up to `--timeout` seconds (default 120) for Xero to finish
- Continues to the neutral homepage
- Returns authenticated JSON when successful

### Status inspection (read-only)

```bash
uv run xero-cli auth status --json
```

### Manual fallback

```bash
uv run xero-cli login --interactive --manual-timeout 300
```

Use only when the automated flow cannot handle a new Xero authentication/checkpoint variant.

### Authentication success criteria

Before feature work, verify:

```bash
uv run xero-cli session clear
uv run xero-cli login --json
```

If MFA is required:

```bash
uv run xero-cli auth mfa CODE --json
```

Expected successful response:

```json
{
  "ok": true,
  "authenticated": true,
  "url": "https://go.xero.com/app/!yj48m/homepage",
  "state": "logged_in"
}
```
