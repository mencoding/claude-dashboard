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
        # 1M input tokens @ $5/M = $5
        assert cost_of("claude-opus-4-7", Usage(input_tokens=1_000_000)) == pytest.approx(5.0)

    def test_opus_1m_output_tokens(self) -> None:
        # 1M output tokens @ $25/M = $25 (5× input)
        assert cost_of("claude-opus-4-7", Usage(output_tokens=1_000_000)) == pytest.approx(25.0)

    def test_opus_1m_cache_read(self) -> None:
        # 1M cache read @ $0.50/M = $0.50 (0.1× input)
        assert cost_of("claude-opus-4-7", Usage(cache_read=1_000_000)) == pytest.approx(0.5)

    def test_opus_cache_write_1h_vs_5m(self) -> None:
        # 1h cache write deve ser ~1.6× mais caro que 5m cache write
        # (invariantes: 5m = 1.25× input, 1h = 2× input)
        c_5m = cost_of("claude-opus-4-7", Usage(cache_creation_5m=1_000_000))
        c_1h = cost_of("claude-opus-4-7", Usage(cache_creation_1h=1_000_000))
        assert c_5m == pytest.approx(6.25)
        assert c_1h == pytest.approx(10.0)
        assert c_1h == pytest.approx(c_5m * 1.6)

    def test_bracket_suffix_resolves_to_opus_pricing(self) -> None:
        # Tokens do transcript vêm como "claude-opus-4-7[1m]" — precisa casar
        assert cost_of("claude-opus-4-7[1m]", Usage(input_tokens=1_000_000)) == pytest.approx(5.0)

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
        # Opus (preços 2026-04): 100k*5 + 50k*25 + 200k*10 + 800k*0.5 all over 1M
        expected = (100_000 * 5 + 50_000 * 25 + 200_000 * 10 + 800_000 * 0.5) / 1_000_000
        assert cost_of("claude-opus-4-7", u) == pytest.approx(expected)


class TestPricingInvariants:
    """Valida que a tabela segue os multiplicadores oficiais Anthropic."""

    def test_output_is_5x_input(self) -> None:
        for model, price in PRICING.items():
            assert price.output_usd_per_m == pytest.approx(price.input_usd_per_m * 5), model

    def test_cache_read_is_0_1x_input(self) -> None:
        for model, price in PRICING.items():
            assert price.cache_read_usd_per_m == pytest.approx(
                price.input_usd_per_m * 0.1
            ), model

    def test_cache_write_5m_is_1_25x_input(self) -> None:
        for model, price in PRICING.items():
            assert price.cache_write_5m_usd_per_m == pytest.approx(
                price.input_usd_per_m * 1.25
            ), model

    def test_cache_write_1h_is_2x_input(self) -> None:
        for model, price in PRICING.items():
            assert price.cache_write_1h_usd_per_m == pytest.approx(
                price.input_usd_per_m * 2.0
            ), model


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
