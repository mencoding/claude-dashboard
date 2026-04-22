"""TUI viva com rich.Live — snapshot das sessões ativas agora."""
from __future__ import annotations

import signal
import time
from datetime import datetime
from pathlib import Path

from rich.align import Align
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from claude_dash.account import AccountInfo, read_account_info
from claude_dash.aggregator import collect_live_sessions
from claude_dash.models import SessionStats, Usage
from claude_dash.pricing import cost_of
from claude_dash.rate_limits import (
    RateLimitSnapshot,
    describe_unavailable,
    format_reset_delta,
    global_worst_case,
)

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

    Padrão: dim abaixo de thresholds[0], amarelo entre, vermelho acima
    de thresholds[1]. Argumentos:
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

    Thresholds: ($0.50, $1.00) vs. ($10, $50) da sessão — cerca de
    20× e 50× mais baixos, refletindo que um único turno raramente
    aproxima do custo cumulativo de uma sessão de horas.
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


def _cost_label(acc: AccountInfo | None) -> str:
    """Rótulo adequado ao plano de billing do usuário.

    Em assinatura flat-rate (Max/Pro), 'Custo estimado' em USD é
    desinformação — o usuário paga mensalidade fixa. O dashboard
    sinaliza isso com o rótulo 'Custo (ref. API)' para deixar claro
    que é custo hipotético, não cobrança efetiva.
    """
    if acc is not None and acc.is_flat_rate:
        return "Custo (ref. API)"
    return "Custo estimado"


def _account_line(acc: AccountInfo | None) -> Text:
    """Linha de info da conta, organizada em 3 campos semânticos:
    email · plano (fallback: billing type) · status dos créditos extras.

    O nome do plano (Max, Pro, etc) é lido de
    ~/.claude/.credentials.json. Se inacessível, cai em billing_label
    como aproximação.
    """
    line = Text()
    if acc is None:
        return line
    line.append(" Conta ", style="dim")
    line.append(f"{acc.email}", style="bold")
    line.append("  ·  Plano: ", style="dim")
    plan = acc.plan_label
    if plan == "—":
        # Sem credentials lidas — cai em billing como aproximação
        line.append(f"{acc.billing_label}", style="bold cyan")
    else:
        line.append(f"{plan}", style="bold cyan")
    line.append("  ·  Créditos extras: ", style="dim")
    # Cor do status de créditos: verde=disponível, amarelo=sem créditos,
    # dim=desabilitado.
    label = acc.extra_usage_label
    if "disponível" in label:
        style = "bold green"
    elif "sem créditos" in label or "bloqueado" in label:
        style = "bold yellow"
    else:
        style = "dim"
    line.append(label, style=style)
    return line


def _rate_limit_bar(pct: float, width: int = 14) -> str:
    """Barra ASCII estilo progresso, proporcional ao percentual.

    Exemplo para pct=42, width=14:
        '[█████░░░░░░░░]'
    """
    pct = max(0.0, min(100.0, pct))
    filled = int(round(pct / 100 * width))
    empty = width - filled
    return "[" + "█" * filled + "░" * empty + "]"


def _rate_limit_block(
    snap: RateLimitSnapshot | None,
    fallback_reason: str | None = None,
) -> Text:
    """Bloco de 2 linhas exibindo consumo 5h e 7d da conta.

    Layout (cada janela em uma linha):
        Janela 5h  [██░░░░░░░░░░░░]  13%   reseta em 3h48m
        Janela 7d  [███░░░░░░░░░░░]  18%   reseta em 6d 1h

    Quando não há captura fresca, renderiza `fallback_reason` em estilo
    dim (se fornecido) — dá feedback ao usuário em vez de silenciar o
    bloco. Se nem snapshot nem fallback houver, retorna Text vazio
    (graceful degradation para callers que preferem o comportamento
    antigo).
    """
    out = Text()
    if snap is None or not snap.is_fresh:
        if fallback_reason:
            out.append(f" {fallback_reason}", style="dim")
        return out

    for label, pct, resets_in_s in (
        ("Janela 5h", snap.five_hour_pct, snap.five_hour_resets_in_seconds),
        ("Janela 7d", snap.seven_day_pct, snap.seven_day_resets_in_seconds),
    ):
        color = (
            "bold red" if pct >= 85
            else "bold yellow" if pct >= 60
            else "bold green"
        )
        out.append(f" {label}  ", style="dim")
        out.append(_rate_limit_bar(pct), style=color)
        out.append(f"  {pct:5.1f}%", style=color)
        delta = format_reset_delta(resets_in_s)
        if delta:
            out.append(f"   reseta em {delta}", style="dim")
        out.append("\n")

    # Remove o último \n para não adicionar linha vazia no header
    if out.plain.endswith("\n"):
        out = out[:-1]
    return out


def _header(
    sessions: list[SessionStats],
    acc: AccountInfo | None = None,
    rl_snap: RateLimitSnapshot | None = None,
) -> Panel:
    """Renderiza o painel de header.

    `acc` e `rl_snap` são injetados opcionalmente pelo caller (em
    `_render`) para evitar dupla leitura de disco: o caller já
    precisa desses valores para calcular `header_size`. Se `None`,
    a função lê sozinha (útil para chamadas standalone/testes).
    """
    n = len(sessions)
    total_tokens = sum(s.total_usage.total for s in sessions)
    total_cost = sum(_total_cost(s) for s in sessions)
    if acc is None:
        acc = read_account_info()

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
    acc_line = _account_line(acc)
    if len(acc_line) > 0:
        header_text.append_text(acc_line)
        header_text.append("\n")
    if rl_snap is None:
        rl_snap = global_worst_case()
    fallback_reason = describe_unavailable() if rl_snap is None else None
    rl_block = _rate_limit_block(rl_snap, fallback_reason=fallback_reason)
    if len(rl_block) > 0:
        header_text.append_text(rl_block)
        header_text.append("\n")
    header_text.append(f" {now}  ", style="dim")
    header_text.append("│  Sessões vivas: ", style="dim")
    header_text.append(f"{n}", style="bold cyan")
    header_text.append("  │  Tokens agregados: ", style="dim")
    header_text.append(f"{_fmt_tokens(total_tokens)}", style="bold")
    header_text.append(f"  │  {_cost_label(acc)}: ", style="dim")
    header_text.append(f"${total_cost:,.2f}", style="bold yellow")
    header_text.append("  │  Cache hit: ", style="dim")
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
    # Header: 1 borda sup + data/stats + 1 borda inf = 3 linhas base.
    # Linhas condicionais: +1 se tem account info, +2 se tem rate limits.
    # Fazemos UMA leitura de cada fonte aqui e injetamos em _header()
    # para evitar dupla I/O por refresh (antes: 4 reads/2s, agora: 2).
    acc = read_account_info()
    rl_snap = global_worst_case()
    has_account = acc is not None
    has_rl = rl_snap is not None
    header_size = 3 + (1 if has_account else 0) + (2 if has_rl else 0)

    layout = Layout()
    layout.split_column(
        Layout(_header(sessions, acc=acc, rl_snap=rl_snap), size=header_size, name="header"),
        Layout(Panel(_session_table(sessions), border_style="dim", title="Sessões",
                     title_align="left"), name="body"),
        Layout(_footer(refresh_sec), size=3, name="footer"),
    )
    return layout


def run(refresh_sec: float = REFRESH_SEC) -> int:
    """Loop principal. Retorna exit code."""
    console = Console()

    # Ctrl+C sai limpo
    def _on_sigint(_sig: int, _frame) -> None:
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
