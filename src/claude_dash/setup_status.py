"""Setup idempotente: registra claude-dash-statusline como statusLine oficial.

Tratamento de estado:
- Se `statusLine.command` já é `claude-dash-statusline` → noop
- Se `statusLine.command` aponta para outro script → preserva o path
  em `~/.claude/.claude-dash.json:statusline_wrap_path` (wrap chain)
  e reatribui para `claude-dash-statusline`
- Se não há statusLine configurado → adiciona limpo
- Faz backup de settings.json e .claude-dash.json antes de escrever
"""
from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime
from pathlib import Path


SETTINGS_JSON = Path.home() / ".claude" / "settings.json"
DASHBOARD_CONFIG = Path.home() / ".claude" / ".claude-dash.json"
BACKUP_DIR = Path.home() / ".claude" / "backups"
STATUSLINE_CMD = "claude-dash-statusline"


def _backup(path: Path) -> Path | None:
    """Copia arquivo para backups com timestamp; retorna path do backup."""
    if not path.is_file():
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = BACKUP_DIR / f"{path.name}.backup.{stamp}"
    shutil.copy2(path, dest)
    return dest


def _load_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _save_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(path)


def run() -> int:
    """Idempotente — pode rodar várias vezes sem quebrar.

    Imprime relatório do que foi feito (ou não) no stdout.
    """
    settings = _load_json(SETTINGS_JSON)
    dash_cfg = _load_json(DASHBOARD_CONFIG)

    current_statusline = settings.get("statusLine") or {}
    current_cmd = (
        current_statusline.get("command") if isinstance(current_statusline, dict) else None
    )

    print("claude-dash setup-status")
    print(f"  settings.json : {SETTINGS_JSON}")
    print(f"  dashboard cfg : {DASHBOARD_CONFIG}")
    print()

    # Caso 1: já está configurado com nosso statusline → idempotente noop
    if current_cmd == STATUSLINE_CMD:
        print(f"✓ statusLine já aponta para '{STATUSLINE_CMD}'; nada a fazer.")
        wrap_path = dash_cfg.get("statusline_wrap_path")
        if wrap_path:
            print(f"  Wrap ativo: {wrap_path}")
        return 0

    # Backup preventivo antes de escrever
    settings_bak = _backup(SETTINGS_JSON)
    cfg_bak = _backup(DASHBOARD_CONFIG)
    if settings_bak:
        print(f"Backup settings.json  → {settings_bak}")
    if cfg_bak:
        print(f"Backup .claude-dash.json → {cfg_bak}")

    # Caso 2: há um statusLine externo configurado — preserva via wrap
    if current_cmd:
        print(
            f"Detectei statusLine existente: '{current_cmd}'.\n"
            f"  → preservando em statusline_wrap_path (chain)"
        )
        dash_cfg["statusline_wrap_path"] = current_cmd
        _save_json_atomic(DASHBOARD_CONFIG, dash_cfg)

    # Caso 3 (ou seguinte do 2): registra o nosso como statusLine ativo
    settings["statusLine"] = {
        "type": "command",
        "command": STATUSLINE_CMD,
        "padding": 0,
    }
    _save_json_atomic(SETTINGS_JSON, settings)

    print()
    print(f"✓ statusLine agora aponta para '{STATUSLINE_CMD}'")
    print("  Reinicie uma sessão do Claude Code para ativar.")
    print()
    print("Para remover: edite ~/.claude/settings.json e remova o bloco")
    print("  statusLine (ou restaure a partir do backup acima).")
    return 0


def main() -> int:
    try:
        return run()
    except Exception as exc:  # noqa: BLE001
        print(f"erro: {exc}", file=sys.stderr)
        return 1
