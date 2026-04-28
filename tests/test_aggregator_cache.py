"""Testes da memoização TTL em collect_live_sessions e collect_sessions_since.

Cobre os comportamentos garantidos pelo decorator `_ttl_cached`:
- hit dentro da janela retorna o mesmo objeto (identidade);
- miss após expiração re-executa;
- chaves diferentes não compartilham cache;
- cache_clear() força re-execução.

Não usa `time.sleep` — controla o relógio via monkeypatch em
`time.monotonic`, conforme orientação do issue #27.
"""
from __future__ import annotations

import pytest

from claude_dash import aggregator


@pytest.fixture
def fake_clock(monkeypatch: pytest.MonkeyPatch):
    """Relógio monotônico controlável via `clock['t']`."""
    clock = {"t": 1000.0}
    monkeypatch.setattr(aggregator.time, "monotonic", lambda: clock["t"])
    return clock


def test_hit_dentro_do_ttl_retorna_mesmo_objeto(
    fake_clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Chamada idêntica dentro do TTL deve devolver o objeto cacheado."""
    chamadas = {"n": 0}

    def fake_discover_live():
        chamadas["n"] += 1
        return []

    def fake_discover_transcripts():
        return []

    monkeypatch.setattr(aggregator, "discover_live_sessions", fake_discover_live)
    monkeypatch.setattr(aggregator, "discover_transcripts", fake_discover_transcripts)

    aggregator.collect_live_sessions.cache_clear()

    primeiro = aggregator.collect_live_sessions()
    fake_clock["t"] += 0.5  # ainda dentro do TTL de 1.0s
    segundo = aggregator.collect_live_sessions()

    assert primeiro is segundo, "esperava cache hit (mesmo objeto)"
    assert chamadas["n"] == 1, "função interna executou mais de uma vez"


def test_miss_apos_expiracao_re_executa(
    fake_clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Após o TTL expirar, a função interna deve rodar novamente."""
    chamadas = {"n": 0}

    def fake_discover_live():
        chamadas["n"] += 1
        return []

    monkeypatch.setattr(aggregator, "discover_live_sessions", fake_discover_live)
    monkeypatch.setattr(aggregator, "discover_transcripts", lambda: [])

    aggregator.collect_live_sessions.cache_clear()

    aggregator.collect_live_sessions()
    fake_clock["t"] += 1.5  # > TTL de 1.0s
    aggregator.collect_live_sessions()

    assert chamadas["n"] == 2, "esperava re-execução após expiração"


def test_argumentos_diferentes_nao_compartilham_cache(
    fake_clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`collect_sessions_since(a)` e `(b)` devem ter entradas separadas."""
    chamadas = {"n": 0}

    def fake_discover_live():
        return []

    def fake_discover_transcripts():
        chamadas["n"] += 1
        return []

    monkeypatch.setattr(aggregator, "discover_live_sessions", fake_discover_live)
    monkeypatch.setattr(aggregator, "discover_transcripts", fake_discover_transcripts)

    aggregator.collect_sessions_since.cache_clear()

    aggregator.collect_sessions_since(1000)
    aggregator.collect_sessions_since(2000)  # since_ms diferente → outra chave
    # Sem avançar o relógio, mesma chamada → cache hit
    aggregator.collect_sessions_since(1000)

    assert chamadas["n"] == 2, (
        "esperava 2 execuções (uma por valor de since_ms); "
        f"obteve {chamadas['n']}"
    )


def test_cache_clear_forca_re_execucao(
    fake_clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`cache_clear()` deve invalidar entradas mesmo dentro do TTL."""
    chamadas = {"n": 0}

    def fake_discover_live():
        chamadas["n"] += 1
        return []

    monkeypatch.setattr(aggregator, "discover_live_sessions", fake_discover_live)
    monkeypatch.setattr(aggregator, "discover_transcripts", lambda: [])

    aggregator.collect_live_sessions.cache_clear()

    aggregator.collect_live_sessions()
    aggregator.collect_live_sessions.cache_clear()
    aggregator.collect_live_sessions()  # sem avançar o relógio

    assert chamadas["n"] == 2, "esperava re-execução após cache_clear"


def test_collect_live_e_collect_since_tem_chaves_distintas(
    fake_clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """O nome da função compõe a chave; uma não invalida a outra."""
    monkeypatch.setattr(aggregator, "discover_live_sessions", lambda: [])
    monkeypatch.setattr(aggregator, "discover_transcripts", lambda: [])

    aggregator.collect_live_sessions.cache_clear()
    aggregator.collect_sessions_since.cache_clear()

    a = aggregator.collect_live_sessions()
    b = aggregator.collect_sessions_since(0)

    # Listas distintas (objetos diferentes), mas dentro do TTL cada uma
    # devolve o próprio objeto cacheado em chamadas subsequentes.
    assert aggregator.collect_live_sessions() is a
    assert aggregator.collect_sessions_since(0) is b
