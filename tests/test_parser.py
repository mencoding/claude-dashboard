"""Testes do parser de transcripts JSONL."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from claude_dash.parser import (
    extract_model,
    extract_timestamp_ms,
    extract_usage,
    iter_entries,
    iter_tool_uses,
)

# --- Fixtures de dados sintéticos ----------------------------------------


USER_ENTRY = {
    "type": "user",
    "sessionId": "abc",
    "timestamp": "2026-04-21T11:00:00.000Z",
    "cwd": "/home/x",
}

ASSISTANT_WITH_USAGE = {
    "type": "assistant",
    "message": {
        "model": "claude-opus-4-7",
        "usage": {
            "input_tokens": 100,
            "output_tokens": 200,
            "cache_read_input_tokens": 800,
            "cache_creation_input_tokens": 500,
            "cache_creation": {
                "ephemeral_1h_input_tokens": 400,
                "ephemeral_5m_input_tokens": 100,
            },
        },
        "content": [{"type": "text", "text": "hi"}],
    },
}

ASSISTANT_WITH_TOOLS = {
    "type": "assistant",
    "message": {
        "model": "claude-opus-4-7",
        "content": [
            {"type": "tool_use", "id": "1", "name": "Bash", "input": {"command": "ls"}},
            {"type": "tool_use", "id": "2", "name": "Read", "input": {"file_path": "/x"}},
            {"type": "text", "text": "done"},
        ],
    },
}


@pytest.fixture
def transcript_file(tmp_path: Path) -> Path:
    path = tmp_path / "t.jsonl"
    with path.open("w") as f:
        for entry in [USER_ENTRY, ASSISTANT_WITH_USAGE, ASSISTANT_WITH_TOOLS]:
            f.write(json.dumps(entry) + "\n")
    return path


# --- iter_entries --------------------------------------------------------


class TestIterEntries:
    def test_reads_all_entries(self, transcript_file: Path) -> None:
        entries = [e for _, e in iter_entries(transcript_file)]
        assert len(entries) == 3
        assert entries[0]["type"] == "user"

    def test_offset_monotonically_increases(self, transcript_file: Path) -> None:
        offsets = [off for off, _ in iter_entries(transcript_file)]
        assert offsets == sorted(offsets)
        assert len(set(offsets)) == len(offsets)

    def test_start_offset_skips_consumed(self, transcript_file: Path) -> None:
        all_entries = list(iter_entries(transcript_file))
        first_offset = all_entries[0][0]
        remaining = list(iter_entries(transcript_file, start_offset=first_offset))
        assert len(remaining) == 2

    def test_ignores_malformed_lines(self, tmp_path: Path) -> None:
        path = tmp_path / "t.jsonl"
        path.write_text('{"type":"user"}\n{ broken\n{"type":"assistant"}\n')
        entries = [e for _, e in iter_entries(path)]
        assert len(entries) == 2
        assert {e["type"] for e in entries} == {"user", "assistant"}

    def test_empty_lines_skipped(self, tmp_path: Path) -> None:
        path = tmp_path / "t.jsonl"
        path.write_text('{"type":"user"}\n\n\n{"type":"assistant"}\n')
        entries = [e for _, e in iter_entries(path)]
        assert len(entries) == 2


# --- extract_usage -------------------------------------------------------


class TestExtractUsage:
    def test_returns_none_for_user(self) -> None:
        assert extract_usage(USER_ENTRY) is None

    def test_returns_none_when_usage_absent(self) -> None:
        assert extract_usage(ASSISTANT_WITH_TOOLS) is None

    def test_parses_full_usage(self) -> None:
        u = extract_usage(ASSISTANT_WITH_USAGE)
        assert u is not None
        assert u.input_tokens == 100
        assert u.output_tokens == 200
        assert u.cache_read == 800
        assert u.cache_creation_1h == 400
        assert u.cache_creation_5m == 100


# --- extract_model --------------------------------------------------------


class TestExtractModel:
    def test_returns_model_for_assistant(self) -> None:
        assert extract_model(ASSISTANT_WITH_USAGE) == "claude-opus-4-7"

    def test_returns_none_for_user(self) -> None:
        assert extract_model(USER_ENTRY) is None

    def test_returns_none_when_model_absent(self) -> None:
        assert extract_model({"type": "assistant", "message": {}}) is None


# --- iter_tool_uses -------------------------------------------------------


class TestIterToolUses:
    def test_yields_tool_names(self) -> None:
        assert list(iter_tool_uses(ASSISTANT_WITH_TOOLS)) == ["Bash", "Read"]

    def test_empty_when_only_text(self) -> None:
        assert list(iter_tool_uses(ASSISTANT_WITH_USAGE)) == []

    def test_empty_for_user_entry(self) -> None:
        assert list(iter_tool_uses(USER_ENTRY)) == []


# --- extract_timestamp_ms ------------------------------------------------


class TestExtractTimestamp:
    def test_parses_z_suffix(self) -> None:
        ms = extract_timestamp_ms({"timestamp": "2026-04-21T11:00:00.000Z"})
        # 2026-04-21T11:00:00 UTC = 1776769200000 ms
        assert ms == 1776769200000

    def test_parses_offset_suffix(self) -> None:
        # Mesmo instante em -03:00 (SP) seria 14:00 UTC
        ms = extract_timestamp_ms({"timestamp": "2026-04-21T11:00:00.000-03:00"})
        assert ms == 1776780000000

    def test_none_when_absent(self) -> None:
        assert extract_timestamp_ms({}) is None

    def test_none_when_malformed(self) -> None:
        assert extract_timestamp_ms({"timestamp": "not-a-date"}) is None
