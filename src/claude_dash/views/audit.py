"""View da aba 'Audit' — tabela, footer e logica de filtro.

Funcoes puras (sem estado Textual) para facilitar teste e reuso. O
wire de eventos da TUI fica em ``views/tui.py``.

Color scheme (D2):
- Bash, Read, Edit, Write -> dim/text-muted
- WebFetch, WebSearch -> yellow
- Agent, Skill, mcp__* -> blue
- outras -> default
- status="error" -> red **override** (qualquer tool com erro vira red)
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timedelta

from rich.table import Table
from rich.text import Text

from claude_dash.audit.models import AuditEntry

# Sessao de teste = qualquer session_id que nao seja UUID v4 valido.
# Mantemos hide-by-default por que o hook foi exercitado com fixtures
# tipo "test-abc-123" durante a fase 1; tecla `?` toggla.
_UUID_V4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)

_GRAY_TOOLS = {"Bash", "Read", "Edit", "Write"}
_YELLOW_TOOLS = {"WebFetch", "WebSearch"}
_BLUE_TOOLS = {"Agent", "Skill"}


def _color_for(entry: AuditEntry) -> str:
    """Resolve estilo Rich para a linha. status=error sempre vence."""
    if entry.status == "error":
        return "red"
    tool = entry.tool
    if tool in _GRAY_TOOLS:
        return "dim"
    if tool in _YELLOW_TOOLS:
        return "yellow"
    if tool in _BLUE_TOOLS or tool.startswith("mcp__"):
        return "blue"
    return ""


def _is_test_session(session_id: str) -> bool:
    """True se session_id NAO bate com UUID v4 (provavel fixture/teste)."""
    return _UUID_V4_RE.match(session_id) is None


def _within_window(entry: AuditEntry, now: datetime, window_hours: float | None) -> bool:
    """Aplica janela temporal. ``window_hours=None`` = sem janela (all)."""
    if window_hours is None:
        return True
    cutoff = now - timedelta(hours=window_hours)
    # Comparar timestamps timezone-aware vs naive levanta TypeError.
    # Audit log sempre tem tz (isoformat com offset); now() usa o mesmo
    # caminho.
    try:
        return entry.timestamp >= cutoff
    except TypeError:
        return True


def filter_entries(
    entries: list[AuditEntry],
    *,
    filters: dict[str, str] | None = None,
    window_hours: float | None = 24.0,
    show_test_sessions: bool = False,
    now: datetime | None = None,
) -> list[AuditEntry]:
    """Aplica todos os filtros (D4 + D7 + janela temporal).

    Filtros suportados (uma key por vez, conforme D4):
    - ``tool``: match exato no nome da tool
    - ``status``: match exato (geralmente "error")
    - ``session_prefix``: prefix-match no session_id
    """
    filters = filters or {}
    if now is None:
        # Usa o tz da entrada mais recente para evitar mismatch
        # naive/aware. Se vazio, qualquer datetime serve (lista resulta vazia).
        if entries:
            now = datetime.now(tz=entries[-1].timestamp.tzinfo)
        else:
            now = datetime.now().astimezone()

    out: list[AuditEntry] = []
    for e in entries:
        if not show_test_sessions and _is_test_session(e.session_id):
            continue
        if not _within_window(e, now, window_hours):
            continue
        if "tool" in filters and e.tool != filters["tool"]:
            continue
        if "status" in filters and e.status != filters["status"]:
            continue
        if "session_prefix" in filters and not e.session_id.startswith(
            filters["session_prefix"]
        ):
            continue
        out.append(e)
    return out


def _fmt_bytes(n: int) -> str:
    """Compacta tamanhos: 1234 -> '1.2k'."""
    if n < 1024:
        return str(n)
    if n < 1024 * 1024:
        return f"{n / 1024:.1f}k"
    return f"{n / (1024 * 1024):.1f}M"


def render_table(entries: list[AuditEntry], *, max_rows: int = 500) -> Table:
    """Renderiza Rich Table das entries (ja filtradas).

    Mostra so as ultimas ``max_rows`` para evitar custo de render em
    listas gigantes (Rich Table nao virtualiza).
    """
    table = Table(
        expand=True,
        show_header=True,
        header_style="bold",
        row_styles=[],
        pad_edge=False,
    )
    table.add_column("time", style="cyan", no_wrap=True, width=12)
    table.add_column("sess", no_wrap=True, width=8)
    table.add_column("tool", no_wrap=True)
    table.add_column("dur_ms", justify="right", no_wrap=True, width=8)
    table.add_column("in", justify="right", no_wrap=True, width=7)
    table.add_column("out", justify="right", no_wrap=True, width=7)

    visible = entries[-max_rows:]
    for e in visible:
        style = _color_for(e)
        time_str = e.timestamp.strftime("%H:%M:%S.%f")[:-3]
        sess_str = e.session_id[:8] if e.session_id else "-"
        tool_str = e.tool
        if e.subagent_type:
            tool_str = f"{e.tool}({e.subagent_type})"
        table.add_row(
            time_str,
            sess_str,
            tool_str,
            str(e.duration_ms),
            _fmt_bytes(e.input_bytes),
            _fmt_bytes(e.output_bytes),
            style=style,
        )
    return table


def render_footer(entries: list[AuditEntry]) -> Text:
    """Footer da aba: total + top 3 tools + taxa de erro."""
    total = len(entries)
    if total == 0:
        return Text("(sem entries na janela)", style="dim")

    counter: Counter[str] = Counter(e.tool for e in entries)
    top3 = counter.most_common(3)
    top3_str = ", ".join(f"{name}={n}" for name, n in top3)

    errors = sum(1 for e in entries if e.status == "error")
    err_pct = (errors / total) * 100 if total else 0.0

    err_style = "red" if errors else "green"
    txt = Text()
    txt.append(f"total={total}  ", style="bold")
    txt.append(f"top: {top3_str}  ")
    txt.append(f"err={errors} ({err_pct:.1f}%)", style=err_style)
    return txt


def parse_filter_input(raw: str) -> dict[str, str]:
    """Converte input do prompt `/` em dict de filtros (D4).

    Vazio -> dict vazio (limpa). Nao combina filtros — uma key so.
    """
    s = raw.strip().lstrip("/")
    if not s:
        return {}
    if s == "error":
        return {"status": "error"}
    if "=" not in s:
        return {}
    key, _, value = s.partition("=")
    key = key.strip()
    value = value.strip()
    if not value:
        return {}
    if key == "tool":
        return {"tool": value}
    if key == "status":
        return {"status": value}
    if key == "session":
        return {"session_prefix": value}
    return {}
