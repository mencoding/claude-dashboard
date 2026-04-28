"""Audit log de tool calls do Claude Code.

Pacote responsavel por:
- hook.py: entry point ``claude-dash-audit-hook`` que substitui o bash legado
  ``~/.claude/iris/hooks/audit-tool.sh``. Le payload PostToolUse via stdin e
  emite linha syslog RFC 5424 em dois destinos (logger -> rsyslog e append
  direto em ~/.claude/iris/audit/sessions.log).
- setup.py: subcomando ``claude-dash setup-audit`` idempotente que detecta
  o estado do bootstrap (fresh / migrate / partial / installed) e age so
  no delta. Parte root e isolada num script gerado em /tmp para o usuario
  rodar manualmente com sudo.
- templates/: configuracoes-modelo (rsyslog, logrotate, systemd) lidas via
  importlib.resources.

Fase 1 do issue #24 — apenas captura/persistencia. A aba "Audit" da TUI
fica para fase 2 (issue derivada).
"""
