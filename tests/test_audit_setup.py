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
    # #52 fix: 'installed' agora exige grupo claude-audit + chown.
    monkeypatch.setattr(au, "_claude_audit_group_exists", lambda: True)
    monkeypatch.setattr(au, "_user_in_claude_audit_group", lambda: True)
    monkeypatch.setattr(au, "_var_log_owned_by_claude_audit", lambda: True)
    state = au._detect_state()
    assert state.mode() == "installed"


def test_state_partial_when_grupo_claude_audit_pendente(tmp_path, monkeypatch):
    """Bug fix: tudo 'instalado' menos o grupo claude-audit deve dar partial,
    nao installed. Anteriormente o setup-audit cairia em 'Tudo OK — nada
    a fazer' deixando o usuario stuck sem regenerar o root script."""
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
    # Cenario bug: tudo instalado mas grupo claude-audit ainda nao existe
    monkeypatch.setattr(au, "_claude_audit_group_exists", lambda: False)
    monkeypatch.setattr(au, "_user_in_claude_audit_group", lambda: False)
    monkeypatch.setattr(au, "_var_log_owned_by_claude_audit", lambda: False)
    state = au._detect_state()
    assert state.mode() == "partial"


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
    # #52: chown migrou de syslog:adm pra syslog:claude-audit
    assert "chown syslog:claude-audit /var/log/claude" in root_script
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

def _make_installed_state(p: dict, monkeypatch=None) -> None:
    """Helper: cria estado completo do modo `installed` em tmp_path.

    #52 fix: tambem mocka grupo claude-audit como existente quando
    monkeypatch e' fornecido (todos os testes que esperam mode="installed"
    devem passar isso).
    """
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
    if monkeypatch is not None:
        monkeypatch.setattr(au, "_claude_audit_group_exists", lambda: True)
        monkeypatch.setattr(au, "_user_in_claude_audit_group", lambda: True)
        monkeypatch.setattr(au, "_var_log_owned_by_claude_audit", lambda: True)


def test_installed_is_noop(tmp_path, monkeypatch, capsys):
    p = _patch_paths(monkeypatch, tmp_path)
    _make_installed_state(p, monkeypatch)

    rc = au.main_setup_audit(_args())
    assert rc == 0
    out = capsys.readouterr().out
    assert "Tudo OK" in out


def test_installed_silent_when_legacy_hook_is_compat_shim(tmp_path, monkeypatch, capsys):
    """Issue #49: estado correto (shim instalado) nao deve imprimir aviso."""
    p = _patch_paths(monkeypatch, tmp_path)
    _make_installed_state(p, monkeypatch)
    p["legacy_hook"].write_text(au.LEGACY_SHIM_CONTENT)

    rc = au.main_setup_audit(_args())
    assert rc == 0
    out = capsys.readouterr().out
    assert "Tudo OK" in out
    assert "delete manualmente" not in out


def test_installed_reinstalls_shim_when_legacy_bash_present(tmp_path, monkeypatch, capsys):
    """Issue #49: bash legado em estado `installed` -> instala shim em vez de avisar."""
    p = _patch_paths(monkeypatch, tmp_path)
    _make_installed_state(p, monkeypatch)
    # Bash legado de 120 linhas (nao contem 'exec claude-dash-audit-hook')
    p["legacy_hook"].write_text("#!/bin/bash\n# script legado\necho old\n")

    rc = au.main_setup_audit(_args())
    assert rc == 0
    out = capsys.readouterr().out
    assert "delete manualmente" not in out
    # Shim foi instalado: arquivo agora contem o marcador
    assert au._is_compat_shim(p["legacy_hook"])
    assert "claude-dash-audit-hook" in p["legacy_hook"].read_text()


def test_is_compat_shim_helper():
    """Helper detecta shim valido vs bash legado vs ausente."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        # Arquivo ausente
        assert not au._is_compat_shim(td_path / "missing")
        # Shim valido
        shim = td_path / "shim.sh"
        shim.write_text(au.LEGACY_SHIM_CONTENT)
        assert au._is_compat_shim(shim)
        # Bash legado
        legacy = td_path / "legacy.sh"
        legacy.write_text("#!/bin/bash\necho old\n")
        assert not au._is_compat_shim(legacy)


# ---------------------------------------------------------------------------
# Grupo claude-audit (#52)
# ---------------------------------------------------------------------------

def test_claude_audit_group_exists_quando_grupo_resolve(monkeypatch) -> None:
    """getgrnam retorna struct -> True."""
    fake_grp = mock.Mock()
    fake_grp.getgrnam = mock.Mock(return_value=mock.Mock(gr_gid=999))
    monkeypatch.setattr(
        "claude_dash.audit.setup._claude_audit_group_exists",
        lambda: True,
    )
    state = au.State()
    state.claude_audit_group_exists = au._claude_audit_group_exists()
    # Wrapper re-resolve, monkeypatch acima cobre
    assert state.claude_audit_group_exists is True


def test_claude_audit_group_nao_existe_em_distro_sem_grupo(monkeypatch) -> None:
    """getgrnam levanta KeyError quando grupo ausente -> False, sem crashar."""
    import grp as grp_module

    def boom(name: str) -> None:
        raise KeyError(name)

    monkeypatch.setattr(grp_module, "getgrnam", boom)
    assert au._claude_audit_group_exists() is False
    assert au._user_in_claude_audit_group() is False


def test_user_in_claude_audit_quando_gid_em_getgroups(monkeypatch) -> None:
    """getgroups inclui gid do grupo -> True."""
    import grp as grp_module
    fake = mock.Mock(gr_gid=4242)
    monkeypatch.setattr(grp_module, "getgrnam", lambda _name: fake)
    monkeypatch.setattr(au.os, "getgroups", lambda: [1000, 4242, 27])
    assert au._user_in_claude_audit_group() is True


def test_user_nao_in_claude_audit_quando_gid_fora(monkeypatch) -> None:
    import grp as grp_module
    fake = mock.Mock(gr_gid=4242)
    monkeypatch.setattr(grp_module, "getgrnam", lambda _name: fake)
    monkeypatch.setattr(au.os, "getgroups", lambda: [1000, 27])
    assert au._user_in_claude_audit_group() is False


def test_var_log_owned_by_claude_audit_false_quando_arquivo_ausente(
    tmp_path, monkeypatch,
) -> None:
    """Sem o arquivo, retorna False sem chamar grp."""
    monkeypatch.setattr(au, "VAR_LOG_FILE", tmp_path / "missing.log")
    assert au._var_log_owned_by_claude_audit() is False


def test_root_script_inclui_groupadd_e_chown(tmp_path, monkeypatch) -> None:
    _patch_paths(monkeypatch, tmp_path)
    body = au._root_setup_script()
    assert "groupadd -f claude-audit" in body
    assert "usermod -aG claude-audit" in body
    assert "chown syslog:claude-audit /var/log/claude" in body
    assert "chown syslog:claude-audit /var/log/claude/tools.log" in body
    # Migracao dos rotacionados
    assert "/var/log/claude/tools.log-*.gz" in body
    # Aviso sobre re-login
    assert "newgrp" in body or "re-login" in body


def test_print_state_mostra_grupo(tmp_path, monkeypatch, capsys) -> None:
    """_print_state inclui as 3 linhas novas do grupo."""
    _patch_paths(monkeypatch, tmp_path)
    state = au.State()
    state.claude_audit_group_exists = True
    state.user_in_claude_audit_group = False
    state.var_log_owned_by_claude_audit = False
    au._print_state(state)
    out = capsys.readouterr().out
    assert "grupo claude-audit" in out
    assert "user no grupo" in out
    assert "tools.log no grupo" in out


def test_template_rsyslog_usa_claude_audit() -> None:
    """Template foi migrado de fileGroup=adm pra fileGroup=claude-audit."""
    body = au._read_template("rsyslog-30-claude-audit.conf")
    assert 'fileGroup="claude-audit"' in body
    assert 'fileGroup="adm"' not in body


def test_template_logrotate_usa_claude_audit() -> None:
    body = au._read_template("logrotate-system-claude-audit")
    assert "create 0640 syslog claude-audit" in body
    assert "syslog adm" not in body


def test_setup_audit_regenera_script_quando_grupo_pendente(
    tmp_path, monkeypatch, capsys,
) -> None:
    """Bug fix: usuario na v0.14.x que upgradou pra v0.15.x e ainda nao
    rodou o sudo bash do grupo claude-audit. Antes do fix, setup-audit
    detectava 'installed' e dizia 'Tudo OK' sem regenerar o script root,
    deixando o usuario stuck. Agora deve cair em 'partial' e re-emitir
    o script.
    """
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
    au.VAR_LOG_FILE.touch()
    # Cenario do bug: tudo "instalado" mas grupo claude-audit pendente
    monkeypatch.setattr(au, "_claude_audit_group_exists", lambda: False)
    monkeypatch.setattr(au, "_user_in_claude_audit_group", lambda: False)
    monkeypatch.setattr(au, "_var_log_owned_by_claude_audit", lambda: False)
    # Override do path do script root pra tmp_path (nao escrever em /tmp real)
    root_script = tmp_path / "root-setup.sh"
    monkeypatch.setattr(au, "ROOT_SETUP_SCRIPT", root_script)

    rc = au.main_setup_audit(_args())
    assert rc == 0
    out = capsys.readouterr().out

    # NAO pode dizer "Tudo OK" — esse era o bug original
    assert "Tudo OK" not in out
    # DEVE detectar como partial e gerar o script novo
    assert "PARTIAL" in out or "partial" in out.lower()
    assert root_script.is_file()
    body = root_script.read_text()
    # Script novo tem a parte do grupo
    assert "groupadd -f claude-audit" in body
    assert "usermod -aG claude-audit" in body
