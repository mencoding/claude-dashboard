"""Dataclasses — modelo de domínio."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class Usage:
    """Tokens agregados, discriminados por tipo.

    Os campos batem 1:1 com o schema `.message.usage` do transcript JSONL.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_1h: int = 0
    cache_creation_5m: int = 0
    cache_read: int = 0

    @property
    def total(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_creation_1h
            + self.cache_creation_5m
            + self.cache_read
        )

    def __iadd__(self, other: Usage) -> Usage:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_creation_1h += other.cache_creation_1h
        self.cache_creation_5m += other.cache_creation_5m
        self.cache_read += other.cache_read
        return self

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_creation_1h=self.cache_creation_1h + other.cache_creation_1h,
            cache_creation_5m=self.cache_creation_5m + other.cache_creation_5m,
            cache_read=self.cache_read + other.cache_read,
        )


@dataclass(slots=True)
class ToolStat:
    """Contagem de chamadas de uma ferramenta dentro de um escopo."""

    name: str
    count: int = 0


@dataclass(slots=True)
class SessionStats:
    """Métricas agregadas de uma sessão (viva ou histórica)."""

    session_id: str
    cwd: Path
    started_at_ms: int                    # epoch ms; 0 se desconhecido
    last_activity_ms: int = 0             # mtime do transcript ou último timestamp
    pid: int | None = None                # None se sessão morta
    alive: bool = False
    version: str = ""
    transcript_path: Path | None = None
    usage_by_model: dict[str, Usage] = field(default_factory=dict)
    tools: dict[str, int] = field(default_factory=dict)
    messages_user: int = 0
    messages_assistant: int = 0
    subagents: int = 0                    # número de transcripts filhos

    @property
    def total_usage(self) -> Usage:
        acc = Usage()
        for u in self.usage_by_model.values():
            acc += u
        return acc

    @property
    def dominant_model(self) -> str | None:
        """Modelo com maior volume de output_tokens (proxy de 'principal')."""
        if not self.usage_by_model:
            return None
        return max(
            self.usage_by_model.items(),
            key=lambda kv: kv[1].output_tokens,
        )[0]
