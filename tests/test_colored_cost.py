"""Testes do helper colored_cost (faixas de cor por custo)."""
from __future__ import annotations

from claude_dash.views.now import colored_cost, colored_turn_cost


class TestColoredCost:
    def test_below_first_threshold_is_dim(self) -> None:
        out = colored_cost(0.5)
        assert "dim" in out
        assert "yellow" not in out
        assert "red" not in out

    def test_between_thresholds_is_yellow(self) -> None:
        out = colored_cost(20.0)  # default thresholds (10, 50)
        assert "yellow" in out
        assert "red" not in out

    def test_above_second_threshold_is_red(self) -> None:
        out = colored_cost(75.0)
        assert "red" in out

    def test_custom_thresholds(self) -> None:
        # Custo baixo com thresholds pequenos fica amarelo
        out = colored_cost(2.0, thresholds=(1.0, 5.0))
        assert "yellow" in out

    def test_format_under_1_usd_uses_4_decimals(self) -> None:
        out = colored_cost(0.1234)
        assert "$0.1234" in out

    def test_format_over_1_usd_uses_2_decimals(self) -> None:
        out = colored_cost(12.3456)
        assert "$12.35" in out


class TestColoredTurnCost:
    def test_turn_scale_is_tighter(self) -> None:
        # $0.75 por turno: yellow (acima de $0.50)
        out = colored_turn_cost(0.75)
        assert "yellow" in out

    def test_turn_above_1_usd_is_red(self) -> None:
        out = colored_turn_cost(1.5)
        assert "red" in out

    def test_cheap_turn_is_dim(self) -> None:
        out = colored_turn_cost(0.001)
        assert "dim" in out
