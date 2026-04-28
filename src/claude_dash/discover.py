"""Descoberta de sessões e transcripts no filesystem do Claude Code."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

CLAUDE_HOME = Path(os.environ.get("CLAUDE_HOME", Path.home() / ".claude"))
SESSIONS_DIR = CLAUDE_HOME / "sessions"
PROJECTS_DIR = CLAUDE_HOME / "projects"


@dataclass(slots=True)
class LiveSession:
    """Metadados de uma sessão atualmente em execução."""

    pid: int
    session_id: str
    cwd: Path
    started_at_ms: int
    version: str
    alive: bool


@dataclass(slots=True)
class TranscriptRef:
    """Referência a um arquivo de transcript (principal ou subagente)."""

    session_id: str
    path: Path
    workspace: str          # diretório do projeto em projects/ (ex: "-home-menzani-claude--iris")
    mtime_ms: int
    is_subagent: bool = False
    parent_session_id: str | None = None


def _proc_alive(pid: int) -> bool:
    """Retorna True se o processo existe (sinal 0 não mata, só verifica)."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Existe mas pertence a outro usuário
        return True
    except OSError:
        return False
    return True


def discover_live_sessions(sessions_dir: Path = SESSIONS_DIR) -> list[LiveSession]:
    """Lê `~/.claude/sessions/*.json` e confere vida com `kill -0`."""
    if not sessions_dir.is_dir():
        return []

    out: list[LiveSession] = []
    for f in sorted(sessions_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            continue

        pid = data.get("pid")
        sid = data.get("sessionId")
        cwd_raw = data.get("cwd", "")
        if not isinstance(pid, int) or not isinstance(sid, str) or not sid:
            continue

        out.append(
            LiveSession(
                pid=pid,
                session_id=sid,
                cwd=Path(cwd_raw) if cwd_raw else Path(),
                started_at_ms=int(data.get("startedAt") or 0),
                version=str(data.get("version") or ""),
                alive=_proc_alive(pid),
            )
        )
    return out


def discover_transcripts(
    projects_dir: Path = PROJECTS_DIR,
    since_ms: int | None = None,
) -> list[TranscriptRef]:
    """Varre `projects/<ws>/*.jsonl` + `<ws>/<sid>/subagents/*.jsonl`.

    Se `since_ms` for dado, filtra por mtime >= since_ms.
    """
    if not projects_dir.is_dir():
        return []

    out: list[TranscriptRef] = []
    for workspace_dir in sorted(projects_dir.iterdir()):
        if not workspace_dir.is_dir():
            continue
        workspace = workspace_dir.name

        # Transcripts principais: projects/<ws>/<sid>.jsonl
        for jsonl in workspace_dir.glob("*.jsonl"):
            mtime_ms = int(jsonl.stat().st_mtime * 1000)
            if since_ms is not None and mtime_ms < since_ms:
                continue
            sid = jsonl.stem
            out.append(
                TranscriptRef(
                    session_id=sid,
                    path=jsonl,
                    workspace=workspace,
                    mtime_ms=mtime_ms,
                )
            )

            # Subagentes: projects/<ws>/<sid>/subagents/agent-*.jsonl
            subagents_dir = workspace_dir / sid / "subagents"
            if subagents_dir.is_dir():
                for sub in subagents_dir.glob("*.jsonl"):
                    sub_mtime = int(sub.stat().st_mtime * 1000)
                    if since_ms is not None and sub_mtime < since_ms:
                        continue
                    out.append(
                        TranscriptRef(
                            session_id=sub.stem,
                            path=sub,
                            workspace=workspace,
                            mtime_ms=sub_mtime,
                            is_subagent=True,
                            parent_session_id=sid,
                        )
                    )

    return out


def find_transcript_for_session(
    session_id: str,
    projects_dir: Path = PROJECTS_DIR,
) -> TranscriptRef | None:
    """Busca o transcript principal de uma sessão.

    Aceita prefixo: se `session_id` bater como prefixo de um UUID
    único, retorna esse; se bater em vários, retorna None.
    """
    all_refs = discover_transcripts(projects_dir)
    # Só transcripts principais (não subagentes)
    candidates = [
        t for t in all_refs
        if not t.is_subagent and t.session_id.startswith(session_id)
    ]
    if len(candidates) != 1:
        return None
    return candidates[0]


def subagents_of(session_id: str, projects_dir: Path = PROJECTS_DIR) -> list[TranscriptRef]:
    """Retorna todos os transcripts de subagentes de uma sessão."""
    return [
        t for t in discover_transcripts(projects_dir)
        if t.is_subagent and t.parent_session_id == session_id
    ]
