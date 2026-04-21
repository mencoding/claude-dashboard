"""Testes de discover — usa tmp_path para simular filesystem do Claude."""
from __future__ import annotations

import json
from pathlib import Path

from claude_dash.discover import (
    discover_live_sessions,
    discover_transcripts,
    find_transcript_for_session,
    subagents_of,
)


def _write_session_file(sessions_dir: Path, pid: int, sid: str, cwd: str = "/x") -> None:
    sessions_dir.mkdir(parents=True, exist_ok=True)
    (sessions_dir / f"{pid}.json").write_text(
        json.dumps({
            "pid": pid,
            "sessionId": sid,
            "cwd": cwd,
            "startedAt": 1776780000000,
            "version": "2.1.116",
        })
    )


def _write_transcript(projects_dir: Path, workspace: str, sid: str, content: str = "") -> Path:
    ws = projects_dir / workspace
    ws.mkdir(parents=True, exist_ok=True)
    f = ws / f"{sid}.jsonl"
    f.write_text(content)
    return f


def _write_subagent(projects_dir: Path, workspace: str, parent_sid: str, agent_id: str) -> Path:
    sub_dir = projects_dir / workspace / parent_sid / "subagents"
    sub_dir.mkdir(parents=True, exist_ok=True)
    f = sub_dir / f"{agent_id}.jsonl"
    f.write_text("")
    return f


# --- discover_live_sessions ---------------------------------------------


class TestDiscoverLiveSessions:
    def test_empty_when_dir_missing(self, tmp_path: Path) -> None:
        assert discover_live_sessions(tmp_path / "nada") == []

    def test_reads_session_json(self, tmp_path: Path) -> None:
        _write_session_file(tmp_path, pid=9999999, sid="sid-abc")
        result = discover_live_sessions(tmp_path)
        assert len(result) == 1
        assert result[0].session_id == "sid-abc"
        assert result[0].pid == 9999999
        assert result[0].alive is False  # PID gigante, não existe

    def test_current_pid_detected_alive(self, tmp_path: Path) -> None:
        import os
        _write_session_file(tmp_path, pid=os.getpid(), sid="self")
        result = discover_live_sessions(tmp_path)
        assert result[0].alive is True

    def test_skips_malformed_json(self, tmp_path: Path) -> None:
        tmp_path.mkdir(exist_ok=True)
        (tmp_path / "bad.json").write_text("{ not json")
        _write_session_file(tmp_path, pid=1, sid="ok")
        result = discover_live_sessions(tmp_path)
        assert len(result) == 1
        assert result[0].session_id == "ok"


# --- discover_transcripts ------------------------------------------------


class TestDiscoverTranscripts:
    def test_finds_main_transcripts(self, tmp_path: Path) -> None:
        _write_transcript(tmp_path, "-ws1", "sid1")
        _write_transcript(tmp_path, "-ws2", "sid2")
        result = discover_transcripts(tmp_path)
        assert {t.session_id for t in result} == {"sid1", "sid2"}
        assert all(not t.is_subagent for t in result)

    def test_finds_subagents(self, tmp_path: Path) -> None:
        _write_transcript(tmp_path, "-ws", "parent-sid")
        _write_subagent(tmp_path, "-ws", "parent-sid", "agent-1")
        _write_subagent(tmp_path, "-ws", "parent-sid", "agent-2")
        result = discover_transcripts(tmp_path)
        subs = [t for t in result if t.is_subagent]
        assert len(subs) == 2
        assert all(t.parent_session_id == "parent-sid" for t in subs)

    def test_since_ms_filter(self, tmp_path: Path) -> None:
        import os
        old = _write_transcript(tmp_path, "-ws", "old-sid")
        new = _write_transcript(tmp_path, "-ws", "new-sid")
        # Força old no passado
        past = 1700000000
        os.utime(old, (past, past))
        cutoff_ms = 1700000000_000 + 1  # 1ms acima de old
        result = discover_transcripts(tmp_path, since_ms=cutoff_ms)
        sids = {t.session_id for t in result}
        assert "new-sid" in sids
        assert "old-sid" not in sids

    def test_workspace_captured(self, tmp_path: Path) -> None:
        _write_transcript(tmp_path, "-home-menzani-xpto", "s")
        result = discover_transcripts(tmp_path)
        assert result[0].workspace == "-home-menzani-xpto"


# --- find_transcript_for_session -----------------------------------------


class TestFindTranscript:
    def test_exact_match(self, tmp_path: Path) -> None:
        _write_transcript(tmp_path, "-ws", "abc-def-123")
        ref = find_transcript_for_session("abc-def-123", tmp_path)
        assert ref is not None
        assert ref.session_id == "abc-def-123"

    def test_prefix_match(self, tmp_path: Path) -> None:
        _write_transcript(tmp_path, "-ws", "abc-def-123")
        ref = find_transcript_for_session("abc", tmp_path)
        assert ref is not None

    def test_ambiguous_prefix_returns_none(self, tmp_path: Path) -> None:
        _write_transcript(tmp_path, "-ws", "abc-1")
        _write_transcript(tmp_path, "-ws", "abc-2")
        assert find_transcript_for_session("abc", tmp_path) is None


# --- subagents_of --------------------------------------------------------


class TestSubagentsOf:
    def test_returns_all_subagents(self, tmp_path: Path) -> None:
        _write_transcript(tmp_path, "-ws", "parent")
        _write_subagent(tmp_path, "-ws", "parent", "agent-a")
        _write_subagent(tmp_path, "-ws", "parent", "agent-b")
        subs = subagents_of("parent", tmp_path)
        assert {t.session_id for t in subs} == {"agent-a", "agent-b"}

    def test_filters_other_parents(self, tmp_path: Path) -> None:
        _write_transcript(tmp_path, "-ws", "p1")
        _write_transcript(tmp_path, "-ws", "p2")
        _write_subagent(tmp_path, "-ws", "p1", "x")
        _write_subagent(tmp_path, "-ws", "p2", "y")
        subs = subagents_of("p1", tmp_path)
        assert len(subs) == 1
        assert subs[0].session_id == "x"
