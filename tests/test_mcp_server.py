"""Testes do MCP server (FastMCP).

As tools são expostas como funções Python — a camada MCP é um
transport. Testamos a lógica invocando as funções diretamente com
monkeypatch nos coletores.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from claude_dash import mcp_server
from claude_dash.models import SessionStats, Usage


def _mk_session(
    sid: str = "abc12345-def",
    cost_usage: Usage | None = None,
    alive: bool = True,
    messages_assistant: int = 10,
    last_usage: Usage | None = None,
) -> SessionStats:
    """Fábrica de SessionStats para testes."""
    s = SessionStats(
        session_id=sid,
        cwd=Path("/home/x"),
        started_at_ms=1776700000000,
        last_activity_ms=1776700500000,
        pid=12345,
        alive=alive,
        messages_user=messages_assistant,
        messages_assistant=messages_assistant,
        last_usage=last_usage,
    )
    if cost_usage is not None:
        s.usage_by_model["claude-opus-4-7"] = cost_usage
    return s


def test_active_sessions_structure() -> None:
    """active_sessions retorna dict com count e lista de sessões serializadas."""
    fake = [_mk_session(cost_usage=Usage(input_tokens=100, output_tokens=200))]
    with patch.object(mcp_server, "collect_live_sessions", return_value=fake):
        result = mcp_server.active_sessions()

    assert result["count"] == 1
    assert len(result["sessions"]) == 1
    s = result["sessions"][0]
    assert s["session_id"] == "abc12345-def"
    assert s["alive"] is True
    assert s["pid"] == 12345
    # Custo estimado não-zero (200 output @ $25/M = $0.005)
    assert s["cost_usd"] > 0


def test_today_summary_aggregates_correctly() -> None:
    """today_summary soma tokens e custo de todas as sessões."""
    fake = [
        _mk_session(sid="a", cost_usage=Usage(input_tokens=1_000_000)),
        _mk_session(sid="b", cost_usage=Usage(input_tokens=2_000_000), alive=False),
    ]
    with patch.object(mcp_server, "collect_sessions_since", return_value=fake):
        result = mcp_server.today_summary()

    assert result["sessions_count"] == 2
    assert result["sessions_alive"] == 1
    assert result["sessions_dead"] == 1
    # Opus input $5/M × 3M tokens = $15
    assert result["cost_usd"] == 15.0
    assert result["tokens_total"] == 3_000_000


def test_workflow_snapshot_has_alerts_section() -> None:
    """workflow_snapshot sempre retorna a chave 'alerts' (pode ser vazia)."""
    with patch.object(mcp_server, "collect_live_sessions", return_value=[]), \
         patch.object(mcp_server, "collect_sessions_since", return_value=[]), \
         patch.object(mcp_server, "collect_tool_usage_since", return_value={}):
        snap = mcp_server.workflow_snapshot()

    assert "alerts" in snap
    assert isinstance(snap["alerts"], list)
    # Sem sessões → sem alertas
    assert snap["alerts"] == []
    assert snap["active"]["sessions_count"] == 0


def test_workflow_snapshot_alert_on_high_cost() -> None:
    """Custo agregado do dia > $200 dispara alerta."""
    big_spender = _mk_session(
        cost_usage=Usage(input_tokens=50_000_000),  # $250 em input Opus
    )
    with patch.object(mcp_server, "collect_live_sessions", return_value=[]), \
         patch.object(mcp_server, "collect_sessions_since", return_value=[big_spender]), \
         patch.object(mcp_server, "collect_tool_usage_since", return_value={}):
        snap = mcp_server.workflow_snapshot()

    assert any("Consumo agregado do dia" in a for a in snap["alerts"])


def test_workflow_snapshot_alert_on_high_context() -> None:
    """Sessão com active_context > 150k dispara alerta."""
    big_ctx = _mk_session(
        cost_usage=Usage(input_tokens=100, cache_read=200_000),
        last_usage=Usage(input_tokens=100, cache_read=200_000),
    )
    with patch.object(mcp_server, "collect_live_sessions", return_value=[big_ctx]), \
         patch.object(mcp_server, "collect_sessions_since", return_value=[]), \
         patch.object(mcp_server, "collect_tool_usage_since", return_value={}):
        snap = mcp_server.workflow_snapshot()

    assert any("contexto ativo" in a and "/compact" in a for a in snap["alerts"])


def test_session_details_returns_error_for_unknown_sid() -> None:
    with patch.object(mcp_server, "find_transcript_for_session", return_value=None):
        result = mcp_server.session_details("nao-existe")

    assert "error" in result
    assert "nao-existe" in result["error"]


def test_tools_breakdown_returns_ordered_list() -> None:
    """tools_breakdown retorna lista ordenada por count descendente."""
    from claude_dash.models import ToolUsageStats

    fake_tools = {
        "Bash": ToolUsageStats(name="Bash", total_count=100),
        "Read": ToolUsageStats(name="Read", total_count=50),
        "Edit": ToolUsageStats(name="Edit", total_count=25),
    }
    with patch.object(mcp_server, "collect_tool_usage_since", return_value=fake_tools):
        result = mcp_server.tools_breakdown(hours=24)

    names = [t["name"] for t in result["tools"]]
    assert names == ["Bash", "Read", "Edit"]
    assert result["total_calls"] == 175
    # % do total correto
    assert result["tools"][0]["pct_of_total"] == round(100 * 100 / 175, 2)
