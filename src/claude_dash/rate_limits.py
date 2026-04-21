"""Leitura dos snapshots de rate_limits capturados pelo hook opcional.

Se o hook `claude-dash-rate-limit-capture` está instalado (ver
`rate_limit_capture.py`), este módulo lê os arquivos JSON por sessão
que ele grava em /tmp e expõe uma visão consolidada para views e MCP.

Se o hook NÃO está instalado, todas as funções retornam vazio —
graceful degradation, o dashboard continua funcional sem rate limits.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from claude_dash.rate_limit_capture import CAPTURE_DIR


# Quanto tempo um snapshot é considerado "fresco". Claude Code atualiza
# o statusline a cada interação — se passa de 15 min sem update, o
# número provavelmente está desatualizado (sessão idle ou terminada).
FRESHNESS_MS = 15 * 60 * 1000


@dataclass(slots=True)
class RateLimitSnapshot:
    """Snapshot mais recente de rate limits de uma sessão."""

    session_id: str
    captured_at_ms: int
    five_hour_pct: float         # 0..100
    five_hour_resets_at_ms: int  # epoch ms; 0 se desconhecido
    seven_day_pct: float
    seven_day_resets_at_ms: int
    model_id: str | None = None

    @property
    def age_ms(self) -> int:
        return int(datetime.now().timestamp() * 1000) - self.captured_at_ms

    @property
    def is_fresh(self) -> bool:
        return self.age_ms < FRESHNESS_MS

    @property
    def five_hour_resets_in_seconds(self) -> int:
        now_ms = int(datetime.now().timestamp() * 1000)
        return max(0, self.five_hour_resets_at_ms - now_ms) // 1000

    @property
    def seven_day_resets_in_seconds(self) -> int:
        now_ms = int(datetime.now().timestamp() * 1000)
        return max(0, self.seven_day_resets_at_ms - now_ms) // 1000


def _parse_snapshot(data: dict) -> RateLimitSnapshot | None:
    """Deserializa um arquivo JSON gravado pelo capturador."""
    rl = data.get("rate_limits") or {}
    five = rl.get("five_hour") or {}
    seven = rl.get("seven_day") or {}
    sid = data.get("session_id")
    captured = data.get("captured_at_ms")

    if not sid or not captured:
        return None

    # `resets_at` no JSON do Claude Code vem em epoch SEGUNDOS
    def _to_ms(val) -> int:  # noqa: ANN001
        if val is None:
            return 0
        try:
            n = int(val)
        except (TypeError, ValueError):
            return 0
        # Se o número couber em epoch seconds (até 2030), multiplica
        # por 1000; se já é ms (≥10^12), mantém.
        return n * 1000 if n < 10**12 else n

    return RateLimitSnapshot(
        session_id=sid,
        captured_at_ms=int(captured),
        five_hour_pct=float(five.get("used_percentage") or 0),
        five_hour_resets_at_ms=_to_ms(five.get("resets_at")),
        seven_day_pct=float(seven.get("used_percentage") or 0),
        seven_day_resets_at_ms=_to_ms(seven.get("resets_at")),
        model_id=data.get("model_id"),
    )


def read_all(capture_dir: Path = CAPTURE_DIR) -> dict[str, RateLimitSnapshot]:
    """Lê todos os snapshots disponíveis; key = session_id."""
    if not capture_dir.is_dir():
        return {}
    out: dict[str, RateLimitSnapshot] = {}
    for f in capture_dir.glob("*.json"):
        try:
            data = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        snap = _parse_snapshot(data)
        if snap is not None:
            out[snap.session_id] = snap
    return out


def read_for_session(
    session_id: str, capture_dir: Path = CAPTURE_DIR
) -> RateLimitSnapshot | None:
    """Snapshot de uma sessão específica, ou None se não houver captura."""
    path = capture_dir / f"{session_id}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return _parse_snapshot(data)


def global_worst_case(
    capture_dir: Path = CAPTURE_DIR,
) -> RateLimitSnapshot | None:
    """Snapshot "pior caso" entre todas as sessões capturadas.

    Rate limits são por conta (compartilhados entre sessões). O snapshot
    retornado é o que tem o maior percentual entre 5h E 7d — qualquer
    um dos dois perto do throttle é motivo para atenção. Em empate,
    desempate pelo mais recente.

    Retorna None se não há snapshots frescos (<15 min).
    """
    fresh = [s for s in read_all(capture_dir).values() if s.is_fresh]
    if not fresh:
        return None
    # Pior caso = max entre 5h% e 7d% (qualquer um crítico pesa igual),
    # desempate por captured_at_ms descendente
    return max(
        fresh,
        key=lambda s: (max(s.five_hour_pct, s.seven_day_pct), s.captured_at_ms),
    )


def format_reset_delta(seconds: int) -> str:
    """Formato compacto: '3h42m', '42m', '2d3h'. Vazio se <=0."""
    if seconds <= 0:
        return ""
    if seconds >= 86400:
        d, rem = divmod(seconds, 86400)
        return f"{d}d{rem // 3600}h"
    if seconds >= 3600:
        h, rem = divmod(seconds, 3600)
        return f"{h}h{rem // 60}m"
    return f"{seconds // 60}m"
