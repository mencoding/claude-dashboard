# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.15.9] - 2026-04-28

### Added
- TUI: aba `Audit` ganhou **multi-select via Space**. Pressionar Space na linha sob o cursor toggla mark — entries marcadas mostram `✓` na nova coluna `Mk` (primeira coluna). Footer status indica `marked=N` quando há marcadas. Pressionar Space numa entry já marcada remove a marca.
- TUI: tecla **`Enter`** agora dispara ação contextual:
  - **0-1 marcadas** → drill-down do cursor (mesmo comportamento da tecla `s`).
  - **2+ marcadas** → comparison cross-session usando os `session_id`s das marcadas (sem precisar do prompt manual de SIDs).
- TUI: tecla `c` (compare) também aceita marcadas: com 2+ marcadas, compara direto; com 0-1, abre o prompt manual (comportamento antigo).
- 2 testes novos em `tests/test_audit_view.py` cobrindo `_marked_session_ids` e `_clear_marks_and_refresh`.

### Fixed
- TUI: bug do auto-tail forçando cursor pra última linha **voltou a aparecer após o split 50/50 da v0.15.8**. O fix anterior dependia exclusivamente do evento `RowHighlighted` do Textual + flag `_audit_cursor_managed`, que tem race condition se o evento chegar atrasado em relação ao próximo tick. Adicionada **segunda linha de defesa determinística**: rastreio de `_audit_last_set_cursor` (posição que setamos no último tick); no tick seguinte, se o cursor está em posição diferente da que registramos, o usuário moveu — marca interação independente de timing de evento. Testes que cobrem o caminho via `pilot.press("up")` continuam verdes.

### Changed
- DataTable da aba `Audit` agora tem 8 colunas (era 7): nova coluna `Mk` no início, seguida de `time`, `sess`, `host`, `tool`, `dur_ms`, `in`, `out`. `views/audit.py:render_table` (Rich, usado pra rendering shell standalone) permanece com 7 colunas — a `Mk` é exclusiva da TUI.
- Linha de atalhos in-tab atualizada: `Spc mark`, `Enter/s drill-down`, `c compare`.

## [0.15.8] - 2026-04-28

### Added
- TUI: drill-down forense (`s` na aba Audit) agora renderiza cada linha do `tools.log` como **painel humano-legível** com cada campo identificado por label — `Timestamp:`, `Hostname:`, `PID:`, `Event:`, `Session:`, `Tool:`, `Tool use ID:`, `Status:`, `Duration:`, `Permission mode:`, `Input SHA:`, `Input bytes:`, `Output bytes:` + trailing fields tool-específicos: `CWD:`, `Command:` (Bash), `Path:` (Read/Edit/Write), `URL:` (WebFetch), `Query:` (WebSearch), `Skill:`, `Subagent:` + `Description:` (Agent). Status em cores (`red` pra erro, `yellow` pra running, `green` pra success); border do painel vermelho quando entry é erro. Antes mostrava texto bruto RFC 5424 que era ilegível em scan rápido.
- Novo helper `claude_dash.audit.parser.parse_full_line(line)` — retorna `(AuditEntry, extra_fields)` extraindo cwd/cmd/path/url/query/skill do trailing após o `]`. Esses campos só existem no `/var/log/claude/tools.log` (forense completo), não na `sessions.log` metadata-only.
- Novo `views/audit.py:render_drill_down_human(line)` — pure function que produz Rich Panel labeled. Testável sem TUI.

### Changed
- TUI: aba `Audit` agora usa **split 50/50** entre tabela e painel inferior (era 70/30). Drill-down e comparação ganharam espaço útil. `#audit-detail` ganhou `overflow-y: scroll` + `scrollbar-gutter: stable` — barra de rolagem sempre visível, sem layout shift quando o conteúdo cresce.

### Fixed
- TUI: comparação cross-session (`c`) tinha conteúdo cortado quando 3+ sessões geravam mais linhas do que cabia em ~25% da tela. Agora com 50% de altura + scrollbar dedicado, o conteúdo todo fica acessível via scroll.

## [0.15.7] - 2026-04-28

### Fixed
- `claude-dash setup-audit` now correctly detects pending `claude-audit` group migration as `partial` mode (was incorrectly reporting `installed` and saying "Tudo OK — nada a fazer"). State detector already tracked `claude_audit_group_exists` and `var_log_owned_by_claude_audit` since v0.15.0 (#52), but the `mode()` heuristic ignored both — leaving users who upgraded from v0.14.x stuck with a stale `/tmp/claude-audit-root-setup.sh` and no way to regenerate it short of manual `groupadd`/`usermod`/`chown`. Now adding a user to `claude-audit` after the upgrade simply requires re-running `claude-dash setup-audit` + `sudo bash /tmp/claude-audit-root-setup.sh` + re-login. Reported during validation on RET-DTI-601048.
- 2 new tests in `tests/test_audit_setup.py`: `test_state_partial_when_grupo_claude_audit_pendente` covers the mode detection; `test_setup_audit_regenera_script_quando_grupo_pendente` is the end-to-end regression — confirms `setup-audit` writes the root script with `groupadd -f claude-audit` and `usermod -aG` lines when group is missing.

## [0.15.6] - 2026-04-28

### Added
- TUI: `Audit` tab gained a `c` key for **cross-session comparison** (#38). Opens an inline prompt; user types 2-4 SID prefixes separated by space/comma/semicolon (e.g., `0efd3 a1b2 8f9c`). The audit-detail panel renders parallel columns — one per session — with timestamps, tool calls, and a correlations summary below. Useful when running 3+ Claude Code sessions in parallel and wanting to understand who did what and in what order.
- Correlation heuristics in new module `claude_dash.views.audit_compare`:
  - `same_path` / `same_cmd`: same `input_sha` across distinct sessions within ±500ms.
  - `read_then_edit`: session A read file X at t=0; session B edited X within 5s — classic signal of parallel-refactor race.
  - All correlations highlighted in `yellow` (or `bold red` for `read_then_edit`) in the comparison view.
- Pure-function helpers exported: `parse_compare_input(raw)`, `group_by_session(entries, sids)`, `merge_timelines(grouped)`, `find_correlations(grouped, window_ms=500)`. All testable without TUI.
- 15 new tests in `tests/test_audit_compare.py` covering parsing, grouping, merge ordering, all correlation reasons, edge cases (same session ignored, target-less tools ignored, out-of-window).
- In-tab key hints line includes `c compare`.

## [0.15.5] - 2026-04-28

### Changed
- **MCP `_infer_alerts` agora retorna alertas estruturados** (#54-d2). Cada alerta passou de string livre pra dict com campos: `level` (`"warning"` | `"critical"`), `code` (`"high_turn_tokens"` | `"idle_session"` | `"context_full"` | `"daily_cost"` | `"rate_limit_5h"` | `"rate_limit_7d"`), `session_id` (ou `None` quando agregado), `message` (human-readable, mantido pra UI/display), e `data` (dict com os números brutos, parseável por agentes). Agentes podem agora filtrar por `code`/`level` sem regex em string. Schema gravado em `docs/mcp-design.md`. **Breaking pra clientes que iteravam `for s in alerts` esperando string** — em pseudo-código novo: `for a in alerts: print(a["message"])`. Schema version permanece `1` (decisão pragmática: clientes não-estritos que faziam só `for a in alerts: print(a)` veem o `repr` do dict, não bom mas não-quebra).
- `context_full` agora distingue `level="critical"` (>180k) vs `"warning"` (>150k); `rate_limit_5h`/`rate_limit_7d` distinguem `"critical"` (>=95%) vs `"warning"` (>=85%).
- 3 testes novos verificam shape estruturado, thresholds de level, lista canônica de codes. 2 testes existentes adaptados pra dict shape.

## [0.15.4] - 2026-04-28

### Added
- MCP tool `dashboard_health()` — health check da pipeline do dashboard em uma chamada. Retorna `rsyslog_active` (via `systemctl is-active`), `audit_hook_wired` (via parse do `~/.claude/settings.json`), `audit_log_exists`, `audit_log_last_entry_age_seconds` (mtime da `sessions.log`), e lista `issues` humano-readable do que está errado (vazia = tudo OK). `systemctl` ausente (containers, non-systemd) -> `rsyslog_active=None` sem flagar issue. Closes part of #54 (issue derivada d4). 5 testes novos.

## [0.15.3] - 2026-04-28

### Added
- MCP tool `audit_entries(session, tool, status, host, hours, limit)` — exposes audit log metadata via MCP. Wraps `views/audit.filter_entries` with prefix-match filters; runs `correlate_start_end` first so end entries override their corresponding start. Returns most recent entries first (caller can `head` the result). Reads `~/.claude/iris/audit/sessions.log` only — `/var/log/claude/tools.log` (with `cmd`/`path`/`url`) is intentionally NOT exposed via MCP. Schema documented inline in docstring. Closes part of #54 (issue derivada d3).
- MCP tool `audit_session_partial(sid)` — drill-down parcial por sessão sem precisar do JSONL local. Útil pra meta-agentes consultando sessões cross-device ou sessões cujo transcript foi rotacionado. Wraps `audit.partial_stats.build_partial_stats_from_audit`. Retorna `{found, session_id, hostname, first_ts, last_ts, duration_ms, total_calls, error_count, error_rate, top_tools}`.
- 6 testes novos em `tests/test_mcp_server.py` cobrindo arquivo inexistente, filtros por tool e session prefix, envelope/filters_applied, drill-down found/not-found.

## [0.15.2] - 2026-04-28

### Added
- **PreToolUse hook (real-time tool tracking).** The `claude-dash-audit-hook` binary now distinguishes `PreToolUse` vs `PostToolUse` via `payload["hook_event_name"]`. PreToolUse emits a syslog line with msgid `TOOLSTART` and `event="start"` *before* the tool executes — before only PostToolUse emitted, so long-running tools (multi-minute Bash, Agent, slow WebFetch) appeared as silence on the Audit tab until they completed. Now they show up immediately as "running" italic-dim rows. When the matching `TOOLCALL` arrives, the row is replaced in-place with the real `dur_ms`/`output_bytes`/`status`. Closes #50.
- `AuditEntry.event: Literal["start","end"]` field (default `"end"` for backward compatibility with logs predating #50).
- `views/audit.py:correlate_start_end(entries)` pure function: collapses `start`+`end` pairs by `tool_use_id`, preserving orphan starts (tools still running) and entries without `tool_use_id`.
- TUI `_populate_audit_table` renders running rows with `italic dim` style + `dur_ms="running..."` + `out="—"`.
- Filter `status=error` now ignores `event="start"` entries (they have `status="running"` so wouldn't match anyway, but documented explicitly).
- `setup-audit` wires both `PreToolUse` and `PostToolUse` blocks in `~/.claude/settings.json` (idempotent — re-run to add `PreToolUse` to existing v0.15.x installs).

### Changed
- Audit log entries now include an explicit `event="end"` field in the STRUCTURED-DATA when emitted by the new hook (was implicit/absent before). Old logs continue to parse correctly — parser defaults `event` to `"end"` when absent.
- **Volume of audit log doubles.** Each tool call now produces 2 lines (start + end) instead of 1. Logrotate continues at 26-week retention; if you have very high tool throughput, you may want to revisit the rotation policy. Standard usage shouldn't notice.

### Migration

Re-run `claude-dash setup-audit` to wire the `PreToolUse` block (idempotent — won't disturb existing config). New sessions started after the re-run begin emitting both events; old sessions continue with PostToolUse only via cached settings until restarted.

## [0.15.1] - 2026-04-28

### Added
- MCP server: all 7 tools (`account_info`, `rate_limits`, `active_sessions`, `today_summary`, `tools_breakdown`, `session_details`, `workflow_snapshot`) now wrap their return through `_envelope()` adding `_schema_version: 1` (bump only on breaking changes — additions don't bump) and `dashboard_version` (current package version) at the top level. Versioning policy documented in `docs/mcp-design.md`. Closes part of #54 (issue derivada d1). 7 new tests in `tests/test_mcp_server.py`.

## [0.15.0] - 2026-04-28

### Changed
- **Audit log group migrated from `adm` to dedicated `claude-audit`.** `/var/log/claude/tools.log` is now `syslog:claude-audit 0640` instead of `syslog:adm 0640`. Templates `rsyslog-30-claude-audit.conf` (`fileGroup="claude-audit"`) and `logrotate-system-claude-audit` (`create 0640 syslog claude-audit`) updated accordingly. Closes #52.

### Added
- `claude-dash setup-audit` root script now creates the `claude-audit` group via `groupadd -f` (idempotent), adds the invoking user (`SUDO_USER`) via `usermod -aG`, and `chown`s `/var/log/claude/` plus existing rotated `.gz` files to `syslog:claude-audit`. Migration is automatic on re-run.
- State detector reports three new lines in `_print_state`: `grupo claude-audit`, `user no grupo`, `tools.log no grupo` — useful to verify migration completion at a glance.
- `claude_dash.audit.setup.CLAUDE_AUDIT_GROUP` exported as `"claude-audit"` constant; helpers `_claude_audit_group_exists()`, `_user_in_claude_audit_group()`, `_var_log_owned_by_claude_audit()` for state detection (testable).

### Migration

Re-run `claude-dash setup-audit` after upgrade:

```bash
claude-dash setup-audit
sudo bash /tmp/claude-audit-root-setup.sh
# Re-login (or `newgrp claude-audit` in a fresh shell) for group membership to take effect.
```

The script is idempotent — running it twice produces the same state. Existing v0.14.x installs that were on the `adm` group continue working until the script is re-run, but the TUI drill-down (`s` key) will fail with a permission error until the user is in `claude-audit`.

### Cross-distro

`adm` is a Debian-family convention; Fedora/Arch may lack it or use it differently. `claude-audit` is portable across distros via `groupadd`/`usermod` (shadow-utils, ubiquitous on mainstream Linux). Validated on Debian-family; not yet validated on RHEL/Fedora/Arch — please report if you run setup-audit on a non-Debian distro.

### Reasoning

- **Least privilege**: membership in `claude-audit` grants access only to Claude audit logs, not all of `/var/log/`.
- **Auditability**: `getent group claude-audit` lists exactly who has visibility.
- **Revocability**: removing a user from `claude-audit` revokes audit-only access without affecting other system roles.

## [0.14.7] - 2026-04-28

### Added
- TUI: `Audit` tab now emits push notifications when a new entry with `status="error"` arrives via the incremental tail. Configured via env var `CLAUDE_DASH_AUDIT_ALERT_LEVEL ∈ {none, toast, sound, both}` — default `none` preserves the previous quiet behavior. `toast` shows an in-app Textual notification; `sound` calls `notify-send` (libnotify, also adds the system "ding"); `both` does both. Anti-flood: same `tool_use_id` (or `session_id` fallback for legacy entries without `tool_use_id`) is deduplicated within a 60s window. `notify-send` absent from `$PATH` is silent — graceful degradation. Alerts fire even when the active tab isn't `Audit`. Closes #37.
- New module `claude_dash.audit.alerts` with pure-function `resolve_alert_level()` and `ErrorAlertEmitter` class. Testable in isolation with injectable `clock`, `textual_notify`, and `libnotify` callables.

### Fixed
- TUI: `Audit` tab cursor was still snapping back to the latest row at every refresh after the user moved it with ↑/↓. Root cause: arrow keys are consumed by the focused `DataTable` and stopped before bubbling to `App.on_key`, so the previous fix (which relied on `_audit_last_user_action` being set in `on_key`) never triggered for cursor navigation. Now uses `on_data_table_row_highlighted` to detect cursor changes regardless of source, with an internal `_audit_cursor_managed` flag to ignore programmatic moves from auto-tail. Regression test added in `tests/test_audit_view.py::test_cursor_user_pausa_auto_tail`.

## [0.14.6] - 2026-04-28

### Added
- TUI: `Audit` tab gained a `host` column showing each entry's `hostname`. Default behavior filters entries to the current host (via `socket.gethostname().split('.')[0]`); press `h` to toggle and see all hosts. Entries from a different host are rendered in `dim` style as a visual cue that the original transcript is on another machine. Empty `hostname` (legacy entries pre-#55) always pass — they're treated as "host unknown" rather than hidden. Closes part of #55.
- TUI: `Audit` tab gained an in-tab key-hint line above the global Footer, listing `/` filter, `t` window, `?` tests, `h` host, `s` drill-down, `e` export, `End` resume. The Audit-specific bindings stay `show=False` in the global Footer (to keep other tabs uncluttered), so this line is the canonical place to discover them.
- `parse_filter_input` accepts `/host=<name>` as a fourth filter key (prefix-match). When set, the implicit "current host only" toggle is suspended automatically so `/host=PREDATOR` works from any machine. Closes part of #55.
- Status footer shows `host=<current>` or `host=todos` depending on toggle state, alongside existing `window=`, `filter=`, `show-tests=` indicators.
- `claude-dash session <sid>` (and the `s` drill-down in the TUI) now has a three-tier response: (1) JSONL local present → full drill-down (unchanged); (2) JSONL absent but metadata exists in `sessions.log` → partial drill-down with `Origem: <hostname>` header, tool-calls table, aggregate of total/errors/duration (no per-turn timeline or per-turn cost — those need JSONL); (3) nothing → clear "Nao existem dados neste host" message instead of the previous misleading "transcript not found". Closes part of #55.
- New module `claude_dash.audit.partial_stats` with `PartialSessionStats` dataclass and `build_partial_stats_from_audit(sid, audit_log_path=...)` helper. Pure-function, testable in isolation; useful even outside the cross-device case (e.g., when a JSONL has been rotated locally on the origin machine).
- New constant `claude_dash.audit.CURRENT_HOSTNAME` (resolved at import) for callers that need consistent hostname identification.
- README "Audit log" section documents the cross-device behavior.

### Fixed
- TUI: `Audit` tab no longer snaps the cursor back to the latest row at every 1Hz refresh after the user has interacted recently. Auto-tail now respects `_is_audit_paused()` (the `_audit_last_user_action` grace window of `AUDIT_AUTO_SCROLL_GRACE_SEC`), so moving the cursor with ↑/↓ stays put. Press `End` to resume auto-tail explicitly.

## [0.14.5] - 2026-04-28

### Added
- TUI: `Audit` tab gained an `e` key for export. Opens an inline prompt for an output path; empty path defaults to `~/audit-export-<ts>.csv`. Path extension picks format (`.json` → JSON, otherwise CSV). Schema is the same for both formats: `timestamp, hostname, session_id, tool, subagent_type, tool_use_id, status, duration_ms, perm_mode, input_sha, input_bytes, output_bytes, pid`. Writes are atomic via `<path>.tmp` + `os.replace`. Closes #36.

### Fixed
- `claude-dash setup-audit` in `installed` mode no longer prints the contradictory "delete `audit-tool.sh` manually" warning when the legacy file is already the compat shim installed by v0.14.2 (PR #48). It now detects the shim via the `exec claude-dash-audit-hook` marker and stays silent. If a real legacy bash script is still in place under an otherwise `installed` state, the command auto-installs the shim instead of just complaining. Closes #49.

## [0.14.4] - 2026-04-28

### Fixed
- TUI: `Audit` tab drill-down (`s` key) was filtering by `session_id`, so all rows of the same session showed identical content (looked like "nothing changed" when moving the cursor between Bash/Edit/Read of the same session). Now filters by `tool_use_id` (unique per call) — each row drills into its specific entry. Header gained `HH:MM:SS tool` context. Falls back to `session_id` for legacy entries (e.g., Agent calls without `tool_use_id`).
- TUI: `_audit_visible_cache` now stores the same slice that's rendered in the `DataTable` (last `AUDIT_TABLE_MAX_ROWS=500` entries) instead of the full filtered list. Previously, `cursor_row` (a DataTable index) was used to index into the full list, picking the wrong entry whenever the filter produced more than 500 results.

### Changed
- Added `AUDIT_TABLE_MAX_ROWS = 500` constant (was hardcoded inline in `_populate_audit_table`).

### Changed
- TUI: `Audit` tab drill-down (`s` key) now reads `/var/log/claude/tools.log` directly instead of going through `pkexec`. The log file is `0640` with group `adm`, and users on Debian-family systems are typically in that group already, so direct read works without any password prompt. Falls back to a helpful message suggesting `sudo usermod -aG adm $USER` for users not in the group. No more Polkit dependency, no more hangs, no more password prompts.

### Removed
- `subprocess` and Polkit-related code paths in the audit drill-down (the entire `--disable-internal-agent` + `start_new_session=True` machinery was over-engineering once we realized direct read works for the target audience).

### Added
- TUI: arrow keys `←` / `→` cycle through the 5 tabs (App-level bindings without priority — DataTable and Input still consume them for internal navigation when focused).
- `setup-audit`: in `migrate` and `fresh`/`partial` modes, `~/.claude/iris/hooks/audit-tool.sh` is now installed as a compat shim that delegates to `claude-dash-audit-hook`. Idempotent. `--uninstall` removes the shim too (preserves user-customized files).

### Changed
- TUI: `Audit` tab now uses `DataTable` with row cursor instead of a static Rich Table. Arrow navigation, row highlight, and `Enter` selection work natively.
- TUI: `Audit` tab table renders incrementally — only new entries are appended each tick instead of full rebuild. Full rebuild only when filter/window changes or ring buffer rotates. Cursor position preserved during refresh.
- TUI: `Audit` tab `s` (drill-down) now uses the **selected row's** `session_id` (cursor position) instead of always the last visible entry.
- TUI: `Session` tab no longer pre-selects the first item on activation. Focus is set on the `ListView` via `call_after_refresh` so arrow keys take effect on first press.

### Fixed
- TUI: `pkexec` drill-down (`s` key) was hanging indefinitely when no graphical Polkit agent was running because the internal text-mode agent tried to read from the TTY which Textual already monopolizes. Fixed with `--disable-internal-agent` + `stdin=DEVNULL` + `start_new_session=True`. Returncode 127 now produces a helpful error message guiding the user to start a graphical agent.
- `setup-audit migrate`: previous behavior left `~/.claude/iris/hooks/audit-tool.sh` untouched and asked the user to delete it manually. But Claude Code reads `settings.json` only at session startup and caches the hook path; deleting the legacy file would silently break audit logging in any session opened before the migration. The shim resolves this — the file path stays valid and delegates to the new hook.

## [0.14.1] - 2026-04-28

### Fixed
- TUI: `Audit` tab drill-down (`s` key) no longer freezes the event loop while waiting for the Polkit password prompt. The `pkexec` call now runs in a background thread via `run_worker(thread=True)`, with an immediate placeholder ("Aguardando autorização...") shown to the user (closes [#45]).

## [0.14.0] - 2026-04-28

### Added
- TUI: new `Audit` tab with incremental tail of `~/.claude/iris/audit/sessions.log`, color-coded tool calls, filters (`/tool=`, `/error`, `/session=`), drill-down via `pkexec` to `/var/log/claude/tools.log`, and pausable auto-scroll (audit log feature, phase 2 of [#24], closes [#33]) ([#41]).
- Audit module: `parser.py`, `tail.py` (incremental tailer with offset+inode tracking), `models.py`, and `views/audit.py`.

## [0.13.1] - 2026-04-28

### Fixed
- TUI: `Ctrl+C` now exits directly instead of showing the Textual default quit popup ([#34]).

## [0.13.0] - 2026-04-27

### Added
- `claude-dash setup-audit` subcommand and packaged audit hook (audit log feature, phase 1 of [#24]) ([#30]).
- Version line displayed in the TUI header.

### Changed
- TUI: version is rendered as `Header` `SUB_TITLE` instead of a separate `Static` widget, restoring the bottom `Footer` ([#32]).

### Performance
- Aggregator: TTL cache in `collect_live_sessions` and `collect_sessions_since` reduces redundant filesystem scans ([#29], closes [#27]).

## [0.12.0] - 2026-04-27

### Added
- `claude-dash --version` flag ([#25], closes [#22]).
- Session naming via `/rename` exposed in MCP tools and TUI ([#26], closes [#23]).

## [0.11.3] - 2026-04-22

### Fixed
- Detects missing `statusLine` entry in `~/.claude/settings.json` and points the user to `setup-status` instead of showing a misleading "idle session" message ([#21]).

## [0.11.2] - 2026-04-22

### Added
- Explicit feedback when rate-limit bars cannot be displayed (missing hook data) ([#20]).

## [0.11.1] - 2026-04-22

### Changed
- Documentation: README aligned with the current harness — MCP CLI usage, `pipx` install, v0.11.x status ([#19]).

## [0.11.0] - 2026-04-21

### Changed
- The dashboard now owns the Claude Code `statusLine` (coupling inversion): instead of being a passive consumer, `claude-dash` registers and manages the statusLine entry itself ([#18]).

### Fixed
- `~` is correctly expanded in the wrapped path shown by the statusline.

## [0.10.0] - 2026-04-21

### Added
- Detects the real account plan (Max / Pro / Team) by reading `credentials.json` ([#17]).

## [0.9.1] - 2026-04-21

### Fixed
- Rate-limit display rendering issues.
- Separates `billing` from `extra_usage` in account information ([#16]).

## [0.9.0] - 2026-04-21

### Added
- Optional hook captures 5-hour and 7-day rate limits from the statusline and renders them as visual bars ([#15]).

## [0.8.0] - 2026-04-21

### Added
- Account info panel with billing awareness (flat-rate Max/Pro plans vs API pay-as-you-go) ([#14]).

## [0.7.3] - 2026-04-21

### Changed
- TUI: replaced ambiguous `●` / `○` state glyphs (which looked like radio buttons) with colored text labels ([#13]).

## [0.7.2] - 2026-04-21

### Fixed
- TUI: the Session tab drill-down was invisible due to a CSS layout bug ([#12]).

## [0.7.1] - 2026-04-21

### Fixed
- TUI: the `ListView` on the Session tab now receives focus automatically when the tab is activated ([#11]).

## [0.7.0] - 2026-04-21

### Added
- MCP server exposing the Claude Code state as queryable tools for agents ([#10]).

### Fixed
- TUI: pressing `Enter` on the Session tab now correctly triggers the drill-down ([#9]).

## [0.6.0] - 2026-04-21

### Added
- Interactive TUI mode: running `claude-dash` with no arguments opens a full-screen Textual app with four tabs (Now / Today / Tools / Session) navigable via keyboard (`1-4`, `←/→`, `r`, `q`) ([#7]).
- `scripts/claude-dash-tui` helper that opens the TUI in a new terminal window (auto-detects `gnome-terminal`, `kitty`, `alacritty`, etc.).

### Changed
- Explicit subcommands (`now` / `today` / `tools` / `session`) are preserved for scriptable use.

## [0.5.0] - 2026-04-21

First stable release.

### Added
- `claude-dash now` — live TUI view, refresh every 2 s ([#1], [#2]).
- `claude-dash today` — current day, exact filtering by timestamp ([#3]).
- `claude-dash tools` — last 24 h, temporal histogram and per-session breakdown ([#4]).
- `claude-dash session <sid>` — full drill-down with timeline and subagents ([#5]).
- Pricing table validated against the official Anthropic table; Opus correctly priced 3× cheaper than the previous estimate.
- Cost-band coloring across all views ([#6]).

[Unreleased]: https://github.com/mencoding/claude-dashboard/compare/v0.14.4...HEAD
[0.14.4]: https://github.com/mencoding/claude-dashboard/compare/v0.14.3...v0.14.4
[0.14.3]: https://github.com/mencoding/claude-dashboard/compare/v0.14.2...v0.14.3
[0.14.2]: https://github.com/mencoding/claude-dashboard/compare/v0.14.1...v0.14.2
[0.14.1]: https://github.com/mencoding/claude-dashboard/compare/v0.14.0...v0.14.1
[0.14.0]: https://github.com/mencoding/claude-dashboard/compare/v0.13.1...v0.14.0
[0.13.1]: https://github.com/mencoding/claude-dashboard/compare/v0.13.0...v0.13.1
[0.13.0]: https://github.com/mencoding/claude-dashboard/compare/v0.12.0...v0.13.0
[0.12.0]: https://github.com/mencoding/claude-dashboard/compare/v0.11.3...v0.12.0
[0.11.3]: https://github.com/mencoding/claude-dashboard/compare/v0.11.2...v0.11.3
[0.11.2]: https://github.com/mencoding/claude-dashboard/compare/v0.11.1...v0.11.2
[0.11.1]: https://github.com/mencoding/claude-dashboard/compare/v0.11.0...v0.11.1
[0.11.0]: https://github.com/mencoding/claude-dashboard/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/mencoding/claude-dashboard/compare/v0.9.1...v0.10.0
[0.9.1]: https://github.com/mencoding/claude-dashboard/compare/v0.9.0...v0.9.1
[0.9.0]: https://github.com/mencoding/claude-dashboard/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/mencoding/claude-dashboard/compare/v0.7.3...v0.8.0
[0.7.3]: https://github.com/mencoding/claude-dashboard/compare/v0.7.2...v0.7.3
[0.7.2]: https://github.com/mencoding/claude-dashboard/compare/v0.7.1...v0.7.2
[0.7.1]: https://github.com/mencoding/claude-dashboard/compare/v0.7.0...v0.7.1
[0.7.0]: https://github.com/mencoding/claude-dashboard/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/mencoding/claude-dashboard/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/mencoding/claude-dashboard/releases/tag/v0.5.0

[#1]: https://github.com/mencoding/claude-dashboard/pull/1
[#2]: https://github.com/mencoding/claude-dashboard/pull/2
[#3]: https://github.com/mencoding/claude-dashboard/pull/3
[#4]: https://github.com/mencoding/claude-dashboard/pull/4
[#5]: https://github.com/mencoding/claude-dashboard/pull/5
[#6]: https://github.com/mencoding/claude-dashboard/pull/6
[#7]: https://github.com/mencoding/claude-dashboard/pull/7
[#9]: https://github.com/mencoding/claude-dashboard/pull/9
[#10]: https://github.com/mencoding/claude-dashboard/pull/10
[#11]: https://github.com/mencoding/claude-dashboard/pull/11
[#12]: https://github.com/mencoding/claude-dashboard/pull/12
[#13]: https://github.com/mencoding/claude-dashboard/pull/13
[#14]: https://github.com/mencoding/claude-dashboard/pull/14
[#15]: https://github.com/mencoding/claude-dashboard/pull/15
[#16]: https://github.com/mencoding/claude-dashboard/pull/16
[#17]: https://github.com/mencoding/claude-dashboard/pull/17
[#18]: https://github.com/mencoding/claude-dashboard/pull/18
[#19]: https://github.com/mencoding/claude-dashboard/pull/19
[#20]: https://github.com/mencoding/claude-dashboard/pull/20
[#21]: https://github.com/mencoding/claude-dashboard/pull/21
[#22]: https://github.com/mencoding/claude-dashboard/issues/22
[#23]: https://github.com/mencoding/claude-dashboard/issues/23
[#24]: https://github.com/mencoding/claude-dashboard/issues/24
[#25]: https://github.com/mencoding/claude-dashboard/pull/25
[#26]: https://github.com/mencoding/claude-dashboard/pull/26
[#27]: https://github.com/mencoding/claude-dashboard/issues/27
[#29]: https://github.com/mencoding/claude-dashboard/pull/29
[#30]: https://github.com/mencoding/claude-dashboard/pull/30
[#32]: https://github.com/mencoding/claude-dashboard/pull/32
[#34]: https://github.com/mencoding/claude-dashboard/pull/34
[#33]: https://github.com/mencoding/claude-dashboard/issues/33
[#41]: https://github.com/mencoding/claude-dashboard/pull/41
[#45]: https://github.com/mencoding/claude-dashboard/issues/45
