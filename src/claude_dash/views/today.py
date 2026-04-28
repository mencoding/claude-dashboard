"""View 'today' — agregado do dia corrente desde 00:00 local.

Render estático (um único print), diferente do `now` que é Live.
Os totais exibidos são **exatos da janela** — entradas do transcript
anteriores a 00:00 são ignoradas.
"""
from __future__ import annotations

from datetime import datetime

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from claude_dash.aggregator import collect_sessions_since
from claude_dash.models import SessionStats
from claude_dash.pricing import cost_of
from claude_dash.account import read_account_info
from claude_dash.views.now import (
    _account_line,
    _cost_label,
    _fmt_duration,
    _fmt_short_cwd,
    _fmt_tokens,
    _session_label,
    _short_sid,
    _tools_summary,
    _total_cost,
    colored_cost,
)


def today_start_ms() -> int:
    """Epoch ms do início do dia corrente no timezone local."""
    now = datetime.now()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(start.timestamp() * 1000)


def _header(sessions: list[SessionStats], since_ms: int) -> Panel:
    total_tokens = sum(s.total_usage.total for s in sessions)
    total_cost = sum(_total_cost(s) for s in sessions)
    total_msgs_user = sum(s.messages_user for s in sessions)
    total_msgs_asst = sum(s.messages_assistant for s in sessions)
    alive = sum(1 for s in sessions if s.alive)
    dead = len(sessions) - alive
    acc = read_account_info()

    since_str = datetime.fromtimestamp(since_ms / 1000).strftime("%Y-%m-%d %H:%M")
    now_str = datetime.now().strftime("%H:%M:%S")

    header = Text()
    acc_line = _account_line(acc)
    if len(acc_line) > 0:
        header.append_text(acc_line)
        header.append("\n")
    header.append(f" {since_str}  →  {now_str}   ", style="dim")
    header.append(f"{len(sessions)}", style="bold cyan")
    header.append(" sessões   ", style="dim")
    header.append(f"[{alive} vivas · {dead} mortas]\n", style="dim")
    header.append(f" Tokens ", style="dim")
    header.append(f"{_fmt_tokens(total_tokens)}", style="bold")
    header.append(f"   {_cost_label(acc)} ", style="dim")
    header.append(f"${total_cost:,.2f}", style="bold yellow")
    header.append("   Mensagens ", style="dim")
    header.append(f"{total_msgs_user}u/{total_msgs_asst}a", style="bold")

    return Panel(header, border_style="cyan", title="claude-dash today", title_align="left")


def _session_table(sessions: list[SessionStats]) -> Panel:
    table = Table(
        show_header=True,
        header_style="bold cyan",
        border_style="dim",
        expand=True,
        padding=(0, 1),
    )
    table.add_column("Sessão", width=20, overflow="fold")
    table.add_column("CWD", overflow="ellipsis")
    table.add_column("Estado", width=7, justify="center")
    table.add_column("Última atividade", width=18)
    table.add_column("Modelo", width=14)
    table.add_column("Tokens", justify="right", width=10)
    table.add_column("Custo", justify="right", width=9)
    table.add_column("Msgs", justify="right", width=10)
    table.add_column("Sub", justify="right", width=4)
    table.add_column("Tools (top 4)", overflow="fold")

    for s in sessions:
        total = s.total_usage.total
        cost = _total_cost(s)

        state = "[green]viva[/green]" if s.alive else "[red]morta[/red]"
        dom_model = s.dominant_model or "—"
        dom_short = dom_model.replace("claude-", "") if dom_model != "—" else dom_model

        last_activity = (
            datetime.fromtimestamp(s.last_activity_ms / 1000).strftime("%H:%M:%S")
            if s.last_activity_ms
            else "—"
        )

        msgs = f"{s.messages_user}u/{s.messages_assistant}a"

        table.add_row(
            _session_label(s),
            _fmt_short_cwd(s.cwd),
            state,
            last_activity,
            dom_short,
            _fmt_tokens(total),
            colored_cost(cost),
            msgs,
            str(s.subagents),
            _tools_summary(s),
        )

    if len(sessions) == 0:
        return Panel(
            Text("Nenhuma sessão com atividade hoje ainda.", style="dim"),
            title="Sessões",
            title_align="left",
            border_style="dim",
        )

    return Panel(table, title="Sessões", title_align="left", border_style="dim")


def _tools_aggregate(sessions: list[SessionStats], top_n: int = 10) -> Panel:
    totals: dict[str, int] = {}
    for s in sessions:
        for name, count in s.tools.items():
            totals[name] = totals.get(name, 0) + count

    if not totals:
        return Panel(
            Text("Nenhum tool usado.", style="dim"),
            title="Tools (todas as sessões)",
            title_align="left",
            border_style="dim",
        )

    top = sorted(totals.items(), key=lambda kv: -kv[1])[:top_n]
    # Denominador é a soma de TODOS os tools, não só os exibidos — assim
    # as porcentagens refletem share real, e a tail (tools fora do top N)
    # "some" em forma de pct não-mostrados que fazem a soma visível
    # ficar abaixo de 100% quando existe cauda.
    total_calls = sum(totals.values())

    lines = []
    for name, count in top:
        pct = 100 * count / total_calls if total_calls else 0
        lines.append(f"  [bold cyan]{name:<16}[/bold cyan] [bold]{count:>4}[/bold]  [dim]({pct:4.1f}%)[/dim]")

    content = "\n".join(lines)
    if len(totals) > top_n:
        content += f"\n[dim]  … e mais {len(totals) - top_n} tool(s) com menor uso[/dim]"

    return Panel(
        content,
        title=f"Top {min(top_n, len(totals))} tools",
        title_align="left",
        border_style="dim",
    )


def run() -> int:
    """Imprime snapshot estático do dia corrente. Retorna exit code."""
    console = Console()
    since_ms = today_start_ms()
    sessions = collect_sessions_since(since_ms)

    console.print(_header(sessions, since_ms))
    console.print(_session_table(sessions))
    console.print(_tools_aggregate(sessions))
    return 0
