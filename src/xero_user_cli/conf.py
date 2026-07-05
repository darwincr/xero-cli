from __future__ import annotations

import os
from pathlib import Path

XERO_ORG = "!M0777"
XERO_APP_BASE_URL = f"https://go.xero.com/app/{XERO_ORG}"
XERO_HOME_URL = f"{XERO_APP_BASE_URL}/homepage"
XERO_EXPENSES_URL = f"{XERO_APP_BASE_URL}/expenses"
XERO_CREATE_EXPENSE_URL = f"{XERO_APP_BASE_URL}/expenses/detail/create-new"
XERO_CREATE_MILEAGE_URL = f"{XERO_APP_BASE_URL}/expenses/detail/create-new-mileage"
XERO_TIMESHEETS_URL = f"https://payroll.xero.com/Timesheets?CID={XERO_ORG}"

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


def load_dotenv_file(path: Path | None = None) -> dict[str, str]:
    """Load KEY=VALUE pairs from .env without overriding existing env vars."""
    env_path = path or Path.cwd() / ".env"
    if not env_path.exists():
        return {}

    loaded = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        parsed = _parse_dotenv_line(raw_line)
        if parsed is None:
            continue
        key, value = parsed
        if key in os.environ:
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


def xero_credentials() -> tuple[str, str]:
    user = os.environ.get("XERO_USER") or os.environ.get("XERO_USERNAME")
    password = os.environ.get("SECRET_XERO_PASSWORD") or os.environ.get("XERO_PASSWORD")
    if not user or not password:
        raise RuntimeError("XERO_USER and SECRET_XERO_PASSWORD must be set in the environment or .env")
    return user, password
