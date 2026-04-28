"""Subcomando ``claude-dash setup-audit``.

Idempotente: detecta o estado atual e age so no delta. Os efeitos sao
divididos em duas camadas:

- **User-mode**: tudo que vive em ``~/.claude/`` e ``~/.config/`` —
  diretorios, sessions.log, logrotate user, systemd user timer e re-wire
  do ``settings.json``. Executado direto pelo subcomando (com ``--dry-run``
  apenas simula).
- **Root-mode**: tudo que exige ``sudo`` (criar ``/var/log/claude/``,
  escrever em ``/etc/rsyslog.d/`` e ``/etc/logrotate.d/``, restart do
  rsyslog). Materializado num script idempotente em
  ``/tmp/claude-audit-root-setup.sh``; o usuario roda manualmente com
  ``sudo bash``.

Modos detectados:

- ``fresh``: nada instalado.
- ``migrate``: ``settings.json`` ainda referencia
  ``~/.claude/iris/hooks/audit-tool.sh`` -> apenas re-wira para
  ``claude-dash-audit-hook``. Nao toca em rsyslog/logrotate/timer.
- ``partial``: alguns componentes ja presentes -> completa o que falta.
- ``installed``: tudo presente e wired para o entry point novo -> no-op.

NUNCA deleta ``~/.claude/iris/hooks/audit-tool.sh`` automaticamente —
imprime aviso pedindo remocao manual apos validacao.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from importlib import resources
from pathlib import Path

# Paths user-mode -------------------------------------------------------------
SETTINGS_JSON = Path.home() / ".claude" / "settings.json"
LEGACY_HOOK = Path.home() / ".claude" / "iris" / "hooks" / "audit-tool.sh"
LOCAL_LOG_DEFAULT = Path.home() / ".claude" / "iris" / "audit" / "sessions.log"
ARCHIVE_DIR_DEFAULT = Path.home() / ".claude" / "iris" / "audit" / "archive"
LOGROTATE_USER_CONF = Path.home() / ".config" / "logrotate" / "claude-audit.conf"
SYSTEMD_USER_DIR = Path.home() / ".config" / "systemd" / "user"
SYSTEMD_SERVICE = SYSTEMD_USER_DIR / "claude-audit-rotate.service"
SYSTEMD_TIMER = SYSTEMD_USER_DIR / "claude-audit-rotate.timer"

# Paths root-mode -------------------------------------------------------------
RSYSLOG_CONF = Path("/etc/rsyslog.d/30-claude-audit.conf")
LOGROTATE_SYS_CONF = Path("/etc/logrotate.d/claude-audit")
VAR_LOG_DIR = Path("/var/log/claude")
VAR_LOG_FILE = Path("/var/log/claude/tools.log")

# Comando alvo do PostToolUse (entry point novo)
NEW_HOOK_CMD = "claude-dash-audit-hook"
LEGACY_HOOK_CMD_FRAGMENT = "audit-tool.sh"

# Conteudo do shim de compat que substitui o audit-tool.sh legado.
# Necessario porque o Claude Code lê settings.json *uma vez no startup* da
# sessao e cacheia o path do hook em memoria. Sessoes abertas ANTES do
# `setup-audit migrate` continuariam invocando ~/.claude/iris/hooks/audit-tool.sh
# pelo path cacheado. Se simplesmente trocassemos settings.json e deletassemos
# o arquivo, todas as tool calls dessas sessoes virariam fail silent ate
# restart. O shim mantem o path estavel e delega pro entry point novo.
LEGACY_SHIM_CONTENT = """\
#!/usr/bin/env bash
# Compat shim: hook PostToolUse migrado para entry point Python.
# Sessoes Claude Code abertas ANTES da migracao tem o path antigo cacheado em
# memoria — sem este shim, todas as tool calls dessas sessoes virariam fail
# silent ate o restart. Delega pro entry point novo via PATH.
exec claude-dash-audit-hook "$@"
"""

# Path do script root gerado
ROOT_SETUP_SCRIPT = Path("/tmp/claude-audit-root-setup.sh")
ROOT_UNINSTALL_SCRIPT = Path("/tmp/claude-audit-root-uninstall.sh")


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

def _read_template(name: str) -> str:
    """Le template empacotado em ``claude_dash.audit.templates``."""
    return resources.files("claude_dash.audit.templates").joinpath(name).read_text(
        encoding="utf-8"
    )


def _render_logrotate_user(local_log: Path, archive_dir: Path, user: str) -> str:
    tpl = _read_template("logrotate-user-claude-audit.conf")
    return (
        tpl.replace("__LOCAL_LOG__", str(local_log))
        .replace("__ARCHIVE_DIR__", str(archive_dir))
        .replace("__USER__", user)
    )


# ---------------------------------------------------------------------------
# Detector de estado
# ---------------------------------------------------------------------------

@dataclass
class State:
    settings_present: bool = False
    posttooluse_entries: list[dict] = field(default_factory=list)
    has_legacy_wire: bool = False
    has_new_wire: bool = False
    legacy_hook_file_exists: bool = False

    local_log_exists: bool = False
    archive_dir_exists: bool = False
    logrotate_user_exists: bool = False
    systemd_service_exists: bool = False
    systemd_timer_exists: bool = False
    timer_active: bool = False

    rsyslog_conf_exists: bool = False
    logrotate_sys_exists: bool = False
    var_log_dir_exists: bool = False
    var_log_file_exists: bool = False

    def mode(self) -> str:
        """fresh | migrate | partial | installed."""
        any_user = (
            self.local_log_exists
            or self.logrotate_user_exists
            or self.systemd_timer_exists
            or self.has_new_wire
        )
        any_root = (
            self.rsyslog_conf_exists
            or self.logrotate_sys_exists
            or self.var_log_dir_exists
        )
        if self.has_legacy_wire and not self.has_new_wire:
            return "migrate"
        all_user = (
            self.local_log_exists
            and self.logrotate_user_exists
            and self.systemd_timer_exists
            and self.has_new_wire
        )
        all_root = (
            self.rsyslog_conf_exists
            and self.logrotate_sys_exists
            and self.var_log_dir_exists
        )
        if all_user and all_root:
            return "installed"
        if not any_user and not any_root:
            return "fresh"
        return "partial"


def _load_settings() -> dict:
    if not SETTINGS_JSON.is_file():
        return {}
    try:
        return json.loads(SETTINGS_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _detect_state() -> State:
    s = State()
    settings = _load_settings()
    s.settings_present = bool(settings)

    # PostToolUse
    hooks = (settings.get("hooks") or {}).get("PostToolUse") or []
    for entry in hooks:
        for h in entry.get("hooks", []) or []:
            cmd = h.get("command", "") or ""
            s.posttooluse_entries.append({"matcher": entry.get("matcher", ""), "command": cmd})
            if LEGACY_HOOK_CMD_FRAGMENT in cmd:
                s.has_legacy_wire = True
            if NEW_HOOK_CMD in cmd:
                s.has_new_wire = True

    s.legacy_hook_file_exists = LEGACY_HOOK.is_file()

    # User-mode artefatos
    s.local_log_exists = LOCAL_LOG_DEFAULT.is_file()
    s.archive_dir_exists = ARCHIVE_DIR_DEFAULT.is_dir()
    s.logrotate_user_exists = LOGROTATE_USER_CONF.is_file()
    s.systemd_service_exists = SYSTEMD_SERVICE.is_file()
    s.systemd_timer_exists = SYSTEMD_TIMER.is_file()
    if s.systemd_timer_exists:
        try:
            r = subprocess.run(
                ["systemctl", "--user", "is-active", "claude-audit-rotate.timer"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            s.timer_active = r.stdout.strip() == "active"
        except Exception:
            s.timer_active = False

    # Root-mode artefatos (legiveis sem sudo)
    s.rsyslog_conf_exists = RSYSLOG_CONF.is_file()
    s.logrotate_sys_exists = LOGROTATE_SYS_CONF.is_file()
    s.var_log_dir_exists = VAR_LOG_DIR.is_dir()
    s.var_log_file_exists = VAR_LOG_FILE.is_file()
    return s


# ---------------------------------------------------------------------------
# Acoes user-mode
# ---------------------------------------------------------------------------

@dataclass
class Plan:
    actions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _action(plan: Plan, msg: str, dry_run: bool, fn) -> None:
    plan.actions.append(("[dry-run] " if dry_run else "") + msg)
    if not dry_run:
        fn()


def _ensure_user_artifacts(plan: Plan, dry_run: bool) -> None:
    user = os.environ.get("USER") or Path.home().name
    archive = ARCHIVE_DIR_DEFAULT
    local_log = LOCAL_LOG_DEFAULT

    if not archive.is_dir():
        def _mk_archive() -> None:
            archive.mkdir(parents=True, exist_ok=True)
            os.chmod(archive.parent, 0o700)
            os.chmod(archive, 0o700)
        _action(plan, f"mkdir -p {archive} (mode 0700)", dry_run, _mk_archive)

    if not local_log.is_file():
        def _create_log() -> None:
            local_log.parent.mkdir(parents=True, exist_ok=True)
            local_log.touch()
            os.chmod(local_log, 0o600)
        _action(plan, f"touch {local_log} (mode 0600)", dry_run, _create_log)

    if not LOGROTATE_USER_CONF.is_file():
        rendered = _render_logrotate_user(local_log, archive, user)
        def _write_logrotate() -> None:
            LOGROTATE_USER_CONF.parent.mkdir(parents=True, exist_ok=True)
            LOGROTATE_USER_CONF.write_text(rendered, encoding="utf-8")
        _action(plan, f"escrever {LOGROTATE_USER_CONF}", dry_run, _write_logrotate)

    if not SYSTEMD_SERVICE.is_file():
        content = _read_template("systemd-claude-audit-rotate.service")
        def _write_svc() -> None:
            SYSTEMD_USER_DIR.mkdir(parents=True, exist_ok=True)
            SYSTEMD_SERVICE.write_text(content, encoding="utf-8")
        _action(plan, f"escrever {SYSTEMD_SERVICE}", dry_run, _write_svc)

    if not SYSTEMD_TIMER.is_file():
        content = _read_template("systemd-claude-audit-rotate.timer")
        def _write_tmr() -> None:
            SYSTEMD_USER_DIR.mkdir(parents=True, exist_ok=True)
            SYSTEMD_TIMER.write_text(content, encoding="utf-8")
        _action(plan, f"escrever {SYSTEMD_TIMER}", dry_run, _write_tmr)


def _install_legacy_shim(plan: Plan, dry_run: bool) -> None:
    """Garante que ``~/.claude/iris/hooks/audit-tool.sh`` existe como shim
    delegando pro entry point novo.

    Idempotente: se ja eh um shim valido, no-op. Caso contrario, sobrescreve
    (em ``migrate``: substitui o bash legado de 120 linhas por wrapper de
    1 linha; em ``fresh``: cria do zero pra cobrir sessoes pre-existentes
    que talvez ja tenham configurado audit-tool.sh manualmente).
    """
    if LEGACY_HOOK.is_file():
        try:
            current = LEGACY_HOOK.read_text(encoding="utf-8")
            if "claude-dash-audit-hook" in current and "exec" in current:
                return  # Ja eh shim valido — no-op
        except OSError:
            pass

    def _write_shim() -> None:
        LEGACY_HOOK.parent.mkdir(parents=True, exist_ok=True)
        LEGACY_HOOK.write_text(LEGACY_SHIM_CONTENT, encoding="utf-8")
        os.chmod(LEGACY_HOOK, 0o755)

    label = "instalar" if not LEGACY_HOOK.is_file() else "substituir por"
    _action(
        plan,
        f"{label} shim de compat em {LEGACY_HOOK} (delega pro entry point novo)",
        dry_run,
        _write_shim,
    )


def _enable_timer(plan: Plan, dry_run: bool) -> None:
    def _do() -> None:
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False, timeout=10)
        subprocess.run(
            ["systemctl", "--user", "enable", "--now", "claude-audit-rotate.timer"],
            check=False, timeout=10,
        )
    _action(plan, "systemctl --user daemon-reload && enable --now claude-audit-rotate.timer",
            dry_run, _do)


def _backup_settings() -> Path | None:
    if not SETTINGS_JSON.is_file():
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    bak = SETTINGS_JSON.with_suffix(f".json.bak.{stamp}")
    shutil.copy2(SETTINGS_JSON, bak)
    return bak


def _wire_settings(plan: Plan, dry_run: bool, mode: str) -> None:
    """Re-wira settings.json.

    Em ``migrate``: substitui o command da entry com ``audit-tool.sh`` por
    ``claude-dash-audit-hook``, preservando matcher e demais hooks.
    Em ``fresh``/``partial``: garante que existe uma entry PostToolUse
    matcher ``.*`` apontando para ``claude-dash-audit-hook``, sem mexer
    nas outras.
    """
    settings = _load_settings()
    if not isinstance(settings, dict):
        plan.warnings.append("settings.json invalido; pulando wire")
        return

    hooks = settings.setdefault("hooks", {})
    pt = hooks.setdefault("PostToolUse", [])

    changed = False
    found_new = False

    for entry in pt:
        for h in entry.get("hooks", []) or []:
            cmd = h.get("command", "") or ""
            if NEW_HOOK_CMD in cmd:
                found_new = True
            if LEGACY_HOOK_CMD_FRAGMENT in cmd and NEW_HOOK_CMD not in cmd:
                h["command"] = NEW_HOOK_CMD
                changed = True

    if not found_new and not changed:
        # Fresh ou partial sem wire: adiciona entry nova
        pt.append({
            "matcher": ".*",
            "hooks": [{
                "type": "command",
                "command": NEW_HOOK_CMD,
                "timeout": 5,
            }],
        })
        changed = True

    if not changed:
        plan.actions.append("settings.json: ja wired para claude-dash-audit-hook")
        return

    bak_label = "settings.json.bak.<ts>"
    prefix = "[dry-run] " if dry_run else ""
    plan.actions.append(f"{prefix}re-wire {SETTINGS_JSON} (backup {bak_label})")
    if dry_run:
        return

    bak = _backup_settings()
    if bak:
        plan.actions.append(f"  backup criado: {bak}")
    SETTINGS_JSON.parent.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_JSON.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(SETTINGS_JSON)


# ---------------------------------------------------------------------------
# Geracao do script root
# ---------------------------------------------------------------------------

def _root_setup_script() -> str:
    rsyslog_body = _read_template("rsyslog-30-claude-audit.conf")
    logrotate_body = _read_template("logrotate-system-claude-audit")
    # Heredocs com EOF unico por bloco; sem expansao de variaveis
    return f"""#!/usr/bin/env bash
# Bootstrap root-mode do audit log do Claude Code.
# Idempotente: rodar varias vezes produz o mesmo estado.
# Gerado por `claude-dash setup-audit` em {datetime.now().isoformat(timespec='seconds')}.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Este script precisa ser executado como root: sudo bash $0" >&2
  exit 1
fi

# 1. /var/log/claude/ (owner syslog:adm, mode 0750)
if [ ! -d /var/log/claude ]; then
  mkdir -p /var/log/claude
fi
chown syslog:adm /var/log/claude
chmod 0750 /var/log/claude

if [ ! -f /var/log/claude/tools.log ]; then
  touch /var/log/claude/tools.log
fi
chown syslog:adm /var/log/claude/tools.log
chmod 0640 /var/log/claude/tools.log

# 2. Regra rsyslog
cat > /etc/rsyslog.d/30-claude-audit.conf <<'RSYSLOG_EOF'
{rsyslog_body.rstrip()}
RSYSLOG_EOF
chmod 0644 /etc/rsyslog.d/30-claude-audit.conf

# 3. Logrotate sistema
cat > /etc/logrotate.d/claude-audit <<'LOGROTATE_EOF'
{logrotate_body.rstrip()}
LOGROTATE_EOF
chmod 0644 /etc/logrotate.d/claude-audit

# 4. Restart rsyslog para aplicar a nova regra
systemctl restart rsyslog

echo "OK — bootstrap root-mode concluido."
echo "Smoke test:"
echo "  logger -t claude-audit -p local0.info -- \\"bootstrap test \\$(date -Iseconds)\\""
echo "  sudo tail -1 /var/log/claude/tools.log"
"""


def _root_uninstall_script() -> str:
    stamp = datetime.now().isoformat(timespec="seconds")
    return f"""#!/usr/bin/env bash
# Uninstall root-mode do audit log do Claude Code.
# Remove rsyslog rule e logrotate sistema; PRESERVA /var/log/claude/tools.log
# (forense). Para apagar de vez: rm -rf /var/log/claude/ apos validar.
# Gerado por `claude-dash setup-audit --uninstall` em {stamp}.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Este script precisa ser executado como root: sudo bash $0" >&2
  exit 1
fi

rm -f /etc/rsyslog.d/30-claude-audit.conf
rm -f /etc/logrotate.d/claude-audit
systemctl restart rsyslog

echo "OK — rsyslog rule e logrotate sistema removidos."
echo "/var/log/claude/ preservado. Para apagar:"
echo "  sudo rm -rf /var/log/claude/"
"""


def _write_root_script(path: Path, body: str, dry_run: bool) -> None:
    if dry_run:
        return
    path.write_text(body, encoding="utf-8")
    os.chmod(path, 0o755)


# ---------------------------------------------------------------------------
# Uninstall user-mode
# ---------------------------------------------------------------------------

def _uninstall_user(plan: Plan, dry_run: bool) -> None:
    # Para o timer e desabilita
    if SYSTEMD_TIMER.is_file():
        def _stop() -> None:
            subprocess.run(["systemctl", "--user", "disable", "--now", "claude-audit-rotate.timer"],
                           check=False, timeout=10)
        _action(plan, "systemctl --user disable --now claude-audit-rotate.timer", dry_run, _stop)

    for p in (SYSTEMD_TIMER, SYSTEMD_SERVICE, LOGROTATE_USER_CONF):
        if p.is_file():
            _action(plan, f"rm {p}", dry_run, lambda p=p: p.unlink())

    # Remove shim de compat — mas SO se for o nosso shim (preserva arquivo
    # que o usuario tenha customizado).
    if LEGACY_HOOK.is_file():
        try:
            current = LEGACY_HOOK.read_text(encoding="utf-8")
            if "claude-dash-audit-hook" in current and "exec" in current:
                _action(
                    plan,
                    f"rm {LEGACY_HOOK} (shim)",
                    dry_run,
                    lambda: LEGACY_HOOK.unlink(),
                )
        except OSError:
            pass

    # Daemon-reload final
    def _reload() -> None:
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False, timeout=10)
    _action(plan, "systemctl --user daemon-reload", dry_run, _reload)

    # Re-wire: remove a entry com claude-dash-audit-hook
    settings = _load_settings()
    if isinstance(settings, dict):
        hooks = settings.get("hooks") or {}
        pt = hooks.get("PostToolUse") or []
        new_pt = []
        changed = False
        for entry in pt:
            sub = []
            for h in entry.get("hooks", []) or []:
                if NEW_HOOK_CMD in (h.get("command", "") or ""):
                    changed = True
                    continue
                sub.append(h)
            if sub:
                e2 = dict(entry)
                e2["hooks"] = sub
                new_pt.append(e2)
            else:
                changed = True  # entry vazia removida
        if changed:
            hooks["PostToolUse"] = new_pt
            prefix = "[dry-run] " if dry_run else ""
            plan.actions.append(f"{prefix}remover wire de {SETTINGS_JSON}")
            if not dry_run:
                bak = _backup_settings()
                if bak:
                    plan.actions.append(f"  backup criado: {bak}")
                tmp = SETTINGS_JSON.with_suffix(".json.tmp")
                payload = json.dumps(settings, indent=2, ensure_ascii=False) + "\n"
                tmp.write_text(payload, encoding="utf-8")
                tmp.replace(SETTINGS_JSON)

    plan.warnings.append(f"sessions.log preservado em {LOCAL_LOG_DEFAULT} (nao apagado).")


# ---------------------------------------------------------------------------
# Pretty-print
# ---------------------------------------------------------------------------

def _print_state(state: State) -> None:
    def yn(b: bool) -> str:
        return "sim" if b else "nao"

    legacy = "sim (audit-tool.sh)" if state.has_legacy_wire else "nao"
    new_wire = "sim (claude-dash-audit-hook)" if state.has_new_wire else "nao"
    timer = f"{yn(state.systemd_timer_exists)} (active={state.timer_active})"

    print("Estado detectado:")
    print(f"  modo                : {state.mode()}")
    print(f"  settings.json       : {yn(state.settings_present)}")
    print(f"  wire legado         : {legacy}")
    print(f"  wire novo           : {new_wire}")
    print(f"  hook legado em FS   : {yn(state.legacy_hook_file_exists)} ({LEGACY_HOOK})")
    print(f"  sessions.log        : {yn(state.local_log_exists)}")
    print(f"  archive dir         : {yn(state.archive_dir_exists)}")
    print(f"  logrotate user      : {yn(state.logrotate_user_exists)}")
    print(f"  systemd timer       : {timer}")
    print(f"  rsyslog rule        : {yn(state.rsyslog_conf_exists)}")
    print(f"  logrotate sistema   : {yn(state.logrotate_sys_exists)}")
    print(f"  /var/log/claude/    : {yn(state.var_log_dir_exists)}")


def _print_plan(plan: Plan) -> None:
    if plan.actions:
        print("\nAcoes:")
        for a in plan.actions:
            print(f"  - {a}")
    if plan.warnings:
        print("\nAvisos:")
        for w in plan.warnings:
            print(f"  ! {w}")


# ---------------------------------------------------------------------------
# Entry-point
# ---------------------------------------------------------------------------

def main_setup_audit(args: argparse.Namespace) -> int:
    state = _detect_state()
    mode = state.mode()

    if args.print_sudo:
        print(f"# === {ROOT_SETUP_SCRIPT} ===")
        print(_root_setup_script())
        print(f"# === {ROOT_UNINSTALL_SCRIPT} ===")
        print(_root_uninstall_script())
        return 0

    _print_state(state)
    plan = Plan()

    if args.uninstall:
        print("\nModo: UNINSTALL")
        _uninstall_user(plan, args.dry_run)
        body = _root_uninstall_script()
        _write_root_script(ROOT_UNINSTALL_SCRIPT, body, args.dry_run)
        prefix = "[dry-run] " if args.dry_run else ""
        plan.actions.append(f"{prefix}escrever {ROOT_UNINSTALL_SCRIPT}")
        _print_plan(plan)
        if not args.dry_run:
            print(f"\nProximo passo (root):  sudo bash {ROOT_UNINSTALL_SCRIPT}")
        return 0

    if mode == "installed":
        print("\nTudo OK — nada a fazer.")
        if state.legacy_hook_file_exists:
            print(
                f"\nAviso: arquivo legado ainda existe em {LEGACY_HOOK} — "
                f"delete manualmente apos validar."
            )
        return 0

    print(f"\nModo: {mode.upper()}")

    if mode == "migrate":
        # Re-wira settings + substitui audit-tool.sh por shim de compat.
        # NAO toca em rsyslog/logrotate/timer (ja estao certos).
        _wire_settings(plan, args.dry_run, mode)
        _install_legacy_shim(plan, args.dry_run)
        _print_plan(plan)
        print(
            f"\nNota: {LEGACY_HOOK} foi substituido por shim que delega pro entry point novo."
        )
        print(
            "      Sessoes Claude Code abertas antes do migrate continuam funcionando\n"
            "      via path cacheado; sessoes novas leem settings.json e usam o entry point."
        )
        print("\nNota: rsyslog/logrotate/timer ja estavam configurados pelo setup antigo.")
        print("      Nada a fazer no lado root para migracao.")
        return 0

    # fresh ou partial: bootstrap user-mode + script root
    _ensure_user_artifacts(plan, args.dry_run)
    if not state.timer_active:
        _enable_timer(plan, args.dry_run)
    _wire_settings(plan, args.dry_run, mode)
    _install_legacy_shim(plan, args.dry_run)

    body = _root_setup_script()
    _write_root_script(ROOT_SETUP_SCRIPT, body, args.dry_run)
    plan.actions.append(("[dry-run] " if args.dry_run else "") + f"escrever {ROOT_SETUP_SCRIPT}")

    _print_plan(plan)

    needs_root = not (
        state.rsyslog_conf_exists and state.logrotate_sys_exists and state.var_log_dir_exists
    )
    if needs_root and not args.dry_run:
        print(f"\nProximo passo (root):  sudo bash {ROOT_SETUP_SCRIPT}")
        print("Conteudo do script:    claude-dash setup-audit --print-sudo")
    return 0
