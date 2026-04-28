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

import csv
import json
import os
import re
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from claude_dash.audit.models import AuditEntry

ExportFormat = Literal["csv", "json"]

# Ordem dos campos no export — fixa pra reprodutibilidade do CSV.
# Mantem em sync com AuditEntry; mudancas aqui devem refletir tambem em
# tests/test_audit_view.py::test_export_csv_schema.
_EXPORT_FIELDS: tuple[str, ...] = (
    "timestamp",
    "hostname",
    "session_id",
    "tool",
    "subagent_type",
    "tool_use_id",
    "status",
    "duration_ms",
    "perm_mode",
    "input_sha",
    "input_bytes",
    "output_bytes",
    "pid",
)

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
    current_host: str | None = None,
    now: datetime | None = None,
) -> list[AuditEntry]:
    """Aplica todos os filtros (D4 + D7 + janela temporal + host).

    Filtros suportados (uma key por vez no dict ``filters``, conforme D4):
    - ``tool``: match exato no nome da tool
    - ``status``: match exato (geralmente "error")
    - ``session_prefix``: prefix-match no session_id
    - ``host``: prefix-match no hostname (#55)

    Parametro extra ``current_host``: quando nao-None, aplica filtro
    "so este host" alem de qualquer ``host=`` explicito em ``filters``.
    Entries com ``hostname`` vazio (logs antigos pre-#55) sao incluidas
    como "sem host conhecido" — nao queremos esconder dado historico
    legitimo so porque o campo nao foi populado.
    """
    filters = filters or {}
    if now is None:
        # Usa o tz da entrada mais recente para evitar mismatch
        # naive/aware. Se vazio, qualquer datetime serve (lista resulta vazia).
        if entries:
            now = datetime.now(tz=entries[-1].timestamp.tzinfo)
        else:
            now = datetime.now().astimezone()

    # #50: status="error" so se aplica a entries event="end" — start nao
    # tem status valido (sempre "running"). Documentado no docstring.
    error_filter = filters.get("status") == "error"

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
        # #50: filtro por status="error" exclui implicitamente entries
        # de start (essas tem status="running" default — nao seriam
        # "error" mas tambem nao queremos mostrar como pending no filtro).
        if error_filter and e.event == "start":
            continue
        if "session_prefix" in filters and not e.session_id.startswith(
            filters["session_prefix"]
        ):
            continue
        if "host" in filters and not e.hostname.startswith(filters["host"]):
            continue
        if current_host is not None and e.hostname and e.hostname != current_host:
            continue
        out.append(e)
    return out


def render_drill_down_human(line: str) -> Panel:
    """Renderiza uma linha bruta do tools.log como Panel labeled (#67-followup).

    Substitui a saida raw RFC 5424 por painel com cada campo identificado:
    Timestamp, Hostname, PID, Event, Session, Tool, Tool use ID, Status,
    Duration, Permission mode, Input SHA, Input bytes, Output bytes,
    plus trailing fields (CWD + tool-specific: cmd/path/url/query/skill).

    Linhas malformadas viram painel red com a linha original — nao crasha.
    """
    from claude_dash.audit.parser import parse_full_line

    entry, extra = parse_full_line(line)
    if entry is None:
        return Panel(
            Text(line.rstrip(), style="red"),
            title="Linha invalida",
            title_align="left",
            border_style="red",
        )

    txt = Text()

    def field(label: str, value: str, *, value_style: str = "") -> None:
        txt.append(f"  {label:<18}", style="dim")
        txt.append(f"{value}\n", style=value_style)

    field("Timestamp:", entry.timestamp.isoformat())
    field("Hostname:", entry.hostname)
    field("PID:", entry.pid or "—")
    event_style = "bold cyan" if entry.event == "start" else "bold"
    field("Event:", entry.event, value_style=event_style)
    field("Session:", entry.session_id)
    field("Tool:", entry.tool, value_style="bold")
    if entry.subagent_type:
        field("Subagent:", entry.subagent_type)
    field("Tool use ID:", entry.tool_use_id or "—")
    status_style = (
        "red" if entry.status == "error"
        else ("yellow" if entry.status == "running" else "green")
    )
    field("Status:", entry.status, value_style=status_style)
    field("Duration:", f"{entry.duration_ms} ms")
    field("Permission mode:", entry.perm_mode or "—")
    field("Input SHA:", entry.input_sha or "—")
    field("Input bytes:", str(entry.input_bytes))
    field("Output bytes:", str(entry.output_bytes))

    # Trailing — separa visualmente
    if extra:
        txt.append("\n")
        if "cwd" in extra:
            field("CWD:", extra["cwd"])
        # Tool-specific summaries do hook _summarize()
        for key, label in (
            ("cmd", "Command:"),
            ("path", "Path:"),
            ("url", "URL:"),
            ("query", "Query:"),
            ("skill", "Skill:"),
            ("subagent", "Subagent input:"),
            ("desc", "Description:"),
        ):
            if key in extra:
                field(label, extra[key])

    ts_str = entry.timestamp.strftime("%H:%M:%S.%f")[:-3]
    title = f"{entry.tool} @ {ts_str}"
    if entry.subagent_type:
        title = f"{entry.tool}({entry.subagent_type}) @ {ts_str}"
    border = "red" if entry.status == "error" else "cyan"
    return Panel(txt, title=title, title_align="left", border_style=border)


def correlate_start_end(entries: list[AuditEntry]) -> list[AuditEntry]:
    """Colapsa pares start↔end pelo mesmo tool_use_id (#50).

    Entries com event="end" prevalecem sobre suas correspondentes
    event="start": o end ja contem duration_ms/output_bytes/status reais,
    enquanto o start so era util enquanto a tool estava em execucao.

    Entries de start sem end correspondente (tools ainda rodando) sao
    preservadas — caller tipicamente exibe com indicador "running".

    Implementacao: passada unica O(n). Usa dict tool_use_id->idx do
    start; quando o end chega, descarta o start. Entries sem
    tool_use_id (ex.: Agent legacy) passam direto sem correlacao.
    """
    # idx do start no array out (pra remocao quando o end chega)
    start_idx_by_tuid: dict[str, int] = {}
    out: list[AuditEntry] = []
    for e in entries:
        if not e.tool_use_id:
            out.append(e)
            continue
        if e.event == "end":
            # Se ha start correspondente, marca pra remover
            si = start_idx_by_tuid.pop(e.tool_use_id, None)
            if si is not None:
                out[si] = e  # substitui in-place
            else:
                out.append(e)
        else:  # event == "start"
            start_idx_by_tuid[e.tool_use_id] = len(out)
            out.append(e)
    return out


def _fmt_bytes(n: int) -> str:
    """Compacta tamanhos: 1234 -> '1.2k'."""
    if n < 1024:
        return str(n)
    if n < 1024 * 1024:
        return f"{n / 1024:.1f}k"
    return f"{n / (1024 * 1024):.1f}M"


def render_table(
    entries: list[AuditEntry],
    *,
    max_rows: int = 500,
    current_host: str | None = None,
) -> Table:
    """Renderiza Rich Table das entries (ja filtradas).

    Mostra so as ultimas ``max_rows`` para evitar custo de render em
    listas gigantes (Rich Table nao virtualiza).

    ``current_host`` (#55): quando nao-None, hostname diferente do atual
    e' renderizado em ``dim`` pra sinalizar que o transcript original esta
    em outra maquina. None desabilita o styling diferenciado (todos iguais).
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
    table.add_column("host", no_wrap=True, width=10)
    table.add_column("tool", no_wrap=True)
    table.add_column("dur_ms", justify="right", no_wrap=True, width=8)
    table.add_column("in", justify="right", no_wrap=True, width=7)
    table.add_column("out", justify="right", no_wrap=True, width=7)

    visible = entries[-max_rows:]
    for e in visible:
        # Se entry e' de outro host, vence o styling de tool (drill-down
        # cross-device e' info mais saliente que o tipo da tool).
        host_other = (
            current_host is not None
            and e.hostname
            and e.hostname != current_host
        )
        style = "dim" if host_other else _color_for(e)
        time_str = e.timestamp.strftime("%H:%M:%S.%f")[:-3]
        sess_str = e.session_id[:8] if e.session_id else "-"
        host_str = e.hostname or "-"
        tool_str = e.tool
        if e.subagent_type:
            tool_str = f"{e.tool}({e.subagent_type})"
        table.add_row(
            time_str,
            sess_str,
            host_str,
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


def _entry_to_row(e: AuditEntry) -> dict[str, str | int | None]:
    """Converte AuditEntry pro shape exportavel (timestamp em ISO 8601)."""
    d = asdict(e)
    d["timestamp"] = e.timestamp.isoformat()
    return {k: d[k] for k in _EXPORT_FIELDS}


def default_export_path(fmt: ExportFormat, now: datetime | None = None) -> Path:
    """Path default `~/audit-export-<ts>.<ext>` com timestamp ISO local."""
    if now is None:
        now = datetime.now()
    # ISO compacto sem microssegundos: 2026-04-28T143052
    ts = now.strftime("%Y-%m-%dT%H%M%S")
    return Path.home() / f"audit-export-{ts}.{fmt}"


def export_entries(
    entries: list[AuditEntry],
    fmt: ExportFormat,
    path: Path,
) -> Path:
    """Escreve `entries` em `path` no formato `fmt` ('csv' ou 'json').

    Escrita atomica via `<path>.tmp` -> `os.replace` pra evitar arquivo
    parcial em caso de erro. Retorna o path final escrito.

    Schema (mesmo p/ csv e json): _EXPORT_FIELDS na ordem definida.
    `subagent_type` e' None pra entries que nao sao Agent — escapa como
    string vazia em CSV, null em JSON.
    """
    if fmt not in ("csv", "json"):
        raise ValueError(f"Formato invalido: {fmt!r} (use 'csv' ou 'json').")

    rows = [_entry_to_row(e) for e in entries]
    tmp = path.with_suffix(path.suffix + ".tmp")
    if fmt == "csv":
        with tmp.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(_EXPORT_FIELDS))
            writer.writeheader()
            for r in rows:
                # CSV nao tem null nativo; None vira "".
                writer.writerow({k: ("" if v is None else v) for k, v in r.items()})
    else:
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=2)
            f.write("\n")
    os.replace(tmp, path)
    return path


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
    if key == "host":
        return {"host": value}
    return {}
