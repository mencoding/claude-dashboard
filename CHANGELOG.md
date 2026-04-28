# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
