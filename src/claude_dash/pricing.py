"""Tabela de preços e cálculo de custo estimado.

Valores em USD por milhão de tokens. Validado em 2026-04-21 contra
https://claude.com/pricing e
https://platform.claude.com/docs/en/build-with-claude/prompt-caching

Invariantes documentados pela Anthropic (consistentes entre modelos):
- Output = 5× Input
- Cache read = 0.1× Input
- Cache write 5m = 1.25× Input
- Cache write 1h = 2× Input

Se a Anthropic alterar preços, basta ajustar `input_usd_per_m`; os
demais campos são derivados dessa base.
"""
from __future__ import annotations

from dataclasses import dataclass

from claude_dash.models import Usage


@dataclass(frozen=True, slots=True)
class Price:
    """Preços de um modelo em USD por 1M tokens."""

    input_usd_per_m: float
    output_usd_per_m: float
    cache_read_usd_per_m: float
    cache_write_1h_usd_per_m: float
    cache_write_5m_usd_per_m: float

    def cost(self, u: Usage) -> float:
        """Retorna custo total em USD para um Usage dado."""
        return (
            u.input_tokens * self.input_usd_per_m
            + u.output_tokens * self.output_usd_per_m
            + u.cache_read * self.cache_read_usd_per_m
            + u.cache_creation_1h * self.cache_write_1h_usd_per_m
            + u.cache_creation_5m * self.cache_write_5m_usd_per_m
        ) / 1_000_000


# Tabela hard-coded. A chave deve bater com o valor `.message.model` do
# transcript (ex: "claude-opus-4-7", "claude-sonnet-4-6"). Sufixos como
# "[1m]" (1M context) são removidos via `normalize_model`.
def _price_from_input(input_per_m: float) -> Price:
    """Constrói Price aplicando os multiplicadores oficiais Anthropic."""
    return Price(
        input_usd_per_m=input_per_m,
        output_usd_per_m=input_per_m * 5.0,         # 5× input
        cache_read_usd_per_m=input_per_m * 0.1,     # 0.1× input
        cache_write_5m_usd_per_m=input_per_m * 1.25,  # 1.25× input
        cache_write_1h_usd_per_m=input_per_m * 2.0,   # 2× input
    )


PRICING: dict[str, Price] = {
    "claude-opus-4-7": _price_from_input(5.00),
    "claude-sonnet-4-6": _price_from_input(3.00),
    "claude-haiku-4-5": _price_from_input(1.00),
}


def normalize_model(raw: str) -> str:
    """Remove sufixos como '[1m]' ou '-20251001' para casar com PRICING."""
    if not raw:
        return raw
    # Remove sufixo entre colchetes (ex: 'claude-opus-4-7[1m]')
    if "[" in raw:
        raw = raw.split("[", 1)[0]
    # Remove sufixo de data no formato -YYYYMMDD (ex: 'claude-haiku-4-5-20251001')
    parts = raw.split("-")
    if len(parts) >= 2 and parts[-1].isdigit() and len(parts[-1]) == 8:
        raw = "-".join(parts[:-1])
    return raw


def cost_of(model: str, usage: Usage) -> float:
    """Custo em USD de um `Usage` atribuído a `model`.

    Retorna 0.0 se o modelo não estiver na tabela (sem falhar — custo
    desconhecido é melhor que crash; o chamador pode logar warning).
    """
    price = PRICING.get(normalize_model(model))
    if price is None:
        return 0.0
    return price.cost(usage)
