"""Testes do statusline próprio e do setup idempotente."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from claude_dash import setup_status, statusline


def _minimal_statusline_input(**overrides) -> dict:
    """JSON base que o Claude Code injeta no statusline."""
    data = {
        "model": {"display_name": "Opus 4.7", "id": "claude-opus-4-7"},
        "session_id": "test-sid",
        "transcript_path": "/tmp/inexistente.jsonl",
        "output_style": {"name": "default"},
        "context_window": {
            "used_percentage": 12,
            "context_window_size": 200_000,
            "current_usage": {"input_tokens": 24_000},
        },
        "cost": {
            "total_cost_usd": 1.42,
            "total_api_duration_ms": 83_000,
            "total_lines_added": 12,
            "total_lines_removed": 3,
        },
        "rate_limits": {
            "five_hour": {
                "used_percentage": 13,
                "resets_at": int(time.time()) + 3600,
            },
            "seven_day": {
                "used_percentage": 18,
                "resets_at": int(time.time()) + 86400 * 3,
            },
        },
    }
    data.update(overrides)
    return data


class TestRenderStatusline:
    def test_includes_model(self) -> None:
        out = statusline.render_statusline(_minimal_statusline_input())
        assert "Opus 4.7" in out

    def test_falls_back_to_model_id_when_no_display_name(self) -> None:
        data = _minimal_statusline_input(model={"id": "claude-haiku-4-5"})
        assert "claude-haiku-4-5" in statusline.render_statusline(data)

    def test_shows_na_when_no_model(self) -> None:
        data = _minimal_statusline_input(model={})
        assert "N/A" in statusline.render_statusline(data)

    def test_hides_style_when_default(self) -> None:
        out = statusline.render_statusline(_minimal_statusline_input())
        # "default" é omitido; mas a palavra pode aparecer em outros lugares,
        # então checa que não há segmento autônomo " default "
        assert " default " not in out and not out.endswith("default")

    def test_shows_style_when_non_default(self) -> None:
        data = _minimal_statusline_input(output_style={"name": "Explanatory"})
        assert "Explanatory" in statusline.render_statusline(data)

    def test_context_bar_proportional_to_pct(self) -> None:
        data = _minimal_statusline_input()
        data["context_window"]["used_percentage"] = 50
        out = statusline.render_statusline(data)
        # 50% de 12 chars = 6 blocos cheios
        assert "█" * 6 in out
        assert "50%" in out

    def test_cost_formatting_under_1(self) -> None:
        data = _minimal_statusline_input()
        data["cost"]["total_cost_usd"] = 0.1234
        out = statusline.render_statusline(data)
        assert "$0.1234" in out

    def test_cost_formatting_over_1(self) -> None:
        data = _minimal_statusline_input()
        data["cost"]["total_cost_usd"] = 12.3456
        out = statusline.render_statusline(data)
        assert "$12.35" in out

    def test_shows_diff_lines(self) -> None:
        out = statusline.render_statusline(_minimal_statusline_input())
        assert "+12" in out and "-3" in out

    def test_rate_limits_both_present(self) -> None:
        out = statusline.render_statusline(_minimal_statusline_input())
        assert "5h:" in out and "7d:" in out
        assert "13%" in out and "18%" in out

    def test_duration_formatting(self) -> None:
        data = _minimal_statusline_input()
        data["cost"]["total_api_duration_ms"] = 83_000  # 1m 23s
        out = statusline.render_statusline(data)
        assert "1m23s" in out

    def test_tolerates_missing_fields(self) -> None:
        """Campo mínimo: só model. Não deve crashar."""
        out = statusline.render_statusline({"model": {"display_name": "X"}})
        assert "X" in out


class TestFmtHelpers:
    def test_fmt_tokens(self) -> None:
        assert statusline._fmt_tokens(999) == "999"
        assert statusline._fmt_tokens(1_500) == "2k"
        assert statusline._fmt_tokens(200_000) == "200k"

    def test_fmt_duration(self) -> None:
        assert statusline._fmt_duration(30_000) == "30s"
        assert statusline._fmt_duration(90_000) == "1m30s"
        assert statusline._fmt_duration(3_600_000) == "1h0m"
        assert statusline._fmt_duration(90_000_000) == "1d1h"

    def test_fmt_reset_delta_negative_is_empty(self) -> None:
        past = int(time.time()) - 100
        assert statusline._fmt_reset_delta(past) == ""

    def test_fmt_reset_delta_future(self) -> None:
        future = int(time.time()) + 5400  # 1h30m
        assert statusline._fmt_reset_delta(future) == "1h30m"


class TestWrapResolution:
    def test_env_var_takes_precedence(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(statusline.WRAP_ENV_VAR, "/env/path")
        # Config file também tem valor — env deve vencer
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"statusline_wrap_path": "/cfg/path"}))
        monkeypatch.setattr(statusline, "DASHBOARD_CONFIG", cfg)
        assert statusline._resolve_wrap_path() == "/env/path"

    def test_falls_back_to_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(statusline.WRAP_ENV_VAR, raising=False)
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"statusline_wrap_path": "/from/cfg"}))
        monkeypatch.setattr(statusline, "DASHBOARD_CONFIG", cfg)
        assert statusline._resolve_wrap_path() == "/from/cfg"

    def test_none_when_neither_set(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(statusline.WRAP_ENV_VAR, raising=False)
        monkeypatch.setattr(
            statusline, "DASHBOARD_CONFIG", tmp_path / "nao-existe.json"
        )
        assert statusline._resolve_wrap_path() is None


class TestRunWrap:
    def test_expands_tilde_in_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regressão: se wrap_path começa com '~/', expandir antes de execv.

        Review do PR #18 pegou este bug — o Claude Code aceita '~'
        no settings.json:statusLine.command, então quando preservamos
        esse valor como wrap_path, subprocess.run precisa expandir.
        Sem este fix, wrap silenciosamente não executa.
        """
        # Cria um script executável em tmp_path
        script = tmp_path / "meu-statusline.sh"
        script.write_text("#!/bin/sh\necho 'wrapped output'\n")
        script.chmod(0o755)
        # Simula cenário em que o path foi salvo como ~/ e HOME é tmp_path
        monkeypatch.setenv("HOME", str(tmp_path))
        tilde_path = "~/meu-statusline.sh"
        result = statusline._run_wrap(tilde_path, "{}")
        assert result == "wrapped output"


class TestSetupStatus:
    def _patch_paths(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> tuple[Path, Path]:
        settings = tmp_path / "settings.json"
        cfg = tmp_path / ".claude-dash.json"
        backup_dir = tmp_path / "backups"
        monkeypatch.setattr(setup_status, "SETTINGS_JSON", settings)
        monkeypatch.setattr(setup_status, "DASHBOARD_CONFIG", cfg)
        monkeypatch.setattr(setup_status, "BACKUP_DIR", backup_dir)
        return settings, cfg

    def test_fresh_install_adds_statusline(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        settings, _ = self._patch_paths(tmp_path, monkeypatch)
        settings.write_text(json.dumps({"language": "pt-BR"}))
        rc = setup_status.run()
        assert rc == 0
        saved = json.loads(settings.read_text())
        assert saved["statusLine"]["command"] == "claude-dash-statusline"
        # Preservou outras chaves
        assert saved["language"] == "pt-BR"

    def test_idempotent_when_already_installed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        settings, _ = self._patch_paths(tmp_path, monkeypatch)
        settings.write_text(json.dumps({
            "statusLine": {"type": "command", "command": "claude-dash-statusline"}
        }))
        mtime_before = settings.stat().st_mtime
        time.sleep(0.01)
        rc = setup_status.run()
        assert rc == 0
        # Arquivo não foi reescrito — é idempotente
        assert settings.stat().st_mtime == mtime_before
        out = capsys.readouterr().out
        assert "nada a fazer" in out

    def test_preserves_existing_statusline_as_wrap(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        settings, cfg = self._patch_paths(tmp_path, monkeypatch)
        settings.write_text(json.dumps({
            "statusLine": {"type": "command", "command": "~/meu-script.sh"}
        }))
        rc = setup_status.run()
        assert rc == 0
        saved_settings = json.loads(settings.read_text())
        saved_cfg = json.loads(cfg.read_text())
        # Nosso comando agora é o statusLine
        assert saved_settings["statusLine"]["command"] == "claude-dash-statusline"
        # O statusline anterior foi preservado em wrap
        assert saved_cfg["statusline_wrap_path"] == "~/meu-script.sh"

    def test_creates_backup_before_writing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        settings, _ = self._patch_paths(tmp_path, monkeypatch)
        settings.write_text(json.dumps({"language": "pt-BR"}))
        setup_status.run()
        # Algum backup foi criado
        backups = list((tmp_path / "backups").iterdir())
        assert len(backups) >= 1
        assert any("settings.json.backup" in b.name for b in backups)
