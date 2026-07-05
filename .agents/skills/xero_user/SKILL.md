---
name: xero_user
description: Operate and validate the xero-user Python CLI for browser-driven Xero workflows. Use when an agent needs to authenticate to Xero, inspect session state, manage the browser worker/profile, list/create expenses, create mileage claims, inspect live Xero pages, or discover the current command surface through --help while the CLI is still evolving.
---

# xero-user CLI

Use this skill when working with the `xero-user` CLI in this repository. The CLI is under active development, so do not rely on hardcoded command examples beyond help discovery. Always inspect the relevant `--help` output before choosing flags for an operation.

## Project Context

- Package manager: `uv`
- CLI entry point: `xero-user`
- Browser automation: Camoufox with Playwright sync API
- Persistent profile: `~/.xero-user-cli/profiles/<session>`
- Background browser worker: UNIX socket worker
- Environment config: `.env` in the current working directory
- Required environment variables are loaded by the CLI from `.env` when present.

## Operating Principles

- Prefer JSON output for agent workflows when the command supports it.
- Keep create/edit workflows non-destructive unless the user explicitly asks to submit/save/approve.
- Do not use submit flags unless the user explicitly requested a real submission.
- Do not clear the browser profile unless the user explicitly asks, because it can remove trusted-device/session state.
- Stopping the worker is safe when needed to reload code; it does not delete the profile.
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

Run these help commands to learn the current supported syntax for each functional area.

### Top-Level Capability Discovery

Use this first when unsure what command groups exist:

```bash
uv run xero-user --help
```

### Session Management

Use this for stopping the worker, clearing the profile, or learning current session subcommands:

```bash
uv run xero-user session --help
```

If a session subcommand exists and you need its exact flags, inspect that subcommand's help before using it.

### Authentication And MFA

Use these to learn the current login, auth status, and MFA flows:

```bash
uv run xero-user login --help
uv run xero-user auth --help
```

After discovering auth subcommands, inspect the specific subcommand help before using it, for example status or MFA continuation.

### Expenses Overview

Use this to discover available expense operations:

```bash
uv run xero-user expenses --help
```

### Listing Expenses

Use this before reading visible Xero expenses:

```bash
uv run xero-user expenses list --help
```

Prefer JSON output if supported. Listing is read-only.

### Creating Expenses

Use this before filling or submitting an expense form:

```bash
uv run xero-user expenses create --help
```

Default behavior should be non-destructive. Only use the submit/save/approve flag shown in help when the user explicitly asks to create/submit the real expense.

### Creating Mileage Claims

Use this before filling or submitting a mileage claim:

```bash
uv run xero-user expenses mileage --help
```

Default behavior should be non-destructive. Only use the submit/save/approve flag shown in help when the user explicitly asks to create/submit the real claim.

### Live Page Debugging

Use this to inspect the current or target Xero page when selectors or fields are uncertain:

```bash
uv run xero-user debug --help
```

After discovering debug subcommands, inspect the specific debug subcommand help before using it.

The debug output is intended to expose visible page structure such as headings, buttons, labels, inputs, links, and body text. Do not add logging that exposes credentials, MFA codes, or other secrets.

## Validation Workflow

When validating the CLI after code changes:

1. Run the Python compile check for source changes.
2. Run top-level and relevant subcommand help checks.
3. If code was changed and the worker may already be running, stop the worker without clearing the profile so the next CLI invocation imports the updated code.
4. Run read-only or non-submit smoke checks first.
5. Only run submit/save/approve workflows with explicit user approval.

Discover the exact commands and flags for steps 2-5 through the relevant `--help` commands above.

## Authentication Workflow Guidance

- Before feature work, verify whether the browser session is authenticated using the current auth/status help flow.
- If login is required, inspect `login --help` and use the current login workflow.
- If MFA is required, keep the worker/browser session alive and inspect the current MFA help flow.
- Do not clear the session as a first response to auth issues; preserving trusted-device state is valuable.

## Authentication Command Reference

These are the validated stable auth flows. For evolving flags, still cross-check the relevant `--help` output.

### Login (non-interactive primary flow)

```bash
uv run xero-user login --json
```

Expected behavior:

- Opens the neutral Xero homepage URL: `https://go.xero.com/app/!M0777/homepage`
- Fills username from `XERO_USER`
- Fills password from `SECRET_XERO_PASSWORD`
- Detects whether the user is authenticated
- Detects MFA and returns a structured `mfa_required` response
- Keeps the browser open in the background worker if MFA is required

### MFA continuation

```bash
uv run xero-user auth mfa CODE [--no-trust-device] [--timeout SECONDS] --json
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
uv run xero-user auth status --json
```

### Manual fallback

```bash
uv run xero-user login --interactive --manual-timeout 300
```

Use only when the automated flow cannot handle a new Xero authentication/checkpoint variant.

### Authentication success criteria

Before feature work, verify:

```bash
uv run xero-user session clear
uv run xero-user login --json
```

If MFA is required:

```bash
uv run xero-user auth mfa CODE --json
```

Expected successful response:

```json
{
  "ok": true,
  "authenticated": true,
  "url": "https://go.xero.com/app/!M0777/homepage",
  "state": "logged_in"
}
```

## Expense Workflow Guidance

- Start with the expense group help.
- Use list help for read-only validation.
- Use create or mileage help before filling forms.
- Treat all create/mileage operations as dry-runs unless the user explicitly asks to submit.
- If Xero UI fields do not fill or selectors are uncertain, use debug page help to inspect the live page and then adjust selectors minimally.

## Worker/Profile Guidance

- The CLI usually routes commands through a background worker so the browser can stay open across MFA and subsequent actions.
- If source code changes are made while a worker is running, stop the worker so the next command reloads code.
- Stopping the worker is different from clearing the session. Clearing removes browser profile state and should require explicit user intent.

## When To Modify Code

Modify code when:

- Help output shows the command exists but live behavior fails.
- Xero's UI changed and selectors need hardening.
- A workflow lacks safe validation or returns unstructured output.
- The user asks to add a new workflow or field.

Keep changes minimal, selector-specific, and non-destructive by default.
