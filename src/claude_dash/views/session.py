"""View 'session <sid>' — drill-down de uma sessão específica.

Aceita prefixo de sessionId (se único). Tres caminhos de resposta (#55):

1. **Transcript JSONL local existe** -> drill-down completo (header +
   overview + timeline + subagents).
2. **Sem JSONL, mas ha entries em sessions.log** -> drill-down parcial:
   header indica `Origem: <hostname>`, lista as tool calls com timestamp
   e duracao, agrega total/top-tools/erro/duracao. Sem timeline de turnos
   nem custos por turno (esses dados so vem do JSONL).
3. **Nem JSONL nem metadata** -> mensagem clara "nao existem dados neste
   host sobre a sessao".
"""
from __future__ import annotations

from datetime import datetime

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from claude_dash.aggregator import (
    build_stats_for_transcript,
    extract_turns,
)
from claude_dash.audit.partial_stats import (
    PartialSessionStats,
    build_partial_stats_from_audit,
)
from claude_dash.discover import (
    discover_live_sessions,
    find_transcript_for_session,
    subagents_of,
)
from claude_dash.models import SessionStats, Turn
from claude_dash.pricing import cost_of
from claude_dash.views.now import (
    _fmt_duration,
    _fmt_tokens,
    _total_cost,
    colored_cost,
    colored_turn_cost,
)

DEFAULT_TIMELINE_TAIL = 20  # últimos N turnos na timeline


def _header(stats: SessionStats) -> Panel:
    now_ms = int(datetime.now().timestamp() * 1000)
    age_ms = now_ms - stats.started_at_ms if stats.started_at_ms else 0

    state = "[green]viva[/green]" if stats.alive else "[red]morta[/red]"
    pid_str = str(stats.pid) if stats.pid else "—"

    started = (
        datetime.fromtimestamp(stats.started_at_ms / 1000).strftime("%Y-%m-%d %H:%M:%S")
        if stats.started_at_ms
        else "—"
    )
    last_act = (
        datetime.fromtimestamp(stats.last_activity_ms / 1000).strftime("%Y-%m-%d %H:%M:%S")
        if stats.last_activity_ms
        else "—"
    )

    h = Text()
    if stats.session_name:
        h.append(f" Nome   {stats.session_name}\n", style="bold cyan")
        h.append(f" SID    {stats.session_id}\n", style="dim cyan")
    else:
        h.append(f" SID    {stats.session_id}\n", style="bold cyan")
    h.append(f" CWD    {stats.cwd}\n", style="dim")
    h.append(" Estado ", style="dim")
    h.append(f"{state}", style="")
    h.append("  |  PID ", style="dim")
    h.append(f"{pid_str}", style="bold")
    h.append("  |  Versão Claude Code ", style="dim")
    h.append(f"{stats.version or '—'}\n", style="")
    h.append(" Iniciada ", style="dim")
    h.append(f"{started}", style="")
    h.append(f" ({_fmt_duration(age_ms) if age_ms else '—'} atrás)\n", style="dim")
    h.append(" Última atividade ", style="dim")
    h.append(f"{last_act}", style="")

    return Panel(h, border_style="cyan", title="claude-dash session", title_align="left")


def _overview(stats: SessionStats) -> Panel:
    total = stats.total_usage
    cost = _total_cost(stats)

    lines = Text()
    lines.append("Totais (cumulativos da sessão):\n", style="bold")
    lines.append("  Tokens     ", style="dim")
    lines.append(f"{_fmt_tokens(total.total)}", style="bold")
    lines.append(f"    (in {_fmt_tokens(total.input_tokens)} · ", style="dim")
    lines.append(f"out {_fmt_tokens(total.output_tokens)} · ", style="dim")
    lines.append(f"cache r {_fmt_tokens(total.cache_read)} · ", style="dim")
    lines.append(
        f"w {_fmt_tokens(total.cache_creation_1h + total.cache_creation_5m)})\n",
        style="dim",
    )
    lines.append("  Custo      ", style="dim")
    lines.append_text(Text.from_markup(f"{colored_cost(cost)}\n"))
    lines.append("  Mensagens  ", style="dim")
    lines.append(
        f"{stats.messages_user} user / {stats.messages_assistant} assistant\n",
        style="bold",
    )
    lines.append("  Subagents  ", style="dim")
    lines.append(f"{stats.subagents}\n", style="bold")

    # Modelos usados
    if stats.usage_by_model:
        lines.append(" Modelos    ", style="dim")
        for i, (model, u) in enumerate(
            sorted(stats.usage_by_model.items(), key=lambda kv: -kv[1].output_tokens)
        ):
            short = model.replace("claude-", "")
            if i > 0:
                lines.append(", ", style="dim")
            lines.append(f"{short}", style="cyan")
            lines.append(f" ({_fmt_tokens(u.total)})", style="dim")
        lines.append("\n")

    # Tools usados
    if stats.tools:
        lines.append(" Tools      ", style="dim")
        top = sorted(stats.tools.items(), key=lambda kv: -kv[1])[:8]
        for i, (name, count) in enumerate(top):
            if i > 0:
                lines.append(" ", style="dim")
            lines.append(f"{name}:{count}", style="dim")

    return Panel(lines, title="Resumo", title_align="left", border_style="dim")


def _timeline(turns: list[Turn], tail: int = DEFAULT_TIMELINE_TAIL) -> Panel:
    if not turns:
        return Panel(
            Text("Nenhum turno assistant com usage encontrado.", style="dim"),
            title="Timeline",
            title_align="left",
            border_style="dim",
        )

    shown = turns[-tail:]
    omitted = len(turns) - len(shown)

    table = Table(
        show_header=True,
        header_style="bold cyan",
        border_style="dim",
        expand=True,
        padding=(0, 1),
    )
    table.add_column("#", justify="right", width=5)
    table.add_column("Hora", width=10)
    table.add_column("Modelo", width=14)
    table.add_column("In", justify="right", width=8)
    table.add_column("Out", justify="right", width=8)
    table.add_column("Cache r", justify="right", width=9)
    table.add_column("Custo", justify="right", width=9)
    table.add_column("Tools disparados", overflow="fold")

    for t in shown:
        time_str = (
            datetime.fromtimestamp(t.timestamp_ms / 1000).strftime("%H:%M:%S")
            if t.timestamp_ms
            else "—"
        )
        short_model = t.model.replace("claude-", "")
        cost = cost_of(t.model, t.usage)
        tools_str = (
            " ".join(t.tools_called) if t.tools_called else "[dim]—[/dim]"
        )
        table.add_row(
            str(t.index + 1),
            time_str,
            short_model,
            _fmt_tokens(t.usage.input_tokens),
            _fmt_tokens(t.usage.output_tokens),
            _fmt_tokens(t.usage.cache_read),
            colored_turn_cost(cost),
            tools_str,
        )

    title = f"Timeline — últimos {len(shown)} de {len(turns)} turnos"
    if omitted > 0:
        title += f" (+{omitted} anteriores)"
    return Panel(table, title=title, title_align="left", border_style="dim")


def _subagents_panel(session_id: str) -> Panel | None:
    subs = subagents_of(session_id)
    if not subs:
        return None

    table = Table(
        show_header=True,
        header_style="bold cyan",
        border_style="dim",
        expand=True,
        padding=(0, 1),
    )
    table.add_column("Subagent ID", width=20)
    table.add_column("Tokens", justify="right", width=10)
    table.add_column("Custo", justify="right", width=9)
    # Formato u/a (user/assistant) alinhado com o resto do dashboard
    table.add_column("Msgs (u/a)", justify="right", width=10)
    table.add_column("Tools", overflow="fold")

    for sub in subs:
        try:
            sub_stats = build_stats_for_transcript(sub)
        except OSError:
            continue
        cost = sum(cost_of(m, u) for m, u in sub_stats.usage_by_model.items())
        top_tools = sorted(sub_stats.tools.items(), key=lambda kv: -kv[1])[:3]
        tools_str = " ".join(f"{n}:{c}" for n, c in top_tools) if top_tools else "—"
        table.add_row(
            sub.session_id,
            _fmt_tokens(sub_stats.total_usage.total),
            colored_cost(cost, thresholds=(1.0, 5.0)),  # escala menor para subagents
            f"{sub_stats.messages_user}/{sub_stats.messages_assistant}",
            tools_str,
        )

    return Panel(table, title=f"Subagentes ({len(subs)})", title_align="left", border_style="dim")


def _partial_header(stats: PartialSessionStats) -> Panel:
    """Header do drill-down parcial (#55) — destaca origem cross-device."""
    first = (
        stats.first_ts.strftime("%Y-%m-%d %H:%M:%S") if stats.first_ts else "—"
    )
    last = stats.last_ts.strftime("%Y-%m-%d %H:%M:%S") if stats.last_ts else "—"

    h = Text()
    h.append(" SID    ", style="dim cyan")
    h.append(f"{stats.session_id}\n", style="bold cyan")
    h.append(" Origem ", style="dim")
    h.append(f"{stats.hostname or '—'}\n", style="bold yellow")
    h.append(" Aviso  ", style="dim")
    h.append(
        "Transcript original nao esta nesta maquina; mostrando apenas metadata\n",
        style="dim italic",
    )
    h.append("        da audit log (sem timeline de turnos, sem custo por turno).\n",
            style="dim italic")
    h.append(" Janela ", style="dim")
    h.append(f"{first}  ate  {last}", style="")

    return Panel(h, border_style="yellow", title="claude-dash session (parcial)",
                 title_align="left")


def _partial_overview(stats: PartialSessionStats) -> Panel:
    """Resumo agregado do drill-down parcial — totais derivaveis do audit."""
    duration_s = stats.duration_ms / 1000 if stats.duration_ms else 0

    lines = Text()
    lines.append("Totais (derivados do audit log):\n", style="bold")
    lines.append("  Tool calls   ", style="dim")
    lines.append(f"{stats.total_calls}\n", style="bold")
    lines.append("  Erros        ", style="dim")
    err_style = "red" if stats.error_count else "green"
    lines.append(
        f"{stats.error_count} ({stats.error_rate * 100:.1f}%)\n",
        style=err_style,
    )
    lines.append("  Duracao      ", style="dim")
    if duration_s >= 60:
        lines.append(f"{duration_s / 60:.1f} min\n", style="bold")
    else:
        lines.append(f"{duration_s:.1f} s\n", style="bold")

    if stats.top_tools:
        lines.append(" Top tools    ", style="dim")
        for i, (name, count) in enumerate(stats.top_tools):
            if i > 0:
                lines.append(" ", style="dim")
            lines.append(f"{name}:{count}", style="dim")

    return Panel(lines, title="Resumo (parcial)", title_align="left", border_style="dim")


def _partial_timeline(stats: PartialSessionStats, tail: int = 50) -> Panel:
    """Lista as tool calls observadas — substitui a timeline de turnos."""
    if not stats.entries:
        return Panel(
            Text("Sem tool calls.", style="dim"),
            title="Tool calls (audit)",
            title_align="left",
            border_style="dim",
        )

    shown = stats.entries[-tail:]
    omitted = len(stats.entries) - len(shown)

    table = Table(
        show_header=True,
        header_style="bold cyan",
        border_style="dim",
        expand=True,
        padding=(0, 1),
    )
    table.add_column("#", justify="right", width=5)
    table.add_column("Hora", width=12)
    table.add_column("Tool", overflow="fold")
    table.add_column("Sub", overflow="fold")
    table.add_column("dur_ms", justify="right", width=8)
    table.add_column("status", width=8)

    for idx, e in enumerate(shown, start=len(stats.entries) - len(shown) + 1):
        time_str = e.timestamp.strftime("%H:%M:%S")
        status_style = "red" if e.status == "error" else "green"
        table.add_row(
            str(idx),
            time_str,
            e.tool,
            e.subagent_type or "—",
            str(e.duration_ms),
            f"[{status_style}]{e.status}[/{status_style}]",
        )

    title = f"Tool calls (audit) — ultimas {len(shown)} de {len(stats.entries)}"
    if omitted > 0:
        title += f" (+{omitted} anteriores)"
    return Panel(table, title=title, title_align="left", border_style="dim")


def run(sid: str) -> int:
    console = Console()

    ref = find_transcript_for_session(sid)
    if ref is not None:
        # Caminho 1: transcript JSONL local — drill-down completo.
        live_by_sid = {ls.session_id: ls for ls in discover_live_sessions()}
        live = live_by_sid.get(ref.session_id)

        stats = build_stats_for_transcript(ref, live=live)
        stats.subagents = len(subagents_of(ref.session_id))

        turns = extract_turns(ref)

        console.print(_header(stats))
        console.print(_overview(stats))
        console.print(_timeline(turns))

        sub_panel = _subagents_panel(ref.session_id)
        if sub_panel is not None:
            console.print(sub_panel)
        return 0

    # Sem JSONL local — tenta caminho 2 (metadata da sessions.log).
    partial = build_partial_stats_from_audit(sid)
    if partial is not None:
        console.print(_partial_header(partial))
        console.print(_partial_overview(partial))
        console.print(_partial_timeline(partial))
        return 0

    # Caminho 3: nem JSONL nem metadata.
    console.print(
        f"[red]Nao existem dados neste host sobre a sessao "
        f"'[bold]{sid}[/bold]'.[/red]\n"
        f"[dim]Verifique se o SID esta correto ou rode em outra maquina.[/dim]"
    )
    return 1
