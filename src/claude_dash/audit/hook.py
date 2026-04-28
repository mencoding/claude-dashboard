"""Hook PostToolUse: registra cada tool call do Claude Code.

Entry point ``claude-dash-audit-hook``. Substitui 1:1 o bash legado
``~/.claude/iris/hooks/audit-tool.sh``. A saida emitida (linha syslog RFC 5424
no logger e linha de metadata no LOCAL_LOG) e **byte-identica** a do bash
para o mesmo payload — mudar formato exige mudar o teste de equivalencia.

Fail-silent: qualquer excecao -> ``sys.exit(0)``. Critical: hook NUNCA pode
quebrar a sessao Claude. Trade-off consciente — prefere-se perder um log a
travar o fluxo do usuario.

Variaveis de ambiente reconhecidas:

- ``CLAUDE_AUDIT_LOCAL_LOG``: override do path do sessions.log (default:
  ``~/.claude/iris/audit/sessions.log``).
- ``CLAUDE_PID``: PID logico da sessao Claude. Default: ``os.getppid()``.
- ``HOSTNAME``: ignorado; usa ``socket.gethostname().split('.')[0]`` para
  bater com ``hostname -s`` do bash.
"""
from __future__ import annotations

import contextlib
import datetime
import hashlib
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

MAX_INPUT = 4096


def _local_log_path() -> str:
    override = os.environ.get("CLAUDE_AUDIT_LOCAL_LOG")
    if override:
        return override
    return str(Path.home() / ".claude" / "iris" / "audit" / "sessions.log")


def _hostname_short() -> str:
    # Equivalente a `hostname -s 2>/dev/null || hostname` do bash.
    try:
        return socket.gethostname().split(".")[0]
    except Exception:
        return "localhost"


def _pid() -> str:
    val = os.environ.get("CLAUDE_PID")
    if val:
        return val
    # Sem CLAUDE_PID -> PPID. No bash: ${CLAUDE_PID:-$PPID}.
    return str(os.getppid())


def _summarize(t_name: str, t_input: dict) -> str:
    """Resumo legivel por tipo de tool. Mantem ordem do bash legado."""
    if t_name == "Bash":
        return f'cmd={t_input.get("command", "")!r}'
    if t_name in ("Read", "Edit", "Write"):
        return f'path={t_input.get("file_path", "")!r}'
    if t_name == "WebFetch":
        return f'url={t_input.get("url", "")!r}'
    if t_name == "WebSearch":
        return f'query={t_input.get("query", "")!r}'
    if t_name == "Skill":
        return f'skill={t_input.get("skill", "")!r}'
    if t_name == "Agent":
        return (
            f'subagent={t_input.get("subagent_type", "")!r} '
            f'desc={t_input.get("description", "")!r}'
        )
    return ""


def _build_messages(payload: dict) -> tuple[str, str]:
    """Monta as duas mensagens (full + meta-only) a partir do payload.

    Retorna ``(full_msg, meta_msg)`` — meta_msg ja inclui o ``\\n`` final
    para append direto no LOCAL_LOG. full_msg vai como argumento do
    ``logger`` (sem trailing newline; logger adiciona).
    """
    session_id = payload.get("session_id", "")
    tool_name = payload.get("tool_name", "?")
    tool_input = payload.get("tool_input", {}) or {}
    tool_resp = payload.get("tool_response", {}) or {}
    cwd = payload.get("cwd", "")
    tool_use_id = payload.get("tool_use_id", "")
    duration_ms = payload.get("duration_ms", 0)
    perm_mode = payload.get("permission_mode", "")

    input_serialized = json.dumps(tool_input, sort_keys=True, ensure_ascii=False)
    input_bytes = len(input_serialized.encode("utf-8"))
    input_sha = hashlib.sha256(input_serialized.encode("utf-8")).hexdigest()[:16]

    status = "success"
    if isinstance(tool_resp, dict) and (tool_resp.get("is_error") or tool_resp.get("error")):
        status = "error"

    try:
        output_bytes = len(json.dumps(tool_resp, ensure_ascii=False).encode("utf-8"))
    except Exception:
        output_bytes = 0

    summary = _summarize(tool_name, tool_input)
    if len(summary.encode("utf-8")) > MAX_INPUT:
        summary = summary[:MAX_INPUT] + "...[truncated]"

    ts = datetime.datetime.now().astimezone().isoformat(timespec="milliseconds")
    hostname = _hostname_short()
    pid = _pid()

    sd_parts = [
        f'session="{session_id}"',
        f'tool="{tool_name}"',
        f'tool_use_id="{tool_use_id}"',
        f'status="{status}"',
        f'duration_ms="{duration_ms}"',
        f'perm_mode="{perm_mode}"',
        f'input_sha="{input_sha}"',
        f'input_bytes="{input_bytes}"',
        f'output_bytes="{output_bytes}"',
    ]
    if tool_name == "Agent":
        subagent_type = (tool_input.get("subagent_type") or "").replace('"', "'")
        sd_parts.append(f'subagent_type="{subagent_type}"')

    sd = "[audit@iris " + " ".join(sd_parts) + "]"

    full_msg = (
        f"<134>1 {ts} {hostname} claude-code {pid} TOOLCALL {sd} "
        f"cwd={cwd!r} {summary}"
    )
    meta_msg = f"<134>1 {ts} {hostname} claude-code {pid} TOOLCALL {sd}\n"
    return full_msg, meta_msg


def _emit_logger(full_msg: str) -> None:
    """Despacha pro syslog via /usr/bin/logger. Fail-silent."""
    with contextlib.suppress(Exception):
        subprocess.run(
            ["logger", "-t", "claude-audit", "-p", "local0.info", "--", full_msg],
            timeout=2,
            check=False,
        )


def _append_local(meta_msg: str, local_log: str) -> None:
    """Append direto no LOCAL_LOG. Fail-silent."""
    with contextlib.suppress(Exception), open(local_log, "a", encoding="utf-8") as f:
        f.write(meta_msg)


def main() -> int:
    """Entry point. Le stdin (JSON), emite as duas mensagens, sai 0."""
    try:
        raw = sys.stdin.read()
    except Exception:
        return 0
    if not raw:
        return 0
    try:
        payload = json.loads(raw)
    except Exception:
        return 0

    try:
        full_msg, meta_msg = _build_messages(payload)
    except Exception:
        return 0

    _emit_logger(full_msg)
    _append_local(meta_msg, _local_log_path())
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
