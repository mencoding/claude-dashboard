"""View 'session <sid>' — drill-down de uma sessão específica.

Aceita prefixo de sessionId (se único). Mostra:
- Identificação e estado
- Resumo agregado (herda de SessionStats)
- Timeline dos últimos N turnos com tokens e tools por turno
- Subagentes disparados com custos individuais
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
from claude_dash.discover import (
    discover_live_sessions,
    find_transcript_for_session,
    subagents_of,
)
from claude_dash.models import SessionStats, Turn
from claude_dash.pricing import cost_of
from claude_dash.views.now import (
    _fmt_duration,
    _fmt_short_cwd,
    _fmt_tokens,
    _total_cost,
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
    lines.append(f"  Tokens     ", style="dim")
    lines.append(f"{_fmt_tokens(total.total)}", style="bold")
    lines.append(f"    (in {_fmt_tokens(total.input_tokens)} · ", style="dim")
    lines.append(f"out {_fmt_tokens(total.output_tokens)} · ", style="dim")
    lines.append(f"cache r {_fmt_tokens(total.cache_read)} · ", style="dim")
    lines.append(f"w {_fmt_tokens(total.cache_creation_1h + total.cache_creation_5m)})\n", style="dim")
    lines.append(f"  Custo      ", style="dim")
    lines.append(f"${cost:,.2f}\n", style="bold yellow")
    lines.append(f"  Mensagens  ", style="dim")
    lines.append(f"{stats.messages_user} user / {stats.messages_assistant} assistant\n", style="bold")
    lines.append(f"  Subagents  ", style="dim")
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
            f"${cost:,.4f}" if cost < 1 else f"${cost:,.2f}",
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
            f"${cost:,.2f}",
            f"{sub_stats.messages_user}/{sub_stats.messages_assistant}",
            tools_str,
        )

    return Panel(table, title=f"Subagentes ({len(subs)})", title_align="left", border_style="dim")


def run(sid: str) -> int:
    console = Console()

    ref = find_transcript_for_session(sid)
    if ref is None:
        console.print(
            f"[red]Não encontrei transcript para SID '[bold]{sid}[/bold]' "
            f"(pode ser prefixo ambíguo ou inexistente).[/red]"
        )
        return 1

    # Busca info viva (se houver)
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
