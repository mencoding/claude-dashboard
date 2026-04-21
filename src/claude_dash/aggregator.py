"""Monta SessionStats a partir de transcripts, com cache incremental."""
from __future__ import annotations

from pathlib import Path

from claude_dash import cache
from claude_dash.discover import (
    LiveSession,
    TranscriptRef,
    discover_live_sessions,
    discover_transcripts,
    subagents_of,
)
from claude_dash.models import SessionStats, Usage
from claude_dash.parser import (
    extract_model,
    extract_timestamp_ms,
    extract_usage,
    iter_entries,
    iter_tool_uses,
)


def _apply_entry(entry: dict, stats: SessionStats) -> None:
    """Atualiza `stats` in-place com uma entrada do transcript."""
    etype = entry.get("type")
    if etype == "user":
        stats.messages_user += 1
    elif etype == "assistant":
        stats.messages_assistant += 1

    u = extract_usage(entry)
    if u is not None:
        model = extract_model(entry) or "unknown"
        stats.usage_by_model.setdefault(model, Usage())
        stats.usage_by_model[model] += u

    for tool_name in iter_tool_uses(entry):
        stats.tools[tool_name] = stats.tools.get(tool_name, 0) + 1

    ts = extract_timestamp_ms(entry)
    if ts is not None and ts > stats.last_activity_ms:
        stats.last_activity_ms = ts


def build_stats_for_transcript(
    ref: TranscriptRef,
    live: LiveSession | None = None,
    use_cache: bool = True,
) -> SessionStats:
    """Agrega estatísticas de um transcript, aproveitando cache quando possível."""
    stats: SessionStats | None = None
    start_offset = 0

    if use_cache:
        cached = cache.load(ref.session_id)
        if cached is not None:
            cached_mtime = int(cached.get("mtime_ms") or 0)
            cached_offset = int(cached.get("byte_offset") or 0)
            file_size = ref.path.stat().st_size
            # Validações:
            # 1. mtime bate perfeitamente → nada mudou; reaproveita direto
            # 2. mtime mudou mas file_size >= cached_offset → append-only;
            #    continua do offset
            # 3. file_size < cached_offset → arquivo truncou, invalida
            if cached_mtime == ref.mtime_ms:
                stats = cache.deserialize_stats(cached["stats"])
                start_offset = cached_offset
            elif file_size >= cached_offset > 0:
                stats = cache.deserialize_stats(cached["stats"])
                start_offset = cached_offset
            # else: invalida tudo, reparseia do zero

    if stats is None:
        stats = SessionStats(
            session_id=ref.session_id,
            cwd=live.cwd if live else Path(),
            started_at_ms=live.started_at_ms if live else 0,
            transcript_path=ref.path,
        )

    # Enriquece com info viva (sempre refresca, não depende de cache)
    if live is not None:
        stats.pid = live.pid
        stats.alive = live.alive
        stats.cwd = live.cwd
        stats.version = live.version
        if not stats.started_at_ms:
            stats.started_at_ms = live.started_at_ms

    # Parsing incremental dos bytes novos
    last_offset = start_offset
    for offset, entry in iter_entries(ref.path, start_offset=start_offset):
        _apply_entry(entry, stats)
        last_offset = offset

    # mtime do arquivo como fallback para last_activity
    if not stats.last_activity_ms:
        stats.last_activity_ms = ref.mtime_ms

    # Atualiza cache (sempre; barato)
    if use_cache:
        cache.save(ref.session_id, ref.mtime_ms, last_offset, stats)

    return stats


def collect_live_sessions(use_cache: bool = True) -> list[SessionStats]:
    """Coleta SessionStats de todas as sessões atualmente vivas.

    Inclui o consumo de subagentes disparados por cada sessão.
    """
    live = discover_live_sessions()
    all_transcripts = discover_transcripts()
    by_sid = {t.session_id: t for t in all_transcripts if not t.is_subagent}

    out: list[SessionStats] = []
    for ls in live:
        ref = by_sid.get(ls.session_id)
        if ref is None:
            # Sessão viva sem transcript (muito recente?); skippa
            continue
        stats = build_stats_for_transcript(ref, live=ls, use_cache=use_cache)

        # Agrega subagentes
        subs = subagents_of(ls.session_id)
        stats.subagents = len(subs)
        for sub_ref in subs:
            sub_stats = build_stats_for_transcript(sub_ref, use_cache=use_cache)
            for model, usage in sub_stats.usage_by_model.items():
                stats.usage_by_model.setdefault(model, Usage())
                stats.usage_by_model[model] += usage
            for tool_name, count in sub_stats.tools.items():
                stats.tools[tool_name] = stats.tools.get(tool_name, 0) + count

        out.append(stats)

    # Ordena por started_at_ms (mais antigas primeiro)
    out.sort(key=lambda s: s.started_at_ms)
    return out
