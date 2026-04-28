"""claude-dashboard — TUI de monitoramento do Claude Code."""
from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

# Fonte de verdade da versão é o pyproject.toml (distribuição instalada).
# Em execuções fora de uma instalação (ex.: árvore de fontes sem `pip install`),
# caímos num fallback estável.
try:
    __version__ = _pkg_version("claude-dashboard")
except PackageNotFoundError:  # pragma: no cover - defesa para árvore não instalada
    __version__ = "0.0.0+unknown"
