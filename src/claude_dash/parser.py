"""Parser streaming de transcripts JSONL.

Cada linha do transcript é um JSON auto-contido. Esta camada converte
em eventos tipados (ou dicionários já processados) que o aggregator
consome. Tudo é lazy/streaming: nenhuma etapa carrega o transcript
inteiro em memória.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from claude_dash.models import Usage


def iter_entries(path: Path, start_offset: int = 0) -> Iterator[tuple[int, dict[str, Any]]]:
    """Itera entradas do JSONL a partir de `start_offset` (bytes).

    Yield: `(offset_apos_linha, entry_dict)`.

    O offset é pós-leitura — guardá-lo e passar em `start_offset` numa
    chamada futura permite parsing incremental sem reler bytes antigos.
    Linhas vazias e entradas que não parseiam são silenciosamente
    puladas (transcripts em escrita ativa podem ter linha parcial no
    fim).
    """
    with path.open("rb") as f:
        if start_offset:
            f.seek(start_offset)
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                # Linha incompleta (arquivo em escrita) — ignora
                continue
            yield f.tell(), entry


def extract_usage(entry: dict[str, Any]) -> Usage | None:
    """Extrai `Usage` de uma entrada `type=="assistant"`.

    Retorna None se a entrada não tiver usage (ex: system/user/etc).
    O schema observado em `message.usage` é:

        input_tokens, output_tokens, cache_read_input_tokens,
        cache_creation_input_tokens, cache_creation.ephemeral_1h_input_tokens,
        cache_creation.ephemeral_5m_input_tokens, ...
    """
    if entry.get("type") != "assistant":
        return None
    msg = entry.get("message") or {}
    usage_raw = msg.get("usage") or {}
    if not usage_raw:
        return None

    cache_creation = usage_raw.get("cache_creation") or {}
    return Usage(
        input_tokens=int(usage_raw.get("input_tokens") or 0),
        output_tokens=int(usage_raw.get("output_tokens") or 0),
        cache_read=int(usage_raw.get("cache_read_input_tokens") or 0),
        cache_creation_1h=int(cache_creation.get("ephemeral_1h_input_tokens") or 0),
        cache_creation_5m=int(cache_creation.get("ephemeral_5m_input_tokens") or 0),
    )


def extract_model(entry: dict[str, Any]) -> str | None:
    """Retorna o nome do modelo da entrada assistant, ou None."""
    if entry.get("type") != "assistant":
        return None
    msg = entry.get("message") or {}
    model = msg.get("model")
    return model if isinstance(model, str) and model else None


def iter_tool_uses(entry: dict[str, Any]) -> Iterator[str]:
    """Yield nome de cada `tool_use` dentro de uma entrada assistant.

    Uma mesma resposta do modelo pode ter vários `tool_use` no
    `message.content[]`.
    """
    if entry.get("type") != "assistant":
        return
    msg = entry.get("message") or {}
    content = msg.get("content")
    if not isinstance(content, list):
        return
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            name = block.get("name")
            if isinstance(name, str) and name:
                yield name


def extract_custom_title(entry: dict[str, Any]) -> str | None:
    """Retorna o nome dado à sessão via `/rename`, se a entry for do tipo
    `custom-title`.

    O harness do Claude Code persiste o nome no transcript JSONL como
    uma linha do tipo `{"type":"custom-title","customTitle":"<nome>",...}`.
    Cada `/rename` aplicado durante a sessão gera uma nova entry — a
    convenção do aggregator é "última vence" (a mais recente sobrescreve).

    Strings vazias são descartadas (retornam None) para não poluir o
    payload com nomes nulos.
    """
    if entry.get("type") != "custom-title":
        return None
    title = entry.get("customTitle")
    if isinstance(title, str) and title.strip():
        return title
    return None


def extract_timestamp_ms(entry: dict[str, Any]) -> int | None:
    """Converte `timestamp` ISO8601 para epoch ms, se presente."""
    ts = entry.get("timestamp")
    if not isinstance(ts, str):
        return None
    # Formato observado: "2026-04-21T11:08:54.123Z" ou "…-03:00"
    # Python 3.12 fromisoformat aceita ambos
    try:
        from datetime import datetime

        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)
    except (ValueError, TypeError):
        return None
