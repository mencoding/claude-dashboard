"""Comparacao cross-session de tool calls (#38).

Pure functions que recebem entries da audit log e produzem timeline
merged + correlacoes entre sessoes paralelas.

Caso de uso: 3+ sessoes Claude Code rodando simultaneamente; usuario
quer entender quem fez o que e em que ordem. Ex.: sessao A leu arquivo
X as 14:30, sessao B editou X as 14:30:03 — pattern read-then-edit
cross-session que pode indicar trabalho em paralelo no mesmo arquivo.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from rich.columns import Columns
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from claude_dash.audit.models import AuditEntry


@dataclass(slots=True)
class Correlation:
    """Par de tool calls cross-session com signal de relacionamento."""

    session_a: str
    session_b: str
    entry_a: AuditEntry
    entry_b: AuditEntry
    delta_ms: int  # quanto tempo passou entre A -> B (sempre positivo)
    reason: str  # "same_path" | "same_cmd" | "read_then_edit"


def group_by_session(
    entries: list[AuditEntry],
    sids: list[str],
) -> dict[str, list[AuditEntry]]:
    """Particiona entries por session_id, fazendo prefix-match nos sids dados.

    Retorna dict ``sid -> [entries]`` ordenado por timestamp. Sids sem
    match viram lista vazia (caller decide como sinalizar).
    """
    out: dict[str, list[AuditEntry]] = {sid: [] for sid in sids}
    for e in entries:
        for sid in sids:
            if e.session_id.startswith(sid):
                out[sid].append(e)
                break
    for v in out.values():
        v.sort(key=lambda x: x.timestamp)
    return out


def merge_timelines(
    grouped: dict[str, list[AuditEntry]],
) -> list[tuple[str, AuditEntry]]:
    """Merge global por timestamp das listas ja-ordenadas (#38).

    Retorna lista ``[(session_id, entry), ...]`` ordenada
    cronologicamente. Tie-break por session_id (lexicografico) pra
    determinismo em testes.
    """
    flat: list[tuple[str, AuditEntry]] = []
    for sid, entries in grouped.items():
        for e in entries:
            flat.append((sid, e))
    flat.sort(key=lambda kv: (kv[1].timestamp, kv[0]))
    return flat


def _entry_target(entry: AuditEntry) -> str | None:
    """Identificador do 'alvo' da tool call pra correlacao.

    Returns:
        - input_sha truncado pra Bash/WebFetch/WebSearch (mesmo cmd/url/query)
        - input_sha pra Read/Edit/Write (que codifica o file_path)
        - None pra tools sem target identificavel (Skill, Agent etc.)
    """
    if entry.tool in ("Bash", "Read", "Edit", "Write", "WebFetch", "WebSearch"):
        return entry.input_sha or None
    return None


def find_correlations(
    grouped: dict[str, list[AuditEntry]],
    window_ms: int = 500,
) -> list[Correlation]:
    """Detecta tool calls correlacionadas entre sessoes diferentes.

    Heuristicas:
    1. **same_path / same_cmd**: mesmo input_sha em sessoes distintas
       dentro da janela ``window_ms``. Bash com mesmo cmd, Read/Edit/Write
       com mesmo path.
    2. **read_then_edit**: sessao A faz Read em path X, sessao B faz
       Edit/Write em mesmo path X em <= 5s. Pattern de "uma sessao leu,
       outra editou" — sinal classico de race em refactor paralelo.

    Implementacao O(n²) sobre `merged` — caller espera N pequeno
    (poucas entries em janela curta). Pra escalar, particionar por
    `_entry_target` e aplicar matching local; fica como otimizacao
    futura se virar gargalo.
    """
    merged = merge_timelines(grouped)
    out: list[Correlation] = []
    window = timedelta(milliseconds=window_ms)
    rte_window = timedelta(seconds=5)

    for i, (sid_a, e_a) in enumerate(merged):
        target_a = _entry_target(e_a)
        if target_a is None:
            continue
        for sid_b, e_b in merged[i + 1:]:
            if sid_a == sid_b:
                continue
            delta = e_b.timestamp - e_a.timestamp
            # Optimizacao: lista esta ordenada — assim que delta passa
            # do maior threshold considerado, podemos parar.
            if delta > rte_window:
                break
            target_b = _entry_target(e_b)
            if target_b is None:
                continue
            delta_ms = int(delta.total_seconds() * 1000)
            # 1. Mesma assinatura (cmd/path) em sessoes diferentes
            if target_a == target_b and delta <= window:
                reason = "same_cmd" if e_a.tool == "Bash" else "same_path"
                out.append(Correlation(
                    session_a=sid_a, session_b=sid_b,
                    entry_a=e_a, entry_b=e_b,
                    delta_ms=delta_ms, reason=reason,
                ))
                continue
            # 2. read_then_edit: A=Read X, B=Edit/Write X em <=5s
            if (
                e_a.tool == "Read"
                and e_b.tool in ("Edit", "Write")
                and target_a == target_b
                and delta <= rte_window
            ):
                out.append(Correlation(
                    session_a=sid_a, session_b=sid_b,
                    entry_a=e_a, entry_b=e_b,
                    delta_ms=delta_ms, reason="read_then_edit",
                ))
    return out


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

_REASON_STYLE = {
    "same_path": "yellow",
    "same_cmd": "yellow",
    "read_then_edit": "bold red",
}


def _short_sid(sid: str) -> str:
    return sid[:8] if len(sid) > 8 else sid


def render_session_column(
    sid: str,
    entries: list[AuditEntry],
    *,
    correlated_keys: set[str] | None = None,
    max_rows: int = 30,
) -> Panel:
    """Renderiza coluna pra uma sessao na vista de comparacao.

    ``correlated_keys`` e' um set de tool_use_ids cujas linhas devem
    ser destacadas (entrada esta em alguma Correlation).
    """
    correlated_keys = correlated_keys or set()
    table = Table(
        show_header=True, header_style="bold cyan",
        border_style="dim", expand=True, padding=(0, 1),
    )
    table.add_column("Hora", width=12)
    table.add_column("Tool", overflow="fold")
    table.add_column("dur", justify="right", width=8)

    visible = entries[-max_rows:]
    for e in visible:
        ts = e.timestamp.strftime("%H:%M:%S.%f")[:-3]
        tool_str = e.tool
        if e.subagent_type:
            tool_str = f"{e.tool}({e.subagent_type})"
        is_correlated = e.tool_use_id in correlated_keys
        row_style = "yellow" if is_correlated else ""
        dur = "running..." if e.event == "start" else str(e.duration_ms)
        table.add_row(
            Text(ts, style=row_style or "cyan"),
            Text(tool_str, style=row_style),
            Text(dur, style=row_style, justify="right"),
        )
    title = f"{_short_sid(sid)}  ({len(entries)} entries)"
    return Panel(table, title=title, title_align="left", border_style="dim")


def render_comparison(
    grouped: dict[str, list[AuditEntry]],
    correlations: list[Correlation],
    *,
    max_rows_per_column: int = 30,
) -> Columns:
    """Layout em colunas paralelas, uma por sessao."""
    correlated_tids = set()
    for c in correlations:
        if c.entry_a.tool_use_id:
            correlated_tids.add(c.entry_a.tool_use_id)
        if c.entry_b.tool_use_id:
            correlated_tids.add(c.entry_b.tool_use_id)

    panels = [
        render_session_column(
            sid, entries,
            correlated_keys=correlated_tids,
            max_rows=max_rows_per_column,
        )
        for sid, entries in grouped.items()
    ]
    return Columns(panels, expand=True, equal=True)


def render_correlations_summary(correlations: list[Correlation]) -> Panel:
    """Painel-resumo das correlacoes detectadas."""
    if not correlations:
        return Panel(
            Text("Nenhuma correlacao detectada nas sessoes selecionadas.",
                 style="dim"),
            title="Correlacoes",
            title_align="left",
            border_style="dim",
        )
    table = Table(
        show_header=True, header_style="bold cyan",
        border_style="dim", expand=True, padding=(0, 1),
    )
    table.add_column("Sessao A", width=10)
    table.add_column("Tool A", width=14)
    table.add_column("Δ (ms)", justify="right", width=8)
    table.add_column("Sessao B", width=10)
    table.add_column("Tool B", width=14)
    table.add_column("Tipo", overflow="fold")

    for c in correlations:
        style = _REASON_STYLE.get(c.reason, "")
        table.add_row(
            Text(_short_sid(c.session_a), style=style),
            Text(c.entry_a.tool, style=style),
            Text(str(c.delta_ms), style=style, justify="right"),
            Text(_short_sid(c.session_b), style=style),
            Text(c.entry_b.tool, style=style),
            Text(c.reason, style=style),
        )
    return Panel(
        table,
        title=f"Correlacoes ({len(correlations)})",
        title_align="left",
        border_style="yellow",
    )


def parse_compare_input(raw: str) -> list[str]:
    """Extrai SIDs do prompt de comparacao.

    Aceita separadores comuns (espaco, virgula, ponto-virgula). Retorna
    lista de prefixos em ordem; deduplica preservando primeira ocorrencia.
    """
    import re
    tokens = [t for t in re.split(r"[\s,;]+", raw.strip()) if t]
    seen: set[str] = set()
    out: list[str] = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out
