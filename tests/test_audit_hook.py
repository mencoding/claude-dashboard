"""Testes do hook ``claude_dash.audit.hook``.

Cobre:
- Resumo por tool (Bash/Read/Edit/Write/WebFetch/WebSearch/Skill/Agent).
- STRUCTURED-DATA RFC 5424 com campos esperados; subagent_type so em Agent.
- Status ``error`` quando ``tool_response.is_error``.
- input_sha estavel (hash determinico).
- LOCAL_LOG via env var.
- Fail-silent quando logger ou disco falham.
- Paridade byte-a-byte com ``~/.claude/iris/hooks/audit-tool.sh`` (apenas
  se o script bash existir na maquina; caso contrario o teste e skipped).
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

from claude_dash.audit import hook as ah

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

BASH_PAYLOAD = {
    "session_id": "00000000-1111-2222-3333-444444444444",
    "tool_use_id": "toolu_abc123",
    "tool_name": "Bash",
    "tool_input": {"command": "ls -la"},
    "tool_response": {"output": "total 0", "is_error": False},
    "cwd": "/tmp",
    "duration_ms": 42,
    "permission_mode": "default",
    "transcript_path": "/tmp/x.jsonl",
    "hook_event_name": "PostToolUse",
}

READ_PAYLOAD = {
    "session_id": "s1",
    "tool_use_id": "t1",
    "tool_name": "Read",
    "tool_input": {"file_path": "/etc/hostname"},
    "tool_response": {"output": "host"},
    "cwd": "/tmp",
    "duration_ms": 1,
    "permission_mode": "default",
}

WEBFETCH_PAYLOAD = {
    "session_id": "s2",
    "tool_use_id": "t2",
    "tool_name": "WebFetch",
    "tool_input": {"url": "https://example.com/x", "prompt": "..."},
    "tool_response": {"output": "..."},
    "cwd": "/tmp",
    "duration_ms": 100,
    "permission_mode": "default",
}

AGENT_PAYLOAD = {
    "session_id": "s3",
    "tool_use_id": "t3",
    "tool_name": "Agent",
    "tool_input": {
        "subagent_type": "general-purpose",
        "description": "exemplo",
        "prompt": "faca x",
    },
    "tool_response": {"output": "..."},
    "cwd": "/tmp",
    "duration_ms": 5000,
    "permission_mode": "default",
}

ERROR_PAYLOAD = {
    "session_id": "s4",
    "tool_use_id": "t4",
    "tool_name": "Bash",
    "tool_input": {"command": "false"},
    "tool_response": {"output": "", "is_error": True},
    "cwd": "/tmp",
    "duration_ms": 1,
    "permission_mode": "default",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build(payload: dict) -> tuple[str, str]:
    return ah._build_messages(payload)


def _sd(full_msg: str) -> str:
    m = re.search(r"\[audit@iris ([^\]]+)\]", full_msg)
    assert m, f"sem SD em {full_msg!r}"
    return m.group(1)


def _kv(sd_inner: str) -> dict[str, str]:
    return dict(re.findall(r'(\w+)="([^"]*)"', sd_inner))


# ---------------------------------------------------------------------------
# Resumos
# ---------------------------------------------------------------------------

def test_summarize_bash():
    full, _ = _build(BASH_PAYLOAD)
    assert "cmd='ls -la'" in full


def test_summarize_read():
    full, _ = _build(READ_PAYLOAD)
    assert "path='/etc/hostname'" in full


def test_summarize_edit():
    p = dict(READ_PAYLOAD)
    p["tool_name"] = "Edit"
    full, _ = _build(p)
    assert "path='/etc/hostname'" in full


def test_summarize_write():
    p = dict(READ_PAYLOAD)
    p["tool_name"] = "Write"
    full, _ = _build(p)
    assert "path='/etc/hostname'" in full


def test_summarize_webfetch():
    full, _ = _build(WEBFETCH_PAYLOAD)
    assert "url='https://example.com/x'" in full


def test_summarize_websearch():
    p = dict(WEBFETCH_PAYLOAD)
    p["tool_name"] = "WebSearch"
    p["tool_input"] = {"query": "ifsp licitacao"}
    full, _ = _build(p)
    assert "query='ifsp licitacao'" in full


def test_summarize_skill():
    p = dict(BASH_PAYLOAD)
    p["tool_name"] = "Skill"
    p["tool_input"] = {"skill": "wrapup"}
    full, _ = _build(p)
    assert "skill='wrapup'" in full


def test_summarize_agent_subagent_in_sd():
    full, _ = _build(AGENT_PAYLOAD)
    sd = _kv(_sd(full))
    assert sd["subagent_type"] == "general-purpose"
    assert "subagent='general-purpose'" in full
    assert "desc='exemplo'" in full


def test_subagent_type_only_for_agent():
    full, _ = _build(BASH_PAYLOAD)
    sd = _kv(_sd(full))
    assert "subagent_type" not in sd


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def test_status_error_when_is_error():
    full, _ = _build(ERROR_PAYLOAD)
    sd = _kv(_sd(full))
    assert sd["status"] == "error"


def test_status_success_default():
    full, _ = _build(BASH_PAYLOAD)
    sd = _kv(_sd(full))
    assert sd["status"] == "success"


# ---------------------------------------------------------------------------
# input_sha
# ---------------------------------------------------------------------------

def test_input_sha_stable_and_16_hex():
    sd1 = _kv(_sd(_build(BASH_PAYLOAD)[0]))
    sd2 = _kv(_sd(_build(BASH_PAYLOAD)[0]))
    assert sd1["input_sha"] == sd2["input_sha"]
    assert re.fullmatch(r"[0-9a-f]{16}", sd1["input_sha"])


def test_input_sha_changes_with_input():
    p2 = dict(BASH_PAYLOAD)
    p2["tool_input"] = {"command": "ls -la /"}
    sd_a = _kv(_sd(_build(BASH_PAYLOAD)[0]))
    sd_b = _kv(_sd(_build(p2)[0]))
    assert sd_a["input_sha"] != sd_b["input_sha"]


# ---------------------------------------------------------------------------
# Estrutura geral RFC 5424
# ---------------------------------------------------------------------------

def test_full_msg_starts_with_pri_version():
    full, _ = _build(BASH_PAYLOAD)
    assert full.startswith("<134>1 ")


def test_meta_msg_ends_with_newline_and_has_no_summary():
    _full, meta = _build(BASH_PAYLOAD)
    assert meta.endswith("\n")
    # meta nao tem cwd/cmd, so o SD
    assert "cmd=" not in meta
    assert "cwd=" not in meta


def test_hostname_short():
    full, _ = _build(BASH_PAYLOAD)
    expected = socket.gethostname().split(".")[0]
    assert f" {expected} claude-code " in full


# ---------------------------------------------------------------------------
# Local log via env
# ---------------------------------------------------------------------------

def test_local_log_env_override(tmp_path, monkeypatch):
    target = tmp_path / "custom.log"
    monkeypatch.setenv("CLAUDE_AUDIT_LOCAL_LOG", str(target))
    assert ah._local_log_path() == str(target)


def test_local_log_default(monkeypatch):
    monkeypatch.delenv("CLAUDE_AUDIT_LOCAL_LOG", raising=False)
    assert ah._local_log_path().endswith("/.claude/iris/audit/sessions.log")


# ---------------------------------------------------------------------------
# main() — fluxo completo
# ---------------------------------------------------------------------------

def test_main_writes_local_log_and_invokes_logger(tmp_path, monkeypatch, capsys):
    target = tmp_path / "sessions.log"
    monkeypatch.setenv("CLAUDE_AUDIT_LOCAL_LOG", str(target))
    monkeypatch.setattr(sys, "stdin", _StringStdin(json.dumps(BASH_PAYLOAD)))
    calls = []

    class _FakeResult:
        returncode = 0

    def fake_run(*args, **kwargs):
        calls.append(args[0])
        return _FakeResult()

    monkeypatch.setattr(subprocess, "run", fake_run)
    rc = ah.main()
    assert rc == 0
    assert target.is_file()
    contents = target.read_text()
    assert contents.startswith("<134>1 ")
    assert calls and calls[0][:3] == ["logger", "-t", "claude-audit"]


def test_main_fail_silent_on_logger_failure(tmp_path, monkeypatch):
    target = tmp_path / "sessions.log"
    monkeypatch.setenv("CLAUDE_AUDIT_LOCAL_LOG", str(target))
    monkeypatch.setattr(sys, "stdin", _StringStdin(json.dumps(BASH_PAYLOAD)))
    monkeypatch.setattr(subprocess, "run", mock.Mock(side_effect=OSError("logger ausente")))
    rc = ah.main()
    assert rc == 0
    # LOCAL_LOG ainda assim foi escrito
    assert target.read_text().startswith("<134>1 ")


def test_main_fail_silent_on_local_log_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_AUDIT_LOCAL_LOG", "/proc/1/this-cannot-be-written")
    monkeypatch.setattr(sys, "stdin", _StringStdin(json.dumps(BASH_PAYLOAD)))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: None)
    rc = ah.main()
    assert rc == 0


def test_main_fail_silent_on_malformed_json(monkeypatch):
    monkeypatch.setattr(sys, "stdin", _StringStdin("not json"))
    rc = ah.main()
    assert rc == 0


def test_main_fail_silent_on_empty_stdin(monkeypatch):
    monkeypatch.setattr(sys, "stdin", _StringStdin(""))
    rc = ah.main()
    assert rc == 0


# ---------------------------------------------------------------------------
# Paridade byte-a-byte com bash legado
# ---------------------------------------------------------------------------

LEGACY_BASH = Path.home() / ".claude" / "iris" / "hooks" / "audit-tool.sh"


def _normalize_dynamic(line: str) -> str:
    """Remove timestamp e PID variaveis; mantem tudo o resto."""
    # Timestamp ISO com offset tz (precisao ms)
    line = re.sub(
        r"<134>1 \S+ ",
        "<134>1 <TS> ",
        line,
    )
    # PID apos hostname/claude-code
    line = re.sub(
        r"(claude-code) \d+ ",
        r"\1 <PID> ",
        line,
    )
    return line


@pytest.mark.skipif(not LEGACY_BASH.is_file(), reason="audit-tool.sh legado nao presente")
def test_byte_identical_to_legacy_bash(tmp_path, monkeypatch):
    """Roda o bash legado e o hook novo com o mesmo payload e LOCAL_LOG;
    compara as linhas escritas (apos normalizar timestamp e PID).
    """
    py_log = tmp_path / "py.log"

    payload = json.dumps(BASH_PAYLOAD)

    # Bash legado: env var AUDIT_LOCAL_LOG (interno) nao e exposta;
    # ele monta o path via $HOME. Para isolar, simulamos $HOME.
    fake_home = tmp_path / "home"
    (fake_home / ".claude" / "iris" / "audit").mkdir(parents=True)
    (fake_home / ".claude" / "iris" / "audit" / "sessions.log").touch()
    bash_env = dict(os.environ, HOME=str(fake_home), CLAUDE_PID="99999")
    # Mock logger -> /dev/null (capturamos so o LOCAL_LOG)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "logger").write_text("#!/usr/bin/env bash\nexit 0\n")
    os.chmod(fake_bin / "logger", 0o755)
    bash_env["PATH"] = f"{fake_bin}:{bash_env.get('PATH','')}"
    subprocess.run(
        ["bash", str(LEGACY_BASH)],
        input=payload, text=True, env=bash_env, check=True, timeout=10,
    )
    bash_line = (fake_home / ".claude" / "iris" / "audit" / "sessions.log").read_text()

    # Hook novo
    monkeypatch.setenv("CLAUDE_AUDIT_LOCAL_LOG", str(py_log))
    monkeypatch.setenv("CLAUDE_PID", "99999")
    monkeypatch.setattr(sys, "stdin", _StringStdin(payload))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: None)
    rc = ah.main()
    assert rc == 0
    py_line = py_log.read_text()

    assert _normalize_dynamic(bash_line) == _normalize_dynamic(py_line), (
        f"\n--- BASH ---\n{bash_line}\n--- PY ---\n{py_line}\n"
    )


# ---------------------------------------------------------------------------
# Util de fixture
# ---------------------------------------------------------------------------

class _StringStdin:
    """Stdin fake; aceita read()."""
    def __init__(self, text: str) -> None:
        self._text = text
    def read(self) -> str:
        return self._text
