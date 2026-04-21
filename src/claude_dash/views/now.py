"""TUI viva com rich.Live — snapshot das sessões ativas agora."""
from __future__ import annotations

import signal
import time
from datetime import datetime
from pathlib import Path

from rich.align import Align
from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from claude_dash.aggregator import collect_live_sessions
from claude_dash.models import SessionStats, Usage
from claude_dash.pricing import cost_of


REFRESH_SEC = 2.0


def _fmt_tokens(n: int) -> str:
    """3_500_000 → '3.5M'; 12_345 → '12k'; 999 → '999'."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return str(n)


def _fmt_duration(ms_delta: int) -> str:
    """Duração em ms → 'HhMm' / 'Mm' / 'Ns'."""
    s = ms_delta // 1000
    if s >= 86400:
        d, rem = divmod(s, 86400)
        return f"{d}d{rem // 3600}h"
    if s >= 3600:
        h, rem = divmod(s, 3600)
        return f"{h}h{rem // 60}m"
    if s >= 60:
        m, rem = divmod(s, 60)
        return f"{m}m{rem:02d}s"
    return f"{s}s"


def _fmt_short_cwd(cwd: Path, width: int = 24) -> str:
    """Abrevia cwd para caber na coluna; mantém sufixo significativo."""
    s = str(cwd)
    # Reduz /home/<user>/ → ~
    home = str(Path.home())
    if s.startswith(home):
        s = "~" + s[len(home):]
    if len(s) <= width:
        return s
    return "…" + s[-(width - 1):]


def _short_sid(sid: str) -> str:
    return sid[:8] + "…"


def _total_cost(s: SessionStats) -> float:
    return sum(cost_of(model, u) for model, u in s.usage_by_model.items())


def colored_cost(usd: float, thresholds: tuple[float, float] = (10.0, 50.0)) -> str:
    """Formata custo em USD com cor por faixa.

    Padrão: verde/dim abaixo de thresholds[0], amarelo entre, vermelho
    acima de thresholds[1]. Argumentos:
        thresholds[0]: limite para cor amarela ("atenção")
        thresholds[1]: limite para cor vermelha ("custo alto")
    Usado no display de sessões agregadas (`now`/`today`).
    """
    if usd >= thresholds[1]:
        style = "bold red"
    elif usd >= thresholds[0]:
        style = "bold yellow"
    else:
        style = "dim"
    fmt = f"${usd:,.4f}" if usd < 1 else f"${usd:,.2f}"
    return f"[{style}]{fmt}[/{style}]"


def colored_turn_cost(usd: float) -> str:
    """Versão com thresholds menores, apropriados para custo de um turno.

    A escala por-turno é 2 ordens de grandeza menor que por-sessão:
    $0.50/turno já é alto, $1.00/turno é caso de investigação.
    """
    return colored_cost(usd, thresholds=(0.5, 1.0))


def _tools_summary(s: SessionStats, top_n: int = 4) -> str:
    if not s.tools:
        return "-"
    top = sorted(s.tools.items(), key=lambda kv: -kv[1])[:top_n]
    return " ".join(f"{name}:{count}" for name, count in top)


def _tokens_breakdown(s: SessionStats) -> str:
    """Célula multi-linha: total (bold) + in/out + cache r/w + /turno.

    Usa markup Rich para colorir: total em branco forte, breakdown em dim.
    """
    total = s.total_usage
    per_turn = s.tokens_per_turn
    # Soma por tipo (agregando todos os modelos da sessão + subagentes)
    lines = [
        f"[bold]{_fmt_tokens(total.total)}[/bold]",
        f"[dim]in {_fmt_tokens(total.input_tokens)} / out {_fmt_tokens(total.output_tokens)}[/dim]",
        f"[dim]cache r {_fmt_tokens(total.cache_read)} / w {_fmt_tokens(total.cache_creation_1h + total.cache_creation_5m)}[/dim]",
        f"[dim]/turno {_fmt_tokens(int(per_turn))}[/dim]",
    ]
    return "\n".join(lines)


def _context_cell(s: SessionStats) -> str:
    """Tokens em uso no contexto ativo (último turno assistant)."""
    ctx = s.active_context_tokens
    if ctx == 0:
        return "—"
    return _fmt_tokens(ctx)


def _session_table(sessions: list[SessionStats]) -> Table:
    table = Table(
        title=None,
        show_header=True,
        header_style="bold cyan",
        border_style="dim",
        expand=True,
        padding=(0, 1),
    )
    table.add_column("PID", style="dim", justify="right", width=6)
    table.add_column("SID", width=10)
    table.add_column("CWD", overflow="ellipsis")
    table.add_column("Idade", justify="right", width=8)
    table.add_column("Modelo", width=14)
    table.add_column("Tokens (total / in·out / cache r·w / turno)",
                     justify="right", width=22)
    table.add_column("Ctx", justify="right", width=7)
    table.add_column("Custo", justify="right", width=9)
    table.add_column("Msgs", justify="right", width=10)
    table.add_column("Sub", justify="right", width=4)
    table.add_column("Tools (top 4)", overflow="fold")

    now_ms = int(time.time() * 1000)
    for s in sessions:
        cost = _total_cost(s)
        age_ms = now_ms - s.started_at_ms if s.started_at_ms else 0

        pid_txt = str(s.pid) if s.pid else "—"
        status = "[green]●[/green]" if s.alive else "[red]●[/red]"
        dom_model = s.dominant_model or "—"
        # Encurta "claude-opus-4-7" → "opus-4-7"
        dom_short = dom_model.replace("claude-", "") if dom_model != "—" else dom_model

        msgs = f"{s.messages_user}u/{s.messages_assistant}a"

        table.add_row(
            f"{status}{pid_txt}",
            _short_sid(s.session_id),
            _fmt_short_cwd(s.cwd),
            _fmt_duration(age_ms) if age_ms else "—",
            dom_short,
            _tokens_breakdown(s),
            _context_cell(s),
            colored_cost(cost),
            msgs,
            str(s.subagents),
            _tools_summary(s),
            end_section=True,  # linha divisória entre sessões
        )
    return table


def _header(sessions: list[SessionStats]) -> Panel:
    n = len(sessions)
    total_tokens = sum(s.total_usage.total for s in sessions)
    total_cost = sum(_total_cost(s) for s in sessions)

    total_usage = Usage()
    tool_totals: dict[str, int] = {}
    for s in sessions:
        for u in s.usage_by_model.values():
            total_usage += u
        for name, count in s.tools.items():
            tool_totals[name] = tool_totals.get(name, 0) + count

    cache_hit_pct = 0.0
    denom = total_usage.input_tokens + total_usage.cache_read
    if denom > 0:
        cache_hit_pct = 100 * total_usage.cache_read / denom

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header_text = Text()
    header_text.append(f" {now}  ", style="dim")
    header_text.append(f"│  Sessões vivas: ", style="dim")
    header_text.append(f"{n}", style="bold cyan")
    header_text.append(f"  │  Tokens agregados: ", style="dim")
    header_text.append(f"{_fmt_tokens(total_tokens)}", style="bold")
    header_text.append(f"  │  Custo estimado: ", style="dim")
    header_text.append(f"${total_cost:,.2f}", style="bold yellow")
    header_text.append(f"  │  Cache hit: ", style="dim")
    header_text.append(f"{cache_hit_pct:.1f}%", style="bold green")

    return Panel(Align.left(header_text), border_style="cyan", padding=(0, 1))


def _footer(refresh_sec: float) -> Panel:
    # Formata como inteiro quando possível ("2s"), senão com 1 casa ("2.5s")
    interval = f"{refresh_sec:g}s"
    hints = Text.from_markup(
        f"[dim]Refresh a cada {interval}  •  Ctrl+C para sair[/dim]"
    )
    return Panel(Align.center(hints), border_style="dim", padding=(0, 1))


def _render(sessions: list[SessionStats], refresh_sec: float) -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(_header(sessions), size=3, name="header"),
        Layout(Panel(_session_table(sessions), border_style="dim", title="Sessões",
                     title_align="left"), name="body"),
        Layout(_footer(refresh_sec), size=3, name="footer"),
    )
    return layout


def run(refresh_sec: float = REFRESH_SEC) -> int:
    """Loop principal. Retorna exit code."""
    console = Console()

    # Ctrl+C sai limpo
    def _on_sigint(_sig: int, _frame) -> None:  # noqa: ANN001
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _on_sigint)

    try:
        with Live(
            _render(collect_live_sessions(), refresh_sec),
            console=console,
            refresh_per_second=1 / refresh_sec,
            screen=False,
        ) as live:
            while True:
                time.sleep(refresh_sec)
                sessions = collect_live_sessions()
                live.update(_render(sessions, refresh_sec))
    except KeyboardInterrupt:
        console.print("[dim]saindo…[/dim]")
        return 0
