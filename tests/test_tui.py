"""Smoke tests da TUI Textual (usa `app.run_test()`)."""
from __future__ import annotations

import asyncio

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


def test_session_tab_enter_triggers_drill_down() -> None:
    """Regressão: pressionar Enter num SessionListItem deve chamar
    _render_session_detail com o sid correto.

    Fluxo simulado é o REAL do usuário (sem focus() explícito): troca
    pra aba Session com tecla 4 e pressiona Enter. O bug original era
    `.data` não persistir em ListItem; o bug secundário (v0.7.0) é que
    a ListView não recebia foco automaticamente ao ativar a aba, então
    Enter caía nas bindings globais. Fix: hook em on_tabbed_content_tab_activated
    que chama list_view.focus() ao ativar a aba Session.
    """
    async def run():
        app = DashboardApp()
        called_with: list[str] = []

        def spy(sid: str) -> None:
            called_with.append(sid)

        async with app.run_test() as pilot:
            await pilot.pause(0.5)
            app._render_session_detail = spy  # type: ignore[method-assign]

            # Troca pra aba Session — deve focar ListView automaticamente
            await pilot.press("4")
            await pilot.pause(0.3)

            from textual.widgets import ListView

            from claude_dash.views.tui import SessionListItem

            list_view = app.query_one("#session-list", ListView)
            session_items = [c for c in list_view.children if isinstance(c, SessionListItem)]
            if not session_items:
                return  # Ambiente sem transcripts → nada a testar

            # Nenhum focus() explícito aqui — deve estar focado pelo hook
            assert list_view.has_focus, "ListView não foi focado automaticamente ao ativar a aba"
            # Primeiro item deve estar highlighted desde o populate
            assert list_view.index == 0

            await pilot.press("enter")
            await pilot.pause(0.2)

            assert len(called_with) == 1, "drill-down não foi disparado por Enter"
            assert called_with[0] == session_items[0].sid

    _run(run())


def test_all_tab_contents_mount_without_error() -> None:
    """Valida que os Static de conteúdo de cada aba existem após on_mount.

    Se `on_mount` (que invoca `_refresh_{now,today,tools}`) tivesse
    crashado, o app não chegaria num estado onde query_one retorna os
    widgets — então a ausência de exceção aqui implica que os refreshers
    principais rodaram. A ListView da aba Session é checada
    separadamente em outros testes via press/ select.
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
