"""Monta SessionStats a partir de transcripts, com cache incremental."""
from __future__ import annotations

from pathlib import Path

from claude_dash import cache
from claude_dash.discover import (
    LiveSession,
    TranscriptRef,
    discover_live_sessions,
    discover_transcripts,
)
from claude_dash.models import SessionStats, Usage
from claude_dash.parser import (
    extract_model,
    extract_timestamp_ms,
    extract_usage,
    iter_entries,
    iter_tool_uses,
)
from claude_dash.pricing import normalize_model


def _apply_entry(entry: dict, stats: SessionStats) -> None:
    """Atualiza `stats` in-place com uma entrada do transcript."""
    etype = entry.get("type")
    if etype == "user":
        stats.messages_user += 1
    elif etype == "assistant":
        stats.messages_assistant += 1

    u = extract_usage(entry)
    if u is not None:
        # Chave canônica (sem sufixos [1m]/-YYYYMMDD): garante que
        # display, cost e agregação usem o mesmo identificador.
        raw = extract_model(entry) or "unknown"
        model = normalize_model(raw) if raw != "unknown" else raw
        stats.usage_by_model.setdefault(model, Usage())
        stats.usage_by_model[model] += u
        # last_usage = snapshot do turno mais recente (não acumulado);
        # útil para inferir o tamanho atual do contexto ativo.
        stats.last_usage = u

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
    skip_parse = False

    # Inode atual + tamanho: identidade forte do arquivo para detectar
    # truncate+rewrite (inode muda) e truncamento puro (size < offset)
    try:
        st = ref.path.stat()
        file_inode = st.st_ino
        file_size = st.st_size
    except OSError:
        file_inode = 0
        file_size = 0

    if use_cache:
        cached = cache.load(ref.session_id)
        if cached is not None:
            cached_inode = int(cached.get("inode") or 0)
            cached_mtime = int(cached.get("mtime_ms") or 0)
            cached_offset = int(cached.get("byte_offset") or 0)

            # Validações em ordem de força:
            # 1. inode diferente → arquivo recriado; invalida tudo
            # 2. inode igual + mtime igual → nada mudou; reaproveita
            #    direto e pula iter_entries
            # 3. inode igual + mtime mudou + file_size >= offset →
            #    append-only; continua do offset
            # 4. file_size < offset → truncamento parcial; invalida
            inode_matches = cached_inode and cached_inode == file_inode
            if inode_matches and cached_mtime == ref.mtime_ms:
                stats = cache.deserialize_stats(cached["stats"])
                start_offset = cached_offset
                skip_parse = True
            elif inode_matches and file_size >= cached_offset > 0:
                stats = cache.deserialize_stats(cached["stats"])
                start_offset = cached_offset
            # else: arquivo mudou de forma não-append-only; reparseia

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

    # Parsing incremental dos bytes novos — pulado no cache hit exato
    last_offset = start_offset
    if not skip_parse:
        for offset, entry in iter_entries(ref.path, start_offset=start_offset):
            _apply_entry(entry, stats)
            last_offset = offset

    # mtime do arquivo como fallback para last_activity
    if not stats.last_activity_ms:
        stats.last_activity_ms = ref.mtime_ms

    # Atualiza cache (sempre; barato). Inclui inode para identidade.
    if use_cache:
        cache.save(ref.session_id, ref.mtime_ms, last_offset, stats, inode=file_inode)

    return stats


def aggregate_transcript_since(
    ref: TranscriptRef,
    since_ms: int,
    live: LiveSession | None = None,
) -> SessionStats:
    """Agrega um transcript contando APENAS entries com timestamp >= since_ms.

    Bypassa o cache porque este sempre guarda totais acumulados desde
    o início da sessão. Para janelas de tempo (ex: "hoje"), precisamos
    re-parsear do zero filtrando por `timestamp`. Entradas sem campo
    `timestamp` (ex: `system`, `attachment`) não são contadas — isso é
    intencional: usage/tools só vêm de assistant/user, que têm
    timestamp.
    """
    stats = SessionStats(
        session_id=ref.session_id,
        cwd=live.cwd if live else Path(),
        started_at_ms=live.started_at_ms if live else 0,
        transcript_path=ref.path,
    )

    if live is not None:
        stats.pid = live.pid
        stats.alive = live.alive
        stats.cwd = live.cwd
        stats.version = live.version
        if not stats.started_at_ms:
            stats.started_at_ms = live.started_at_ms

    for _offset, entry in iter_entries(ref.path):
        ts = extract_timestamp_ms(entry)
        # Descarta entries sem timestamp OU antes da janela
        if ts is None or ts < since_ms:
            continue
        _apply_entry(entry, stats)

    if not stats.last_activity_ms:
        stats.last_activity_ms = ref.mtime_ms

    return stats


def collect_sessions_since(since_ms: int) -> list[SessionStats]:
    """Coleta SessionStats de sessões com atividade desde `since_ms`.

    Diferente de `collect_live_sessions`:
    - Inclui sessões mortas (desde que o transcript tenha atividade no
      período)
    - Totais são restritos ao período (exact), não cumulativos
    - Ordenação por última atividade (mais recente primeiro)

    Subagentes são agregados apenas se também tiveram atividade no
    período.
    """
    live = discover_live_sessions()
    all_transcripts = discover_transcripts()
    live_by_sid = {ls.session_id: ls for ls in live}

    main_candidates = [
        t for t in all_transcripts
        if not t.is_subagent and t.mtime_ms >= since_ms
    ]

    subs_by_parent: dict[str, list[TranscriptRef]] = {}
    for t in all_transcripts:
        if t.is_subagent and t.parent_session_id:
            subs_by_parent.setdefault(t.parent_session_id, []).append(t)

    out: list[SessionStats] = []
    for ref in main_candidates:
        ls = live_by_sid.get(ref.session_id)
        stats = aggregate_transcript_since(ref, since_ms, live=ls)

        # Pre-filtra por mtime (barato) para evitar abrir arquivos
        # claramente fora da janela — mas só conta como subagente "ativo"
        # aquele que realmente contribuiu no período (messages ou usage),
        # para não inflar stats.subagents em casos onde mtime foi mexido
        # sem appends novos (touch, fs repair, etc).
        subs_candidates = [
            s for s in subs_by_parent.get(ref.session_id, [])
            if s.mtime_ms >= since_ms
        ]
        active_subagents = 0
        for sub_ref in subs_candidates:
            sub_stats = aggregate_transcript_since(sub_ref, since_ms)
            if sub_stats.messages_assistant == 0 and sub_stats.total_usage.total == 0:
                continue
            active_subagents += 1
            for model, usage in sub_stats.usage_by_model.items():
                stats.usage_by_model.setdefault(model, Usage())
                stats.usage_by_model[model] += usage
            for tool_name, count in sub_stats.tools.items():
                stats.tools[tool_name] = stats.tools.get(tool_name, 0) + count
        stats.subagents = active_subagents

        # Só retorna sessões com atividade real na janela
        if stats.total_usage.total > 0 or stats.tools or stats.messages_assistant:
            out.append(stats)

    out.sort(key=lambda s: s.last_activity_ms, reverse=True)
    return out


def collect_live_sessions(use_cache: bool = True) -> list[SessionStats]:
    """Coleta SessionStats de todas as sessões atualmente vivas.

    Inclui o consumo de subagentes disparados por cada sessão.
    Faz uma única varredura do filesystem e reutiliza a lista para
    extrair tanto transcripts principais quanto subagentes.
    """
    live = discover_live_sessions()
    all_transcripts = discover_transcripts()
    by_sid: dict[str, TranscriptRef] = {}
    subs_by_parent: dict[str, list[TranscriptRef]] = {}
    for t in all_transcripts:
        if t.is_subagent and t.parent_session_id:
            subs_by_parent.setdefault(t.parent_session_id, []).append(t)
        else:
            by_sid[t.session_id] = t

    out: list[SessionStats] = []
    for ls in live:
        ref = by_sid.get(ls.session_id)
        if ref is None:
            # Sessão viva sem transcript (muito recente?); skippa
            continue
        stats = build_stats_for_transcript(ref, live=ls, use_cache=use_cache)

        # Agrega subagentes reusando a lista já obtida acima — evita
        # re-scan O(N) de projects/ por sessão viva
        subs = subs_by_parent.get(ls.session_id, [])
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
