# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/mencoding/claude-dashboard/compare/v0.13.1...HEAD
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
