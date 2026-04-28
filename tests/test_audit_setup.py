"""Testes do subcomando ``claude-dash setup-audit``.

Cobre:
- Detector de estado (settings.json em tmp_path).
- Modo migrate: settings.json com audit-tool.sh -> re-wire para entry novo.
- Modo fresh: bootstrap completo cria todos os artefatos user-mode.
- Dry-run nao escreve nada.
- Uninstall remove user-mode mas preserva sessions.log.
- Script root gerado e idempotente (contem mkdir -p, etc).
- ``--print-sudo`` so imprime os scripts.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest import mock

from claude_dash.audit import setup as au

# ---------------------------------------------------------------------------
# Helpers para isolar paths em tmp_path
# ---------------------------------------------------------------------------

def _patch_paths(monkeypatch, tmp_path: Path) -> dict[str, Path]:
    home = tmp_path / "home"
    (home / ".claude" / "iris" / "hooks").mkdir(parents=True)
    (home / ".config").mkdir(parents=True)

    settings = home / ".claude" / "settings.json"
    legacy_hook = home / ".claude" / "iris" / "hooks" / "audit-tool.sh"
    local_log = home / ".claude" / "iris" / "audit" / "sessions.log"
    archive = home / ".claude" / "iris" / "audit" / "archive"
    logrotate_user = home / ".config" / "logrotate" / "claude-audit.conf"
    systemd_dir = home / ".config" / "systemd" / "user"
    systemd_svc = systemd_dir / "claude-audit-rotate.service"
    systemd_tmr = systemd_dir / "claude-audit-rotate.timer"

    monkeypatch.setattr(au, "SETTINGS_JSON", settings)
    monkeypatch.setattr(au, "LEGACY_HOOK", legacy_hook)
    monkeypatch.setattr(au, "LOCAL_LOG_DEFAULT", local_log)
    monkeypatch.setattr(au, "ARCHIVE_DIR_DEFAULT", archive)
    monkeypatch.setattr(au, "LOGROTATE_USER_CONF", logrotate_user)
    monkeypatch.setattr(au, "SYSTEMD_USER_DIR", systemd_dir)
    monkeypatch.setattr(au, "SYSTEMD_SERVICE", systemd_svc)
    monkeypatch.setattr(au, "SYSTEMD_TIMER", systemd_tmr)

    # Root paths apontam para tmp para nao bater com o sistema real
    rsyslog = tmp_path / "etc-rsyslog" / "30-claude-audit.conf"
    logrotate_sys = tmp_path / "etc-logrotate" / "claude-audit"
    var_log_dir = tmp_path / "var-log-claude"
    var_log_file = var_log_dir / "tools.log"
    monkeypatch.setattr(au, "RSYSLOG_CONF", rsyslog)
    monkeypatch.setattr(au, "LOGROTATE_SYS_CONF", logrotate_sys)
    monkeypatch.setattr(au, "VAR_LOG_DIR", var_log_dir)
    monkeypatch.setattr(au, "VAR_LOG_FILE", var_log_file)

    # Bloqueia subprocess.run (systemctl) por padrao
    monkeypatch.setattr(au.subprocess, "run", mock.Mock(return_value=mock.Mock(stdout="inactive")))

    return {
        "home": home, "settings": settings, "legacy_hook": legacy_hook,
        "local_log": local_log, "archive": archive, "logrotate_user": logrotate_user,
        "systemd_svc": systemd_svc, "systemd_tmr": systemd_tmr,
    }


def _args(uninstall=False, print_sudo=False, dry_run=False) -> argparse.Namespace:
    return argparse.Namespace(
        command="setup-audit",
        uninstall=uninstall,
        print_sudo=print_sudo,
        dry_run=dry_run,
    )


# ---------------------------------------------------------------------------
# Detector de estado
# ---------------------------------------------------------------------------

def test_state_fresh(tmp_path, monkeypatch):
    _patch_paths(monkeypatch, tmp_path)
    state = au._detect_state()
    assert state.mode() == "fresh"
    assert not state.has_legacy_wire
    assert not state.has_new_wire


def test_state_migrate_detects_legacy_wire(tmp_path, monkeypatch):
    p = _patch_paths(monkeypatch, tmp_path)
    settings = {
        "hooks": {
            "PostToolUse": [{
                "matcher": ".*",
                "hooks": [{"type": "command", "command": "~/.claude/iris/hooks/audit-tool.sh"}],
            }]
        }
    }
    p["settings"].write_text(json.dumps(settings))
    p["legacy_hook"].write_text("#!/bin/bash\n")
    state = au._detect_state()
    assert state.mode() == "migrate"
    assert state.has_legacy_wire and not state.has_new_wire
    assert state.legacy_hook_file_exists


def test_state_installed_when_all_present(tmp_path, monkeypatch):
    p = _patch_paths(monkeypatch, tmp_path)
    settings = {
        "hooks": {"PostToolUse": [{
            "matcher": ".*",
            "hooks": [{"type": "command", "command": "claude-dash-audit-hook"}],
        }]}
    }
    p["settings"].write_text(json.dumps(settings))
    p["local_log"].parent.mkdir(parents=True, exist_ok=True)
    p["local_log"].touch()
    p["archive"].mkdir(parents=True)
    p["logrotate_user"].parent.mkdir(parents=True, exist_ok=True)
    p["logrotate_user"].write_text("x")
    p["systemd_svc"].parent.mkdir(parents=True, exist_ok=True)
    p["systemd_svc"].write_text("x")
    p["systemd_tmr"].write_text("x")
    au.RSYSLOG_CONF.parent.mkdir(parents=True, exist_ok=True)
    au.RSYSLOG_CONF.write_text("x")
    au.LOGROTATE_SYS_CONF.parent.mkdir(parents=True, exist_ok=True)
    au.LOGROTATE_SYS_CONF.write_text("x")
    au.VAR_LOG_DIR.mkdir(parents=True, exist_ok=True)
    state = au._detect_state()
    assert state.mode() == "installed"


def test_state_partial(tmp_path, monkeypatch):
    p = _patch_paths(monkeypatch, tmp_path)
    p["local_log"].parent.mkdir(parents=True, exist_ok=True)
    p["local_log"].touch()
    state = au._detect_state()
    assert state.mode() == "partial"


# ---------------------------------------------------------------------------
# Modo migrate: re-wire
# ---------------------------------------------------------------------------

def test_migrate_replaces_command_in_settings(tmp_path, monkeypatch, capsys):
    p = _patch_paths(monkeypatch, tmp_path)
    initial = {
        "hooks": {
            "PostToolUse": [
                {"matcher": "Write|Edit", "hooks": [{"type": "command",
                  "command": "~/.claude/iris/hooks/memory-timestamp.sh"}]},
                {"matcher": ".*", "hooks": [{"type": "command",
                  "command": "~/.claude/iris/hooks/audit-tool.sh", "timeout": 5}]},
            ]
        },
        "permissions": {"allow": []},
    }
    p["settings"].write_text(json.dumps(initial))
    p["legacy_hook"].write_text("#!/bin/bash\n")

    rc = au.main_setup_audit(_args())
    assert rc == 0

    final = json.loads(p["settings"].read_text())
    pt = final["hooks"]["PostToolUse"]
    cmds = [h["command"] for entry in pt for h in entry["hooks"]]
    assert "claude-dash-audit-hook" in cmds
    assert "~/.claude/iris/hooks/audit-tool.sh" not in cmds
    # memory-timestamp preservado
    assert "~/.claude/iris/hooks/memory-timestamp.sh" in cmds
    # backup criado (.bak.<ts>)
    baks = list(p["settings"].parent.glob("settings.json.bak.*"))
    assert baks, "backup nao foi criado"

    out = capsys.readouterr().out
    assert "MIGRATE" in out
    # v0.14.2: legacy file substituido por shim de compat (nao deleta mais)
    assert "shim" in out
    # Conteudo do shim foi escrito no path legado
    shim_content = p["legacy_hook"].read_text()
    assert "claude-dash-audit-hook" in shim_content
    assert shim_content.startswith("#!/usr/bin/env bash")


# ---------------------------------------------------------------------------
# Modo fresh: bootstrap completo
# ---------------------------------------------------------------------------

def test_fresh_creates_user_artifacts_and_root_script(tmp_path, monkeypatch, capsys):
    p = _patch_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(au, "ROOT_SETUP_SCRIPT", tmp_path / "root-setup.sh")

    rc = au.main_setup_audit(_args())
    assert rc == 0

    assert p["local_log"].is_file()
    assert p["archive"].is_dir()
    assert p["logrotate_user"].is_file()
    assert p["systemd_svc"].is_file()
    assert p["systemd_tmr"].is_file()

    settings = json.loads(p["settings"].read_text())
    cmds = [
        h["command"]
        for entry in settings["hooks"]["PostToolUse"]
        for h in entry["hooks"]
    ]
    assert "claude-dash-audit-hook" in cmds

    root_script = (tmp_path / "root-setup.sh").read_text()
    assert "mkdir -p /var/log/claude" in root_script
    assert "chown syslog:adm /var/log/claude" in root_script
    assert "/etc/rsyslog.d/30-claude-audit.conf" in root_script
    assert "/etc/logrotate.d/claude-audit" in root_script
    assert "systemctl restart rsyslog" in root_script

    out = capsys.readouterr().out
    assert "FRESH" in out
    assert "sudo bash" in out


# ---------------------------------------------------------------------------
# Dry-run: nao escreve nada
# ---------------------------------------------------------------------------

def test_dry_run_writes_nothing(tmp_path, monkeypatch, capsys):
    p = _patch_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(au, "ROOT_SETUP_SCRIPT", tmp_path / "root-setup.sh")
    rc = au.main_setup_audit(_args(dry_run=True))
    assert rc == 0
    assert not p["local_log"].is_file()
    assert not p["logrotate_user"].is_file()
    assert not p["systemd_tmr"].is_file()
    assert not p["settings"].is_file()
    assert not (tmp_path / "root-setup.sh").is_file()
    out = capsys.readouterr().out
    assert "[dry-run]" in out


# ---------------------------------------------------------------------------
# --print-sudo
# ---------------------------------------------------------------------------

def test_print_sudo_emits_both_scripts(tmp_path, monkeypatch, capsys):
    _patch_paths(monkeypatch, tmp_path)
    rc = au.main_setup_audit(_args(print_sudo=True))
    assert rc == 0
    out = capsys.readouterr().out
    assert "claude-audit-root-setup.sh" in out
    assert "claude-audit-root-uninstall.sh" in out
    assert "mkdir -p /var/log/claude" in out
    assert "rm -f /etc/rsyslog.d/30-claude-audit.conf" in out


# ---------------------------------------------------------------------------
# Uninstall preserva sessions.log
# ---------------------------------------------------------------------------

def test_uninstall_removes_user_mode_preserves_sessions_log(tmp_path, monkeypatch, capsys):
    p = _patch_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(au, "ROOT_UNINSTALL_SCRIPT", tmp_path / "root-uninstall.sh")
    # Pre-popula tudo
    p["local_log"].parent.mkdir(parents=True, exist_ok=True)
    p["local_log"].write_text("dado-importante\n")
    p["logrotate_user"].parent.mkdir(parents=True, exist_ok=True)
    p["logrotate_user"].write_text("x")
    p["systemd_svc"].parent.mkdir(parents=True, exist_ok=True)
    p["systemd_svc"].write_text("x")
    p["systemd_tmr"].write_text("x")
    settings = {"hooks": {"PostToolUse": [
        {"matcher": ".*", "hooks": [{"type": "command", "command": "claude-dash-audit-hook"}]},
        {"matcher": "Write|Edit", "hooks": [{"type": "command", "command": "other"}]},
    ]}}
    p["settings"].write_text(json.dumps(settings))

    rc = au.main_setup_audit(_args(uninstall=True))
    assert rc == 0

    # User-mode removido
    assert not p["logrotate_user"].is_file()
    assert not p["systemd_svc"].is_file()
    assert not p["systemd_tmr"].is_file()
    # sessions.log preservado
    assert p["local_log"].is_file()
    assert p["local_log"].read_text() == "dado-importante\n"
    # Wire removido, mas outras entries preservadas
    final = json.loads(p["settings"].read_text())
    cmds = [h["command"] for entry in final["hooks"]["PostToolUse"] for h in entry["hooks"]]
    assert "claude-dash-audit-hook" not in cmds
    assert "other" in cmds
    # Script root de uninstall foi gerado
    assert (tmp_path / "root-uninstall.sh").is_file()
    out = capsys.readouterr().out
    assert "UNINSTALL" in out


# ---------------------------------------------------------------------------
# Script root: idempotencia textual
# ---------------------------------------------------------------------------

def test_root_setup_script_is_idempotent_shape():
    body = au._root_setup_script()
    # Guards de existencia
    assert "if [ ! -d /var/log/claude ]" in body
    assert "if [ ! -f /var/log/claude/tools.log ]" in body
    # set -euo pipefail e checagem de root
    assert "set -euo pipefail" in body
    assert 'id -u' in body
    # Termina com echo OK
    assert "OK" in body


def test_root_uninstall_script_preserves_log():
    body = au._root_uninstall_script()
    assert "rm -f /etc/rsyslog.d/30-claude-audit.conf" in body
    assert "rm -f /etc/logrotate.d/claude-audit" in body
    # Nao apaga /var/log/claude automaticamente; so menciona como sugestao no echo
    # (linhas executaveis nao devem conter rm -rf de /var/log/claude)
    exec_lines = [
        ln for ln in body.splitlines()
        if ln.strip() and not ln.lstrip().startswith("#") and not ln.lstrip().startswith("echo")
    ]
    assert not any("rm -rf /var/log/claude" in ln for ln in exec_lines)
    assert "preservado" in body


# ---------------------------------------------------------------------------
# Modo installed: no-op
# ---------------------------------------------------------------------------

def test_installed_is_noop(tmp_path, monkeypatch, capsys):
    p = _patch_paths(monkeypatch, tmp_path)
    settings = {"hooks": {"PostToolUse": [{
        "matcher": ".*",
        "hooks": [{"type": "command", "command": "claude-dash-audit-hook"}],
    }]}}
    p["settings"].write_text(json.dumps(settings))
    p["local_log"].parent.mkdir(parents=True, exist_ok=True)
    p["local_log"].touch()
    p["archive"].mkdir(parents=True)
    p["logrotate_user"].parent.mkdir(parents=True, exist_ok=True)
    p["logrotate_user"].write_text("x")
    p["systemd_svc"].parent.mkdir(parents=True, exist_ok=True)
    p["systemd_svc"].write_text("x")
    p["systemd_tmr"].write_text("x")
    au.RSYSLOG_CONF.parent.mkdir(parents=True, exist_ok=True)
    au.RSYSLOG_CONF.write_text("x")
    au.LOGROTATE_SYS_CONF.parent.mkdir(parents=True, exist_ok=True)
    au.LOGROTATE_SYS_CONF.write_text("x")
    au.VAR_LOG_DIR.mkdir(parents=True, exist_ok=True)
    au.VAR_LOG_FILE.touch()

    rc = au.main_setup_audit(_args())
    assert rc == 0
    out = capsys.readouterr().out
    assert "Tudo OK" in out
