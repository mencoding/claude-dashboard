"""CLI entrypoint — dispatch dos subcomandos.

Comportamento padrão: `claude-dash` sem argumentos abre a TUI com
abas navegáveis (modo interativo). Os subcomandos explícitos são
preservados para uso em scripts / pipelines.
"""
from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="claude-dash",
        description="Dashboard de uso do Claude Code. Sem args = TUI interativa.",
    )
    # subcomando é opcional: sem argumento → TUI
    subparsers = parser.add_subparsers(dest="command", required=False)

    subparsers.add_parser("tui", help="TUI interativa (default quando sem args)")
    subparsers.add_parser("now", help="Snapshot estático das sessões ativas")
    subparsers.add_parser("today", help="Agregado do dia corrente")
    subparsers.add_parser("tools", help="Breakdown de tool_use nas últimas 24h")

    session_p = subparsers.add_parser("session", help="Drill-down de uma sessão")
    session_p.add_argument("sid", help="sessionId (prefixo aceito)")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # Default: sem argumentos ou com 'tui' → abre TUI
    if args.command is None or args.command == "tui":
        from claude_dash.views.tui import run as run_tui

        return run_tui()

    if args.command == "now":
        from claude_dash.views.now import run as run_now

        return run_now()
    if args.command == "today":
        from claude_dash.views.today import run as run_today

        return run_today()
    if args.command == "tools":
        from claude_dash.views.tools import run as run_tools

        return run_tools()
    if args.command == "session":
        from claude_dash.views.session import run as run_session

        return run_session(args.sid)
    return 2  # pragma: no cover


if __name__ == "__main__":
    sys.exit(main())
