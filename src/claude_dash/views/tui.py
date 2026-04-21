"""App Textual com abas para navegar entre as views do dashboard.

Entrypoint default de `claude-dash` quando chamado sem subcomando.
Os subcomandos diretos (now, today, tools, session) permanecem para
uso scriptável.
"""
from __future__ import annotations

from datetime import datetime

from rich.console import Group
from rich.panel import Panel
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Vertical
from textual.reactive import reactive
from textual.widgets import (
    Footer,
    Header,
    Label,
    ListItem,
    ListView,
    Static,
    TabbedContent,
    TabPane,
)

from claude_dash.aggregator import (
    build_stats_for_transcript,
    collect_live_sessions,
    collect_sessions_since,
    collect_tool_usage_since,
    extract_turns,
)
from claude_dash.discover import (
    discover_live_sessions,
    find_transcript_for_session,
    subagents_of,
)
from claude_dash.views.now import (
    _header as _now_header,
    _session_table as _now_session_table,
)
from claude_dash.views.session import (
    _header as _session_header,
    _overview as _session_overview,
    _subagents_panel as _session_subagents,
    _timeline as _session_timeline,
)
from claude_dash.views.today import (
    _header as _today_header,
    _session_table as _today_session_table,
    _tools_aggregate as _today_tools,
    today_start_ms,
)
from claude_dash.views.tools import (
    _header as _tools_header,
    _hourly_aggregate as _tools_hourly,
    _tools_table as _tools_table,
    window_start_ms as _tools_window_start,
)


# Intervalo de refresh automático da aba Now (em segundos)
NOW_REFRESH_SEC = 2.0


class DashboardApp(App):
    """App principal do dashboard — 4 abas navegáveis por teclado."""

    CSS = """
    TabbedContent {
        height: 100%;
    }
    TabPane {
        padding: 0 1;
    }
    ListView {
        border: solid $primary;
        height: 100%;
    }
    ListView > ListItem {
        padding: 0 1;
    }
    #session-detail {
        height: 1fr;
    }
    """

    BINDINGS = [
        Binding("1", "show_tab('tab-now')", "Now"),
        Binding("2", "show_tab('tab-today')", "Today"),
        Binding("3", "show_tab('tab-tools')", "Tools"),
        Binding("4", "show_tab('tab-session')", "Session"),
        Binding("r", "refresh_current", "Refresh"),
        Binding("q", "quit", "Quit"),
    ]

    TITLE = "claude-dashboard"

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(initial="tab-now"):
            with TabPane("Now (1)", id="tab-now"):
                yield Static(id="now-content", expand=True)
            with TabPane("Today (2)", id="tab-today"):
                yield Static(id="today-content", expand=True)
            with TabPane("Tools (3)", id="tab-tools"):
                yield Static(id="tools-content", expand=True)
            with TabPane("Session (4)", id="tab-session"):
                with Vertical():
                    yield Label("Selecione uma sessão (↑/↓, Enter):", id="session-hint")
                    yield ListView(id="session-list")
                    yield Static(id="session-detail")
        yield Footer()

    def on_mount(self) -> None:
        """Renderização inicial + timer de auto-refresh da Now."""
        self._refresh_now()
        self._refresh_today()
        self._refresh_tools()
        self._refresh_session_list()
        # Auto-refresh da Now a cada NOW_REFRESH_SEC segundos
        self.set_interval(NOW_REFRESH_SEC, self._refresh_now)

    # ----- Actions -------------------------------------------------------

    def action_show_tab(self, tab_id: str) -> None:
        tc = self.query_one(TabbedContent)
        tc.active = tab_id

    def action_refresh_current(self) -> None:
        """Re-renderiza o conteúdo da aba atualmente ativa."""
        tc = self.query_one(TabbedContent)
        active = tc.active
        if active == "tab-now":
            self._refresh_now()
        elif active == "tab-today":
            self._refresh_today()
        elif active == "tab-tools":
            self._refresh_tools()
        elif active == "tab-session":
            self._refresh_session_list()

    # ----- Refreshers ----------------------------------------------------

    def _refresh_now(self) -> None:
        try:
            sessions = collect_live_sessions()
            content = Group(
                _now_header(sessions),
                _now_session_table(sessions),
            )
            self.query_one("#now-content", Static).update(content)
        except Exception as e:  # noqa: BLE001
            self.query_one("#now-content", Static).update(
                Panel(Text(f"Erro: {e}", style="red"), title="Now", border_style="red")
            )

    def _refresh_today(self) -> None:
        try:
            since_ms = today_start_ms()
            sessions = collect_sessions_since(since_ms)
            content = Group(
                _today_header(sessions, since_ms),
                _today_session_table(sessions),
                _today_tools(sessions),
            )
            self.query_one("#today-content", Static).update(content)
        except Exception as e:  # noqa: BLE001
            self.query_one("#today-content", Static).update(
                Panel(Text(f"Erro: {e}", style="red"), title="Today", border_style="red")
            )

    def _refresh_tools(self) -> None:
        try:
            since_ms = _tools_window_start()
            tools = collect_tool_usage_since(since_ms)
            content = Group(
                _tools_header(tools, since_ms),
                _tools_table(tools),
                _tools_hourly(tools),
            )
            self.query_one("#tools-content", Static).update(content)
        except Exception as e:  # noqa: BLE001
            self.query_one("#tools-content", Static).update(
                Panel(Text(f"Erro: {e}", style="red"), title="Tools", border_style="red")
            )

    def _refresh_session_list(self) -> None:
        """Popula ListView com sessões vivas + sessões do dia."""
        list_view = self.query_one("#session-list", ListView)
        list_view.clear()

        entries: list[tuple[str, str]] = []
        seen: set[str] = set()

        # Sessões vivas primeiro
        for ls in discover_live_sessions():
            if ls.session_id in seen:
                continue
            seen.add(ls.session_id)
            label = f"● {ls.session_id[:8]}…  {ls.cwd}  (viva, pid={ls.pid})"
            entries.append((ls.session_id, label))

        # Sessões do dia (mesmo que mortas)
        for s in collect_sessions_since(today_start_ms()):
            if s.session_id in seen:
                continue
            seen.add(s.session_id)
            state = "viva" if s.alive else "morta"
            label = f"○ {s.session_id[:8]}…  {s.cwd}  ({state})"
            entries.append((s.session_id, label))

        if not entries:
            list_view.append(ListItem(Label("[dim]Nenhuma sessão encontrada hoje.[/dim]")))
            return

        for sid, label in entries:
            item = ListItem(Label(label))
            item.data = sid  # type: ignore[attr-defined]
            list_view.append(item)

    def _render_session_detail(self, sid: str) -> None:
        """Chamado quando o usuário seleciona um SID na lista."""
        try:
            ref = find_transcript_for_session(sid)
            if ref is None:
                self.query_one("#session-detail", Static).update(
                    Panel(
                        Text(f"Transcript não encontrado para {sid}", style="red"),
                        border_style="red",
                    )
                )
                return

            live_by_sid = {ls.session_id: ls for ls in discover_live_sessions()}
            live = live_by_sid.get(ref.session_id)
            stats = build_stats_for_transcript(ref, live=live)
            stats.subagents = len(subagents_of(ref.session_id))
            turns = extract_turns(ref)

            content_parts = [
                _session_header(stats),
                _session_overview(stats),
                _session_timeline(turns),
            ]
            subs_panel = _session_subagents(ref.session_id)
            if subs_panel is not None:
                content_parts.append(subs_panel)

            self.query_one("#session-detail", Static).update(Group(*content_parts))
        except Exception as e:  # noqa: BLE001
            self.query_one("#session-detail", Static).update(
                Panel(Text(f"Erro: {e}", style="red"), border_style="red")
            )

    # ----- Event handlers ------------------------------------------------

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        sid = getattr(event.item, "data", None)
        if isinstance(sid, str):
            self._render_session_detail(sid)


def run() -> int:
    """Entrypoint da TUI. Retorna exit code."""
    app = DashboardApp()
    app.run()
    return 0
