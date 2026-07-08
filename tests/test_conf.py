import importlib
import os

import pytest


def load_conf(monkeypatch, tmp_path, **env):
    for key in ["XERO_APP_BASE_URL", "XERO_ORG", "XERO_USER_CLI_ENV_FILE"]:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    import xero_user_cli.conf as conf

    return importlib.reload(conf)


def test_default_base_url(monkeypatch, tmp_path):
    conf = load_conf(monkeypatch, tmp_path)

    assert conf.XERO_APP_BASE_URL == "https://go.xero.com/app/!M0777"
    assert conf.XERO_ORG == "!M0777"


def test_org_fallback_builds_base_url(monkeypatch, tmp_path):
    conf = load_conf(monkeypatch, tmp_path, XERO_ORG="!DEMO")

    assert conf.XERO_APP_BASE_URL == "https://go.xero.com/app/!DEMO"
    assert conf.XERO_LEAVE_URL == "https://payroll.xero.com/Leave?CID=!DEMO"


def test_strips_homepage_from_base_url(monkeypatch, tmp_path):
    conf = load_conf(monkeypatch, tmp_path, XERO_APP_BASE_URL="https://go.xero.com/app/!DEMO/homepage/")

    assert conf.XERO_APP_BASE_URL == "https://go.xero.com/app/!DEMO"


def test_rejects_malformed_base_url(monkeypatch, tmp_path):
    with pytest.raises(RuntimeError, match="XERO_APP_BASE_URL"):
        load_conf(monkeypatch, tmp_path, XERO_APP_BASE_URL="https://example.com/app/!DEMO")


def test_payroll_urls_derive_from_org(monkeypatch, tmp_path):
    conf = load_conf(monkeypatch, tmp_path, XERO_APP_BASE_URL="https://go.xero.com/app/!PAYROLL")

    assert conf.XERO_LEAVE_URL.endswith("CID=!PAYROLL")
    assert conf.XERO_TIMESHEETS_URL.endswith("CID=!PAYROLL")
