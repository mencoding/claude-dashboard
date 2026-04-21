"""Testes da view 'today' — foco em helpers puros (sem TTY)."""
from __future__ import annotations

import re

import pytest

from claude_dash.models import SessionStats, Usage
from claude_dash.views.today import _tools_aggregate


def _stats_with_tools(tools: dict[str, int]) -> SessionStats:
    return SessionStats(
        session_id="x",
        cwd="/x",  # type: ignore[arg-type]
        started_at_ms=0,
        tools=tools,
    )


def test_tools_percentages_use_full_total_not_top_slice() -> None:
    """Com mais tools que top_n, os % exibidos devem refletir share do TOTAL,
    não share do top-N (regressão do achado #1 do review do PR #3)."""
    # 12 tools — top 10 serão exibidos, 2 ficam na cauda
    tools = {f"T{i:02d}": 100 - i for i in range(12)}
    sessions = [_stats_with_tools(tools)]

    panel = _tools_aggregate(sessions, top_n=10)
    # `panel.renderable` aqui é uma string com markup Rich. Remove tags
    # entre colchetes para checar o conteúdo literal.
    raw = str(panel.renderable)
    stripped = re.sub(r"\[/?[^\]]*\]", "", raw)

    # T00 tem 100 calls; total_calls deve ser sum(100..89) = 1134
    # % correto ≈ 8.8, NÃO ~10.47 (bug do top-N-denominator)
    match = re.search(r"T00\s+100\s+\(\s*([\d.]+)%\)", stripped)
    assert match is not None, f"T00 não encontrado no render: {stripped[:500]}"
    pct = float(match.group(1))
    assert 8.5 < pct < 9.2, f"T00 pct={pct}; esperado ~8.8, não ~10.47"


def test_tools_percentages_sum_to_100_when_no_tail() -> None:
    """Sem cauda (tools <= top_n), os % do top N somam 100."""
    tools = {"A": 60, "B": 30, "C": 10}
    sessions = [_stats_with_tools(tools)]

    panel = _tools_aggregate(sessions, top_n=10)
    stripped = re.sub(r"\[/?[^\]]*\]", "", str(panel.renderable))

    pcts = [float(m.group(1)) for m in re.finditer(r"\(\s*([\d.]+)%\)", stripped)]
    assert len(pcts) == 3
    assert sum(pcts) == pytest.approx(100.0, abs=0.1)


def test_tools_empty_returns_fallback_panel() -> None:
    sessions = [_stats_with_tools({})]
    panel = _tools_aggregate(sessions)
    assert "Nenhum tool usado" in str(panel.renderable)
