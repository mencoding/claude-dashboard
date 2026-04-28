"""Notificacoes push para status=error no audit log (#37).

Vigia entries novas que chegam pelo tail incremental e dispara alertas
quando `status="error"` aparece. Anti-flood via dedup por `tool_use_id`
em janela curta (default 60s).

Niveis de alerta (env var ``CLAUDE_DASH_AUDIT_ALERT_LEVEL``):

- ``none``: zero notificacoes (default — preserva comportamento antigo)
- ``toast``: notificacao Textual in-app (aparece sobre a TUI)
- ``sound``: ``notify-send`` libnotify (toast do sistema + som default)
- ``both``: toast + sound

`notify-send` ausente do PATH cai silencioso — graceful degradation pra
ambientes sem libnotify. Logica e' pura no nivel do helper; integracao
com Textual fica em ``views/tui.py``.
"""
from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import time
from collections.abc import Callable
from typing import Literal

from claude_dash.audit.models import AuditEntry

AlertLevel = Literal["none", "toast", "sound", "both"]
_VALID_LEVELS: frozenset[str] = frozenset(("none", "toast", "sound", "both"))

# Anti-flood: nao re-notifica o mesmo `tool_use_id` por essa janela.
DEDUP_WINDOW_SEC: float = 60.0

# Env var que controla o nivel.
ENV_LEVEL_VAR: str = "CLAUDE_DASH_AUDIT_ALERT_LEVEL"


def resolve_alert_level(env: dict[str, str] | None = None) -> AlertLevel:
    """Le `CLAUDE_DASH_AUDIT_ALERT_LEVEL` do environment.

    Valor invalido ou ausente -> 'none' (silencioso, sem warning na
    saida pra nao poluir startup da TUI).
    """
    raw = (env or os.environ).get(ENV_LEVEL_VAR, "").strip().lower()
    if raw in _VALID_LEVELS:
        return raw  # type: ignore[return-value]
    return "none"


def _format_alert(entry: AuditEntry) -> str:
    """Formata mensagem do alerta — curta, com info acionavel."""
    sub = f"({entry.subagent_type})" if entry.subagent_type else ""
    sess_short = entry.session_id[:8] if entry.session_id else "-"
    return (
        f"Tool error: {entry.tool}{sub} "
        f"em {sess_short} ({entry.duration_ms}ms)"
    )


def _libnotify_send(message: str) -> None:
    """Dispara `notify-send` libnotify se disponivel; silencioso se ausente.

    Nao espera retorno do subprocess (fire-and-forget) pra nao bloquear
    o tick da TUI. Falhas de IPC com daemon de notificacao sao silenciadas
    pelo subprocess.DEVNULL.
    """
    if shutil.which("notify-send") is None:
        return
    with contextlib.suppress(OSError):
        subprocess.Popen(
            [
                "notify-send",
                "--app-name=claude-dash",
                "--urgency=normal",
                "claude-dash",
                message,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


class ErrorAlertEmitter:
    """Emite alertas de tool error com dedup e nivel configuravel.

    Uso tipico:
        emitter = ErrorAlertEmitter(level="toast", textual_notify=app.notify)
        emitter.emit(new_entries)
    """

    def __init__(
        self,
        level: AlertLevel = "none",
        *,
        textual_notify: Callable[[str], None] | None = None,
        libnotify: Callable[[str], None] = _libnotify_send,
        dedup_window_sec: float = DEDUP_WINDOW_SEC,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.level: AlertLevel = level
        self._textual_notify = textual_notify
        self._libnotify = libnotify
        self._dedup_window = dedup_window_sec
        self._clock = clock
        # tool_use_id -> timestamp do ultimo alerta. Expurgado on-demand
        # em emit() — nao precisa de timer dedicado.
        self._last_notified: dict[str, float] = {}

    def emit(self, new_entries: list[AuditEntry]) -> int:
        """Inspeciona `new_entries` e dispara alertas pra erros.

        Retorna o numero de alertas efetivamente disparados (excluindo
        deduped). Nivel `none` -> zero alertas, sempre.
        """
        if self.level == "none" or not new_entries:
            return 0

        now = self._clock()
        # Expurga entries fora da janela de dedup.
        self._last_notified = {
            tid: ts
            for tid, ts in self._last_notified.items()
            if now - ts < self._dedup_window
        }

        emitted = 0
        for entry in new_entries:
            if entry.status != "error":
                continue
            key = entry.tool_use_id or entry.session_id
            if key in self._last_notified:
                continue
            self._last_notified[key] = now
            self._dispatch(_format_alert(entry))
            emitted += 1
        return emitted

    def _dispatch(self, message: str) -> None:
        """Dispara nos canais ativos conforme `self.level`."""
        if self.level in ("toast", "both") and self._textual_notify is not None:
            with contextlib.suppress(Exception):
                self._textual_notify(message)
        if self.level in ("sound", "both"):
            self._libnotify(message)
