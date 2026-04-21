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

    # Placeholders — views serão implementadas nas próximas iterações
    if args.command == "now":
        print("TODO: view 'now' — TUI viva com rich.Live")
    elif args.command == "today":
        print("TODO: view 'today' — agregado do dia")
    elif args.command == "tools":
        print("TODO: view 'tools' — breakdown de tool_use")
    elif args.command == "session":
        print(f"TODO: view 'session' para sid={args.sid!r}")
    else:  # pragma: no cover
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
