"""Testes de leitura de info da conta (~/.claude.json)."""
from __future__ import annotations

import json
from pathlib import Path

from claude_dash.account import (
    BILLING_API,
    BILLING_FLAT_RATE,
    AccountInfo,
    read_account_info,
)


def _write_claude_json(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "claude.json"
    path.write_text(json.dumps(data))
    return path


def test_read_account_info_from_oauth_block(tmp_path: Path) -> None:
    path = _write_claude_json(tmp_path, {
        "oauthAccount": {
            "accountUuid": "uuid-a",
            "emailAddress": "leo@example.com",
            "organizationUuid": "uuid-b",
            "hasExtraUsageEnabled": True,
            "billingType": BILLING_FLAT_RATE,
            "displayName": "Leo",
            "organizationRole": "admin",
            "organizationName": "Leo's Org",
        },
        "cachedExtraUsageDisabledReason": "out_of_credits",
        "claudeCodeFirstTokenDate": "2026-03-01",
    })
    acc = read_account_info(path)
    assert acc is not None
    assert acc.email == "leo@example.com"
    assert acc.display_name == "Leo"
    assert acc.organization_role == "admin"
    assert acc.billing_type == BILLING_FLAT_RATE
    assert acc.has_extra_usage_enabled is True
    assert acc.extra_usage_disabled_reason == "out_of_credits"
    assert acc.first_token_date == "2026-03-01"


def test_is_flat_rate_true_for_stripe_subscription() -> None:
    acc = AccountInfo(
        email="", display_name="", organization_name="",
        organization_role="", billing_type=BILLING_FLAT_RATE,
        has_extra_usage_enabled=False, extra_usage_disabled_reason=None,
        first_token_date=None, account_uuid="", organization_uuid="",
    )
    assert acc.is_flat_rate is True
    assert acc.billing_label == "Assinatura"


def test_is_flat_rate_false_for_api() -> None:
    acc = AccountInfo(
        email="", display_name="", organization_name="",
        organization_role="", billing_type=BILLING_API,
        has_extra_usage_enabled=False, extra_usage_disabled_reason=None,
        first_token_date=None, account_uuid="", organization_uuid="",
    )
    assert acc.is_flat_rate is False
    assert "API" in acc.billing_label


def test_read_returns_none_when_file_missing(tmp_path: Path) -> None:
    assert read_account_info(tmp_path / "inexistente.json") is None


def test_read_returns_none_when_no_oauth_block(tmp_path: Path) -> None:
    path = _write_claude_json(tmp_path, {"outraChave": 123})
    assert read_account_info(path) is None


def test_read_returns_none_on_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{ not json")
    assert read_account_info(path) is None


def test_unknown_billing_label_includes_raw(tmp_path: Path) -> None:
    path = _write_claude_json(tmp_path, {
        "oauthAccount": {
            "emailAddress": "x@y.com",
            "billingType": "some_new_tier_2028",
        },
    })
    acc = read_account_info(path)
    assert acc is not None
    assert acc.is_flat_rate is False
    assert "some_new_tier_2028" in acc.billing_label
