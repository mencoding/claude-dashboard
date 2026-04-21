"""View 'tools' — breakdown global de tool_use nas últimas 24h.

Diferente de `now` (por sessão) e `today` (sessões do dia), esta view
tem foco transversal em **tools**: para cada tool, quantas vezes foi
invocada, em quantas sessões distintas, qual hora do dia concentrou
mais uso.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from claude_dash.aggregator import collect_tool_usage_since
from claude_dash.models import ToolUsageStats


DEFAULT_WINDOW = timedelta(hours=24)


def window_start_ms(window: timedelta = DEFAULT_WINDOW) -> int:
    return int((datetime.now() - window).timestamp() * 1000)


def _histogram_bar(hist: list[int], width: int = 24) -> str:
    """ASCII sparkline de 24 horas usando blocos Unicode."""
    if not any(hist):
        return " " * width
    max_val = max(hist)
    # 8 níveis de intensidade ASCII (Unicode block elements)
    blocks = " ▁▂▃▄▅▆▇█"
    return "".join(
        blocks[min(len(blocks) - 1, int((v / max_val) * (len(blocks) - 1) + 0.5))]
        for v in hist
    )


def _header(tools: dict[str, ToolUsageStats], since_ms: int) -> Panel:
    total_calls = sum(t.total_count for t in tools.values())
    distinct_sessions = len({
        sid for t in tools.values() for sid in t.count_by_session
    })

    since_str = datetime.fromtimestamp(since_ms / 1000).strftime("%Y-%m-%d %H:%M")
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    header = Text()
    header.append(f" Janela: {since_str}  →  agora ({now_str})\n", style="dim")
    header.append(" Tools distintas: ", style="dim")
    header.append(f"{len(tools)}", style="bold cyan")
    header.append("   Invocações totais: ", style="dim")
    header.append(f"{total_calls:,}", style="bold")
    header.append("   Sessões contribuintes: ", style="dim")
    header.append(f"{distinct_sessions}", style="bold")

    return Panel(header, border_style="cyan", title="claude-dash tools", title_align="left")


def _tools_table(tools: dict[str, ToolUsageStats]) -> Panel:
    table = Table(
        show_header=True,
        header_style="bold cyan",
        border_style="dim",
        expand=True,
        padding=(0, 1),
    )
    table.add_column("Tool", width=18)
    table.add_column("Chamadas", justify="right", width=9)
    table.add_column("% total", justify="right", width=8)
    table.add_column("Sessões", justify="right", width=8)
    table.add_column("Pico h", justify="right", width=7)
    table.add_column("Histograma 24h (00→23)", overflow="crop")

    total_calls = sum(t.total_count for t in tools.values())
    if total_calls == 0:
        return Panel(
            Text("Nenhum tool usado nas últimas 24h.", style="dim"),
            title="Breakdown por tool",
            title_align="left",
            border_style="dim",
        )

    ordered = sorted(tools.values(), key=lambda t: -t.total_count)
    for t in ordered:
        pct = 100 * t.total_count / total_calls
        peak = t.peak_hour
        peak_str = f"{peak:02d}h" if peak is not None else "—"
        table.add_row(
            f"[bold cyan]{t.name}[/bold cyan]",
            f"{t.total_count:,}",
            f"{pct:4.1f}%",
            str(t.session_count),
            peak_str,
            _histogram_bar(t.hours_histogram),
        )

    return Panel(table, title="Breakdown por tool", title_align="left", border_style="dim")


def _hourly_aggregate(tools: dict[str, ToolUsageStats]) -> Panel:
    """Histograma agregado (soma de todos os tools) por hora."""
    agg = [0] * 24
    for t in tools.values():
        for h, count in enumerate(t.hours_histogram):
            agg[h] += count

    total = sum(agg)
    if total == 0:
        return Panel(
            Text("Sem atividade.", style="dim"),
            title="Distribuição por hora (todas as tools)",
            title_align="left",
            border_style="dim",
        )

    max_val = max(agg)
    peak_hour = agg.index(max_val)

    # Desenha histograma mais detalhado: cada hora numa linha
    lines = []
    bar_width = 40
    for h in range(24):
        count = agg[h]
        bar_len = int((count / max_val) * bar_width) if max_val else 0
        bar = "█" * bar_len
        pct = 100 * count / total if total else 0
        label_style = "bold yellow" if h == peak_hour else "dim"
        lines.append(
            f"  [{label_style}]{h:02d}h[/{label_style}]  "
            f"[cyan]{bar:<{bar_width}}[/cyan] "
            f"[dim]{count:>4} ({pct:4.1f}%)[/dim]"
        )

    return Panel(
        "\n".join(lines),
        title=f"Distribuição por hora (pico: {peak_hour:02d}h)",
        title_align="left",
        border_style="dim",
    )


def run() -> int:
    """Render estático do breakdown das últimas 24h. Retorna exit code."""
    console = Console()
    since_ms = window_start_ms()
    tools = collect_tool_usage_since(since_ms)

    console.print(_header(tools, since_ms))
    console.print(_tools_table(tools))
    console.print(_hourly_aggregate(tools))
    return 0
