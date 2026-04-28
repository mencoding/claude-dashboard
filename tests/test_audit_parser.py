"""Testes do parser de linhas RFC 5424 do audit log."""
from __future__ import annotations

from claude_dash.audit.parser import parse_full_line, parse_line


def test_linha_valida_basica() -> None:
    line = (
        '<134>1 2026-04-28T00:00:29.223-03:00 Predator-PH315-54 claude-code 135727 '
        'TOOLCALL [audit@iris session="0efd3cf4-96ef-41b7-9d41-f91ec539becd" '
        'tool="Bash" tool_use_id="toolu_017miaa4KSxVZ2BRQGrXHjjf" status="success" '
        'duration_ms="2018" perm_mode="auto" input_sha="cd82a9dfb59fa59d" '
        'input_bytes="673" output_bytes="995"]'
    )
    e = parse_line(line)
    assert e is not None
    assert e.hostname == "Predator-PH315-54"
    assert e.session_id == "0efd3cf4-96ef-41b7-9d41-f91ec539becd"
    assert e.tool == "Bash"
    assert e.status == "success"
    assert e.duration_ms == 2018
    assert e.input_bytes == 673
    assert e.output_bytes == 995
    assert e.perm_mode == "auto"
    assert e.subagent_type is None
    assert e.pid == "135727"


def test_linha_malformada_retorna_none() -> None:
    assert parse_line("") is None
    assert parse_line("   ") is None
    assert parse_line("texto qualquer") is None
    # Header valido mas sem session/tool -> invalido
    assert (
        parse_line(
            '<134>1 2026-04-28T00:00:29.223-03:00 host claude-code 1 TOOLCALL '
            '[audit@iris foo="bar"]'
        )
        is None
    )


def test_entry_de_agent_carrega_subagent_type() -> None:
    line = (
        '<134>1 2026-04-28T00:00:29.223-03:00 host claude-code 1 TOOLCALL '
        '[audit@iris session="abc" tool="Agent" tool_use_id="x" status="success" '
        'duration_ms="100" perm_mode="auto" input_sha="aa" input_bytes="10" '
        'output_bytes="20" subagent_type="general-purpose"]'
    )
    e = parse_line(line)
    assert e is not None
    assert e.tool == "Agent"
    assert e.subagent_type == "general-purpose"


def test_unicode_em_valores() -> None:
    line = (
        '<134>1 2026-04-28T00:00:29.223-03:00 host claude-code 1 TOOLCALL '
        '[audit@iris session="café-ção" tool="Bash" tool_use_id="x" '
        'status="success" duration_ms="0" perm_mode="" input_sha="0" '
        'input_bytes="0" output_bytes="0"]'
    )
    e = parse_line(line)
    assert e is not None
    assert e.session_id == "café-ção"


def test_status_error_preservado() -> None:
    line = (
        '<134>1 2026-04-28T00:00:29.223-03:00 host claude-code 1 TOOLCALL '
        '[audit@iris session="abc" tool="Bash" tool_use_id="x" status="error" '
        'duration_ms="50" perm_mode="auto" input_sha="0" input_bytes="0" '
        'output_bytes="0"]'
    )
    e = parse_line(line)
    assert e is not None
    assert e.status == "error"


def test_timestamp_parseado_para_datetime() -> None:
    line = (
        '<134>1 2026-04-28T00:00:29.223-03:00 host claude-code 1 TOOLCALL '
        '[audit@iris session="abc" tool="Bash" tool_use_id="x" status="success" '
        'duration_ms="0" perm_mode="" input_sha="0" input_bytes="0" '
        'output_bytes="0"]'
    )
    e = parse_line(line)
    assert e is not None
    assert e.timestamp.year == 2026
    assert e.timestamp.month == 4
    assert e.timestamp.day == 28
    assert e.timestamp.tzinfo is not None


# ---- PreToolUse / TOOLSTART (#50) -----------------------------------


def test_toolstart_parseado_como_event_start() -> None:
    """Linha com msgid TOOLSTART -> event=start."""
    line = (
        '<134>1 2026-04-28T00:00:29.223-03:00 host claude-code 1 TOOLSTART '
        '[audit@iris session="abc" tool="Bash" tool_use_id="x" status="running" '
        'duration_ms="0" perm_mode="auto" input_sha="0" input_bytes="10" '
        'output_bytes="0" event="start"]'
    )
    e = parse_line(line)
    assert e is not None
    assert e.event == "start"
    assert e.status == "running"


def test_toolcall_default_event_end() -> None:
    """Linha TOOLCALL sem campo event explicito -> event=end (retrocompat)."""
    line = (
        '<134>1 2026-04-28T00:00:29.223-03:00 host claude-code 1 TOOLCALL '
        '[audit@iris session="abc" tool="Bash" tool_use_id="x" status="success" '
        'duration_ms="100" perm_mode="auto" input_sha="0" input_bytes="0" '
        'output_bytes="0"]'
    )
    e = parse_line(line)
    assert e is not None
    assert e.event == "end"


def test_toolcall_com_event_explicito_end() -> None:
    """Linha TOOLCALL com event=end (formato novo) -> event=end."""
    line = (
        '<134>1 2026-04-28T00:00:29.223-03:00 host claude-code 1 TOOLCALL '
        '[audit@iris session="abc" tool="Bash" tool_use_id="x" status="success" '
        'duration_ms="100" perm_mode="auto" input_sha="0" input_bytes="0" '
        'output_bytes="0" event="end"]'
    )
    e = parse_line(line)
    assert e is not None
    assert e.event == "end"


# ---- parse_full_line: extrai trailing fields (#67-followup) ---------


def test_full_line_extrai_cwd_e_cmd_de_bash() -> None:
    """Linha do tools.log inclui cwd + cmd no trailing apos o ']'."""
    line = (
        '<134>1 2026-04-28T00:00:29.223-03:00 host claude-code 1 TOOLCALL '
        '[audit@iris session="abc" tool="Bash" tool_use_id="x" status="success" '
        'duration_ms="100" perm_mode="auto" input_sha="0" input_bytes="0" '
        "output_bytes=\"0\"] cwd='/home/menzani' cmd='ls -la /tmp'"
    )
    entry, extra = parse_full_line(line)
    assert entry is not None
    assert entry.tool == "Bash"
    assert extra["cwd"] == "/home/menzani"
    assert extra["cmd"] == "ls -la /tmp"


def test_full_line_extrai_path_de_read() -> None:
    line = (
        '<134>1 2026-04-28T00:00:29.223-03:00 host claude-code 1 TOOLCALL '
        '[audit@iris session="abc" tool="Read" tool_use_id="x" status="success" '
        'duration_ms="100" perm_mode="auto" input_sha="0" input_bytes="0" '
        "output_bytes=\"0\"] cwd='/tmp' path='/etc/hostname'"
    )
    entry, extra = parse_full_line(line)
    assert entry is not None
    assert extra["path"] == "/etc/hostname"


def test_full_line_extrai_url_de_webfetch() -> None:
    line = (
        '<134>1 2026-04-28T00:00:29.223-03:00 host claude-code 1 TOOLCALL '
        '[audit@iris session="abc" tool="WebFetch" tool_use_id="x" status="success" '
        'duration_ms="100" perm_mode="auto" input_sha="0" input_bytes="0" '
        "output_bytes=\"0\"] cwd='/tmp' url='https://example.com/'"
    )
    entry, extra = parse_full_line(line)
    assert entry is not None
    assert extra["url"] == "https://example.com/"


def test_full_line_sem_trailing_retorna_extra_vazio() -> None:
    """Linha so com SD, sem campos depois — extra vazio mas entry valido."""
    line = (
        '<134>1 2026-04-28T00:00:29.223-03:00 host claude-code 1 TOOLCALL '
        '[audit@iris session="abc" tool="Bash" tool_use_id="x" status="success" '
        'duration_ms="100" perm_mode="auto" input_sha="0" input_bytes="0" '
        'output_bytes="0"]'
    )
    entry, extra = parse_full_line(line)
    assert entry is not None
    assert extra == {}


def test_full_line_invalida_retorna_none_e_dict_vazio() -> None:
    entry, extra = parse_full_line("lixo nao parseavel")
    assert entry is None
    assert extra == {}
