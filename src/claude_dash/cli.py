"""CLI entrypoint — dispatch dos subcomandos."""
from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="claude-dash",
        description="Dashboard de uso do Claude Code.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("now", help="TUI viva com sessões ativas")
    subparsers.add_parser("today", help="Agregado do dia corrente")
    subparsers.add_parser("tools", help="Breakdown de tool_use nas últimas 24h")

    session_p = subparsers.add_parser("session", help="Drill-down de uma sessão")
    session_p.add_argument("sid", help="sessionId (prefixo aceito)")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "now":
        from claude_dash.views.now import run as run_now

        return run_now()
    if args.command == "today":
        print("TODO: view 'today' — agregado do dia (v0.2)")
        return 0
    if args.command == "tools":
        print("TODO: view 'tools' — breakdown de tool_use (v0.2)")
        return 0
    if args.command == "session":
        print(f"TODO: view 'session' para sid={args.sid!r} (v0.3)")
        return 0
    return 2  # pragma: no cover


if __name__ == "__main__":
    sys.exit(main())
