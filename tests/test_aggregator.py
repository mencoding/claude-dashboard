"""Testes do aggregator com filesystem sintético."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from claude_dash.aggregator import build_stats_for_transcript
from claude_dash.discover import TranscriptRef


def _make_transcript(tmp_path: Path, entries: list[dict]) -> TranscriptRef:
    f = tmp_path / "t.jsonl"
    with f.open("w") as fp:
        for e in entries:
            fp.write(json.dumps(e) + "\n")
    mtime = int(f.stat().st_mtime * 1000)
    return TranscriptRef(
        session_id="test-sid",
        path=f,
        workspace="-ws",
        mtime_ms=mtime,
    )


@pytest.fixture
def isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redireciona cache_dir para uma pasta isolada por teste."""
    cache_dir = tmp_path / "cache"
    monkeypatch.setenv("CLAUDE_DASH_CACHE", str(cache_dir))
    # Reimporta para pegar o novo CACHE_DIR
    import importlib

    from claude_dash import cache as cache_mod
    importlib.reload(cache_mod)
    return cache_dir


def test_aggregates_usage_by_model(tmp_path: Path, isolated_cache: Path) -> None:
    entries = [
        {"type": "user", "timestamp": "2026-04-21T11:00:00Z"},
        {
            "type": "assistant",
            "message": {
                "model": "claude-opus-4-7",
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "cache_read_input_tokens": 1000,
                },
            },
        },
        {
            "type": "assistant",
            "message": {
                "model": "claude-sonnet-4-6",
                "usage": {
                    "input_tokens": 200,
                    "output_tokens": 80,
                },
            },
        },
    ]
    ref = _make_transcript(tmp_path, entries)
    stats = build_stats_for_transcript(ref, use_cache=False)

    assert stats.messages_user == 1
    assert stats.messages_assistant == 2
    assert stats.usage_by_model["claude-opus-4-7"].input_tokens == 100
    assert stats.usage_by_model["claude-opus-4-7"].cache_read == 1000
    assert stats.usage_by_model["claude-sonnet-4-6"].input_tokens == 200


def test_counts_tools_across_messages(tmp_path: Path, isolated_cache: Path) -> None:
    entries = [
        {
            "type": "assistant",
            "message": {
                "model": "claude-opus-4-7",
                "content": [
                    {"type": "tool_use", "id": "1", "name": "Bash", "input": {}},
                    {"type": "tool_use", "id": "2", "name": "Edit", "input": {}},
                ],
            },
        },
        {
            "type": "assistant",
            "message": {
                "model": "claude-opus-4-7",
                "content": [
                    {"type": "tool_use", "id": "3", "name": "Bash", "input": {}},
                ],
            },
        },
    ]
    ref = _make_transcript(tmp_path, entries)
    stats = build_stats_for_transcript(ref, use_cache=False)
    assert stats.tools == {"Bash": 2, "Edit": 1}


def test_cache_reuses_on_unchanged_mtime(tmp_path: Path, isolated_cache: Path) -> None:
    entries = [
        {"type": "user"},
        {"type": "assistant", "message": {"model": "m", "usage": {"input_tokens": 10}}},
    ]
    ref = _make_transcript(tmp_path, entries)
    stats1 = build_stats_for_transcript(ref, use_cache=True)
    # Segunda chamada: cache hit, mesmo resultado
    stats2 = build_stats_for_transcript(ref, use_cache=True)
    assert stats1.usage_by_model == stats2.usage_by_model
    assert stats1.messages_user == stats2.messages_user


def test_cache_incremental_appends(tmp_path: Path, isolated_cache: Path) -> None:
    """Simula arquivo append-only: primeira passada + nova linha + segunda passada."""
    e1 = {"type": "assistant", "message": {"model": "m", "usage": {"input_tokens": 10}}}
    e2 = {"type": "assistant", "message": {"model": "m", "usage": {"input_tokens": 20}}}

    ref = _make_transcript(tmp_path, [e1])
    s1 = build_stats_for_transcript(ref, use_cache=True)
    assert s1.usage_by_model["m"].input_tokens == 10

    # Append e força mtime futuro
    with ref.path.open("a") as f:
        f.write(json.dumps(e2) + "\n")
    future = int(ref.path.stat().st_mtime) + 10
    os.utime(ref.path, (future, future))

    ref2 = TranscriptRef(
        session_id=ref.session_id,
        path=ref.path,
        workspace=ref.workspace,
        mtime_ms=future * 1000,
    )
    s2 = build_stats_for_transcript(ref2, use_cache=True)
    # Deve ter somado o segundo sem perder o primeiro
    assert s2.usage_by_model["m"].input_tokens == 30


def test_cache_invalidates_on_inode_change(tmp_path: Path, isolated_cache: Path) -> None:
    """Se o arquivo for recriado (mesmo path, inode novo), cache deve ser descartado."""
    e1 = {"type": "assistant", "message": {"model": "m", "usage": {"input_tokens": 10}}}
    e2 = {"type": "assistant", "message": {"model": "m", "usage": {"input_tokens": 777}}}

    # Primeira passada: popula cache
    ref = _make_transcript(tmp_path, [e1])
    old_inode = ref.path.stat().st_ino
    s1 = build_stats_for_transcript(ref, use_cache=True)
    assert s1.usage_by_model["m"].input_tokens == 10

    # Cria novo arquivo em outro path com conteúdo totalmente diferente, depois
    # usa os.replace() para substituir atomicamente — isso garante inode novo
    # (rename(2) transfere o inode da origem), algo que unlink+create em tmpfs
    # não garante por causa de reuso de inodes.
    new_file = tmp_path / "t_new.jsonl"
    padding = " " * 500
    new_file.write_text(
        json.dumps(e2) + "\n" + json.dumps({"type": "user", "pad": padding}) + "\n"
    )
    os.replace(new_file, ref.path)
    new_inode = ref.path.stat().st_ino
    assert new_inode != old_inode, "premissa do teste: inode deve mudar após replace"

    new_mtime = int(ref.path.stat().st_mtime * 1000) + 1000
    os.utime(ref.path, (new_mtime / 1000, new_mtime / 1000))

    ref2 = TranscriptRef(
        session_id=ref.session_id,
        path=ref.path,
        workspace=ref.workspace,
        mtime_ms=new_mtime,
    )
    s2 = build_stats_for_transcript(ref2, use_cache=True)
    # Deve refletir apenas o NOVO conteúdo, não o antigo somado
    assert s2.usage_by_model["m"].input_tokens == 777


def test_cache_exact_hit_skips_parse(tmp_path: Path, isolated_cache: Path) -> None:
    """Quando inode+mtime batem, não deve abrir o arquivo novamente."""
    entries = [
        {"type": "assistant", "message": {"model": "m", "usage": {"input_tokens": 10}}},
    ]
    ref = _make_transcript(tmp_path, entries)
    build_stats_for_transcript(ref, use_cache=True)

    # Intercepta parser.iter_entries para contar chamadas
    from claude_dash import aggregator

    call_count = {"n": 0}
    original = aggregator.iter_entries

    def counting_iter_entries(*args, **kwargs):
        call_count["n"] += 1
        yield from original(*args, **kwargs)

    aggregator.iter_entries = counting_iter_entries
    try:
        build_stats_for_transcript(ref, use_cache=True)
    finally:
        aggregator.iter_entries = original

    # Cache hit exato: iter_entries não deve ser chamado na 2a passada
    assert call_count["n"] == 0


def test_normalizes_model_key_with_bracket_suffix(
    tmp_path: Path, isolated_cache: Path
) -> None:
    """usage_by_model deve usar chave canônica (sem sufixo [1m])."""
    entries = [
        {
            "type": "assistant",
            "message": {
                "model": "claude-opus-4-7[1m]",
                "usage": {"input_tokens": 42},
            },
        },
    ]
    ref = _make_transcript(tmp_path, entries)
    stats = build_stats_for_transcript(ref, use_cache=False)
    # Chave deve estar normalizada
    assert "claude-opus-4-7" in stats.usage_by_model
    assert "claude-opus-4-7[1m]" not in stats.usage_by_model
    assert stats.dominant_model == "claude-opus-4-7"


def test_collect_live_sessions_single_scan(
    tmp_path: Path, isolated_cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """collect_live_sessions deve varrer projects/ uma única vez."""
    from claude_dash import aggregator, discover

    scan_count = {"n": 0}
    original = discover.discover_transcripts

    def counting(*args, **kwargs):
        scan_count["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(aggregator, "discover_transcripts", counting)
    # Sem sessões vivas → 1 scan e retorna []
    monkeypatch.setattr(aggregator, "discover_live_sessions", lambda: [])
    result = aggregator.collect_live_sessions(use_cache=False)
    assert result == []
    assert scan_count["n"] == 1


def test_last_usage_tracks_most_recent_assistant(
    tmp_path: Path, isolated_cache: Path
) -> None:
    """last_usage deve ser snapshot da última assistant entry — não acumulado."""
    entries = [
        {"type": "assistant", "message": {"model": "m",
            "usage": {"input_tokens": 10, "cache_read_input_tokens": 100}}},
        {"type": "assistant", "message": {"model": "m",
            "usage": {"input_tokens": 20, "cache_read_input_tokens": 5000}}},
        {"type": "assistant", "message": {"model": "m",
            "usage": {"input_tokens": 3, "cache_read_input_tokens": 7777}}},
    ]
    ref = _make_transcript(tmp_path, entries)
    stats = build_stats_for_transcript(ref, use_cache=False)
    assert stats.last_usage is not None
    # Snapshot do último turno (não soma dos três)
    assert stats.last_usage.input_tokens == 3
    assert stats.last_usage.cache_read == 7777
    # Contexto ativo = input + cache_read do último turno
    assert stats.active_context_tokens == 3 + 7777


def test_last_usage_is_none_without_assistant(
    tmp_path: Path, isolated_cache: Path
) -> None:
    """Sem entrada assistant, last_usage fica None e context=0."""
    entries = [{"type": "user"}, {"type": "system"}]
    ref = _make_transcript(tmp_path, entries)
    stats = build_stats_for_transcript(ref, use_cache=False)
    assert stats.last_usage is None
    assert stats.active_context_tokens == 0


def test_tokens_per_turn(tmp_path: Path, isolated_cache: Path) -> None:
    """tokens_per_turn = total_output / messages_assistant."""
    entries = [
        {"type": "assistant", "message": {"model": "m",
            "usage": {"output_tokens": 100}}},
        {"type": "assistant", "message": {"model": "m",
            "usage": {"output_tokens": 400}}},
    ]
    ref = _make_transcript(tmp_path, entries)
    stats = build_stats_for_transcript(ref, use_cache=False)
    # (100 + 400) / 2 = 250
    assert stats.tokens_per_turn == 250.0


def test_tokens_per_turn_zero_when_no_assistant(
    tmp_path: Path, isolated_cache: Path
) -> None:
    entries = [{"type": "user"}]
    ref = _make_transcript(tmp_path, entries)
    stats = build_stats_for_transcript(ref, use_cache=False)
    assert stats.tokens_per_turn == 0.0


def test_last_usage_survives_cache_roundtrip(
    tmp_path: Path, isolated_cache: Path
) -> None:
    """Após save+load do cache, last_usage deve ser preservado."""
    entries = [
        {"type": "assistant", "message": {"model": "m",
            "usage": {"input_tokens": 42, "cache_read_input_tokens": 999}}},
    ]
    ref = _make_transcript(tmp_path, entries)
    build_stats_for_transcript(ref, use_cache=True)
    # Segunda chamada: carrega do cache
    stats2 = build_stats_for_transcript(ref, use_cache=True)
    assert stats2.last_usage is not None
    assert stats2.last_usage.input_tokens == 42
    assert stats2.last_usage.cache_read == 999


# --- aggregate_transcript_since -----------------------------------------


def test_since_filters_before_cutoff(tmp_path: Path, isolated_cache: Path) -> None:
    """Entradas com timestamp < since_ms são descartadas."""
    from claude_dash.aggregator import aggregate_transcript_since

    entries = [
        {"type": "assistant", "timestamp": "2026-04-20T10:00:00Z",
         "message": {"model": "m", "usage": {"input_tokens": 111}}},
        {"type": "assistant", "timestamp": "2026-04-21T10:00:00Z",
         "message": {"model": "m", "usage": {"input_tokens": 222}}},
    ]
    ref = _make_transcript(tmp_path, entries)
    # Cutoff = 2026-04-21 00:00 UTC
    cutoff = 1776758400000
    stats = aggregate_transcript_since(ref, since_ms=cutoff)
    # Só a entrada de 21/04 conta
    assert stats.usage_by_model["m"].input_tokens == 222
    assert stats.messages_assistant == 1


def test_since_excludes_untimed_entries(
    tmp_path: Path, isolated_cache: Path
) -> None:
    """Entradas sem `timestamp` não contam (conservador — sem data não dá
    pra afirmar que estão na janela)."""
    from claude_dash.aggregator import aggregate_transcript_since

    entries = [
        {"type": "assistant",  # sem timestamp
         "message": {"model": "m", "usage": {"input_tokens": 10}}},
    ]
    ref = _make_transcript(tmp_path, entries)
    stats = aggregate_transcript_since(ref, since_ms=0)
    assert stats.usage_by_model == {}
    assert stats.messages_assistant == 0


def test_collect_sessions_since_filters_by_mtime(
    tmp_path: Path, isolated_cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """collect_sessions_since ignora transcripts com mtime antes da janela."""
    from claude_dash import aggregator
    from claude_dash.aggregator import collect_sessions_since
    from claude_dash.discover import TranscriptRef

    # Transcripts sintéticos: um antigo (mtime pequeno) e um novo
    old_entries = [
        {"type": "assistant", "timestamp": "2026-04-20T10:00:00Z",
         "message": {"model": "m", "usage": {"input_tokens": 1}}},
    ]
    new_entries = [
        {"type": "assistant", "timestamp": "2026-04-21T10:00:00Z",
         "message": {"model": "m", "usage": {"input_tokens": 999}}},
    ]

    old_file = tmp_path / "old.jsonl"
    new_file = tmp_path / "new.jsonl"
    old_file.write_text("\n".join(json.dumps(e) for e in old_entries) + "\n")
    new_file.write_text("\n".join(json.dumps(e) for e in new_entries) + "\n")
    # Força mtime antigo no old_file
    os.utime(old_file, (1700000000, 1700000000))

    refs = [
        TranscriptRef(session_id="old", path=old_file, workspace="-ws",
                      mtime_ms=int(old_file.stat().st_mtime * 1000)),
        TranscriptRef(session_id="new", path=new_file, workspace="-ws",
                      mtime_ms=int(new_file.stat().st_mtime * 1000)),
    ]

    monkeypatch.setattr(aggregator, "discover_live_sessions", lambda: [])
    monkeypatch.setattr(aggregator, "discover_transcripts", lambda: refs)

    cutoff = 1776758400000  # 2026-04-21 00:00 UTC
    result = collect_sessions_since(cutoff)
    sids = [s.session_id for s in result]
    assert "new" in sids
    assert "old" not in sids
