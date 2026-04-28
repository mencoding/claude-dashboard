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
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from claude_dash import __version__
from claude_dash.account import read_account_info
from claude_dash.aggregator import (
    build_stats_for_transcript,
    collect_live_sessions,
    collect_sessions_since,
    collect_tool_usage_since,
    extract_turns,
)
from claude_dash.audit.partial_stats import build_partial_stats_from_audit
from claude_dash.audit.tail import IncrementalTailer
from claude_dash.discover import (
    find_transcript_for_session,
    subagents_of,
)
from claude_dash.models import SessionStats
from claude_dash.pricing import cost_of
from claude_dash.rate_limits import global_worst_case
from claude_dash.rate_limits import read_all as read_rate_limits
from claude_dash.views.audit import correlate_start_end, filter_entries
from claude_dash.views.today import today_start_ms

# Path do audit log (mantem em sync com views/tui.py:AUDIT_LOG_PATH).
AUDIT_LOG_PATH = Path.home() / ".claude" / "iris" / "audit" / "sessions.log"

mcp = FastMCP("claude-dashboard")

# Versao do schema dos retornos das tools MCP. Bump em mudancas
# breaking (remocao/rename de campo, mudanca de tipo). Adicoes de
# campos novos NAO bumpam — clientes nao-estritos ignoram extras.
# Politica completa em docs/mcp-design.md.
SCHEMA_VERSION: int = 1


def _envelope(payload: dict[str, Any]) -> dict[str, Any]:
    """Encapsula payload com metadata top-level (#54-d1).

    Adiciona ``_schema_version`` (versao do contrato MCP, bump so em
    breaking) e ``dashboard_version`` (versao do package, bump em
    qualquer release). Agentes podem usar o primeiro pra detectar
    incompatibilidades e o segundo pra observabilidade/debug.

    Mantem todos os campos do payload original; conflito de chave nao
    deve acontecer (nao usamos ``_schema_version`` ou
    ``dashboard_version`` em payloads internos).
    """
    return {
        "_schema_version": SCHEMA_VERSION,
        "dashboard_version": __version__,
        **payload,
    }


# --- helpers internos ---------------------------------------------------


def _stats_to_dict(s: SessionStats, *, include_tools_top: int = 5) -> dict[str, Any]:
    """Serializa SessionStats para estrutura JSON-friendly."""
    cost = sum(cost_of(m, u) for m, u in s.usage_by_model.items())
    total = s.total_usage
    top_tools = sorted(s.tools.items(), key=lambda kv: -kv[1])[:include_tools_top]
    return {
        "session_id": s.session_id,
        "session_name": s.session_name,
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

    def _label(s: SessionStats) -> str:
        # Prefere nome humano (/rename) ao prefixo do UUID para alertas.
        # Mantém o prefixo entre parênteses para desambiguação se houver
        # várias sessões com o mesmo nome.
        if s.session_name:
            return f"'{s.session_name}' ({s.session_id[:8]}…)"
        return f"{s.session_id[:8]}…"

    for s in live_stats:
        # Output médio por turno muito alto — proxy de "Opus gerando respostas
        # densas" (ex: arquivos longos, loops, output repetitivo). O threshold
        # é em tokens de OUTPUT por turno (não custo direto).
        if s.tokens_per_turn > 300_000 and s.messages_assistant > 3:
            alerts.append(
                f"Sessão {_label(s)} com média {int(s.tokens_per_turn):,} "
                f"output tokens/turno (esperado < 300k): investigar se está "
                f"gerando arquivos longos ou rodando em loop."
            )

        # Sessão parece silenciada mas viva — possível travamento
        if s.last_activity_ms and (now_ms - s.last_activity_ms) > 30 * 60 * 1000:
            minutes = (now_ms - s.last_activity_ms) // 60_000
            alerts.append(
                f"Sessão {_label(s)} (pid {s.pid}) viva mas sem "
                f"atividade há {minutes} min — pode estar travada, aguardando "
                f"input do usuário, ou idle."
            )

        # Contexto próximo do limite
        ctx = s.active_context_tokens
        if ctx > 150_000:
            alerts.append(
                f"Sessão {_label(s)} usando {ctx:,} tokens de contexto ativo "
                f"— próximo do limite 200k da janela padrão; considerar /compact."
            )

    # Custo agregado do dia — sinal de sessão maratônica. Só relevante
    # em billing pay-as-you-go; em assinatura flat-rate o custo em USD
    # é hipotético (ver `account.is_flat_rate`).
    acc = read_account_info()
    if acc is None or not acc.is_flat_rate:
        total_cost_today = sum(
            sum(cost_of(m, u) for m, u in s.usage_by_model.items())
            for s in today_stats
        )
        if total_cost_today > 200:
            alerts.append(
                f"Consumo agregado do dia: ${total_cost_today:.2f}. Está acima do "
                f"típico — revise se alguma sessão está em loop."
            )

    # Rate limits — só gera alerta se o hook opcional estiver instalado
    worst_rl = global_worst_case()
    if worst_rl is not None:
        if worst_rl.five_hour_pct >= 85:
            delta = max(0, worst_rl.five_hour_resets_in_seconds // 60)
            alerts.append(
                f"Rate limit 5h em {worst_rl.five_hour_pct:.0f}% — reset em "
                f"~{delta} min. Sessões pesadas próximas do throttle."
            )
        if worst_rl.seven_day_pct >= 85:
            days = max(0, worst_rl.seven_day_resets_in_seconds // 86400)
            alerts.append(
                f"Rate limit 7d em {worst_rl.seven_day_pct:.0f}% — reset em "
                f"~{days} dia(s). Consumo semanal perto do limite do plano."
            )

    return alerts


# --- ferramentas expostas ------------------------------------------------


@mcp.tool()
def account_info() -> dict[str, Any]:
    """Info da conta/assinatura do usuário no Claude Code.

    Retorna email, organização, billing type ("stripe_subscription"
    para planos Max/Pro flat-rate; "api" para pay-as-you-go),
    flag `is_flat_rate` (útil para agentes decidirem se custo em
    USD faz sentido), status de extra usage (pay-as-you-go adicional).

    Lida de ~/.claude.json (campos oauthAccount, cachedExtraUsageDisabledReason).
    Retorna `{"error": ...}` se não houver arquivo ou parse falhar.
    """
    acc = read_account_info()
    if acc is None:
        return _envelope({"error": "~/.claude.json ausente ou sem oauthAccount"})
    return _envelope({
        "email": acc.email,
        "display_name": acc.display_name,
        "organization_name": acc.organization_name,
        "organization_role": acc.organization_role,
        "billing_type": acc.billing_type,
        "billing_label": acc.billing_label,
        "plan_label": acc.plan_label,
        "subscription_type": acc.subscription_type,
        "rate_limit_tier": acc.rate_limit_tier,
        "is_flat_rate": acc.is_flat_rate,
        "has_extra_usage_enabled": acc.has_extra_usage_enabled,
        "extra_usage_disabled_reason": acc.extra_usage_disabled_reason,
        "first_token_date": acc.first_token_date,
        "account_uuid": acc.account_uuid,
        "organization_uuid": acc.organization_uuid,
    })


@mcp.tool()
def rate_limits() -> dict[str, Any]:
    """Consumo atual dos rate limits 5h/7d (se hook opcional instalado).

    Retorna:
    - `installed`: True se há pelo menos 1 snapshot capturado (proxy
      para "usuário instalou o hook `claude-dash-rate-limit-capture`")
    - `global_worst_case`: pior caso entre sessões com snapshots
      **frescos** (capturados nos últimos 15 min). Pode ser `None`
      mesmo com `installed=True` se todos os snapshots estão stale —
      sessões idle/encerradas. Consumidores devem checar explicitamente.
    - `by_session`: dict com snapshot por sessão (inclui age e
      freshness — o campo `is_fresh` distingue ativos de stale)

    Se o hook não estiver instalado, retorna `{"installed": false}`
    — instruções no README para adicionar uma linha no statusline
    do usuário.
    """
    all_snaps = read_rate_limits()
    if not all_snaps:
        return _envelope({
            "installed": False,
            "reason": "nenhum snapshot em /tmp/claude-dash-rate-limits/",
        })

    worst = global_worst_case()
    result: dict[str, Any] = {
        "installed": True,
        "global_worst_case": None,
        "by_session": {},
    }
    if worst is not None:
        result["global_worst_case"] = {
            "five_hour_pct": worst.five_hour_pct,
            "five_hour_resets_in_seconds": worst.five_hour_resets_in_seconds,
            "seven_day_pct": worst.seven_day_pct,
            "seven_day_resets_in_seconds": worst.seven_day_resets_in_seconds,
            "captured_from_session": worst.session_id,
            "age_seconds": worst.age_ms // 1000,
        }
    for sid, snap in all_snaps.items():
        result["by_session"][sid] = {
            "five_hour_pct": snap.five_hour_pct,
            "seven_day_pct": snap.seven_day_pct,
            "age_seconds": snap.age_ms // 1000,
            "is_fresh": snap.is_fresh,
            "model_id": snap.model_id,
        }
    return _envelope(result)


@mcp.tool()
def active_sessions() -> dict[str, Any]:
    """Lista sessões Claude Code atualmente vivas (processos em execução).

    Retorna para cada sessão: pid, sessionId, cwd, idade, tokens (total e
    por tipo), custo estimado em USD, modelo dominante, tools mais usados,
    contagem de subagentes disparados, e `active_context_tokens` (tamanho
    do prompt no último turno — indica quão cheio está o contexto).
    """
    live = collect_live_sessions()
    return _envelope({
        "count": len(live),
        "generated_at": datetime.now().isoformat(),
        "sessions": [_stats_to_dict(s) for s in live],
    })


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
    return _envelope({
        "window_start": datetime.fromtimestamp(since_ms / 1000).isoformat(),
        "generated_at": datetime.now().isoformat(),
        "sessions_count": len(sessions),
        "sessions_alive": alive,
        "sessions_dead": len(sessions) - alive,
        "tokens_total": total_tokens,
        "cost_usd": round(total_cost, 2),
        "sessions": [_stats_to_dict(s, include_tools_top=4) for s in sessions],
    })


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
    return _envelope({
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
    })


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
        return _envelope({
            "error": (
                f"Nenhum transcript encontrado para sid='{sid}'"
                " (prefix ambíguo ou inexistente)"
            )
        })

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

    return _envelope({
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
    })


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
      (4) custo agregado do dia > $200 (só em API billing — ignorado
          em plano flat-rate onde custo USD é hipotético),
      (5) rate limit 5h ≥ 85% (requer hook opcional instalado),
      (6) rate limit 7d ≥ 85% (requer hook opcional instalado).
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

    acc = read_account_info()
    worst_rl = global_worst_case()

    rate_limits_block: dict[str, Any] = {"installed": False}
    if worst_rl is not None:
        rate_limits_block = {
            "installed": True,
            "five_hour_pct": worst_rl.five_hour_pct,
            "seven_day_pct": worst_rl.seven_day_pct,
            "five_hour_resets_in_seconds": worst_rl.five_hour_resets_in_seconds,
            "seven_day_resets_in_seconds": worst_rl.seven_day_resets_in_seconds,
        }

    return _envelope({
        "generated_at": datetime.now().isoformat(),
        "account": {
            "email": acc.email if acc else None,
            "plan": acc.plan_label if acc else None,
            "billing_label": acc.billing_label if acc else None,
            "is_flat_rate": acc.is_flat_rate if acc else None,
        },
        "rate_limits": rate_limits_block,
        "active": {
            "sessions_count": len(live),
            "workspaces": sorted({str(s.cwd) for s in live}),
            "tokens_cumulative": cumulative_tokens_live,
            "cost_usd_cumulative": round(cumulative_cost_live, 2),
            "subagents_dispatched": total_subagents_live,
            "sessions_brief": [
                {
                    "sid_short": s.session_id[:8],
                    "session_name": s.session_name,
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
    })


# --- audit log (#54-d3) -------------------------------------------------


def _audit_entry_to_dict(e: Any) -> dict[str, Any]:
    """Serializa AuditEntry pra dict JSON-friendly."""
    return {
        "timestamp": e.timestamp.isoformat(),
        "hostname": e.hostname,
        "pid": e.pid,
        "session_id": e.session_id,
        "tool": e.tool,
        "tool_use_id": e.tool_use_id,
        "status": e.status,
        "duration_ms": e.duration_ms,
        "perm_mode": e.perm_mode,
        "input_sha": e.input_sha,
        "input_bytes": e.input_bytes,
        "output_bytes": e.output_bytes,
        "subagent_type": e.subagent_type,
        "event": e.event,
    }


@mcp.tool()
def audit_entries(
    session: str | None = None,
    tool: str | None = None,
    status: str | None = None,
    host: str | None = None,
    hours: int = 24,
    limit: int = 500,
) -> dict[str, Any]:
    """Lista entries do audit log com filtros (#54-d3).

    Le ``~/.claude/iris/audit/sessions.log`` (metadata-only, syncavel
    cross-device). NUNCA expoe ``/var/log/claude/tools.log`` (esse contem
    cmd/path/url completos — sensivel).

    Wraps ``views/audit.filter_entries``. Filtros aditivos (uma key por
    tipo) — agente combina via reaplicacao com input refinado.

    Args:
        session: prefix-match no session_id (ex.: "0efd3" casa qualquer
            UUID que comece com isso)
        tool: match exato no nome da tool (ex.: "Bash", "Agent")
        status: "success" | "error" | "running" (este so para entries
            de start, ainda em execucao)
        host: prefix-match no hostname; sem filtro = TODOS os hosts
            (cross-device). Use ``host=<atual>`` pra so este host.
        hours: janela temporal (default 24h, max razoavel ~720h=30d)
        limit: cap de entries retornadas (default 500). Sempre as
            mais recentes da janela.

    Returns (Schema):
        {
            "_schema_version": 1,
            "dashboard_version": "...",
            "filters_applied": {...},
            "total_in_window": int,
            "returned": int,
            "entries": [{
                "timestamp": ISO 8601,
                "hostname": str,
                "session_id": str,
                "tool": str,
                "tool_use_id": str,
                "status": "success"|"error"|"running",
                "duration_ms": int,
                "perm_mode": str,
                "input_sha": str (16-char hex),
                "input_bytes": int,
                "output_bytes": int,
                "subagent_type": str|None,
                "event": "start"|"end",
                "pid": str
            }]
        }
    """
    # Le e parseia o log inteiro via tailer (IncrementalTailer.read_new
    # le tudo na primeira chamada).
    tailer = IncrementalTailer(AUDIT_LOG_PATH)
    try:
        all_entries = tailer.read_new()
    except Exception as e:
        return _envelope({
            "error": f"Falha ao ler audit log: {e}",
            "filters_applied": {},
            "total_in_window": 0,
            "returned": 0,
            "entries": [],
        })

    # Correlaciona start↔end (#50): caller espera ver o end quando ele
    # ja chegou; orphan starts indicam tool ainda em execucao.
    all_entries = correlate_start_end(all_entries)

    filters: dict[str, str] = {}
    if tool:
        filters["tool"] = tool
    if status:
        filters["status"] = status
    if session:
        filters["session_prefix"] = session
    if host:
        filters["host"] = host

    visible = filter_entries(
        all_entries,
        filters=filters,
        window_hours=float(hours),
        show_test_sessions=False,
        current_host=None,  # MCP nao filtra por host atual — agente decide
    )
    total = len(visible)
    # Entries mais recentes primeiro (ordem inversa pra agentes que
    # fazem head do retorno).
    sliced = list(reversed(visible[-limit:]))
    return _envelope({
        "filters_applied": {
            "session": session,
            "tool": tool,
            "status": status,
            "host": host,
            "hours": hours,
            "limit": limit,
        },
        "total_in_window": total,
        "returned": len(sliced),
        "entries": [_audit_entry_to_dict(e) for e in sliced],
    })


@mcp.tool()
def audit_session_partial(sid: str) -> dict[str, Any]:
    """Drill-down parcial via metadata da sessions.log (#54-d3 + #55).

    Util quando:
    - Sessao rodou em outra maquina (transcript JSONL nao sincado)
    - Transcript JSONL local foi rotacionado/limpo

    Wraps ``audit.partial_stats.build_partial_stats_from_audit``. Retorna
    counts agregados (total_calls, error_count, top_tools, duracao) sem
    timeline de turnos ou custo (esses dados so existem no JSONL).

    Args:
        sid: session_id (aceita prefix; primeiro match vence)

    Returns (Schema):
        {
            "_schema_version": 1,
            "dashboard_version": "...",
            "found": bool,
            "session_id": str,         # full session_id (resolvido)
            "hostname": str,           # origem da sessao
            "first_ts": ISO 8601,
            "last_ts": ISO 8601,
            "duration_ms": int,
            "total_calls": int,
            "error_count": int,
            "error_rate": float (0.0-1.0),
            "top_tools": [["Bash", 42], ["Read", 28], ...]  # top 8
        }
        ou:
        {
            "_schema_version": 1,
            "dashboard_version": "...",
            "found": false,
            "error": "..."
        }
    """
    partial = build_partial_stats_from_audit(sid)
    if partial is None:
        return _envelope({
            "found": False,
            "error": (
                f"Nao existem dados na sessions.log local sobre sid='{sid}'. "
                "Pode ser sid invalido ou sessao em outra maquina sem sync."
            ),
        })
    return _envelope({
        "found": True,
        "session_id": partial.session_id,
        "hostname": partial.hostname,
        "first_ts": partial.first_ts.isoformat() if partial.first_ts else None,
        "last_ts": partial.last_ts.isoformat() if partial.last_ts else None,
        "duration_ms": partial.duration_ms,
        "total_calls": partial.total_calls,
        "error_count": partial.error_count,
        "error_rate": round(partial.error_rate, 4),
        "top_tools": [list(t) for t in partial.top_tools],
    })


# --- health (#54-d4) ----------------------------------------------------


def _check_rsyslog_active() -> bool | None:
    """Checa se rsyslog esta ativo via systemctl. None se systemctl ausente."""
    import shutil
    import subprocess
    if shutil.which("systemctl") is None:
        return None
    try:
        r = subprocess.run(
            ["systemctl", "is-active", "rsyslog"],
            capture_output=True, text=True, timeout=2, check=False,
        )
        return r.stdout.strip() == "active"
    except Exception:
        return None


def _audit_log_last_entry_age_seconds() -> int | None:
    """Idade da ultima entry da sessions.log em segundos. None se ausente.

    Le mtime do arquivo — proxy barato. Nao parseia.
    """
    if not AUDIT_LOG_PATH.is_file():
        return None
    try:
        mtime = AUDIT_LOG_PATH.stat().st_mtime
        return max(0, int(datetime.now().timestamp() - mtime))
    except OSError:
        return None


def _audit_hook_wired() -> bool:
    """True se settings.json tem hook PostToolUse apontando pro entry novo."""
    settings_path = Path.home() / ".claude" / "settings.json"
    if not settings_path.is_file():
        return False
    try:
        import json
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    pt = ((settings.get("hooks") or {}).get("PostToolUse")) or []
    for entry in pt:
        for h in entry.get("hooks", []) or []:
            if "claude-dash-audit-hook" in (h.get("command", "") or ""):
                return True
    return False


@mcp.tool()
def dashboard_health() -> dict[str, Any]:
    """Health check da pipeline do dashboard (#54-d4).

    Util pra agente diagnosticar "minha audit pipeline esta saudavel?"
    em uma chamada — em vez de combinar account_info + audit_entries +
    inferencia.

    Returns (Schema):
        {
            "_schema_version": 1,
            "dashboard_version": "...",
            "rsyslog_active": bool|None,
                # None = systemctl nao disponivel (container? non-systemd)
            "audit_hook_wired": bool,
                # True se settings.json tem PostToolUse -> claude-dash-audit-hook
            "audit_log_exists": bool,
            "audit_log_last_entry_age_seconds": int|None,
                # idade do mtime da sessions.log; None se ausente
            "audit_log_path": str,
            "issues": [str, ...],
                # lista humano-readable do que esta errado; vazia = tudo OK
        }
    """
    rsyslog_active = _check_rsyslog_active()
    hook_wired = _audit_hook_wired()
    log_exists = AUDIT_LOG_PATH.is_file()
    last_age = _audit_log_last_entry_age_seconds()

    issues: list[str] = []
    if rsyslog_active is False:
        issues.append("rsyslog inativo — entries em /var/log/claude/ nao serao escritas.")
    if not hook_wired:
        issues.append(
            "Hook PostToolUse nao wired em ~/.claude/settings.json — "
            "rode `claude-dash setup-audit`."
        )
    if not log_exists:
        issues.append(
            f"sessions.log nao existe em {AUDIT_LOG_PATH} — "
            "rode `claude-dash setup-audit` ou disparre uma tool call pra criar."
        )
    elif last_age is not None and last_age > 24 * 60 * 60:
        issues.append(
            f"sessions.log sem updates ha {last_age // 3600}h — "
            "hook pode estar quebrado ou sem atividade."
        )

    return _envelope({
        "rsyslog_active": rsyslog_active,
        "audit_hook_wired": hook_wired,
        "audit_log_exists": log_exists,
        "audit_log_last_entry_age_seconds": last_age,
        "audit_log_path": str(AUDIT_LOG_PATH),
        "issues": issues,
    })


# --- entrypoint ----------------------------------------------------------


def main() -> None:
    """Entrypoint console. Inicia o servidor MCP via stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
