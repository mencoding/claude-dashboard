"""Testes do layout de exibição de rate limits (bars + texto)."""
from __future__ import annotations

from claude_dash.rate_limits import RateLimitSnapshot
from claude_dash.views.now import _rate_limit_bar, _rate_limit_block


class TestRateLimitBar:
    def test_zero_pct_is_all_empty(self) -> None:
        bar = _rate_limit_bar(0, width=10)
        assert bar == "[" + "░" * 10 + "]"

    def test_full_pct_is_all_filled(self) -> None:
        bar = _rate_limit_bar(100, width=10)
        assert bar == "[" + "█" * 10 + "]"

    def test_half_pct(self) -> None:
        bar = _rate_limit_bar(50, width=10)
        assert bar.count("█") == 5
        assert bar.count("░") == 5

    def test_clamps_over_100(self) -> None:
        bar = _rate_limit_bar(150, width=10)
        assert bar.count("█") == 10

    def test_clamps_negative(self) -> None:
        bar = _rate_limit_bar(-5, width=10)
        assert bar.count("█") == 0
        assert bar.count("░") == 10


class TestRateLimitBlock:
    def test_empty_when_snap_is_none(self) -> None:
        block = _rate_limit_block(None)
        assert block.plain == ""

    def test_empty_when_stale(self) -> None:
        old_snap = RateLimitSnapshot(
            session_id="x",
            captured_at_ms=1,  # epoch antigo
            five_hour_pct=42,
            five_hour_resets_at_ms=0,
            seven_day_pct=18,
            seven_day_resets_at_ms=0,
        )
        block = _rate_limit_block(old_snap)
        assert block.plain == ""

    def test_renders_two_lines_when_fresh(self) -> None:
        import time
        now_ms = int(time.time() * 1000)
        snap = RateLimitSnapshot(
            session_id="x",
            captured_at_ms=now_ms,
            five_hour_pct=42,
            five_hour_resets_at_ms=now_ms + 3600_000,
            seven_day_pct=18,
            seven_day_resets_at_ms=now_ms + 86400_000 * 6,
        )
        block = _rate_limit_block(snap)
        rendered = block.plain
        assert "Janela 5h" in rendered
        assert "Janela 7d" in rendered
        assert "42.0%" in rendered
        assert "18.0%" in rendered
        # Uma quebra de linha separando as duas janelas
        assert rendered.count("\n") == 1

    def test_includes_reset_time(self) -> None:
        import time
        now_ms = int(time.time() * 1000)
        snap = RateLimitSnapshot(
            session_id="x",
            captured_at_ms=now_ms,
            five_hour_pct=50,
            five_hour_resets_at_ms=now_ms + 3600_000 + 15 * 60_000,  # 1h15m
            seven_day_pct=20,
            seven_day_resets_at_ms=now_ms + 86400_000 * 3,
        )
        block = _rate_limit_block(snap).plain
        assert "reseta em" in block
        assert "1h15m" in block
        assert "3d0h" in block

    def test_fallback_reason_rendered_when_snap_none(self) -> None:
        """Dado snap=None e fallback_reason fornecido, renderiza a razão
        em estilo dim em vez de silenciar (UX v0.11.2)."""
        block = _rate_limit_block(None, fallback_reason="aguardando 1º turno")
        assert "aguardando 1º turno" in block.plain

    def test_no_fallback_keeps_legacy_silent_behavior(self) -> None:
        """Sem fallback_reason, snap=None continua produzindo Text vazio —
        preserva compatibilidade com callers que não adotaram o
        novo parâmetro."""
        assert len(_rate_limit_block(None).plain) == 0
