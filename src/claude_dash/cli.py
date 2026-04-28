"""CLI entrypoint — dispatch dos subcomandos.

Comportamento padrão: `claude-dash` sem argumentos abre a TUI com
abas navegáveis (modo interativo). Os subcomandos explícitos são
preservados para uso em scripts / pipelines.
"""
from __future__ import annotations

import argparse
import sys

from claude_dash import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="claude-dash",
        description="Dashboard de uso do Claude Code. Sem args = TUI interativa.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    # subcomando é opcional: sem argumento → TUI
    subparsers = parser.add_subparsers(dest="command", required=False)

    subparsers.add_parser("tui", help="TUI interativa com abas (default quando sem args)")
    subparsers.add_parser("now", help="Live TUI das sessões ativas (refresh 2s)")
    subparsers.add_parser("today", help="Snapshot do dia corrente (estático)")
    subparsers.add_parser("tools", help="Snapshot de tool_use nas últimas 24h (estático)")

    session_p = subparsers.add_parser("session", help="Drill-down de uma sessão")
    session_p.add_argument("sid", help="sessionId (prefixo aceito)")

    subparsers.add_parser(
        "setup-status",
        help="Configura claude-dash-statusline como statusLine em settings.json "
             "(idempotente, preserva statusline anterior via wrap)",
    )

    audit_p = subparsers.add_parser(
        "setup-audit",
        help="Instala/migra o audit log de tool calls do Claude Code "
             "(hook PostToolUse + rsyslog + logrotate + systemd user timer). "
             "Idempotente; parte root vai num script gerado em /tmp.",
    )
    audit_p.add_argument(
        "--print-sudo",
        action="store_true",
        help="Apenas imprime o conteudo dos scripts root (setup + uninstall); nao toca em nada.",
    )
    audit_p.add_argument(
        "--uninstall",
        action="store_true",
        help="Reverte user-mode (timer, configs, wire) e gera script root de uninstall. "
             "Preserva sessions.log.",
    )
    audit_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Simula todas as acoes sem escrever em disco nem invocar systemctl.",
    )

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
    if args.command == "setup-status":
        from claude_dash.setup_status import main as run_setup

        return run_setup()
    if args.command == "setup-audit":
        from claude_dash.audit.setup import main_setup_audit

        return main_setup_audit(args)
    return 2  # pragma: no cover


if __name__ == "__main__":
    sys.exit(main())
