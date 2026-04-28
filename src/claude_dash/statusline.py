"""Statusline próprio do dashboard — auto-contido.

Substitui o `statusline-dashboard.sh` bash do usuário por uma versão
Python que (1) produz a mesma linha visual (modelo ⚡effort · style ·
barra_ctx · tokens · $custo · Δt · +add/-rem · 5h% · 7d% · [sessão]),
(2) captura `rate_limits` em /tmp para o dashboard consumir, (3)
opcionalmente envelopa um statusline externo pré-existente.

Wrap: se a env var `CLAUDE_DASH_STATUSLINE_WRAP` ou o campo
`statusline_wrap_path` em `~/.claude/.claude-dash.json` apontam para
um script, este é executado com o JSON no stdin e seu output é
retornado em vez do statusline próprio. O capture de rate_limits
continua rodando em paralelo — assim um usuário que tinha statusline
customizado preserva a aparência + ganha rate_limit capture.

Performance: ANSI cru em vez de `rich` para manter o startup abaixo
de 50ms (o statusline é chamado múltiplas vezes por interação).
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from claude_dash.rate_limit_capture import capture_from_json

# --- Locais de config --------------------------------------------------

DASHBOARD_CONFIG = Path(os.environ.get(
    "CLAUDE_DASH_CONFIG",
    Path.home() / ".claude" / ".claude-dash.json",
))

# Env var tem precedência sobre o config file
WRAP_ENV_VAR = "CLAUDE_DASH_STATUSLINE_WRAP"


# --- ANSI ---------------------------------------------------------------

RESET = "\033[0m"
DIM = "\033[2m"
BOLD = "\033[1m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
DIM_GREEN = "\033[2;32m"
DIM_RED = "\033[2;31m"
CYAN_BOLD = "\033[1;36m"
BG_YELLOW = "\033[1;33m"
BG_RED = "\033[1;31m"
DIM_WHITE = "\033[2;37m"

SEP = f"{DIM} │ {RESET}"


# --- Helpers de formato ------------------------------------------------


def _fmt_tokens(n: int) -> str:
    if n >= 1000:
        return f"{n / 1000:.0f}k"
    return str(n)


def _fmt_cost(usd: float) -> str:
    if usd >= 1.0:
        return f"${usd:,.2f}"
    return f"${usd:.4f}"


def _fmt_duration(ms: int) -> str:
    s = ms // 1000
    if s >= 86400:
        d, rem = divmod(s, 86400)
        return f"{d}d{rem // 3600}h"
    if s >= 3600:
        h, rem = divmod(s, 3600)
        return f"{h}h{rem // 60}m"
    if s >= 60:
        m, rem = divmod(s, 60)
        return f"{m}m{rem:02d}s"
    return f"{s}s"


def _fmt_reset_delta(resets_at_s: int) -> str:
    """Formato compacto do tempo restante até reset. Vazio se já resetou."""
    now = int(time.time())
    delta = resets_at_s - now
    if delta <= 0:
        return ""
    if delta >= 86400:
        days, rem = divmod(delta, 86400)
        return f"{days}d{rem // 3600}h"
    if delta >= 3600:
        hours, rem = divmod(delta, 3600)
        return f"{hours}h{rem // 60}m"
    return f"{delta // 60}m"


def _context_bar(pct_int: int, width: int = 12) -> tuple[str, str]:
    """Barra ASCII + cor por faixa. Retorna (texto_barra, cor_ansi)."""
    if pct_int >= 80:
        color = RED
    elif pct_int >= 50:
        color = YELLOW
    else:
        color = GREEN
    filled = pct_int * width // 100
    empty = width - filled
    return f"[{'█' * filled}{'░' * empty}]", color


def _rate_color_ansi(pct: float) -> str:
    if pct >= 85:
        return RED
    if pct >= 60:
        return YELLOW
    return DIM


# --- Effort level com cache por transcript mtime -----------------------


_EFFORT_CACHE_DIR = Path("/tmp")
_EFFORT_RE = re.compile(r"Set effort level to ([a-z]+)")


def _detect_effort(transcript_path: str) -> str:
    """Lê último 'Set effort level to X' do transcript, cacheado por mtime."""
    if not transcript_path:
        return ""
    path = Path(transcript_path)
    if not path.is_file():
        return ""

    # Cache por hash do path (12 chars) — mesmo esquema do bash
    tpath_hash = hashlib.md5(transcript_path.encode()).hexdigest()[:12]
    cache_file = _EFFORT_CACHE_DIR / f".claude-statusline-effort-{tpath_hash}"

    try:
        mtime = int(path.stat().st_mtime)
    except OSError:
        return ""

    # Tenta cache
    if cache_file.is_file():
        try:
            cached_mtime, cached_val = cache_file.read_text().splitlines()[:2]
            if cached_mtime == str(mtime):
                return cached_val.strip()
        except (OSError, ValueError):
            pass

    # Cache miss → lê transcript do fim pra frente
    effort = ""
    try:
        # Lê em blocos do fim pra frente para não carregar arquivo gigante
        with path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            chunk = 64 * 1024
            data = b""
            while size > 0 and not effort:
                read_size = min(chunk, size)
                size -= read_size
                f.seek(size)
                data = f.read(read_size) + data
                for m in _EFFORT_RE.finditer(data.decode(errors="ignore")):
                    effort = m.group(1)
                # Reset: última ocorrência é a válida
                if effort:
                    all_matches = _EFFORT_RE.findall(data.decode(errors="ignore"))
                    if all_matches:
                        effort = all_matches[-1]
    except OSError:
        pass

    # Grava cache mesmo se vazio (evita reler)
    with contextlib.suppress(OSError):
        cache_file.write_text(f"{mtime}\n{effort}\n")
    return effort


def _effort_from_settings() -> str:
    """Fallback: lê effortLevel global de ~/.claude/settings.json."""
    settings = Path.home() / ".claude" / "settings.json"
    if not settings.is_file():
        return ""
    try:
        data = json.loads(settings.read_text())
        return str(data.get("effortLevel") or "")
    except (OSError, json.JSONDecodeError):
        return ""


def _effort_badge(effort: str) -> str:
    """Badge colorido do effort (bg colorido quando alto)."""
    eff = (effort or "").lower().strip()
    if eff in ("xhigh", "max"):
        return f" {BG_RED}⚡{eff}{RESET}"
    if eff == "high":
        return f" {BG_YELLOW}⚡high{RESET}"
    if eff in ("low", "medium"):
        return f" {DIM_WHITE}⚡{eff}{RESET}"
    if not eff:
        return f" {DIM_WHITE}⚡?{RESET}"
    return f" {BG_YELLOW}⚡{eff}{RESET}"


# --- Wrap (env var ou config file) -------------------------------------


def _resolve_wrap_path() -> str | None:
    """Resolve o path do statusline externo a envelopar, se houver."""
    env = os.environ.get(WRAP_ENV_VAR)
    if env:
        return env
    if not DASHBOARD_CONFIG.is_file():
        return None
    try:
        data = json.loads(DASHBOARD_CONFIG.read_text())
        wrap = data.get("statusline_wrap_path")
        return wrap if isinstance(wrap, str) and wrap else None
    except (OSError, json.JSONDecodeError):
        return None


def _run_wrap(wrap_path: str, raw_input: str) -> str | None:
    """Executa wrap script com raw_input via stdin; retorna stdout ou None em falha.

    Expande `~` no path antes de executar. O Claude Code aceita
    `"command": "~/.claude/script.sh"` em settings.json e resolve o
    til no próprio harness; quando preservamos esse path como
    wrap_path, o execv NÃO expande — resultaria em FileNotFoundError
    e fallback silencioso para o renderer próprio.
    """
    expanded = os.path.expanduser(wrap_path)
    try:
        result = subprocess.run(
            [expanded],
            input=raw_input,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return None
        return result.stdout.rstrip("\n")
    except (OSError, subprocess.TimeoutExpired):
        return None


# --- Renderer próprio --------------------------------------------------


def render_statusline(data: dict) -> str:
    """Monta a linha de status a partir do JSON do Claude Code.

    Ordem dos segmentos (compatível com statusline-dashboard.sh bash):
        modelo ⚡effort · style · [barra] N% tokens · $custo · duração ·
        +add/-rem · 5h% · 7d% · [sessão]
    """
    segments: list[str] = []

    # 1. Modelo + effort
    model = (
        data.get("model", {}).get("display_name")
        or data.get("model", {}).get("id")
        or "N/A"
    )
    transcript = data.get("transcript_path") or ""
    effort = _detect_effort(transcript) or _effort_from_settings()
    segments.append(f"{CYAN_BOLD}{model}{RESET}{_effort_badge(effort)}")

    # 2. Output style (se não default)
    style = data.get("output_style", {}).get("name") or ""
    if style and style != "default":
        segments.append(f"{DIM}{style}{RESET}")

    # 3. Barra de contexto + tokens
    ctx = data.get("context_window") or {}
    used_pct = ctx.get("used_percentage")
    if used_pct is not None:
        pct_int = round(float(used_pct))
        bar, color = _context_bar(pct_int)
        ctx_seg = f"{BOLD}{color}{bar} {pct_int}%{RESET}"
        input_tokens = (ctx.get("current_usage") or {}).get("input_tokens")
        ctx_size = ctx.get("context_window_size")
        if input_tokens is not None and ctx_size is not None:
            tokens_str = f"{_fmt_tokens(int(input_tokens))}/{_fmt_tokens(int(ctx_size))}"
            ctx_seg += f" {DIM}{tokens_str}{RESET}"
        segments.append(ctx_seg)

    # 4. Custo
    cost = data.get("cost") or {}
    cost_usd = cost.get("total_cost_usd")
    if cost_usd is not None:
        segments.append(f"{DIM}{_fmt_cost(float(cost_usd))}{RESET}")

    # 5. Duração API
    dur_ms = cost.get("total_api_duration_ms")
    if dur_ms is not None:
        segments.append(f"{DIM}{_fmt_duration(int(dur_ms))}{RESET}")

    # 6. Diff
    lines_add = cost.get("total_lines_added")
    lines_rem = cost.get("total_lines_removed")
    if lines_add is not None or lines_rem is not None:
        add_val = lines_add if lines_add is not None else 0
        rem_val = lines_rem if lines_rem is not None else 0
        segments.append(
            f"{DIM_GREEN}+{add_val}{RESET}{DIM}/{RESET}{DIM_RED}-{rem_val}{RESET}"
        )

    # 7. Rate limits
    rl = data.get("rate_limits") or {}
    for label, key in (("5h", "five_hour"), ("7d", "seven_day")):
        window = rl.get(key)
        if not window:
            continue
        pct = window.get("used_percentage")
        if pct is None:
            continue
        color = _rate_color_ansi(float(pct))
        seg = f"{DIM}{label}:{RESET}{color}{float(pct):.0f}%{RESET}"
        resets_at = window.get("resets_at")
        if resets_at:
            delta = _fmt_reset_delta(int(resets_at))
            if delta:
                seg += f"{DIM} ↻{delta}{RESET}"
        segments.append(seg)

    # 8. Sessão (se campo não documentado existir)
    session_name = data.get("session_name")
    if session_name:
        segments.append(f"{DIM}[{session_name}]{RESET}")

    return SEP.join(segments)


# --- Entrypoint --------------------------------------------------------


def main() -> int:
    """Lê stdin, captura rate_limits, renderiza (ou invoca wrap)."""
    raw = sys.stdin.read()

    # Captura rate_limits em background — nunca falha o statusline
    with contextlib.suppress(Exception):
        capture_from_json(raw)

    # Wrap: se configurado, usa output do script externo
    wrap_path = _resolve_wrap_path()
    if wrap_path:
        wrapped = _run_wrap(wrap_path, raw)
        if wrapped is not None:
            sys.stdout.write(wrapped + "\n")
            return 0
        # Wrap falhou → cai no renderer próprio (silent fallback)

    # Renderer próprio
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return 0  # silent fail para não poluir statusline

    sys.stdout.write(render_statusline(data) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
