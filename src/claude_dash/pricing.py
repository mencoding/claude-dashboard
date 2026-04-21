"""Tabela de preços e cálculo de custo estimado.

Valores em USD por milhão de tokens. Atualizado em 2026-04-21 contra
os preços públicos Anthropic. **Validação pendente contra a página
oficial** — ver TODO abaixo.
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
#
# TODO(v0.4): cruzar com endpoint /v1/models da Anthropic ao iniciar o
# dashboard e logar discrepâncias.
PRICING: dict[str, Price] = {
    "claude-opus-4-7": Price(
        input_usd_per_m=15.00,
        output_usd_per_m=75.00,
        cache_read_usd_per_m=1.50,
        cache_write_1h_usd_per_m=18.75,
        cache_write_5m_usd_per_m=18.75,
    ),
    "claude-sonnet-4-6": Price(
        input_usd_per_m=3.00,
        output_usd_per_m=15.00,
        cache_read_usd_per_m=0.30,
        cache_write_1h_usd_per_m=3.75,
        cache_write_5m_usd_per_m=3.75,
    ),
    "claude-haiku-4-5": Price(
        input_usd_per_m=1.00,
        output_usd_per_m=5.00,
        cache_read_usd_per_m=0.10,
        cache_write_1h_usd_per_m=1.25,
        cache_write_5m_usd_per_m=1.25,
    ),
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
