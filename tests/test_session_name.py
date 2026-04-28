"""Testes da exposição do `session_name` (nome dado via /rename).

Cobre:
- Parser: extract_custom_title
- Aggregator: leitura, "última vence", preservação no aggregate_transcript_since
- Cache: round-trip
- MCP server: campo presente em payloads
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from claude_dash import mcp_server
from claude_dash.aggregator import (
    aggregate_transcript_since,
    build_stats_for_transcript,
)
from claude_dash.discover import TranscriptRef
from claude_dash.models import SessionStats, Usage
from claude_dash.parser import extract_custom_title

# --- Helpers ------------------------------------------------------------


def _make_transcript(tmp_path: Path, entries: list[dict]) -> TranscriptRef:
    f = tmp_path / "t.jsonl"
    with f.open("w") as fp:
        for e in entries:
            fp.write(json.dumps(e) + "\n")
    mtime = int(f.stat().st_mtime * 1000)
    return TranscriptRef(
        session_id="test-sid",
        path=f,
        workspace="-ws",
        mtime_ms=mtime,
    )


@pytest.fixture
def isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redireciona cache_dir para evitar contaminação entre testes."""
    cache_dir = tmp_path / "cache"
    monkeypatch.setenv("CLAUDE_DASH_CACHE", str(cache_dir))
    import importlib

    from claude_dash import cache as cache_mod
    importlib.reload(cache_mod)
    return cache_dir


# --- Parser -------------------------------------------------------------


class TestExtractCustomTitle:
    def test_returns_title_for_custom_title_entry(self) -> None:
        entry = {
            "type": "custom-title",
            "customTitle": "claude-dash",
            "sessionId": "abc",
        }
        assert extract_custom_title(entry) == "claude-dash"

    def test_returns_none_for_other_types(self) -> None:
        assert extract_custom_title({"type": "user"}) is None
        assert extract_custom_title({"type": "assistant"}) is None
        assert extract_custom_title({"type": "system"}) is None

    def test_returns_none_when_title_missing(self) -> None:
        assert extract_custom_title({"type": "custom-title"}) is None

    def test_returns_none_for_empty_string(self) -> None:
        assert extract_custom_title(
            {"type": "custom-title", "customTitle": ""}
        ) is None
        assert extract_custom_title(
            {"type": "custom-title", "customTitle": "   "}
        ) is None

    def test_accepts_unicode_and_special_chars(self) -> None:
        # Nomes reais do Léo: "saturnine+20260427", "auto-normas+20260423"
        # também caracteres especiais e acentos
        assert extract_custom_title(
            {"type": "custom-title", "customTitle": "saturnine+20260427"}
        ) == "saturnine+20260427"
        assert extract_custom_title(
            {"type": "custom-title", "customTitle": "análise/refator"}
        ) == "análise/refator"


# --- Aggregator: build_stats_for_transcript -----------------------------


def test_session_name_is_none_when_no_rename(
    tmp_path: Path, isolated_cache: Path
) -> None:
    """Sessão sem /rename retorna None — sem fallback para sid/cwd."""
    entries = [
        {"type": "assistant", "message": {"model": "m",
            "usage": {"input_tokens": 10}}},
    ]
    ref = _make_transcript(tmp_path, entries)
    stats = build_stats_for_transcript(ref, use_cache=False)
    assert stats.session_name is None


def test_session_name_captured_from_custom_title(
    tmp_path: Path, isolated_cache: Path
) -> None:
    entries = [
        {"type": "custom-title", "customTitle": "claude-dash",
         "sessionId": "test-sid"},
        {"type": "assistant", "message": {"model": "m",
            "usage": {"input_tokens": 10}}},
    ]
    ref = _make_transcript(tmp_path, entries)
    stats = build_stats_for_transcript(ref, use_cache=False)
    assert stats.session_name == "claude-dash"


def test_session_name_last_wins_when_multiple_renames(
    tmp_path: Path, isolated_cache: Path
) -> None:
    """Cada /rename gera nova entry — a mais recente sobrescreve."""
    entries = [
        {"type": "custom-title", "customTitle": "primeiro-nome",
         "sessionId": "test-sid"},
        {"type": "assistant", "message": {"model": "m",
            "usage": {"input_tokens": 10}}},
        {"type": "custom-title", "customTitle": "nome-final",
         "sessionId": "test-sid"},
    ]
    ref = _make_transcript(tmp_path, entries)
    stats = build_stats_for_transcript(ref, use_cache=False)
    assert stats.session_name == "nome-final"


def test_custom_title_does_not_count_as_message(
    tmp_path: Path, isolated_cache: Path
) -> None:
    """custom-title não deve inflar messages_user/messages_assistant."""
    entries = [
        {"type": "custom-title", "customTitle": "x", "sessionId": "test-sid"},
        {"type": "user"},
    ]
    ref = _make_transcript(tmp_path, entries)
    stats = build_stats_for_transcript(ref, use_cache=False)
    assert stats.messages_user == 1
    assert stats.messages_assistant == 0


# --- Aggregator: aggregate_transcript_since -----------------------------


def test_session_name_preserved_in_since_window(
    tmp_path: Path, isolated_cache: Path
) -> None:
    """custom-title não tem timestamp — não pode ser descartado pelo
    filtro de janela usado em today/sessions_since."""
    entries = [
        {"type": "custom-title", "customTitle": "auto-normas",
         "sessionId": "test-sid"},
        {"type": "assistant", "timestamp": "2026-04-21T10:00:00Z",
         "message": {"model": "m", "usage": {"input_tokens": 100}}},
    ]
    ref = _make_transcript(tmp_path, entries)
    cutoff = 1776758400000  # 2026-04-21 00:00 UTC
    stats = aggregate_transcript_since(ref, since_ms=cutoff)
    assert stats.session_name == "auto-normas"
    assert stats.usage_by_model["m"].input_tokens == 100


# --- Cache --------------------------------------------------------------


def test_session_name_survives_cache_roundtrip(
    tmp_path: Path, isolated_cache: Path
) -> None:
    entries = [
        {"type": "custom-title", "customTitle": "saturnine+20260427",
         "sessionId": "test-sid"},
        {"type": "assistant", "message": {"model": "m",
            "usage": {"input_tokens": 42}}},
    ]
    ref = _make_transcript(tmp_path, entries)
    build_stats_for_transcript(ref, use_cache=True)
    # Segunda chamada lê do cache (mtime inalterado)
    stats2 = build_stats_for_transcript(ref, use_cache=True)
    assert stats2.session_name == "saturnine+20260427"


def test_legacy_cache_without_schema_version_is_invalidated(
    tmp_path: Path, isolated_cache: Path
) -> None:
    """Cache pré-v1 (sem schema_version) é tratado como ausente.

    Caches antigos não foram escritos com `session_name` e devolveriam
    None mesmo para sessões com /rename — reprocessamento força a leitura
    da entry custom-title que já existe no transcript.
    """
    from claude_dash import cache as cache_mod

    entries = [
        {"type": "custom-title", "customTitle": "real-name",
         "sessionId": "test-sid"},
        {"type": "assistant", "message": {"model": "m",
            "usage": {"input_tokens": 10}}},
    ]
    ref = _make_transcript(tmp_path, entries)

    # Escreve cache "legacy" manualmente (sem schema_version, sem
    # session_name) imitando o formato pré-v0.12
    legacy_payload = {
        "inode": ref.path.stat().st_ino,
        "mtime_ms": ref.mtime_ms,
        "byte_offset": ref.path.stat().st_size,
        "stats": {
            "session_id": ref.session_id,
            "cwd": "",
            "started_at_ms": 0,
            "last_activity_ms": ref.mtime_ms,
            "pid": None,
            "alive": False,
            "version": "",
            "transcript_path": None,
            "usage_by_model": {"m": {
                "input_tokens": 10, "output_tokens": 0,
                "cache_creation_1h": 0, "cache_creation_5m": 0, "cache_read": 0,
            }},
            "tools": {},
            "messages_user": 0,
            "messages_assistant": 1,
            "subagents": 0,
            "last_usage": None,
        },
    }
    cache_path = cache_mod.cache_path_for(ref.session_id)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(legacy_payload))

    # Carrega: deve invalidar e reprocessar, capturando session_name
    stats = build_stats_for_transcript(ref, use_cache=True)
    assert stats.session_name == "real-name"


# --- MCP server ---------------------------------------------------------


def _mk_session(
    sid: str = "abc12345-def",
    session_name: str | None = None,
    cost_usage: Usage | None = None,
) -> SessionStats:
    s = SessionStats(
        session_id=sid,
        cwd=Path("/home/x"),
        started_at_ms=1776700000000,
        last_activity_ms=1776700500000,
        pid=12345,
        alive=True,
        messages_user=10,
        messages_assistant=10,
        session_name=session_name,
    )
    if cost_usage is not None:
        s.usage_by_model["claude-opus-4-7"] = cost_usage
    return s


def test_active_sessions_includes_session_name() -> None:
    fake = [_mk_session(session_name="claude-dash",
                        cost_usage=Usage(input_tokens=100))]
    with patch.object(mcp_server, "collect_live_sessions", return_value=fake):
        result = mcp_server.active_sessions()
    assert result["sessions"][0]["session_name"] == "claude-dash"


def test_active_sessions_session_name_is_null_when_unset() -> None:
    fake = [_mk_session(session_name=None,
                        cost_usage=Usage(input_tokens=100))]
    with patch.object(mcp_server, "collect_live_sessions", return_value=fake):
        result = mcp_server.active_sessions()
    assert result["sessions"][0]["session_name"] is None


def test_today_summary_includes_session_name() -> None:
    fake = [
        _mk_session(sid="a", session_name="auto-normas",
                    cost_usage=Usage(input_tokens=1_000_000)),
        _mk_session(sid="b", session_name=None,
                    cost_usage=Usage(input_tokens=2_000_000)),
    ]
    with patch.object(mcp_server, "collect_sessions_since", return_value=fake):
        result = mcp_server.today_summary()
    names = [s["session_name"] for s in result["sessions"]]
    assert "auto-normas" in names
    assert None in names


def test_session_details_includes_session_name() -> None:
    fake_ref = TranscriptRef(
        session_id="abc", path=Path("/tmp/fake.jsonl"),
        workspace="-ws", mtime_ms=0,
    )
    fake_stats = _mk_session(sid="abc", session_name="claude-dash",
                             cost_usage=Usage(input_tokens=100))
    with patch.object(mcp_server, "find_transcript_for_session",
                      return_value=fake_ref), \
         patch.object(mcp_server, "build_stats_for_transcript",
                      return_value=fake_stats), \
         patch.object(mcp_server, "subagents_of", return_value=[]), \
         patch.object(mcp_server, "extract_turns", return_value=[]):
        result = mcp_server.session_details("abc")
    assert result["session"]["session_name"] == "claude-dash"


def test_workflow_snapshot_brief_includes_session_name() -> None:
    fake = [_mk_session(session_name="claude-dash",
                        cost_usage=Usage(input_tokens=100))]
    with patch.object(mcp_server, "collect_live_sessions", return_value=fake), \
         patch.object(mcp_server, "collect_sessions_since", return_value=[]), \
         patch.object(mcp_server, "collect_tool_usage_since", return_value={}):
        snap = mcp_server.workflow_snapshot()
    briefs = snap["active"]["sessions_brief"]
    assert briefs[0]["session_name"] == "claude-dash"


def test_workflow_snapshot_alert_uses_session_name_when_present() -> None:
    """Alertas devem citar o nome amigável quando disponível."""
    big_ctx = _mk_session(
        session_name="claude-dash",
        cost_usage=Usage(input_tokens=100, cache_read=200_000),
    )
    big_ctx.last_usage = Usage(input_tokens=100, cache_read=200_000)
    with patch.object(mcp_server, "collect_live_sessions",
                      return_value=[big_ctx]), \
         patch.object(mcp_server, "collect_sessions_since", return_value=[]), \
         patch.object(mcp_server, "collect_tool_usage_since", return_value={}):
        snap = mcp_server.workflow_snapshot()
    assert any("claude-dash" in a for a in snap["alerts"])
