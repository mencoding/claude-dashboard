"""Testes do emitter de alertas pra tool errors (#37)."""
from __future__ import annotations

from datetime import UTC, datetime
from unittest import mock

from claude_dash.audit.alerts import (
    DEDUP_WINDOW_SEC,
    ENV_LEVEL_VAR,
    ErrorAlertEmitter,
    resolve_alert_level,
)
from claude_dash.audit.models import AuditEntry


def _entry(
    *,
    status: str = "error",
    tool: str = "Bash",
    tool_use_id: str = "call-1",
    session_id: str = "0efd3cf4-96ef-41b7-9d41-f91ec539becd",
) -> AuditEntry:
    return AuditEntry(
        timestamp=datetime(2026, 4, 28, 0, 0, 0, tzinfo=UTC),
        hostname="host",
        pid="1",
        session_id=session_id,
        tool=tool,
        tool_use_id=tool_use_id,
        status=status,
        duration_ms=100,
        perm_mode="auto",
        input_sha="0",
        input_bytes=10,
        output_bytes=20,
    )


# ---- resolve_alert_level --------------------------------------------


def test_resolve_default_eh_none() -> None:
    assert resolve_alert_level(env={}) == "none"


def test_resolve_aceita_valores_validos() -> None:
    for level in ("none", "toast", "sound", "both"):
        out = resolve_alert_level(env={ENV_LEVEL_VAR: level})
        assert out == level


def test_resolve_normaliza_case() -> None:
    assert resolve_alert_level(env={ENV_LEVEL_VAR: "TOAST"}) == "toast"
    assert resolve_alert_level(env={ENV_LEVEL_VAR: "  Both  "}) == "both"


def test_resolve_invalido_cai_em_none() -> None:
    assert resolve_alert_level(env={ENV_LEVEL_VAR: "loud"}) == "none"
    assert resolve_alert_level(env={ENV_LEVEL_VAR: ""}) == "none"


# ---- ErrorAlertEmitter ----------------------------------------------


def test_level_none_nao_dispara_nada() -> None:
    notify = mock.Mock()
    libnotify = mock.Mock()
    em = ErrorAlertEmitter(
        level="none", textual_notify=notify, libnotify=libnotify,
    )
    n = em.emit([_entry(status="error"), _entry(status="error", tool_use_id="call-2")])
    assert n == 0
    notify.assert_not_called()
    libnotify.assert_not_called()


def test_ignora_entries_success() -> None:
    notify = mock.Mock()
    libnotify = mock.Mock()
    em = ErrorAlertEmitter(level="both", textual_notify=notify, libnotify=libnotify)
    em.emit([_entry(status="success"), _entry(status="success", tool_use_id="x")])
    notify.assert_not_called()
    libnotify.assert_not_called()


def test_toast_dispara_so_textual() -> None:
    notify = mock.Mock()
    libnotify = mock.Mock()
    em = ErrorAlertEmitter(level="toast", textual_notify=notify, libnotify=libnotify)
    n = em.emit([_entry(tool_use_id="call-1")])
    assert n == 1
    notify.assert_called_once()
    libnotify.assert_not_called()


def test_sound_dispara_so_libnotify() -> None:
    notify = mock.Mock()
    libnotify = mock.Mock()
    em = ErrorAlertEmitter(level="sound", textual_notify=notify, libnotify=libnotify)
    em.emit([_entry(tool_use_id="call-1")])
    notify.assert_not_called()
    libnotify.assert_called_once()


def test_both_dispara_ambos() -> None:
    notify = mock.Mock()
    libnotify = mock.Mock()
    em = ErrorAlertEmitter(level="both", textual_notify=notify, libnotify=libnotify)
    em.emit([_entry(tool_use_id="call-1")])
    notify.assert_called_once()
    libnotify.assert_called_once()


def test_dedup_por_tool_use_id() -> None:
    """Mesmo tool_use_id em ticks consecutivos -> 1 alerta so."""
    notify = mock.Mock()
    libnotify = mock.Mock()
    fake_clock = mock.Mock(return_value=100.0)
    em = ErrorAlertEmitter(
        level="toast", textual_notify=notify, libnotify=libnotify,
        clock=fake_clock,
    )
    # Tick 1: 3 erros, 2 deles com mesmo tool_use_id
    same = _entry(tool_use_id="call-A")
    other = _entry(tool_use_id="call-B")
    n1 = em.emit([same, same, other])
    assert n1 == 2  # call-A vez 1, call-B; call-A vez 2 deduped

    # Tick 2 (10s depois): mesma call-A repete -> ainda dentro da janela
    fake_clock.return_value = 110.0
    n2 = em.emit([same])
    assert n2 == 0


def test_dedup_expira_apos_janela() -> None:
    """Apos DEDUP_WINDOW_SEC, mesmo tool_use_id volta a notificar."""
    notify = mock.Mock()
    fake_clock = mock.Mock(return_value=100.0)
    em = ErrorAlertEmitter(
        level="toast", textual_notify=notify,
        clock=fake_clock,
    )
    same = _entry(tool_use_id="call-A")
    em.emit([same])
    assert notify.call_count == 1

    # Avanca relogio alem da janela de dedup
    fake_clock.return_value = 100.0 + DEDUP_WINDOW_SEC + 1
    em.emit([same])
    assert notify.call_count == 2


def test_fallback_para_session_id_quando_sem_tool_use_id() -> None:
    """Entries antigas (Agent) podem nao ter tool_use_id — usa session_id."""
    notify = mock.Mock()
    em = ErrorAlertEmitter(level="toast", textual_notify=notify)
    e = _entry(tool_use_id="", session_id="abc-123")
    em.emit([e])
    em.emit([e])  # mesma sessao, deve deduplificar
    assert notify.call_count == 1


def test_excecao_no_textual_notify_nao_quebra_emit() -> None:
    """notify falhando (ex.: app fechando) nao deve crashar o tick."""
    def boom(_msg: str) -> None:
        raise RuntimeError("textual is dead")

    em = ErrorAlertEmitter(level="toast", textual_notify=boom)
    # Nao deve levantar
    n = em.emit([_entry()])
    assert n == 1


def test_libnotify_ausente_e_silencioso(monkeypatch) -> None:
    """notify-send fora do PATH -> nao crasha."""
    from claude_dash.audit import alerts as alerts_mod

    monkeypatch.setattr(alerts_mod.shutil, "which", lambda _name: None)
    em = ErrorAlertEmitter(level="sound")
    n = em.emit([_entry()])
    assert n == 1  # contou como dispatched mesmo que libnotify silencioso


def test_format_alert_inclui_subagent_type() -> None:
    """Mensagem distingue Agent(general-purpose) de Bash etc."""
    from claude_dash.audit.alerts import _format_alert

    e = _entry(tool="Agent")
    e_with_sub = AuditEntry(
        timestamp=e.timestamp, hostname=e.hostname, pid=e.pid,
        session_id=e.session_id, tool="Agent", tool_use_id="x",
        status="error", duration_ms=200, perm_mode="auto",
        input_sha="0", input_bytes=0, output_bytes=0,
        subagent_type="general-purpose",
    )
    msg = _format_alert(e_with_sub)
    assert "general-purpose" in msg
    assert "Agent" in msg


def test_emit_lista_vazia_eh_noop() -> None:
    notify = mock.Mock()
    em = ErrorAlertEmitter(level="both", textual_notify=notify)
    assert em.emit([]) == 0
    notify.assert_not_called()
