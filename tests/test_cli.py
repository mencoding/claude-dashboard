"""Testes do CLI — foco em flags utilitárias (sem TTY)."""
from __future__ import annotations

import pytest

from claude_dash import __version__
from claude_dash.cli import build_parser, main


def test_version_flag_prints_prog_and_version(capsys: pytest.CaptureFixture[str]) -> None:
    """`claude-dash --version` deve sair com 0 e imprimir `claude-dash <versão>`.

    A versão exibida é a mesma exposta em `claude_dash.__version__`,
    derivada do pyproject via `importlib.metadata` (não hardcoded).
    """
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0

    captured = capsys.readouterr()
    # argparse roteia `action="version"` para stdout em Python 3.4+.
    output = (captured.out + captured.err).strip()
    assert output == f"claude-dash {__version__}"


def test_version_action_registered_in_parser() -> None:
    """Garante que a flag --version está exposta pelo parser
    (proteção contra remoção acidental em refactors futuros)."""
    parser = build_parser()
    actions = {opt for action in parser._actions for opt in action.option_strings}
    assert "--version" in actions
