from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

BROWSER_DEFAULT_TIMEOUT_MS = 30_000
BROWSER_WIDTH = 1920
BROWSER_HEIGHT = 1080
BROWSER_LOGIN_TIMEOUT_MS = 90_000
HUMAN_TYPE_DELAY_MS = 45
HUMAN_MOUSE_MAX_TIME_S = 0.225
WORKER_IDLE_TIMEOUT_S = 900


def xero_cli_home() -> Path:
    return Path(os.environ.get("XERO_USER_CLI_HOME") or Path.home() / ".xero-user-cli")


def browser_headless() -> bool:
    return os.environ.get("XERO_USER_CLI_HEADLESS", "").lower() in {"1", "true", "yes", "on"}


ENV_FILE_OVERRIDE = "XERO_USER_CLI_ENV_FILE"


def dotenv_search_paths() -> list[Path]:
    """Ordered, de-duplicated .env candidates, highest priority first.

    Resolution is intentionally robust so the CLI finds credentials regardless of
    which working directory it is launched from (the background worker and the
    browser-agent both invoke it from directories that may not hold the .env):

    1. ``XERO_USER_CLI_ENV_FILE`` explicit path (absolute location wins).
    2. The nearest ``.env`` walking up from the current working directory.
    3. ``~/.xero-user-cli/.env`` — stable across cwds and reachable by the worker.
    """
    candidates: list[Path] = []

    explicit = os.environ.get(ENV_FILE_OVERRIDE)
    if explicit:
        candidates.append(Path(explicit).expanduser())

    try:
        cwd = Path.cwd()
    except OSError:
        cwd = None
    if cwd is not None:
        for directory in [cwd, *cwd.parents]:
            candidates.append(directory / ".env")

    candidates.append(xero_cli_home() / ".env")

    seen: set[Path] = set()
    ordered: list[Path] = []
    for path in candidates:
        try:
            resolved = path.expanduser()
        except (OSError, RuntimeError):
            resolved = path
        if resolved in seen:
            continue
        seen.add(resolved)
        ordered.append(resolved)
    return ordered


def load_dotenv_file(path: Path | None = None) -> dict[str, str]:
    """Load KEY=VALUE pairs from .env files without overriding real env vars.

    An existing env var whose value is empty/whitespace-only is treated as unset
    so injected placeholders (e.g. docker-compose ``${XERO_USER:-}``) can still be
    filled from a .env file. Values already set to a non-empty string win.
    """
    search_paths = [path] if path is not None else dotenv_search_paths()

    loaded: dict[str, str] = {}
    for env_path in search_paths:
        if env_path is None or not env_path.exists():
            continue
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            parsed = _parse_dotenv_line(raw_line)
            if parsed is None:
                continue
            key, value = parsed
            if not value:
                continue
            if os.environ.get(key, "").strip():
                continue
            os.environ[key] = value
            loaded[key] = value
    return loaded


def _parse_dotenv_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if stripped.startswith("export "):
        stripped = stripped[len("export ") :].strip()
    if "=" not in stripped:
        return None
    key, value = stripped.split("=", 1)
    key = key.strip()
    if not key:
        return None
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    elif " #" in value:
        value = value.split(" #", 1)[0].rstrip()
    return key, value


def _xero_app_base_url() -> str:
    value = (os.environ.get("XERO_APP_BASE_URL") or "").strip()
    if not value:
        org = (os.environ.get("XERO_ORG") or "!M0777").strip()
        return f"https://go.xero.com/app/{org}"

    value = value.rstrip("/")
    homepage_suffix = "/homepage"
    if value.endswith(homepage_suffix):
        value = value[: -len(homepage_suffix)]
    return value


def _xero_org_from_base_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or parsed.netloc != "go.xero.com":
        raise RuntimeError(f"XERO_APP_BASE_URL must look like https://go.xero.com/app/!ORG, got: {base_url}")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 2 and parts[0] == "app":
        return parts[1]
    raise RuntimeError(f"XERO_APP_BASE_URL must look like https://go.xero.com/app/!ORG, got: {base_url}")


load_dotenv_file()

XERO_APP_BASE_URL = _xero_app_base_url()
XERO_ORG = _xero_org_from_base_url(XERO_APP_BASE_URL)
XERO_HOME_URL = f"{XERO_APP_BASE_URL}/homepage"
XERO_EXPENSES_URL = f"{XERO_APP_BASE_URL}/expenses"
XERO_CREATE_EXPENSE_URL = f"{XERO_APP_BASE_URL}/expenses/detail/create-new"
XERO_CREATE_MILEAGE_URL = f"{XERO_APP_BASE_URL}/expenses/detail/create-new-mileage"
XERO_INVOICES_URL = "https://go.xero.com/AccountsReceivable/Search.aspx"
XERO_CHART_OF_ACCOUNTS_URL = "https://go.xero.com/GeneralLedger/ChartOfAccounts.aspx"
XERO_CREATE_INVOICE_URL = f"{XERO_APP_BASE_URL}/invoicing"
XERO_PAYMENT_LINKS_URL = f"{XERO_APP_BASE_URL}/payment-links"
XERO_PAYMENT_SERVICES_URL = f"{XERO_APP_BASE_URL}/payment-services"
XERO_QUOTES_URL = f"{XERO_APP_BASE_URL}/quotes-list?"
XERO_PRODUCTS_AND_SERVICES_URL = f"{XERO_APP_BASE_URL}/products-and-services"
XERO_CUSTOMERS_URL = f"{XERO_APP_BASE_URL}/contacts/customers"
XERO_BILLS_URL = f"{XERO_APP_BASE_URL}/bills/list/all"
XERO_PAYMENTS_URL = f"{XERO_APP_BASE_URL}/payments"
XERO_PURCHASE_ORDERS_URL = f"{XERO_APP_BASE_URL}/purchase-orders"
XERO_SUPPLIERS_URL = f"{XERO_APP_BASE_URL}/contacts/suppliers"
XERO_EMPLOYEES_URL = f"{XERO_APP_BASE_URL}/payroll/employees"
XERO_LEAVE_URL = f"https://payroll.xero.com/Leave?CID={XERO_ORG}"
XERO_TIMESHEETS_URL = f"https://payroll.xero.com/Timesheets?CID={XERO_ORG}"


def xero_credentials() -> tuple[str, str]:
    user = (os.environ.get("XERO_USER") or os.environ.get("XERO_USERNAME") or "").strip()
    password = os.environ.get("SECRET_XERO_PASSWORD") or os.environ.get("XERO_PASSWORD") or ""
    if not user or not password:
        searched = ", ".join(str(path) for path in dotenv_search_paths())
        raise RuntimeError(
            "XERO_USER and SECRET_XERO_PASSWORD must be set in the environment, "
            f"via {ENV_FILE_OVERRIDE}, or in a .env file. Searched: {searched}"
        )
    return user, password
