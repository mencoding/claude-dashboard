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
    # Snapshot do `message.usage` da ÚLTIMA entrada assistant vista.
    # Aproxima o "contexto ativo" da sessão agora — o que o harness do
    # Claude Code envia como input na próxima chamada (input_tokens +
    # cache_read_input_tokens) é o tamanho real do contexto em uso.
    last_usage: Usage | None = None

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

    @property
    def active_context_tokens(self) -> int:
        """Tamanho aproximado do contexto ativo: input + cache_read do último turno.

        O harness envia, a cada chamada, `input_tokens` novos + todos os
        tokens de `cache_read` (reaproveitados). Esse somatório
        representa o tamanho efetivo do prompt que o modelo recebe —
        ou seja, o contexto em uso. Entradas de cache_creation contam
        como parte do prompt quando escritas, mas como leitura em
        turnos seguintes; usar cache_read+input cobre o caso estável.
        """
        if self.last_usage is None:
            return 0
        return self.last_usage.input_tokens + self.last_usage.cache_read

    @property
    def tokens_per_turn(self) -> float:
        """Output médio por mensagem assistant (0 se não houver msgs)."""
        if self.messages_assistant == 0:
            return 0.0
        total_out = sum(u.output_tokens for u in self.usage_by_model.values())
        return total_out / self.messages_assistant
