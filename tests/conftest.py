"""Fixtures globais da suíte de testes."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clear_aggregator_ttl_cache():
    """Limpa o cache TTL do aggregator entre testes.

    `collect_live_sessions` e `collect_sessions_since` são memoizadas
    com TTL de 1s. Sem isso, testes que rodam em sequência rápida
    poderiam ver resultados de fixtures anteriores (ex: testes do MCP
    que mockam o módulo após uma chamada real).
    """
    from claude_dash import aggregator
    aggregator._cache.clear()
    yield
    aggregator._cache.clear()
