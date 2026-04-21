"""Testes de extract_turns e helpers da view session."""
from __future__ import annotations

import json
from pathlib import Path

from claude_dash.aggregator import extract_turns
from claude_dash.discover import TranscriptRef
from claude_dash.models import Turn, Usage


def _make_ref(tmp: Path, entries: list[dict]) -> TranscriptRef:
    p = tmp / "t.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    return TranscriptRef(session_id="test", path=p, workspace="-ws",
                         mtime_ms=int(p.stat().st_mtime * 1000))


def test_extract_turns_one_per_assistant_with_usage(tmp_path: Path) -> None:
    entries = [
        {"type": "user", "timestamp": "2026-04-21T10:00:00Z"},
        {"type": "assistant", "timestamp": "2026-04-21T10:00:05Z",
         "message": {"model": "claude-opus-4-7",
                     "usage": {"input_tokens": 100, "output_tokens": 50},
                     "content": [
                         {"type": "tool_use", "id": "1", "name": "Bash", "input": {}},
                     ]}},
        {"type": "user", "timestamp": "2026-04-21T10:01:00Z"},
        {"type": "assistant", "timestamp": "2026-04-21T10:01:10Z",
         "message": {"model": "claude-opus-4-7",
                     "usage": {"input_tokens": 200, "output_tokens": 80},
                     "content": [{"type": "text", "text": "done"}]}},
    ]
    turns = extract_turns(_make_ref(tmp_path, entries))
    assert len(turns) == 2
    assert turns[0].index == 0
    assert turns[0].usage.input_tokens == 100
    assert turns[0].usage.output_tokens == 50
    assert turns[0].tools_called == ["Bash"]
    assert turns[1].index == 1
    assert turns[1].usage.input_tokens == 200
    assert turns[1].tools_called == []


def test_extract_turns_skips_assistant_without_usage(tmp_path: Path) -> None:
    """Turnos sem usage (ex: placeholder messages) não viram Turn."""
    entries = [
        {"type": "assistant", "timestamp": "2026-04-21T10:00:00Z",
         "message": {"model": "m",  # sem usage
                     "content": [{"type": "text", "text": "hi"}]}},
        {"type": "assistant", "timestamp": "2026-04-21T10:00:10Z",
         "message": {"model": "m",
                     "usage": {"input_tokens": 5}}},
    ]
    turns = extract_turns(_make_ref(tmp_path, entries))
    assert len(turns) == 1
    assert turns[0].usage.input_tokens == 5


def test_extract_turns_fallback_timestamp(tmp_path: Path) -> None:
    """Turnos sem timestamp herdam o da entry anterior que tinha."""
    entries = [
        {"type": "user", "timestamp": "2026-04-21T10:00:00Z"},
        {"type": "assistant",  # sem timestamp
         "message": {"model": "m",
                     "usage": {"input_tokens": 10}}},
    ]
    turns = extract_turns(_make_ref(tmp_path, entries))
    assert len(turns) == 1
    # Herdou timestamp do user anterior (10:00 UTC)
    assert turns[0].timestamp_ms == 1776765600000


def test_turn_cost_estimate_key_normalizes(tmp_path: Path) -> None:
    """A chave de custo do Turn deve remover sufixos [1m] etc."""
    t = Turn(index=0, timestamp_ms=0, model="claude-opus-4-7[1m]",
             usage=Usage(), tools_called=[])
    assert t.cost_estimate_key == "claude-opus-4-7"


def test_extract_turns_preserves_raw_model(tmp_path: Path) -> None:
    """Turn.model mantém a string raw (com sufixo) — a normalização é feita
    na propriedade cost_estimate_key. Isso preserva info para display."""
    entries = [
        {"type": "assistant", "timestamp": "2026-04-21T10:00:00Z",
         "message": {"model": "claude-opus-4-7[1m]",
                     "usage": {"input_tokens": 1}}},
    ]
    turns = extract_turns(_make_ref(tmp_path, entries))
    assert turns[0].model == "claude-opus-4-7[1m]"
    assert turns[0].cost_estimate_key == "claude-opus-4-7"
