"""Drill-down parcial via audit metadata (#55).

Quando o transcript JSONL nao esta disponivel localmente (sessao rodou
em outra maquina e foi syncada apenas via ~/.claude/iris/audit/sessions.log),
ainda da pra reconstruir um quadro mais limitado da sessao a partir do
metadata: total de tool calls, top tools, taxa de erro, duracao.

Sem timeline de turnos, sem custos por turno, sem usage_by_model — esses
dados so existem no JSONL e o audit nao captura.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from claude_dash.audit.models import AuditEntry
from claude_dash.audit.parser import parse_line


@dataclass(slots=True)
class PartialSessionStats:
    """Subset de SessionStats extraivel apenas do audit log.

    Atributos populados a partir das entries da sessions.log que casam
    com `session_id`. ``hostname`` e' o de qualquer entry (assumido
    consistente — uma sessao roda numa maquina so).
    """

    session_id: str
    hostname: str = ""
    entries: list[AuditEntry] = field(default_factory=list)

    @property
    def total_calls(self) -> int:
        return len(self.entries)

    @property
    def first_ts(self) -> datetime | None:
        return self.entries[0].timestamp if self.entries else None

    @property
    def last_ts(self) -> datetime | None:
        return self.entries[-1].timestamp if self.entries else None

    @property
    def duration_ms(self) -> int:
        if not self.entries or len(self.entries) < 2:
            return 0
        delta = self.last_ts - self.first_ts  # type: ignore[operator]
        return int(delta.total_seconds() * 1000)

    @property
    def error_count(self) -> int:
        return sum(1 for e in self.entries if e.status == "error")

    @property
    def error_rate(self) -> float:
        if not self.entries:
            return 0.0
        return self.error_count / len(self.entries)

    @property
    def top_tools(self) -> list[tuple[str, int]]:
        """Top 8 tools por contagem, ordem decrescente."""
        c: Counter[str] = Counter(e.tool for e in self.entries)
        return c.most_common(8)


def build_partial_stats_from_audit(
    session_id: str,
    *,
    audit_log_path: Path | None = None,
) -> PartialSessionStats | None:
    """Varre `audit_log_path` e devolve `PartialSessionStats` agregado.

    Retorna None quando o arquivo nao existe ou nenhuma entry casa com
    `session_id` — caller distingue "nao tem dado nenhum sobre essa sessao"
    de "tem metadata, sem JSONL".

    `session_id` aceita prefix-match (mesmo padrao do `find_transcript_for_session`).
    """
    if audit_log_path is None:
        audit_log_path = Path.home() / ".claude" / "iris" / "audit" / "sessions.log"

    if not audit_log_path.is_file():
        return None

    matched: list[AuditEntry] = []
    hostname = ""
    try:
        with audit_log_path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                # Fast-path: skip linhas que nao mencionam o prefix
                # antes de invocar regex caro.
                if session_id not in line:
                    continue
                entry = parse_line(line)
                if entry is None:
                    continue
                if not entry.session_id.startswith(session_id):
                    continue
                matched.append(entry)
                if not hostname and entry.hostname:
                    hostname = entry.hostname
    except OSError:
        return None

    if not matched:
        return None

    # Garante ordem temporal — sessions.log e' append-only mas defensivo
    # custa pouco (n tipico < 1000 por sessao).
    matched.sort(key=lambda e: e.timestamp)

    return PartialSessionStats(
        session_id=matched[0].session_id,
        hostname=hostname,
        entries=matched,
    )
