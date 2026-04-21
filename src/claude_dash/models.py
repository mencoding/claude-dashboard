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
class Turn:
    """Um turno assistant numa sessão — unidade natural para drill-down.

    Diferente de `SessionStats` (agregado) ou `ToolUsageStats`
    (cross-session), um `Turn` representa *uma* resposta do modelo:
    tokens consumidos nessa resposta específica e tools que ela
    disparou. A ordem dentro da sessão é dada por `index` (0-based).
    """

    index: int
    timestamp_ms: int
    model: str
    usage: Usage
    tools_called: list[str] = field(default_factory=list)

    @property
    def cost_estimate_key(self) -> str:
        """Chave normalizada do modelo para lookup em PRICING."""
        from claude_dash.pricing import normalize_model
        return normalize_model(self.model)


@dataclass(slots=True)
class ToolUsageStats:
    """Estatísticas globais de uso de uma ferramenta numa janela temporal.

    Diferente de `ToolStat` (contagem simples), isto agrega por sessão
    e por hora do dia, permitindo análises como "qual hora do dia
    concentra mais invocações de Bash?" ou "em quantas sessões
    distintas essa tool foi usada?".
    """

    name: str
    total_count: int = 0
    # Contagens discriminadas por sessionId (apenas transcripts
    # principais — subagents contam para a sessão-pai)
    count_by_session: dict[str, int] = field(default_factory=dict)
    # Histograma: 24 buckets, um por hora local (0..23)
    hours_histogram: list[int] = field(default_factory=lambda: [0] * 24)

    @property
    def session_count(self) -> int:
        return len(self.count_by_session)

    @property
    def peak_hour(self) -> int | None:
        """Hora (0..23) com mais invocações, ou None se nenhuma."""
        if self.total_count == 0:
            return None
        return max(range(24), key=lambda h: self.hours_histogram[h])


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
