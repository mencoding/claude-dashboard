"""Smoke tests da TUI Textual (usa `app.run_test()`)."""
from __future__ import annotations

import asyncio

import pytest
from textual.widgets import Static, TabbedContent

from claude_dash.views.tui import DashboardApp


def _run(coro):
    """Wrapper para rodar corrotinas em tests síncronos (evita dep de pytest-asyncio)."""
    return asyncio.run(coro)


def test_app_mounts_with_four_tabs() -> None:
    async def run():
        app = DashboardApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.3)
            tc = app.query_one(TabbedContent)
            # 4 abas esperadas
            tab_ids = [pane.id for pane in tc.query("TabPane")]
            assert set(tab_ids) == {"tab-now", "tab-today", "tab-tools", "tab-session"}
            # Inicia na aba Now
            assert tc.active == "tab-now"

    _run(run())


def test_keyboard_shortcuts_switch_tabs() -> None:
    async def run():
        app = DashboardApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.3)
            tc = app.query_one(TabbedContent)

            await pilot.press("2")
            await pilot.pause(0.1)
            assert tc.active == "tab-today"

            await pilot.press("3")
            await pilot.pause(0.1)
            assert tc.active == "tab-tools"

            await pilot.press("4")
            await pilot.pause(0.1)
            assert tc.active == "tab-session"

            await pilot.press("1")
            await pilot.pause(0.1)
            assert tc.active == "tab-now"

    _run(run())


def test_refresh_binding_does_not_crash() -> None:
    async def run():
        app = DashboardApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.3)
            # Em cada aba, chamar refresh não deve crashar
            for tab in ["1", "2", "3", "4"]:
                await pilot.press(tab)
                await pilot.pause(0.1)
                await pilot.press("r")
                await pilot.pause(0.1)

    _run(run())


def test_all_tab_contents_mount_without_error() -> None:
    """Valida que os 4 Static de conteúdo foram criados e query_one encontra-os.

    Na prática isto confirma que on_mount → _refresh_{now,today,tools}
    e _refresh_session_list rodaram sem levantar exceção (se levantassem,
    o app teria crashado antes do query_one funcionar).
    """
    async def run():
        app = DashboardApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.5)
            # query_one dispara NoMatches se não existir → serve de assertion
            app.query_one("#now-content", Static)
            app.query_one("#today-content", Static)
            app.query_one("#tools-content", Static)
            app.query_one("#session-detail", Static)

    _run(run())
