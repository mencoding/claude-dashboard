"""Cache incremental de agregação por transcript.

O cache guarda quatro informações de identidade/estado: o `inode` e
`mtime_ms` do transcript quando o estado foi gerado, o `byte_offset`
até onde já parseamos e o estado agregado serializado. O `inode` é
checado primeiro: se mudou, o arquivo foi recriado (truncate+rewrite
ou renomeado) e invalidamos tudo. Se inode bate e mtime também,
reaproveita direto. Se inode bate mas mtime mudou e `file_size >=
byte_offset`, continua do offset (transcript é append-only). Se
`file_size < byte_offset`, invalida.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path

from claude_dash.models import SessionStats, Usage


CACHE_DIR = Path(os.environ.get("CLAUDE_DASH_CACHE", Path.home() / ".cache" / "claude-dash"))


def cache_path_for(session_id: str, cache_dir: Path = CACHE_DIR) -> Path:
    """Caminho canônico do cache de uma sessão."""
    return cache_dir / f"{session_id}.json"


def load(session_id: str, cache_dir: Path = CACHE_DIR) -> dict | None:
    """Carrega o cache; retorna None se ausente ou corrompido."""
    path = cache_path_for(session_id, cache_dir)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def save(
    session_id: str,
    mtime_ms: int,
    byte_offset: int,
    stats: SessionStats,
    cache_dir: Path = CACHE_DIR,
    inode: int = 0,
) -> None:
    """Persiste o cache. Cria cache_dir se necessário."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_path_for(session_id, cache_dir)
    payload = {
        "inode": int(inode),
        "mtime_ms": mtime_ms,
        "byte_offset": byte_offset,
        "stats": _serialize_stats(stats),
    }
    # Write atomically: write to tmp then rename
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":")))
    tmp.replace(path)


def _serialize_stats(s: SessionStats) -> dict:
    """SessionStats → dict JSON-serializable."""
    d = asdict(s)
    # Path não é JSON-serializable por padrão
    d["cwd"] = str(s.cwd)
    d["transcript_path"] = str(s.transcript_path) if s.transcript_path else None
    # dicts com Usage dentro: asdict já transforma Usage em dict
    return d


def deserialize_stats(d: dict) -> SessionStats:
    """dict (do cache) → SessionStats."""
    usage_by_model = {
        model: Usage(**u_dict)
        for model, u_dict in (d.get("usage_by_model") or {}).items()
    }
    transcript_path = d.get("transcript_path")
    last_usage_dict = d.get("last_usage")
    last_usage = Usage(**last_usage_dict) if last_usage_dict else None
    return SessionStats(
        session_id=d["session_id"],
        cwd=Path(d.get("cwd") or ""),
        started_at_ms=int(d.get("started_at_ms") or 0),
        last_activity_ms=int(d.get("last_activity_ms") or 0),
        pid=d.get("pid"),
        alive=bool(d.get("alive", False)),
        version=str(d.get("version") or ""),
        transcript_path=Path(transcript_path) if transcript_path else None,
        usage_by_model=usage_by_model,
        tools=dict(d.get("tools") or {}),
        messages_user=int(d.get("messages_user") or 0),
        messages_assistant=int(d.get("messages_assistant") or 0),
        subagents=int(d.get("subagents") or 0),
        last_usage=last_usage,
    )


def clear(session_id: str, cache_dir: Path = CACHE_DIR) -> None:
    """Remove o cache de uma sessão (usado em testes e debug)."""
    path = cache_path_for(session_id, cache_dir)
    if path.is_file():
        path.unlink()
