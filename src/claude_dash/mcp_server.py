"""MCP server expondo o estado do Claude Code como ferramentas consultáveis.

Complementa o modo TUI interativo oferecendo um canal programático
que agentes MCP podem usar para raciocinar sobre o fluxo de trabalho
do usuário (sessões ativas, consumo do dia, alertas, etc.).

Executável via `claude-dash-mcp`. Para conectar no Claude Code,
adicionar ao `~/.claude/settings.json`:

    "mcpServers": {
        "claude-dashboard": {
            "command": "claude-dash-mcp"
        }
    }
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from mcp.server.fastmcp import FastMCP

from claude_dash.aggregator import (
    build_stats_for_transcript,
    collect_live_sessions,
    collect_sessions_since,
    collect_tool_usage_since,
    extract_turns,
)
from claude_dash.discover import (
    find_transcript_for_session,
    subagents_of,
)
from claude_dash.models import SessionStats
from claude_dash.pricing import cost_of
from claude_dash.views.today import today_start_ms


mcp = FastMCP("claude-dashboard")


# --- helpers internos ---------------------------------------------------


def _stats_to_dict(s: SessionStats, *, include_tools_top: int = 5) -> dict[str, Any]:
    """Serializa SessionStats para estrutura JSON-friendly."""
    cost = sum(cost_of(m, u) for m, u in s.usage_by_model.items())
    total = s.total_usage
    top_tools = sorted(s.tools.items(), key=lambda kv: -kv[1])[:include_tools_top]
    return {
        "session_id": s.session_id,
        "pid": s.pid,
        "alive": s.alive,
        "cwd": str(s.cwd),
        "started_at_ms": s.started_at_ms,
        "last_activity_ms": s.last_activity_ms,
        "version": s.version,
        "dominant_model": s.dominant_model,
        "tokens": {
            "total": total.total,
            "input": total.input_tokens,
            "output": total.output_tokens,
            "cache_read": total.cache_read,
            "cache_write_1h": total.cache_creation_1h,
            "cache_write_5m": total.cache_creation_5m,
        },
        "active_context_tokens": s.active_context_tokens,
        "tokens_per_turn": round(s.tokens_per_turn, 1),
        "cost_usd": round(cost, 4),
        "messages": {
            "user": s.messages_user,
            "assistant": s.messages_assistant,
        },
        "subagents_count": s.subagents,
        "tools_top": [{"name": n, "count": c} for n, c in top_tools],
    }


def _infer_alerts(
    live_stats: list[SessionStats], today_stats: list[SessionStats]
) -> list[str]:
    """Regras simples que transformam dados em "atenção-vale-a-pena".

    Cada alerta é uma frase auto-contida. A ideia é que um agente MCP
    consultando este servidor saiba o que *investigar*, não só o que
    *está acontecendo*.
    """
    now_ms = int(datetime.now().timestamp() * 1000)
    alerts: list[str] = []

    for s in live_stats:
        # Output médio por turno muito alto — proxy de "Opus gerando respostas
        # densas" (ex: arquivos longos, loops, output repetitivo). O threshold
        # é em tokens de OUTPUT por turno (não custo direto).
        if s.tokens_per_turn > 300_000 and s.messages_assistant > 3:
            alerts.append(
                f"Sessão {s.session_id[:8]}… com média {int(s.tokens_per_turn):,} "
                f"output tokens/turno (esperado < 300k): investigar se está "
                f"gerando arquivos longos ou rodando em loop."
            )

        # Sessão parece silenciada mas viva — possível travamento
        if s.last_activity_ms and (now_ms - s.last_activity_ms) > 30 * 60 * 1000:
            minutes = (now_ms - s.last_activity_ms) // 60_000
            alerts.append(
                f"Sessão {s.session_id[:8]}… (pid {s.pid}) viva mas sem "
                f"atividade há {minutes} min — pode estar travada, aguardando "
                f"input do usuário, ou idle."
            )

        # Contexto próximo do limite
        ctx = s.active_context_tokens
        if ctx > 150_000:
            alerts.append(
                f"Sessão {s.session_id[:8]}… usando {ctx:,} tokens de contexto ativo "
                f"— próximo do limite 200k da janela padrão; considerar /compact."
            )

    # Custo agregado do dia — sinal de sessão maratônica
    total_cost_today = sum(
        sum(cost_of(m, u) for m, u in s.usage_by_model.items())
        for s in today_stats
    )
    if total_cost_today > 200:
        alerts.append(
            f"Consumo agregado do dia: ${total_cost_today:.2f}. Está acima do "
            f"típico — revise se alguma sessão está em loop."
        )

    return alerts


# --- ferramentas expostas ------------------------------------------------


@mcp.tool()
def active_sessions() -> dict[str, Any]:
    """Lista sessões Claude Code atualmente vivas (processos em execução).

    Retorna para cada sessão: pid, sessionId, cwd, idade, tokens (total e
    por tipo), custo estimado em USD, modelo dominante, tools mais usados,
    contagem de subagentes disparados, e `active_context_tokens` (tamanho
    do prompt no último turno — indica quão cheio está o contexto).
    """
    live = collect_live_sessions()
    return {
        "count": len(live),
        "generated_at": datetime.now().isoformat(),
        "sessions": [_stats_to_dict(s) for s in live],
    }


@mcp.tool()
def today_summary() -> dict[str, Any]:
    """Agregado do dia corrente (desde 00:00 local) por sessão.

    Inclui sessões mortas se tiveram atividade hoje. Totais são
    **exatos** à janela — entradas pré-00:00 são descartadas mesmo
    se a sessão começou ontem e continuou hoje.
    """
    since_ms = today_start_ms()
    sessions = collect_sessions_since(since_ms)
    total_tokens = sum(s.total_usage.total for s in sessions)
    total_cost = sum(
        sum(cost_of(m, u) for m, u in s.usage_by_model.items()) for s in sessions
    )
    alive = sum(1 for s in sessions if s.alive)
    return {
        "window_start": datetime.fromtimestamp(since_ms / 1000).isoformat(),
        "generated_at": datetime.now().isoformat(),
        "sessions_count": len(sessions),
        "sessions_alive": alive,
        "sessions_dead": len(sessions) - alive,
        "tokens_total": total_tokens,
        "cost_usd": round(total_cost, 2),
        "sessions": [_stats_to_dict(s, include_tools_top=4) for s in sessions],
    }


@mcp.tool()
def tools_breakdown(hours: int = 24) -> dict[str, Any]:
    """Breakdown de tool_use nas últimas N horas (default 24).

    Retorna lista de tools ordenada por uso, com contagem, porcentagem
    do total, número de sessões distintas que usaram o tool e hora do
    dia com pico de chamadas (histograma 24h).
    """
    since_ms = int((datetime.now() - timedelta(hours=hours)).timestamp() * 1000)
    tools = collect_tool_usage_since(since_ms)
    total = sum(t.total_count for t in tools.values())
    ordered = sorted(tools.values(), key=lambda t: -t.total_count)
    return {
        "window_hours": hours,
        "window_start": datetime.fromtimestamp(since_ms / 1000).isoformat(),
        "generated_at": datetime.now().isoformat(),
        "total_calls": total,
        "tools": [
            {
                "name": t.name,
                "count": t.total_count,
                "pct_of_total": round(100 * t.total_count / total, 2) if total else 0,
                "session_count": t.session_count,
                "peak_hour": t.peak_hour,
                "hours_histogram": t.hours_histogram,
            }
            for t in ordered
        ],
    }


@mcp.tool()
def session_details(sid: str) -> dict[str, Any]:
    """Drill-down de uma sessão específica (aceita prefixo de sessionId).

    Retorna o mesmo que `active_sessions` para essa sessão, mais a
    timeline dos últimos 20 turnos com custo estimado por turno e
    tools disparados em cada turno, e resumo dos subagentes com custo
    individual.
    """
    ref = find_transcript_for_session(sid)
    if ref is None:
        return {"error": f"Nenhum transcript encontrado para sid='{sid}' (prefix ambíguo ou inexistente)"}

    stats = build_stats_for_transcript(ref)
    stats.subagents = len(subagents_of(ref.session_id))
    turns = extract_turns(ref)
    subs = subagents_of(ref.session_id)

    subagents_info = []
    for sub in subs:
        try:
            sub_stats = build_stats_for_transcript(sub)
        except OSError:
            continue
        sub_cost = sum(cost_of(m, u) for m, u in sub_stats.usage_by_model.items())
        subagents_info.append({
            "session_id": sub.session_id,
            "tokens": sub_stats.total_usage.total,
            "cost_usd": round(sub_cost, 4),
            "messages_assistant": sub_stats.messages_assistant,
        })

    return {
        "session": _stats_to_dict(stats, include_tools_top=10),
        "timeline_tail": [
            {
                "index": t.index + 1,
                "timestamp_ms": t.timestamp_ms,
                "model": t.model,
                "tokens_total": t.usage.total,
                "input_tokens": t.usage.input_tokens,
                "output_tokens": t.usage.output_tokens,
                "cache_read": t.usage.cache_read,
                "cost_usd": round(cost_of(t.model, t.usage), 4),
                "tools_called": t.tools_called,
            }
            for t in turns[-20:]
        ],
        "turns_total": len(turns),
        "subagents": subagents_info,
    }


@mcp.tool()
def workflow_snapshot() -> dict[str, Any]:
    """Síntese do estado de fluxo de trabalho do usuário.

    **Canal principal** — consolida todas as fontes num só blob pensado
    para consumo por agentes que precisam raciocinar sobre "como está
    o trabalho do usuário agora". Inclui:
    - Overview de sessões ativas (contagem, custo, modelo)
    - Números do dia (tokens, custo, sessões tocadas)
    - Top tools das últimas 24h + hora de pico
    - **Alertas**: frases acionáveis sobre sessões/atividade que podem
      precisar atenção. Categorias geradas (ver `_infer_alerts`):
      (1) output/turno alto em sessão viva,
      (2) sessão viva sem atividade há > 30 min,
      (3) contexto ativo > 150k tokens (perto do limite 200k),
      (4) custo agregado do dia > \$200.
    """
    live = collect_live_sessions()
    today = collect_sessions_since(today_start_ms())
    tools_24h = collect_tool_usage_since(
        int((datetime.now() - timedelta(hours=24)).timestamp() * 1000)
    )

    cumulative_tokens_live = sum(s.total_usage.total for s in live)
    cumulative_cost_live = sum(
        sum(cost_of(m, u) for m, u in s.usage_by_model.items()) for s in live
    )
    total_subagents_live = sum(s.subagents for s in live)

    today_cost = sum(
        sum(cost_of(m, u) for m, u in s.usage_by_model.items()) for s in today
    )
    today_tokens = sum(s.total_usage.total for s in today)

    # Top 5 tools das últimas 24h
    ordered_tools = sorted(tools_24h.values(), key=lambda t: -t.total_count)[:5]

    # Distribuição de atividade por hora (24h agregadas)
    hourly = [0] * 24
    for t in tools_24h.values():
        for h, count in enumerate(t.hours_histogram):
            hourly[h] += count
    peak_hour = hourly.index(max(hourly)) if any(hourly) else None

    return {
        "generated_at": datetime.now().isoformat(),
        "active": {
            "sessions_count": len(live),
            "workspaces": sorted({str(s.cwd) for s in live}),
            "tokens_cumulative": cumulative_tokens_live,
            "cost_usd_cumulative": round(cumulative_cost_live, 2),
            "subagents_dispatched": total_subagents_live,
            "sessions_brief": [
                {
                    "sid_short": s.session_id[:8],
                    "pid": s.pid,
                    "model": s.dominant_model,
                    "cost_usd": round(sum(cost_of(m, u) for m, u in s.usage_by_model.items()), 2),
                    "active_context_tokens": s.active_context_tokens,
                }
                for s in live
            ],
        },
        "today": {
            "sessions_with_activity": len(today),
            "tokens": today_tokens,
            "cost_usd": round(today_cost, 2),
        },
        "tools_24h": {
            "total_calls": sum(t.total_count for t in tools_24h.values()),
            "peak_hour": peak_hour,
            "top": [
                {"name": t.name, "count": t.total_count, "session_count": t.session_count}
                for t in ordered_tools
            ],
        },
        "alerts": _infer_alerts(live, today),
    }


# --- entrypoint ----------------------------------------------------------


def main() -> None:
    """Entrypoint console. Inicia o servidor MCP via stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
