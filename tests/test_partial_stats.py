"""Testes de drill-down parcial via audit metadata (#55)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from claude_dash.audit.partial_stats import (
    PartialSessionStats,
    build_partial_stats_from_audit,
)


# Linhas RFC 5424 minimas validas pelo parser do audit/parser.py.
def _line(
    *,
    ts: datetime,
    host: str = "PREDATOR",
    session: str = "0efd3cf4-96ef-41b7-9d41-f91ec539becd",
    tool: str = "Bash",
    status: str = "success",
    duration_ms: int = 100,
) -> str:
    ts_str = ts.isoformat()
    return (
        f'<134>1 {ts_str} {host} claude-code 12345 TOOLCALL '
        f'[audit@iris session="{session}" tool="{tool}" '
        f'tool_use_id="x" status="{status}" duration_ms="{duration_ms}" '
        f'perm_mode="auto" input_sha="0" input_bytes="10" output_bytes="20"]'
    )


def _write_log(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_arquivo_inexistente_retorna_none(tmp_path) -> None:
    out = build_partial_stats_from_audit(
        "abc", audit_log_path=tmp_path / "missing.log"
    )
    assert out is None


def test_session_id_sem_match_retorna_none(tmp_path) -> None:
    log = tmp_path / "sessions.log"
    _write_log(log, [
        _line(ts=datetime(2026, 4, 28, 10, 0, 0, tzinfo=UTC),
              session="aaaaaaaa-1111-4111-8111-111111111111"),
    ])
    out = build_partial_stats_from_audit(
        "0efd3", audit_log_path=log
    )
    assert out is None


def test_match_basico_agrega_corretamente(tmp_path) -> None:
    log = tmp_path / "sessions.log"
    base = datetime(2026, 4, 28, 10, 0, 0, tzinfo=UTC)
    _write_log(log, [
        _line(ts=base, host="PREDATOR", tool="Bash"),
        _line(ts=base + timedelta(seconds=5), host="PREDATOR", tool="Read"),
        _line(ts=base + timedelta(seconds=12), host="PREDATOR",
              tool="Bash", status="error"),
    ])
    out = build_partial_stats_from_audit(
        "0efd3", audit_log_path=log
    )
    assert out is not None
    assert isinstance(out, PartialSessionStats)
    assert out.hostname == "PREDATOR"
    assert out.total_calls == 3
    assert out.error_count == 1
    assert abs(out.error_rate - (1 / 3)) < 1e-6
    # Top tools: Bash com 2, Read com 1
    top = dict(out.top_tools)
    assert top.get("Bash") == 2
    assert top.get("Read") == 1
    # Duracao: ~12s = 12000ms
    assert out.duration_ms == 12000


def test_filtra_por_session_id_ignora_outros(tmp_path) -> None:
    log = tmp_path / "sessions.log"
    base = datetime(2026, 4, 28, 10, 0, 0, tzinfo=UTC)
    _write_log(log, [
        _line(ts=base, session="0efd3cf4-96ef-41b7-9d41-f91ec539becd"),
        _line(ts=base + timedelta(seconds=1),
              session="aaaaaaaa-1111-4111-8111-111111111111"),
        _line(ts=base + timedelta(seconds=2),
              session="0efd3cf4-96ef-41b7-9d41-f91ec539becd"),
    ])
    out = build_partial_stats_from_audit(
        "0efd3", audit_log_path=log
    )
    assert out is not None
    assert out.total_calls == 2


def test_ignora_linhas_malformadas(tmp_path) -> None:
    """Parser retorna None pra lixo; helper pula sem crashar."""
    log = tmp_path / "sessions.log"
    base = datetime(2026, 4, 28, 10, 0, 0, tzinfo=UTC)
    valid_line = _line(ts=base)
    log.write_text(
        "lixo nao parseavel\n"
        + valid_line + "\n"
        + "0efd3 mas tambem lixo\n",  # contem o prefix mas nao parseia
        encoding="utf-8",
    )
    out = build_partial_stats_from_audit(
        "0efd3", audit_log_path=log
    )
    assert out is not None
    assert out.total_calls == 1


def test_entries_ordenadas_por_timestamp(tmp_path) -> None:
    """Mesmo se log for fora de ordem, helper ordena defensivamente."""
    log = tmp_path / "sessions.log"
    base = datetime(2026, 4, 28, 10, 0, 0, tzinfo=UTC)
    _write_log(log, [
        _line(ts=base + timedelta(seconds=10), tool="Edit"),
        _line(ts=base, tool="Bash"),
        _line(ts=base + timedelta(seconds=5), tool="Read"),
    ])
    out = build_partial_stats_from_audit(
        "0efd3", audit_log_path=log
    )
    assert out is not None
    tools_in_order = [e.tool for e in out.entries]
    assert tools_in_order == ["Bash", "Read", "Edit"]


def test_drill_down_cli_caminho_3_sem_dado(monkeypatch, tmp_path, capsys) -> None:
    """views/session.py:run -> mensagem 'Nao existem dados' quando nem JSONL nem audit."""
    from claude_dash.audit import partial_stats as ps_mod
    from claude_dash.views import session as view_session

    # Aponta default audit log pra arquivo inexistente
    fake_log = tmp_path / "missing.log"
    monkeypatch.setattr(
        view_session, "build_partial_stats_from_audit",
        lambda sid: ps_mod.build_partial_stats_from_audit(
            sid, audit_log_path=fake_log,
        ),
    )
    monkeypatch.setattr(
        view_session, "find_transcript_for_session", lambda sid: None,
    )
    rc = view_session.run("nonexistent-sid")
    assert rc == 1
    out = capsys.readouterr().out
    assert "Nao existem dados neste host" in out


def test_drill_down_cli_caminho_2_partial(monkeypatch, tmp_path, capsys) -> None:
    """views/session.py:run -> drill-down parcial quando ha audit mas nao JSONL."""
    from claude_dash.audit import partial_stats as ps_mod
    from claude_dash.views import session as view_session

    log = tmp_path / "sessions.log"
    base = datetime(2026, 4, 28, 10, 0, 0, tzinfo=UTC)
    _write_log(log, [
        _line(ts=base, host="PREDATOR", tool="Bash"),
        _line(ts=base + timedelta(seconds=5), host="PREDATOR", tool="Read"),
    ])
    monkeypatch.setattr(
        view_session, "build_partial_stats_from_audit",
        lambda sid: ps_mod.build_partial_stats_from_audit(
            sid, audit_log_path=log,
        ),
    )
    monkeypatch.setattr(
        view_session, "find_transcript_for_session", lambda sid: None,
    )
    rc = view_session.run("0efd3")
    assert rc == 0
    out = capsys.readouterr().out
    # Header parcial menciona origem e aviso
    assert "PREDATOR" in out
    assert "Origem" in out
    # Resumo conta tool calls
    assert "Tool calls" in out
