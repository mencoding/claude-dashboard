"""Parser de linhas RFC 5424 do audit log.

Le linhas do formato emitido pelo ``audit/hook.py``:

    <134>1 2026-04-28T00:00:29.223-03:00 Predator-PH315-54 claude-code \\
      135727 TOOLCALL [audit@iris session="..." tool="..." status="..." ...]

Tokens de message-id (#50):
- ``TOOLCALL``: PostToolUse (event="end", classico)
- ``TOOLSTART``: PreToolUse (event="start", sem duration_ms/output_bytes/status validos)

Linhas malformadas retornam ``None`` (skip silencioso) — isso e
deliberado: o tail incremental nao deve crashar em logs corrompidos
ou de versoes antigas do hook.
"""
from __future__ import annotations

import re
from datetime import datetime

from claude_dash.audit.models import AuditEntry

# Cabecalho ate antes do STRUCTURED-DATA. Captura: prio (descartado),
# version (descartado), timestamp, hostname, app (descartado), pid,
# msgid (TOOLCALL ou TOOLSTART pra distinguir end vs start).
_HEADER_RE = re.compile(
    r"^<\d+>\d+\s+(\S+)\s+(\S+)\s+\S+\s+(\S+)\s+(\S+)\s+\[audit@iris\s+(.*?)\]"
)

# Pares chave="valor" dentro do STRUCTURED-DATA. Aceita aspas duplas
# em valor escapadas como ``""`` (RFC 5424 §6.3.3) — improvavel em
# pratica, mas barato suportar.
_KV_RE = re.compile(r'(\w+)="((?:[^"\\]|\\.)*)"')


def _to_int(s: str, default: int = 0) -> int:
    """Converte string p/ int sem levantar; usa default em falha."""
    try:
        return int(s)
    except (TypeError, ValueError):
        return default


def parse_line(line: str) -> AuditEntry | None:
    """Parseia uma linha do audit log; retorna ``None`` se invalida.

    Tolera trailing newline, espacos extras, valores ausentes (default
    "" para strings, 0 para numeros).
    """
    if not line or not line.strip():
        return None

    m = _HEADER_RE.match(line.strip())
    if not m:
        return None

    ts_str, hostname, pid, msgid, sd_inner = (
        m.group(1), m.group(2), m.group(3), m.group(4), m.group(5),
    )

    try:
        ts = datetime.fromisoformat(ts_str)
    except ValueError:
        return None

    # Extrai todos os pares chave="valor" do STRUCTURED-DATA.
    fields = dict(_KV_RE.findall(sd_inner))

    # Campos obrigatorios minimos: session, tool. Sem eles a linha e
    # inutil para a TUI — descarta.
    if "session" not in fields or "tool" not in fields:
        return None

    # Determina event pelo msgid (TOOLSTART/TOOLCALL) ou campo explicito
    # (mais defensivo). Default "end" pra logs pre-#50.
    event_field = fields.get("event", "")
    event = "start" if event_field == "start" or msgid == "TOOLSTART" else "end"

    # Pra entries de start, status default = "running" (vs "success" do end).
    default_status = "running" if event == "start" else "success"

    return AuditEntry(
        timestamp=ts,
        hostname=hostname,
        pid=pid,
        session_id=fields.get("session", ""),
        tool=fields.get("tool", ""),
        tool_use_id=fields.get("tool_use_id", ""),
        status=fields.get("status", default_status),
        duration_ms=_to_int(fields.get("duration_ms", "0")),
        perm_mode=fields.get("perm_mode", ""),
        input_sha=fields.get("input_sha", ""),
        input_bytes=_to_int(fields.get("input_bytes", "0")),
        output_bytes=_to_int(fields.get("output_bytes", "0")),
        subagent_type=fields.get("subagent_type") or None,
        event=event,
    )
