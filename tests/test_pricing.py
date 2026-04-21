"""Testes de pricing e normalização de modelo."""
from __future__ import annotations

import pytest

from claude_dash.models import Usage
from claude_dash.pricing import PRICING, cost_of, normalize_model


class TestNormalizeModel:
    def test_strip_bracket_suffix(self) -> None:
        assert normalize_model("claude-opus-4-7[1m]") == "claude-opus-4-7"

    def test_strip_date_suffix(self) -> None:
        assert normalize_model("claude-haiku-4-5-20251001") == "claude-haiku-4-5"

    def test_passthrough_when_clean(self) -> None:
        assert normalize_model("claude-opus-4-7") == "claude-opus-4-7"

    def test_empty_string(self) -> None:
        assert normalize_model("") == ""


class TestCostOf:
    def test_unknown_model_returns_zero(self) -> None:
        assert cost_of("model-inexistente", Usage(input_tokens=1_000_000)) == 0.0

    def test_opus_1m_input_tokens(self) -> None:
        # 1M input tokens @ $15/M = $15
        assert cost_of("claude-opus-4-7", Usage(input_tokens=1_000_000)) == pytest.approx(15.0)

    def test_opus_1m_output_tokens(self) -> None:
        # 1M output tokens @ $75/M = $75
        assert cost_of("claude-opus-4-7", Usage(output_tokens=1_000_000)) == pytest.approx(75.0)

    def test_opus_1m_cache_read(self) -> None:
        # 1M cache read @ $1.50/M = $1.50
        assert cost_of("claude-opus-4-7", Usage(cache_read=1_000_000)) == pytest.approx(1.5)

    def test_bracket_suffix_resolves_to_opus_pricing(self) -> None:
        # Tokens do transcript vêm como "claude-opus-4-7[1m]" — precisa casar
        assert cost_of("claude-opus-4-7[1m]", Usage(input_tokens=1_000_000)) == pytest.approx(15.0)

    def test_sonnet_cheaper_than_opus(self) -> None:
        u = Usage(input_tokens=1_000_000, output_tokens=1_000_000)
        assert cost_of("claude-sonnet-4-6", u) < cost_of("claude-opus-4-7", u)

    def test_haiku_cheaper_than_sonnet(self) -> None:
        u = Usage(input_tokens=1_000_000, output_tokens=1_000_000)
        assert cost_of("claude-haiku-4-5", u) < cost_of("claude-sonnet-4-6", u)

    def test_composite_usage_sums_correctly(self) -> None:
        u = Usage(
            input_tokens=100_000,
            output_tokens=50_000,
            cache_creation_1h=200_000,
            cache_read=800_000,
        )
        # Opus: 100k*15 + 50k*75 + 200k*18.75 + 800k*1.5 all over 1M
        expected = (100_000 * 15 + 50_000 * 75 + 200_000 * 18.75 + 800_000 * 1.5) / 1_000_000
        assert cost_of("claude-opus-4-7", u) == pytest.approx(expected)


class TestUsage:
    def test_total_sums_all_fields(self) -> None:
        u = Usage(input_tokens=1, output_tokens=2, cache_creation_1h=3, cache_creation_5m=4, cache_read=5)
        assert u.total == 15

    def test_iadd_mutates_in_place(self) -> None:
        u = Usage(input_tokens=1, output_tokens=2)
        u += Usage(input_tokens=10, output_tokens=20)
        assert u.input_tokens == 11
        assert u.output_tokens == 22

    def test_add_returns_new_instance(self) -> None:
        u1 = Usage(input_tokens=1)
        u2 = Usage(input_tokens=2)
        result = u1 + u2
        assert result.input_tokens == 3
        assert u1.input_tokens == 1  # original intacto


class TestPricingTable:
    def test_all_models_have_prices(self) -> None:
        for model, price in PRICING.items():
            assert price.input_usd_per_m > 0, model
            assert price.output_usd_per_m > price.input_usd_per_m, model
            assert price.cache_read_usd_per_m < price.input_usd_per_m, model
