"""Modelo de dados de uma entry do audit log.

Representa uma linha parseada do ``~/.claude/iris/audit/sessions.log`` no
formato RFC 5424 emitido pelo hook em ``audit/hook.py``. Campos opcionais
ficam em ``None`` quando ausentes (ex.: ``subagent_type`` so existe em
entries de tool ``Agent``).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class AuditEntry:
    """Uma chamada de tool registrada pelo hook PostToolUse.

    Atributos correspondem aos campos STRUCTURED-DATA emitidos pelo
    hook (`[audit@iris ...]`) mais o cabecalho RFC 5424 (timestamp,
    hostname). Tipos sao normalizados: ``timestamp`` vira datetime,
    contadores numericos viram int.
    """

    timestamp: datetime
    hostname: str
    session_id: str
    tool: str
    tool_use_id: str
    status: str  # "success" | "error"
    duration_ms: int
    perm_mode: str
    input_sha: str
    input_bytes: int
    output_bytes: int
    subagent_type: str | None = None
    # PID do processo Claude Code que disparou a tool. Util para
    # correlacionar com transcripts mas nao e exibido na tabela.
    pid: str = ""
