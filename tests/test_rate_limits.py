"""Testes do capturador e leitor de rate limits."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from claude_dash.rate_limit_capture import capture_from_json
from claude_dash.rate_limits import (
    format_reset_delta,
    global_worst_case,
    read_all,
    read_for_session,
)


@pytest.fixture
def cap_dir(tmp_path: Path) -> Path:
    return tmp_path / "rate-limits"


def _sample_statusline_json(
    sid: str = "sid-abc",
    five_pct: float = 13,
    seven_pct: float = 18,
    five_resets_in_s: int = 3600 * 3,
    seven_resets_in_s: int = 3600 * 24 * 5,
) -> str:
    now_s = int(time.time())
    return json.dumps({
        "session_id": sid,
        "model": {"id": "claude-opus-4-7"},
        "rate_limits": {
            "five_hour": {
                "used_percentage": five_pct,
                "resets_at": now_s + five_resets_in_s,
            },
            "seven_day": {
                "used_percentage": seven_pct,
                "resets_at": now_s + seven_resets_in_s,
            },
        },
        "context_window": {"used_percentage": 12},
    })


class TestCapture:
    def test_captures_rate_limits(self, cap_dir: Path) -> None:
        raw = _sample_statusline_json(sid="abc", five_pct=42)
        ok = capture_from_json(raw, capture_dir=cap_dir)
        assert ok is True
        assert (cap_dir / "abc.json").is_file()

    def test_captures_nothing_on_invalid_json(self, cap_dir: Path) -> None:
        assert capture_from_json("{ not json", capture_dir=cap_dir) is False

    def test_captures_nothing_without_rate_limits(self, cap_dir: Path) -> None:
        raw = json.dumps({"session_id": "abc", "model": {"id": "x"}})
        assert capture_from_json(raw, capture_dir=cap_dir) is False

    def test_captures_nothing_without_session_id(self, cap_dir: Path) -> None:
        raw = json.dumps({
            "rate_limits": {"five_hour": {"used_percentage": 10}}
        })
        assert capture_from_json(raw, capture_dir=cap_dir) is False

    def test_capture_is_atomic(self, cap_dir: Path) -> None:
        """Deve gravar via rename, não deixando .tmp para trás."""
        raw = _sample_statusline_json(sid="sid-z")
        capture_from_json(raw, capture_dir=cap_dir)
        assert (cap_dir / "sid-z.json").is_file()
        assert not (cap_dir / "sid-z.tmp").exists()


class TestRead:
    def test_read_for_session(self, cap_dir: Path) -> None:
        capture_from_json(_sample_statusline_json(sid="s1", five_pct=67), cap_dir)
        snap = read_for_session("s1", cap_dir)
        assert snap is not None
        assert snap.session_id == "s1"
        assert snap.five_hour_pct == 67
        assert snap.is_fresh is True

    def test_read_all_returns_dict(self, cap_dir: Path) -> None:
        capture_from_json(_sample_statusline_json(sid="s1"), cap_dir)
        capture_from_json(_sample_statusline_json(sid="s2", five_pct=90), cap_dir)
        result = read_all(cap_dir)
        assert set(result.keys()) == {"s1", "s2"}

    def test_read_for_absent_session_returns_none(self, cap_dir: Path) -> None:
        assert read_for_session("nao-existe", cap_dir) is None

    def test_read_all_empty_when_dir_missing(self, tmp_path: Path) -> None:
        assert read_all(tmp_path / "nao-existe") == {}


class TestGlobalWorstCase:
    def test_worst_case_picks_highest_5h_pct(self, cap_dir: Path) -> None:
        capture_from_json(_sample_statusline_json(sid="low", five_pct=10), cap_dir)
        capture_from_json(_sample_statusline_json(sid="high", five_pct=87), cap_dir)
        capture_from_json(_sample_statusline_json(sid="mid", five_pct=50), cap_dir)
        worst = global_worst_case(cap_dir)
        assert worst is not None
        assert worst.session_id == "high"
        assert worst.five_hour_pct == 87

    def test_worst_case_none_when_empty(self, cap_dir: Path) -> None:
        assert global_worst_case(cap_dir) is None


class TestFormatResetDelta:
    def test_zero_returns_empty(self) -> None:
        assert format_reset_delta(0) == ""
        assert format_reset_delta(-1) == ""

    def test_under_hour(self) -> None:
        assert format_reset_delta(42 * 60) == "42m"

    def test_hours_and_minutes(self) -> None:
        assert format_reset_delta(3 * 3600 + 15 * 60) == "3h15m"

    def test_days(self) -> None:
        assert format_reset_delta(2 * 86400 + 3 * 3600) == "2d3h"


class TestResetsInSeconds:
    def test_positive_seconds_to_reset(self, cap_dir: Path) -> None:
        # Timestamp de reset 1 hora no futuro
        capture_from_json(
            _sample_statusline_json(sid="x", five_resets_in_s=3600),
            cap_dir,
        )
        snap = read_for_session("x", cap_dir)
        assert snap is not None
        # Tolerância: 3600 ± 5 segundos (round-trip via timestamps)
        assert 3590 <= snap.five_hour_resets_in_seconds <= 3600
