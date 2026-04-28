"""Testes do MCP server (FastMCP).

As tools são expostas como funções Python — a camada MCP é um
transport. Testamos a lógica invocando as funções diretamente com
monkeypatch nos coletores.
"""
from __future__ import annotations

from datetime import datetime
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
    # Opus input $5/M x 3M tokens = $15
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
    """Custo agregado do dia > $200 dispara alerta (apenas em billing API).

    Em plano flat-rate (is_flat_rate=True), o alerta de custo em USD é
    suprimido porque o valor não representa cobrança real — o usuário
    paga mensalidade fixa. Este teste valida o caminho API.
    """
    big_spender = _mk_session(
        cost_usage=Usage(input_tokens=50_000_000),  # $250 em input Opus
    )
    with patch.object(mcp_server, "collect_live_sessions", return_value=[]), \
         patch.object(mcp_server, "collect_sessions_since", return_value=[big_spender]), \
         patch.object(mcp_server, "collect_tool_usage_since", return_value={}), \
         patch.object(mcp_server, "read_account_info", return_value=None):
        # read_account_info=None é tratado como "API billing" (caminho
        # conservador — quando não se sabe, assume-se que USD é real)
        snap = mcp_server.workflow_snapshot()

    assert any("Consumo agregado do dia" in a for a in snap["alerts"])


def test_workflow_snapshot_suppresses_cost_alert_on_flat_rate() -> None:
    """Em billing flat-rate, alerta de custo diário é suprimido."""
    from claude_dash.account import BILLING_FLAT_RATE, AccountInfo

    big_spender = _mk_session(
        cost_usage=Usage(input_tokens=50_000_000),
    )
    flat_rate_account = AccountInfo(
        email="x@y.com", display_name="", organization_name="",
        organization_role="", billing_type=BILLING_FLAT_RATE,
        has_extra_usage_enabled=False, extra_usage_disabled_reason=None,
        first_token_date=None, account_uuid="", organization_uuid="",
    )
    with patch.object(mcp_server, "collect_live_sessions", return_value=[]), \
         patch.object(mcp_server, "collect_sessions_since", return_value=[big_spender]), \
         patch.object(mcp_server, "collect_tool_usage_since", return_value={}), \
         patch.object(mcp_server, "read_account_info", return_value=flat_rate_account):
        snap = mcp_server.workflow_snapshot()

    # Alerta de custo NÃO deve aparecer porque is_flat_rate=True
    assert not any("Consumo agregado do dia" in a for a in snap["alerts"])


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


# ---- Envelope MCP (#54-d1) ------------------------------------------


def test_envelope_inclui_schema_version_e_dashboard_version() -> None:
    """_envelope helper adiciona ambos os campos canonicos."""
    out = mcp_server._envelope({"foo": "bar"})
    assert out["_schema_version"] == mcp_server.SCHEMA_VERSION
    assert out["_schema_version"] == 1
    # dashboard_version vem do package metadata; basta existir.
    assert "dashboard_version" in out
    assert isinstance(out["dashboard_version"], str)
    # Payload original preservado
    assert out["foo"] == "bar"


def test_envelope_em_active_sessions() -> None:
    fake_live = []
    with patch.object(mcp_server, "collect_live_sessions", return_value=fake_live):
        result = mcp_server.active_sessions()
    assert result["_schema_version"] == 1
    assert "dashboard_version" in result
    assert "sessions" in result


def test_envelope_em_today_summary() -> None:
    with patch.object(mcp_server, "collect_sessions_since", return_value=[]):
        result = mcp_server.today_summary()
    assert result["_schema_version"] == 1
    assert "tokens_total" in result


def test_envelope_em_session_details_erro() -> None:
    """Mesmo o caminho de erro deve ter envelope."""
    with patch.object(mcp_server, "find_transcript_for_session", return_value=None):
        result = mcp_server.session_details("nonexistent")
    assert result["_schema_version"] == 1
    assert "error" in result


def test_envelope_em_tools_breakdown() -> None:
    with patch.object(mcp_server, "collect_tool_usage_since", return_value={}):
        result = mcp_server.tools_breakdown(hours=24)
    assert result["_schema_version"] == 1
    assert result["total_calls"] == 0


def test_envelope_em_account_info_sem_arquivo() -> None:
    with patch.object(mcp_server, "read_account_info", return_value=None):
        result = mcp_server.account_info()
    assert result["_schema_version"] == 1
    assert "error" in result


def test_envelope_em_rate_limits_nao_instalado() -> None:
    with patch.object(mcp_server, "read_rate_limits", return_value={}):
        result = mcp_server.rate_limits()
    assert result["_schema_version"] == 1
    assert result["installed"] is False


# ---- Audit tools (#54-d3) -------------------------------------------


def test_audit_entries_arquivo_inexistente() -> None:
    """Tail de path inexistente retorna lista vazia, sem erro."""
    from pathlib import Path
    fake = Path("/tmp/claude-dash-test-nonexistent.log")
    with patch.object(mcp_server, "AUDIT_LOG_PATH", fake):
        result = mcp_server.audit_entries(hours=24)
    assert result["_schema_version"] == 1
    assert result["total_in_window"] == 0
    assert result["entries"] == []


def test_audit_entries_filtra_por_tool(tmp_path) -> None:
    """Filtro tool aplicado corretamente."""
    log = tmp_path / "sessions.log"
    log.write_text(
        '<134>1 2026-04-28T00:00:29.223+00:00 host claude-code 1 TOOLCALL '
        '[audit@iris session="0efd3cf4-96ef-41b7-9d41-f91ec539becd" '
        'tool="Bash" tool_use_id="x1" status="success" duration_ms="50" '
        'perm_mode="auto" input_sha="0" input_bytes="0" output_bytes="0"]\n'
        '<134>1 2026-04-28T00:00:30.000+00:00 host claude-code 1 TOOLCALL '
        '[audit@iris session="0efd3cf4-96ef-41b7-9d41-f91ec539becd" '
        'tool="Read" tool_use_id="x2" status="success" duration_ms="10" '
        'perm_mode="auto" input_sha="0" input_bytes="0" output_bytes="0"]\n',
        encoding="utf-8",
    )
    with patch.object(mcp_server, "AUDIT_LOG_PATH", log):
        # Janela bem grande pra cobrir o timestamp fixo da fixture
        result = mcp_server.audit_entries(tool="Bash", hours=24 * 365 * 100)
    assert result["returned"] == 1
    assert result["entries"][0]["tool"] == "Bash"


def test_audit_entries_filtra_por_session_prefix(tmp_path) -> None:
    log = tmp_path / "sessions.log"
    log.write_text(
        '<134>1 2026-04-28T00:00:29.223+00:00 host claude-code 1 TOOLCALL '
        '[audit@iris session="0efd3cf4-96ef-41b7-9d41-f91ec539becd" '
        'tool="Bash" tool_use_id="x1" status="success" duration_ms="50" '
        'perm_mode="auto" input_sha="0" input_bytes="0" output_bytes="0"]\n'
        '<134>1 2026-04-28T00:00:30.000+00:00 host claude-code 1 TOOLCALL '
        '[audit@iris session="aabb1234-aaaa-4aaa-8aaa-aaaaaaaaaaaa" '
        'tool="Read" tool_use_id="x2" status="success" duration_ms="10" '
        'perm_mode="auto" input_sha="0" input_bytes="0" output_bytes="0"]\n',
        encoding="utf-8",
    )
    with patch.object(mcp_server, "AUDIT_LOG_PATH", log):
        result = mcp_server.audit_entries(session="0efd3", hours=24 * 365 * 100)
    assert result["returned"] == 1
    assert result["entries"][0]["session_id"].startswith("0efd3")


def test_audit_entries_envelope_e_filters_applied(tmp_path) -> None:
    log = tmp_path / "sessions.log"
    log.write_text("", encoding="utf-8")
    with patch.object(mcp_server, "AUDIT_LOG_PATH", log):
        result = mcp_server.audit_entries(tool="Bash", hours=12, limit=100)
    assert result["_schema_version"] == 1
    assert result["filters_applied"]["tool"] == "Bash"
    assert result["filters_applied"]["hours"] == 12
    assert result["filters_applied"]["limit"] == 100


def test_audit_session_partial_existe(tmp_path) -> None:
    log = tmp_path / "sessions.log"
    log.write_text(
        '<134>1 2026-04-28T00:00:29.223+00:00 PREDATOR claude-code 1 TOOLCALL '
        '[audit@iris session="0efd3cf4-96ef-41b7-9d41-f91ec539becd" '
        'tool="Bash" tool_use_id="x1" status="error" duration_ms="100" '
        'perm_mode="auto" input_sha="0" input_bytes="0" output_bytes="0"]\n'
        '<134>1 2026-04-28T00:00:35.000+00:00 PREDATOR claude-code 1 TOOLCALL '
        '[audit@iris session="0efd3cf4-96ef-41b7-9d41-f91ec539becd" '
        'tool="Bash" tool_use_id="x2" status="success" duration_ms="50" '
        'perm_mode="auto" input_sha="0" input_bytes="0" output_bytes="0"]\n',
        encoding="utf-8",
    )
    with patch.object(
        mcp_server, "build_partial_stats_from_audit",
        lambda sid: __import__("claude_dash.audit.partial_stats", fromlist=[
            "build_partial_stats_from_audit"
        ]).build_partial_stats_from_audit(sid, audit_log_path=log),
    ):
        result = mcp_server.audit_session_partial("0efd3")
    assert result["_schema_version"] == 1
    assert result["found"] is True
    assert result["hostname"] == "PREDATOR"
    assert result["total_calls"] == 2
    assert result["error_count"] == 1
    assert result["error_rate"] == 0.5


# ---- dashboard_health (#54-d4) --------------------------------------


def test_dashboard_health_log_inexistente(tmp_path) -> None:
    """Sem sessions.log, issue lista o problema."""
    fake = tmp_path / "missing.log"
    with patch.object(mcp_server, "AUDIT_LOG_PATH", fake):
        with patch.object(mcp_server, "_check_rsyslog_active", return_value=True):
            with patch.object(mcp_server, "_audit_hook_wired", return_value=False):
                result = mcp_server.dashboard_health()
    assert result["_schema_version"] == 1
    assert result["audit_log_exists"] is False
    assert result["audit_log_last_entry_age_seconds"] is None
    assert any("sessions.log nao existe" in msg for msg in result["issues"])
    assert any("Hook PostToolUse nao wired" in msg for msg in result["issues"])


def test_dashboard_health_tudo_ok(tmp_path) -> None:
    log = tmp_path / "sessions.log"
    log.write_text("dummy\n")
    with patch.object(mcp_server, "AUDIT_LOG_PATH", log):
        with patch.object(mcp_server, "_check_rsyslog_active", return_value=True):
            with patch.object(mcp_server, "_audit_hook_wired", return_value=True):
                result = mcp_server.dashboard_health()
    assert result["audit_log_exists"] is True
    assert result["rsyslog_active"] is True
    assert result["audit_hook_wired"] is True
    assert result["issues"] == []


def test_dashboard_health_rsyslog_inativo(tmp_path) -> None:
    log = tmp_path / "sessions.log"
    log.write_text("dummy\n")
    with patch.object(mcp_server, "AUDIT_LOG_PATH", log):
        with patch.object(mcp_server, "_check_rsyslog_active", return_value=False):
            with patch.object(mcp_server, "_audit_hook_wired", return_value=True):
                result = mcp_server.dashboard_health()
    assert any("rsyslog inativo" in msg for msg in result["issues"])


def test_dashboard_health_log_velho(tmp_path) -> None:
    """Log com mtime > 24h gera issue."""
    import os
    log = tmp_path / "sessions.log"
    log.write_text("dummy\n")
    # Mtime 2 dias atras
    old = datetime.now().timestamp() - 2 * 24 * 3600
    os.utime(log, (old, old))
    with patch.object(mcp_server, "AUDIT_LOG_PATH", log):
        with patch.object(mcp_server, "_check_rsyslog_active", return_value=True):
            with patch.object(mcp_server, "_audit_hook_wired", return_value=True):
                result = mcp_server.dashboard_health()
    assert result["audit_log_last_entry_age_seconds"] >= 24 * 3600
    assert any("sem updates" in msg for msg in result["issues"])


def test_dashboard_health_systemctl_ausente(tmp_path) -> None:
    """systemctl ausente -> rsyslog_active=None, sem issue de rsyslog."""
    log = tmp_path / "sessions.log"
    log.write_text("dummy\n")
    with patch.object(mcp_server, "AUDIT_LOG_PATH", log):
        with patch.object(mcp_server, "_check_rsyslog_active", return_value=None):
            with patch.object(mcp_server, "_audit_hook_wired", return_value=True):
                result = mcp_server.dashboard_health()
    assert result["rsyslog_active"] is None
    # Issue de rsyslog so se False, nao se None
    assert not any("rsyslog inativo" in msg for msg in result["issues"])


def test_audit_session_partial_nao_existe(tmp_path) -> None:
    log = tmp_path / "sessions.log"
    log.write_text("", encoding="utf-8")
    with patch.object(
        mcp_server, "build_partial_stats_from_audit",
        lambda sid: __import__("claude_dash.audit.partial_stats", fromlist=[
            "build_partial_stats_from_audit"
        ]).build_partial_stats_from_audit(sid, audit_log_path=log),
    ):
        result = mcp_server.audit_session_partial("nonexistent")
    assert result["_schema_version"] == 1
    assert result["found"] is False
    assert "error" in result
