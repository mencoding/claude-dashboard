"""Testes da comparacao cross-session (#38)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from claude_dash.audit.models import AuditEntry
from claude_dash.views.audit_compare import (
    Correlation,
    find_correlations,
    group_by_session,
    merge_timelines,
    parse_compare_input,
)


def _e(
    *,
    sid: str = "0efd3cf4-96ef-41b7-9d41-f91ec539becd",
    tool: str = "Bash",
    delta_seconds: float = 0,
    input_sha: str = "aaaaaa",
    tool_use_id: str = "x",
) -> AuditEntry:
    base = datetime(2026, 4, 28, 10, 0, 0, tzinfo=UTC)
    return AuditEntry(
        timestamp=base + timedelta(seconds=delta_seconds),
        hostname="host", pid="1",
        session_id=sid, tool=tool, tool_use_id=tool_use_id,
        status="success", duration_ms=10, perm_mode="auto",
        input_sha=input_sha, input_bytes=10, output_bytes=20,
    )


# ---- parse_compare_input --------------------------------------------


def test_parse_compare_input_separadores_diversos() -> None:
    assert parse_compare_input("0efd3 a1b2 8f9c") == ["0efd3", "a1b2", "8f9c"]
    assert parse_compare_input("0efd3,a1b2,8f9c") == ["0efd3", "a1b2", "8f9c"]
    assert parse_compare_input("0efd3; a1b2;8f9c") == ["0efd3", "a1b2", "8f9c"]
    assert parse_compare_input("  0efd3   a1b2  ") == ["0efd3", "a1b2"]


def test_parse_compare_input_dedup_preservando_ordem() -> None:
    assert parse_compare_input("a b a c b") == ["a", "b", "c"]


def test_parse_compare_input_vazio() -> None:
    assert parse_compare_input("") == []
    assert parse_compare_input("   ") == []


# ---- group_by_session -----------------------------------------------


def test_group_prefix_match_basico() -> None:
    a = _e(sid="0efd3cf4-aaaa-aaaa-aaaa-aaaaaaaaaaaa", tool="Bash")
    b = _e(sid="a1b2cccc-cccc-cccc-cccc-cccccccccccc", tool="Read")
    out = group_by_session([a, b], ["0efd3", "a1b2"])
    assert out["0efd3"] == [a]
    assert out["a1b2"] == [b]


def test_group_sid_sem_match_vira_lista_vazia() -> None:
    a = _e(sid="0efd3cf4-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    out = group_by_session([a], ["0efd3", "ffffff"])
    assert out["0efd3"] == [a]
    assert out["ffffff"] == []


def test_group_ordena_por_timestamp_dentro_da_sessao() -> None:
    e1 = _e(sid="0efd3cf4", delta_seconds=5)
    e2 = _e(sid="0efd3cf4", delta_seconds=0)
    e3 = _e(sid="0efd3cf4", delta_seconds=10)
    out = group_by_session([e1, e2, e3], ["0efd3"])
    assert [x.timestamp for x in out["0efd3"]] == sorted(
        [e1.timestamp, e2.timestamp, e3.timestamp],
    )


# ---- merge_timelines ------------------------------------------------


def test_merge_timelines_global() -> None:
    """Merge respeita ordem global por timestamp + tie-break por sid."""
    grouped = {
        "A": [_e(sid="A", delta_seconds=0), _e(sid="A", delta_seconds=10)],
        "B": [_e(sid="B", delta_seconds=5), _e(sid="B", delta_seconds=15)],
    }
    out = merge_timelines(grouped)
    sids_em_ordem = [sid for sid, _ in out]
    assert sids_em_ordem == ["A", "B", "A", "B"]


def test_merge_timelines_tiebreak_por_sid_lexico() -> None:
    grouped = {
        "B": [_e(sid="B", delta_seconds=0)],
        "A": [_e(sid="A", delta_seconds=0)],
    }
    out = merge_timelines(grouped)
    assert [sid for sid, _ in out] == ["A", "B"]


# ---- find_correlations ----------------------------------------------


def test_correlate_same_path_em_janela_curta() -> None:
    """Mesmo input_sha em sessoes diferentes < 500ms -> same_path."""
    a = _e(sid="A", tool="Read", input_sha="path-X", delta_seconds=0,
           tool_use_id="ta")
    b = _e(sid="B", tool="Read", input_sha="path-X", delta_seconds=0.3,
           tool_use_id="tb")
    out = find_correlations({"A": [a], "B": [b]})
    assert len(out) == 1
    assert out[0].reason == "same_path"
    assert out[0].delta_ms == 300


def test_correlate_same_cmd_para_bash() -> None:
    a = _e(sid="A", tool="Bash", input_sha="cmd-X", tool_use_id="ta")
    b = _e(sid="B", tool="Bash", input_sha="cmd-X", delta_seconds=0.1,
           tool_use_id="tb")
    out = find_correlations({"A": [a], "B": [b]})
    assert len(out) == 1
    assert out[0].reason == "same_cmd"


def test_correlate_read_then_edit_em_5s() -> None:
    """A=Read X em t=0; B=Edit X em t=2 -> read_then_edit."""
    read = _e(sid="A", tool="Read", input_sha="file-X", delta_seconds=0,
              tool_use_id="r1")
    edit = _e(sid="B", tool="Edit", input_sha="file-X", delta_seconds=2,
              tool_use_id="e1")
    out = find_correlations({"A": [read], "B": [edit]})
    assert len(out) == 1
    assert out[0].reason == "read_then_edit"
    assert out[0].delta_ms == 2000


def test_correlate_ignora_mesma_sessao() -> None:
    a = _e(sid="A", tool="Read", input_sha="X", tool_use_id="t1")
    b = _e(sid="A", tool="Read", input_sha="X", delta_seconds=0.1,
           tool_use_id="t2")
    out = find_correlations({"A": [a, b]})
    assert out == []


def test_correlate_ignora_alvo_invalido() -> None:
    """Skill/Agent nao tem _entry_target -> nunca correlaciona."""
    a = _e(sid="A", tool="Skill", input_sha="X", tool_use_id="t1")
    b = _e(sid="B", tool="Skill", input_sha="X", delta_seconds=0.1,
           tool_use_id="t2")
    out = find_correlations({"A": [a], "B": [b]})
    assert out == []


def test_correlate_fora_da_janela_nao_pareia() -> None:
    """Mesmo input_sha mas delta > rte_window (5s) -> sem correlacao."""
    a = _e(sid="A", tool="Read", input_sha="X", delta_seconds=0)
    b = _e(sid="B", tool="Read", input_sha="X", delta_seconds=10)
    out = find_correlations({"A": [a], "B": [b]})
    assert out == []


def test_correlate_retorna_correlation_dataclass() -> None:
    a = _e(sid="A", tool="Bash", input_sha="X", tool_use_id="t1")
    b = _e(sid="B", tool="Bash", input_sha="X", delta_seconds=0.05,
           tool_use_id="t2")
    out = find_correlations({"A": [a], "B": [b]})
    assert isinstance(out[0], Correlation)
    assert out[0].session_a == "A"
    assert out[0].session_b == "B"
    assert out[0].entry_a is a
    assert out[0].entry_b is b
