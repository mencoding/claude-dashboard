"""App Textual com abas para navegar entre as views do dashboard.

Entrypoint default de `claude-dash` quando chamado sem subcomando.
Os subcomandos diretos (now, today, tools, session) permanecem para
uso scriptável.
"""
from __future__ import annotations

import contextlib
import subprocess
import time
from collections import deque
from pathlib import Path

from rich.console import Group
from rich.panel import Panel
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import (
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    Static,
    TabbedContent,
    TabPane,
)

from claude_dash import __version__
from claude_dash.aggregator import (
    build_stats_for_transcript,
    collect_live_sessions,
    collect_sessions_since,
    collect_tool_usage_since,
    extract_turns,
)
from claude_dash.audit.models import AuditEntry
from claude_dash.audit.tail import IncrementalTailer
from claude_dash.discover import (
    discover_live_sessions,
    find_transcript_for_session,
    subagents_of,
)
from claude_dash.views.audit import (
    filter_entries as _audit_filter_entries,
)
from claude_dash.views.audit import (
    parse_filter_input as _audit_parse_filter,
)
from claude_dash.views.audit import (
    render_footer as _audit_render_footer,
)
from claude_dash.views.audit import (
    render_table as _audit_render_table,
)
from claude_dash.views.now import (
    _header as _now_header,
)
from claude_dash.views.now import (
    _session_table as _now_session_table,
)
from claude_dash.views.session import (
    _header as _session_header,
)
from claude_dash.views.session import (
    _overview as _session_overview,
)
from claude_dash.views.session import (
    _subagents_panel as _session_subagents,
)
from claude_dash.views.session import (
    _timeline as _session_timeline,
)
from claude_dash.views.today import (
    _header as _today_header,
)
from claude_dash.views.today import (
    _session_table as _today_session_table,
)
from claude_dash.views.today import (
    _tools_aggregate as _today_tools,
)
from claude_dash.views.today import (
    today_start_ms,
)
from claude_dash.views.tools import (
    _header as _tools_header,
)
from claude_dash.views.tools import (
    _hourly_aggregate as _tools_hourly,
)
from claude_dash.views.tools import (
    _tools_table as _tools_table,
)
from claude_dash.views.tools import (
    window_start_ms as _tools_window_start,
)


class SessionListItem(ListItem):
    """ListItem que carrega o sessionId como atributo tipado.

    Subclassar é mais robusto que atribuir `.data` depois de criado:
    evita depender de Textual preservar atributos arbitrários entre
    versões e dá type-safety no event handler.
    """

    def __init__(self, sid: str, label: str) -> None:
        super().__init__(Label(label))
        self.sid = sid

# Intervalo de refresh automático da aba Now (em segundos)
NOW_REFRESH_SEC = 2.0
# Intervalo de tail incremental do audit log (em segundos)
AUDIT_REFRESH_SEC = 1.0
# Cap do ring buffer in-memory de entries do audit
AUDIT_BUFFER_MAX = 10_000
# Janelas temporais ciclicas (D1). None = "all".
AUDIT_WINDOW_CYCLE: tuple[float | None, ...] = (1.0, 24.0, 24.0 * 7, None)
# Path do audit log (mantem em sync com audit/hook.py).
AUDIT_LOG_PATH = Path.home() / ".claude" / "iris" / "audit" / "sessions.log"
# Path do log de sistema (D6 drill-down externo via pkexec).
AUDIT_SYSTEM_LOG = "/var/log/claude/tools.log"
# Tempo (s) sem interacao do usuario antes de retomar auto-scroll (D8).
AUDIT_AUTO_SCROLL_GRACE_SEC = 3.0


def _format_window_label(hours: float | None) -> str:
    """Rotulo curto pra footer (D1)."""
    if hours is None:
        return "all"
    if hours < 24:
        return f"{int(hours)}h"
    if hours == 24.0:
        return "24h"
    return f"{int(hours / 24)}d"


class DashboardApp(App):
    """App principal do dashboard — 4 abas navegáveis por teclado."""

    CSS = """
    TabbedContent {
        height: 100%;
    }
    TabPane {
        padding: 0 1;
    }
    ListView > ListItem {
        padding: 0 1;
    }
    /* Aba Session: lista limitada a 40% da altura, detail toma o resto.
       O 'ListView { height: 100% }' anterior absorvia toda a altura e
       deixava o drill-down invisível. */
    #session-list {
        border: solid $primary;
        height: 40%;
    }
    #session-detail {
        height: 1fr;
        border: solid $primary;
        overflow-y: auto;
    }
    #session-hint {
        margin: 0 0 1 0;
    }
    /* Aba Audit: tabela em cima, detail painel embaixo, status 1 linha,
       prompt de filtro docked-bottom (so visivel quando ativo). */
    #audit-table {
        border: solid $primary;
        height: 70%;
        overflow-y: auto;
    }
    #audit-detail {
        height: 1fr;
        border: solid $primary;
        overflow-y: auto;
    }
    #audit-status {
        height: 1;
        background: $boost;
        color: $text-muted;
        padding: 0 1;
    }
    #audit-filter-input {
        dock: bottom;
        height: 3;
        display: none;
        border: solid $accent;
    }
    #audit-filter-input.active {
        display: block;
    }
    """

    BINDINGS = [
        Binding("1", "show_tab('tab-now')", "Now"),
        Binding("2", "show_tab('tab-today')", "Today"),
        Binding("3", "show_tab('tab-tools')", "Tools"),
        Binding("4", "show_tab('tab-session')", "Session"),
        Binding("5", "show_tab('tab-audit')", "Audit"),
        Binding("r", "refresh_current", "Refresh"),
        Binding("q", "quit", "Quit"),
        # Ctrl+C fecha direto (convenção de terminal). Sobrescreve o popup
        # padrão do Textual que sugere Ctrl+D — preferimos comportamento
        # SIGINT clássico, já que `q` continua disponível como atalho seguro.
        Binding("ctrl+c", "quit", "Quit", priority=True, show=False),
        # Bindings da aba Audit (so agem quando a aba esta ativa — checagem
        # acontece dentro de cada action_*).
        Binding("slash", "audit_filter_prompt", "Filter", show=False),
        Binding("t", "audit_cycle_window", "Cycle window", show=False),
        Binding("question_mark", "audit_toggle_tests", "Toggle tests", show=False),
        Binding("s", "audit_drill_down_root", "Root drill-down", show=False),
        Binding("end", "audit_resume_scroll", "Resume", show=False),
    ]

    TITLE = "claude-dashboard"
    SUB_TITLE = f"v{__version__}"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # Estado da aba Audit. Mantido na App (nao em reactive) porque
        # as funcoes de filter/render sao puras e queremos controle
        # explicito de quando re-renderizar (evita custo desnecessario).
        self._audit_tailer: IncrementalTailer | None = None
        self._audit_entries: deque[AuditEntry] = deque(maxlen=AUDIT_BUFFER_MAX)
        self._audit_filter: dict[str, str] = {}
        self._audit_window_hours: float | None = 24.0
        self._audit_show_tests: bool = False
        # Timestamp (monotonic) da ultima interacao do usuario na aba
        # Audit. Auto-scroll so segue o fundo se passou
        # AUDIT_AUTO_SCROLL_GRACE_SEC sem interacao (D8).
        self._audit_last_user_action: float = 0.0
        # Quando True, indica explicitamente que o usuario esta com o
        # scroll pausado (tipicamente apos scroll-up). Resetado pelo End.
        self._audit_paused: bool = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(initial="tab-now"):
            with TabPane("Now (1)", id="tab-now"):
                yield Static(id="now-content", expand=True)
            with TabPane("Today (2)", id="tab-today"):
                yield Static(id="today-content", expand=True)
            with TabPane("Tools (3)", id="tab-tools"):
                yield Static(id="tools-content", expand=True)
            with TabPane("Session (4)", id="tab-session"), Vertical():
                yield Label(
                    "↑/↓ navega  •  Enter abre o drill-down da sessão selecionada",
                    id="session-hint",
                )
                yield ListView(id="session-list")
                yield Static(id="session-detail")
            with TabPane("Audit (5)", id="tab-audit"), Vertical():
                yield Static(id="audit-table", expand=True)
                yield Static(id="audit-detail")
                yield Static(id="audit-status")
                yield Input(
                    placeholder="filtro: /tool=Bash | /error | /session=0efd3 | Esc cancela",
                    id="audit-filter-input",
                )
        yield Footer()

    def on_mount(self) -> None:
        """Renderiza as 4 abas no mount e agenda auto-refresh só da Now.

        Render inicial cobre todas as abas para o usuário poder trocar
        sem ver tela vazia. Já o timer periódico dispara apenas
        `_refresh_now()` porque é a única aba "ao vivo"; as demais são
        snapshots (recomputá-las a cada 2s seria custoso — today/tools
        re-parseiam transcripts inteiros).
        """
        self._refresh_now()
        self._refresh_today()
        self._refresh_tools()
        self._refresh_session_list()
        # Inicializa tailer e renderiza primeiro estado da aba Audit.
        self._audit_tailer = IncrementalTailer(AUDIT_LOG_PATH)
        self._refresh_audit()
        # Auto-refresh só da Now (demais via `r` manual). Audit tem
        # ciclo proprio de 1 Hz (tail incremental e barato).
        self.set_interval(NOW_REFRESH_SEC, self._refresh_now)
        self.set_interval(AUDIT_REFRESH_SEC, self._tick_audit)

    def on_tabbed_content_tab_activated(
        self, event: TabbedContent.TabActivated
    ) -> None:
        """Foca o widget interativo da aba ao ativá-la.

        Sem isso, o ListView da aba Session não recebe eventos de
        teclado mesmo quando visível — Enter/Space caem nas bindings
        globais da App e o Selected nunca dispara. Resultado: usuário
        vê os itens navegáveis por seta mas não consegue selecionar.
        """
        if event.pane.id == "tab-session":
            try:
                list_view = self.query_one("#session-list", ListView)
                if len(list_view.children) > 0 and list_view.index is None:
                    list_view.index = 0
                list_view.focus()
            except Exception:
                pass

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
        except Exception as e:
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
        except Exception as e:
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
        except Exception as e:
            self.query_one("#tools-content", Static).update(
                Panel(Text(f"Erro: {e}", style="red"), title="Tools", border_style="red")
            )

    def _refresh_session_list(self) -> None:
        """Popula ListView com sessões vivas + sessões do dia."""
        list_view = self.query_one("#session-list", ListView)
        list_view.clear()

        entries: list[tuple[str, str]] = []
        seen: set[str] = set()

        # Sessões vivas primeiro. O marcador de estado usa texto
        # colorido ("viva"/"morta") em vez de ●/○ porque esses ícones
        # são convencionalmente radio buttons — usuário interpretava
        # "● = item selecionado" em vez de "sessão ativa". O highlight
        # de seleção já é fornecido pelo ListView do Textual.
        # Usa `collect_live_sessions` em vez de `discover_live_sessions`
        # para ter `session_name` disponível (vem do aggregator, não do
        # arquivo de session metadata).
        live_stats = collect_live_sessions()
        for s in live_stats:
            if s.session_id in seen:
                continue
            seen.add(s.session_id)
            name_part = (
                f"[bold]{s.session_name}[/bold]  " if s.session_name else ""
            )
            label = (
                f"[bold green]viva[/bold green]  "
                f"{name_part}{s.session_id[:8]}…  {s.cwd}  pid={s.pid}"
            )
            entries.append((s.session_id, label))

        # Sessões do dia (mesmo que mortas)
        for s in collect_sessions_since(today_start_ms()):
            if s.session_id in seen:
                continue
            seen.add(s.session_id)
            if s.alive:
                state_markup = "[bold green]viva [/bold green]"
            else:
                state_markup = "[bold red]morta[/bold red]"
            name_part = (
                f"[bold]{s.session_name}[/bold]  " if s.session_name else ""
            )
            label = f"{state_markup}  {name_part}{s.session_id[:8]}…  {s.cwd}"
            entries.append((s.session_id, label))

        if not entries:
            list_view.append(ListItem(Label("[dim]Nenhuma sessão encontrada hoje.[/dim]")))
            return

        for sid, label in entries:
            list_view.append(SessionListItem(sid=sid, label=label))

        # Garante que há um item "current" desde o primeiro render —
        # sem isso, Enter num ListView recém-populado não dispara
        # Selected porque não há índice focado.
        if list_view.index is None:
            list_view.index = 0

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
        except Exception as e:
            self.query_one("#session-detail", Static).update(
                Panel(Text(f"Erro: {e}", style="red"), border_style="red")
            )

    # ----- Audit: tick + refresh -----------------------------------------

    def _audit_active(self) -> bool:
        """True se a aba Audit esta ativa no momento."""
        try:
            return self.query_one(TabbedContent).active == "tab-audit"
        except Exception:
            return False

    def _tick_audit(self) -> None:
        """Tick periodico (1 Hz). Le novas entries do tail e re-renderiza.

        Mesmo quando a aba nao esta ativa, continuamos drenando o
        tailer pra nao perder entries ate o usuario abrir a aba. So a
        renderizacao e gateada pela aba ativa.
        """
        if self._audit_tailer is None:
            return
        try:
            new_entries = self._audit_tailer.read_new()
        except Exception:
            return
        if new_entries:
            self._audit_entries.extend(new_entries)
        if self._audit_active():
            self._refresh_audit()

    def _refresh_audit(self) -> None:
        try:
            all_entries = list(self._audit_entries)
            visible = _audit_filter_entries(
                all_entries,
                filters=self._audit_filter,
                window_hours=self._audit_window_hours,
                show_test_sessions=self._audit_show_tests,
            )
            table = _audit_render_table(visible)
            footer = _audit_render_footer(visible)

            self.query_one("#audit-table", Static).update(table)

            # Linha de status: filtros ativos + janela + indicador de PAUSED
            status_parts: list[str] = []
            window_label = _format_window_label(self._audit_window_hours)
            status_parts.append(f"window={window_label}")
            if self._audit_filter:
                fk, fv = next(iter(self._audit_filter.items()))
                status_parts.append(f"filter:{fk}={fv}")
            else:
                status_parts.append("filter=none")
            if self._audit_show_tests:
                status_parts.append("show-tests=on")
            paused = self._is_audit_paused()
            if paused:
                status_parts.append("[bold yellow]PAUSED — Press End to resume[/]")
            status_line = "  •  ".join(status_parts)

            status_widget = self.query_one("#audit-status", Static)
            status_widget.update(Group(footer, Text.from_markup(status_line)))

            # Auto-scroll: se nao pausado, vai pra o fundo do container
            # de tabela. Rich Static scrolla container; scrollable=true
            # via CSS overflow-y: auto ja esta setado.
            if not paused:
                with contextlib.suppress(Exception):
                    self.query_one("#audit-table", Static).scroll_end(animate=False)
        except Exception as e:
            with contextlib.suppress(Exception):
                self.query_one("#audit-table", Static).update(
                    Panel(Text(f"Erro: {e}", style="red"), title="Audit", border_style="red")
                )

    def _is_audit_paused(self) -> bool:
        """True se auto-scroll esta pausado (D8)."""
        if self._audit_paused:
            return True
        if self._audit_last_user_action == 0:
            return False
        return (time.monotonic() - self._audit_last_user_action) < AUDIT_AUTO_SCROLL_GRACE_SEC

    def _mark_audit_user_action(self) -> None:
        """Atualiza timestamp da ultima interacao na aba Audit."""
        self._audit_last_user_action = time.monotonic()

    # ----- Audit: actions ------------------------------------------------

    def action_audit_cycle_window(self) -> None:
        """Tecla `t`: cicla 1h -> 24h -> 7d -> all (D1)."""
        if not self._audit_active():
            return
        cur = self._audit_window_hours
        try:
            idx = AUDIT_WINDOW_CYCLE.index(cur)
        except ValueError:
            idx = -1
        next_idx = (idx + 1) % len(AUDIT_WINDOW_CYCLE)
        self._audit_window_hours = AUDIT_WINDOW_CYCLE[next_idx]
        self._mark_audit_user_action()
        self._refresh_audit()

    def action_audit_toggle_tests(self) -> None:
        """Tecla `?`: toggle entre mostrar/ocultar sessoes de teste (D7)."""
        if not self._audit_active():
            return
        self._audit_show_tests = not self._audit_show_tests
        self._mark_audit_user_action()
        self._refresh_audit()

    def action_audit_filter_prompt(self) -> None:
        """Tecla `/`: mostra prompt minimal pra entrar filtro (D4)."""
        if not self._audit_active():
            return
        self._mark_audit_user_action()
        try:
            inp = self.query_one("#audit-filter-input", Input)
            inp.value = ""
            inp.add_class("active")
            inp.focus()
        except Exception:
            pass

    def action_audit_resume_scroll(self) -> None:
        """Tecla End: retoma auto-scroll no fundo (D8)."""
        if not self._audit_active():
            return
        self._audit_paused = False
        self._audit_last_user_action = 0.0
        self._refresh_audit()

    def action_audit_drill_down_root(self) -> None:
        """Tecla `s`: chama `pkexec grep <session> /var/log/claude/tools.log` (D6).

        Drill-down externo: roda o grep com privilegio (Polkit) pra ler
        o log syslog que nao e legivel pelo usuario. Mostra resultado
        no painel #audit-detail. Erro amigavel se pkexec falhar — NAO
        fallback pra sudo no TTY (quebra TUI).
        """
        if not self._audit_active():
            return
        self._mark_audit_user_action()
        # Tenta pegar a sessao da entry mais recente visivel; sem isso
        # nao tem o que filtrar.
        all_entries = list(self._audit_entries)
        visible = _audit_filter_entries(
            all_entries,
            filters=self._audit_filter,
            window_hours=self._audit_window_hours,
            show_test_sessions=self._audit_show_tests,
        )
        if not visible:
            self._update_audit_detail(
                Text("Nenhuma entry visivel — nao tem session_id pra filtrar.",
                     style="yellow"),
            )
            return
        session_id = visible[-1].session_id
        try:
            result = subprocess.run(
                ["pkexec", "grep", session_id, AUDIT_SYSTEM_LOG],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if result.returncode == 0:
                body = (
                    f"[bold]grep {session_id} {AUDIT_SYSTEM_LOG}[/bold]\n\n"
                    + (result.stdout or "(saida vazia)")
                )
                self._update_audit_detail(Text.from_markup(body))
            else:
                err = result.stderr.strip() or f"exit {result.returncode}"
                self._update_audit_detail(
                    Text(
                        f"pkexec falhou: {err}\n\n"
                        f"Rode manualmente: sudo grep {session_id} {AUDIT_SYSTEM_LOG}",
                        style="yellow",
                    ),
                )
        except FileNotFoundError:
            self._update_audit_detail(
                Text(
                    "pkexec nao esta instalado. Instale Polkit ou rode manualmente:\n"
                    f"sudo grep {session_id} {AUDIT_SYSTEM_LOG}",
                    style="yellow",
                ),
            )
        except subprocess.TimeoutExpired:
            self._update_audit_detail(
                Text("pkexec timeout (>10s). Tente novamente.", style="red"),
            )
        except Exception as e:
            self._update_audit_detail(Text(f"Erro: {e}", style="red"))

    def _update_audit_detail(self, content) -> None:
        with contextlib.suppress(Exception):
            self.query_one("#audit-detail", Static).update(content)

    # ----- Event handlers ------------------------------------------------

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        # Usuário pressionou Enter (ou clicou) numa linha da lista
        if isinstance(event.item, SessionListItem):
            self._render_session_detail(event.item.sid)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Filter prompt da aba Audit: aplica e fecha."""
        if event.input.id != "audit-filter-input":
            return
        self._audit_filter = _audit_parse_filter(event.value)
        event.input.remove_class("active")
        event.input.value = ""
        # Re-foca no container da aba pra chaves voltarem a funcionar.
        with contextlib.suppress(Exception):
            self.query_one("#audit-table", Static).focus()
        self._refresh_audit()

    def on_key(self, event) -> None:
        """Captura Esc no filter input pra fechar sem aplicar."""
        if event.key == "escape":
            try:
                inp = self.query_one("#audit-filter-input", Input)
                if inp.has_class("active"):
                    inp.remove_class("active")
                    inp.value = ""
                    self._audit_filter = {}
                    self._refresh_audit()
                    event.stop()
                    return
            except Exception:
                pass
        # Marca interacao do usuario na aba Audit pra pausar auto-scroll.
        # Tecla End tem action propria de retomar — nao trata como pausa.
        if self._audit_active() and event.key not in {"end"}:
            self._mark_audit_user_action()


def run() -> int:
    """Entrypoint da TUI. Retorna exit code."""
    app = DashboardApp()
    app.run()
    return 0
