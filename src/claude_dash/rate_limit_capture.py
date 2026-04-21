"""Capturador de rate_limits a partir do JSON de input do statusline.

O Claude Code só injeta `rate_limits.five_hour` / `seven_day` no JSON que
passa para o script configurado em `settings.json:statusLine`. Nenhum
hook recebe esses campos. Esta camada resolve com um padrão de pipeline
composable:

1. Usuário adiciona uma linha no seu statusline script que faz tee do
   JSON de entrada para este capturador:
       echo "$input" | claude-dash-rate-limit-capture > /dev/null
2. Este script extrai rate_limits + session_id + context_window atual e
   grava em /tmp/claude-dash-rate-limits/<session_id>.json, com timestamp.
3. O dashboard (TUI, MCP, views) lê esse diretório e exibe o snapshot
   mais recente por sessão.

A gravação é pass-through (stdout mirra stdin) para permitir compor com
pipes, mas no uso típico do Léo o chamador descarta o stdout pra não
duplicar o status line.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


CAPTURE_DIR = Path(os.environ.get(
    "CLAUDE_DASH_RATE_LIMIT_DIR",
    "/tmp/claude-dash-rate-limits",
))


def _atomic_write(path: Path, content: str) -> None:
    """Escrita atômica (tmp + rename) pra evitar leituras parciais."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(content)
    tmp.replace(path)


def capture_from_json(raw: str, capture_dir: Path = CAPTURE_DIR) -> bool:
    """Extrai rate_limits de uma string JSON e grava em `capture_dir`.

    Retorna True se a captura gravou algo, False caso contrário
    (JSON inválido, ausência dos campos, falha de I/O).
    """
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return False

    rate_limits = data.get("rate_limits")
    sid = data.get("session_id")
    if not rate_limits or not sid:
        return False

    payload = {
        "session_id": sid,
        "captured_at_ms": int(time.time() * 1000),
        "rate_limits": rate_limits,
        "context_window": data.get("context_window"),
        "model_id": (data.get("model") or {}).get("id"),
    }

    try:
        capture_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write(capture_dir / f"{sid}.json", json.dumps(payload))
    except OSError:
        return False
    return True


def main() -> int:
    """Entrypoint console. Lê stdin, captura, espelha no stdout (pass-through)."""
    raw = sys.stdin.read()
    capture_from_json(raw)
    # Pass-through: permite colocar este passo num pipe sem quebrar a
    # chain. Ex: `echo "$input" | claude-dash-rate-limit-capture | my_statusline`
    sys.stdout.write(raw)
    return 0


if __name__ == "__main__":
    sys.exit(main())
