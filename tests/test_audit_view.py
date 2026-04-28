"""Testes da view 'Audit' — filtros, render, parsing de filter input."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta, timezone

import pytest
from textual.widgets import TabbedContent

from claude_dash.audit.models import AuditEntry
from claude_dash.views.audit import (
    _color_for,
    _is_test_session,
    filter_entries,
    parse_filter_input,
    render_footer,
    render_table,
)
from claude_dash.views.tui import DashboardApp


def _entry(
    *,
    session_id: str = "0efd3cf4-96ef-41b7-9d41-f91ec539becd",
    tool: str = "Bash",
    status: str = "success",
    delta_seconds: float = 0,
    subagent_type: str | None = None,
) -> AuditEntry:
    """Helper para construir AuditEntry de teste."""
    base = datetime(2026, 4, 28, 0, 0, 0, tzinfo=timezone(timedelta(hours=-3)))
    return AuditEntry(
        timestamp=base + timedelta(seconds=delta_seconds),
        hostname="host",
        pid="1",
        session_id=session_id,
        tool=tool,
        tool_use_id="x",
        status=status,
        duration_ms=100,
        perm_mode="auto",
        input_sha="0",
        input_bytes=10,
        output_bytes=20,
        subagent_type=subagent_type,
    )


# ---- Filtros ---------------------------------------------------------


def test_filter_oculta_sessoes_de_teste_por_default() -> None:
    real = _entry(session_id="0efd3cf4-96ef-41b7-9d41-f91ec539becd")
    fake = _entry(session_id="test-abc-123")
    out = filter_entries([real, fake], window_hours=None)
    assert out == [real]


def test_filter_show_test_sessions_inclui_tudo() -> None:
    real = _entry(session_id="0efd3cf4-96ef-41b7-9d41-f91ec539becd")
    fake = _entry(session_id="test-abc-123")
    out = filter_entries([real, fake], window_hours=None, show_test_sessions=True)
    assert len(out) == 2


def test_filter_por_tool() -> None:
    a = _entry(tool="Bash")
    b = _entry(tool="Read")
    out = filter_entries([a, b], filters={"tool": "Bash"}, window_hours=None)
    assert out == [a]


def test_filter_por_status_error() -> None:
    a = _entry(status="success")
    b = _entry(status="error")
    out = filter_entries([a, b], filters={"status": "error"}, window_hours=None)
    assert out == [b]


def test_filter_session_prefix() -> None:
    a = _entry(session_id="0efd3cf4-96ef-41b7-9d41-f91ec539becd")
    b = _entry(session_id="abcd1234-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    out = filter_entries(
        [a, b], filters={"session_prefix": "0efd3"}, window_hours=None
    )
    assert out == [a]


def test_janela_temporal_24h() -> None:
    now = datetime(2026, 4, 28, 12, 0, 0, tzinfo=timezone(timedelta(hours=-3)))
    recent = _entry(delta_seconds=0)  # base = 2026-04-28 00:00 -- dentro
    old = AuditEntry(
        timestamp=datetime(2026, 4, 26, 0, 0, 0, tzinfo=timezone(timedelta(hours=-3))),
        hostname="host", pid="1", session_id=recent.session_id, tool="Bash",
        tool_use_id="x", status="success", duration_ms=0, perm_mode="auto",
        input_sha="0", input_bytes=0, output_bytes=0,
    )
    out = filter_entries([old, recent], window_hours=24, now=now)
    assert out == [recent]


def test_janela_none_inclui_tudo() -> None:
    now = datetime(2026, 4, 28, 12, 0, 0, tzinfo=timezone(timedelta(hours=-3)))
    e_recent = _entry()
    e_ancient = AuditEntry(
        timestamp=datetime(2020, 1, 1, tzinfo=UTC),
        hostname="host", pid="1", session_id=e_recent.session_id, tool="Bash",
        tool_use_id="x", status="success", duration_ms=0, perm_mode="auto",
        input_sha="0", input_bytes=0, output_bytes=0,
    )
    out = filter_entries([e_ancient, e_recent], window_hours=None, now=now)
    assert len(out) == 2


# ---- Helpers internos ------------------------------------------------


def test_is_test_session_uuid_valido() -> None:
    assert not _is_test_session("0efd3cf4-96ef-41b7-9d41-f91ec539becd")
    assert _is_test_session("test-abc-123")
    assert _is_test_session("not-a-uuid")


def test_color_for_status_error_override() -> None:
    e = _entry(tool="Bash", status="error")
    assert _color_for(e) == "red"


def test_color_for_tools_cinza() -> None:
    assert _color_for(_entry(tool="Bash")) == "dim"
    assert _color_for(_entry(tool="Read")) == "dim"
    assert _color_for(_entry(tool="Edit")) == "dim"
    assert _color_for(_entry(tool="Write")) == "dim"


def test_color_for_web_amarelo() -> None:
    assert _color_for(_entry(tool="WebFetch")) == "yellow"
    assert _color_for(_entry(tool="WebSearch")) == "yellow"


def test_color_for_agent_skill_mcp_azul() -> None:
    assert _color_for(_entry(tool="Agent")) == "blue"
    assert _color_for(_entry(tool="Skill")) == "blue"
    assert _color_for(_entry(tool="mcp__maestra__queue")) == "blue"


# ---- Render ----------------------------------------------------------


def test_render_table_nao_crasha_vazio() -> None:
    table = render_table([])
    # Rich Table tem 6 colunas; nao deve levantar
    assert len(table.columns) == 6


def test_render_table_inclui_subagent_type_no_label() -> None:
    e = _entry(tool="Agent", subagent_type="general-purpose")
    table = render_table([e])
    # tool column = idx 2; primeira linha
    cell = next(iter(table.columns[2].cells))
    assert "Agent" in cell
    assert "general-purpose" in cell


def test_render_footer_vazio() -> None:
    txt = render_footer([])
    assert "sem entries" in str(txt)


def test_render_footer_com_entries() -> None:
    entries = [_entry(tool="Bash") for _ in range(3)] + [_entry(tool="Read")]
    txt = render_footer(entries)
    s = str(txt)
    assert "total=4" in s
    assert "Bash" in s


def test_render_footer_taxa_erro() -> None:
    entries = [_entry(status="success"), _entry(status="error")]
    txt = render_footer(entries)
    s = str(txt)
    assert "err=1" in s


# ---- Filter input parsing -------------------------------------------


def test_parse_filter_vazio_limpa() -> None:
    assert parse_filter_input("") == {}
    assert parse_filter_input("/") == {}


def test_parse_filter_atalho_error() -> None:
    assert parse_filter_input("/error") == {"status": "error"}
    assert parse_filter_input("error") == {"status": "error"}


def test_parse_filter_tool() -> None:
    assert parse_filter_input("/tool=Bash") == {"tool": "Bash"}


def test_parse_filter_session() -> None:
    assert parse_filter_input("/session=0efd3") == {"session_prefix": "0efd3"}


def test_parse_filter_chave_invalida() -> None:
    assert parse_filter_input("/foo=bar") == {}


# ---- Smoke test da TUI ----------------------------------------------


def _run(coro):
    return asyncio.run(coro)


def test_tui_tem_aba_audit() -> None:
    async def run():
        app = DashboardApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.3)
            tc = app.query_one(TabbedContent)
            tab_ids = [pane.id for pane in tc.query("TabPane")]
            assert "tab-audit" in tab_ids
    _run(run())


def test_tecla_5_ativa_aba_audit() -> None:
    async def run():
        app = DashboardApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.3)
            await pilot.press("5")
            await pilot.pause(0.2)
            tc = app.query_one(TabbedContent)
            assert tc.active == "tab-audit"
    _run(run())


def test_tecla_t_cicla_janela() -> None:
    async def run():
        app = DashboardApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.3)
            await pilot.press("5")
            await pilot.pause(0.2)
            initial = app._audit_window_hours
            await pilot.press("t")
            await pilot.pause(0.1)
            assert app._audit_window_hours != initial
    _run(run())


def test_tecla_question_toggle_test_sessions() -> None:
    async def run():
        app = DashboardApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.3)
            await pilot.press("5")
            await pilot.pause(0.2)
            initial = app._audit_show_tests
            await pilot.press("question_mark")
            await pilot.pause(0.1)
            assert app._audit_show_tests != initial
    _run(run())


@pytest.mark.parametrize("key", ["1", "2", "3", "4", "5"])
def test_todas_abas_sao_acessiveis(key: str) -> None:
    async def run():
        app = DashboardApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.3)
            await pilot.press(key)
            await pilot.pause(0.1)
    _run(run())
