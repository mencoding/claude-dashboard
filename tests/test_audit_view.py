"""Testes da view 'Audit' — filtros, render, parsing de filter input."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from textual.widgets import TabbedContent

from claude_dash.audit.models import AuditEntry
from claude_dash.views.audit import (
    _EXPORT_FIELDS,
    _color_for,
    _is_test_session,
    correlate_start_end,
    default_export_path,
    export_entries,
    filter_entries,
    parse_filter_input,
    render_drill_down_human,
    render_footer,
    render_table,
)
from claude_dash.views.tui import DashboardApp


def _entry(
    *,
    session_id: str = "0efd3cf4-96ef-41b7-9d41-f91ec539becd",
    tool: str = "Bash",
    status: str = "success",
    delta_seconds: float = 0,
    subagent_type: str | None = None,
) -> AuditEntry:
    """Helper para construir AuditEntry de teste."""
    base = datetime(2026, 4, 28, 0, 0, 0, tzinfo=timezone(timedelta(hours=-3)))
    return AuditEntry(
        timestamp=base + timedelta(seconds=delta_seconds),
        hostname="host",
        pid="1",
        session_id=session_id,
        tool=tool,
        tool_use_id="x",
        status=status,
        duration_ms=100,
        perm_mode="auto",
        input_sha="0",
        input_bytes=10,
        output_bytes=20,
        subagent_type=subagent_type,
    )


# ---- Filtros ---------------------------------------------------------


def test_filter_oculta_sessoes_de_teste_por_default() -> None:
    real = _entry(session_id="0efd3cf4-96ef-41b7-9d41-f91ec539becd")
    fake = _entry(session_id="test-abc-123")
    out = filter_entries([real, fake], window_hours=None)
    assert out == [real]


def test_filter_show_test_sessions_inclui_tudo() -> None:
    real = _entry(session_id="0efd3cf4-96ef-41b7-9d41-f91ec539becd")
    fake = _entry(session_id="test-abc-123")
    out = filter_entries([real, fake], window_hours=None, show_test_sessions=True)
    assert len(out) == 2


def test_filter_por_tool() -> None:
    a = _entry(tool="Bash")
    b = _entry(tool="Read")
    out = filter_entries([a, b], filters={"tool": "Bash"}, window_hours=None)
    assert out == [a]


def test_filter_por_status_error() -> None:
    a = _entry(status="success")
    b = _entry(status="error")
    out = filter_entries([a, b], filters={"status": "error"}, window_hours=None)
    assert out == [b]


def test_filter_session_prefix() -> None:
    a = _entry(session_id="0efd3cf4-96ef-41b7-9d41-f91ec539becd")
    b = _entry(session_id="abcd1234-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    out = filter_entries(
        [a, b], filters={"session_prefix": "0efd3"}, window_hours=None
    )
    assert out == [a]


def test_janela_temporal_24h() -> None:
    now = datetime(2026, 4, 28, 12, 0, 0, tzinfo=timezone(timedelta(hours=-3)))
    recent = _entry(delta_seconds=0)  # base = 2026-04-28 00:00 -- dentro
    old = AuditEntry(
        timestamp=datetime(2026, 4, 26, 0, 0, 0, tzinfo=timezone(timedelta(hours=-3))),
        hostname="host", pid="1", session_id=recent.session_id, tool="Bash",
        tool_use_id="x", status="success", duration_ms=0, perm_mode="auto",
        input_sha="0", input_bytes=0, output_bytes=0,
    )
    out = filter_entries([old, recent], window_hours=24, now=now)
    assert out == [recent]


def test_janela_none_inclui_tudo() -> None:
    now = datetime(2026, 4, 28, 12, 0, 0, tzinfo=timezone(timedelta(hours=-3)))
    e_recent = _entry()
    e_ancient = AuditEntry(
        timestamp=datetime(2020, 1, 1, tzinfo=UTC),
        hostname="host", pid="1", session_id=e_recent.session_id, tool="Bash",
        tool_use_id="x", status="success", duration_ms=0, perm_mode="auto",
        input_sha="0", input_bytes=0, output_bytes=0,
    )
    out = filter_entries([e_ancient, e_recent], window_hours=None, now=now)
    assert len(out) == 2


# ---- Helpers internos ------------------------------------------------


def test_is_test_session_uuid_valido() -> None:
    assert not _is_test_session("0efd3cf4-96ef-41b7-9d41-f91ec539becd")
    assert _is_test_session("test-abc-123")
    assert _is_test_session("not-a-uuid")


def test_color_for_status_error_override() -> None:
    e = _entry(tool="Bash", status="error")
    assert _color_for(e) == "red"


def test_color_for_tools_cinza() -> None:
    assert _color_for(_entry(tool="Bash")) == "dim"
    assert _color_for(_entry(tool="Read")) == "dim"
    assert _color_for(_entry(tool="Edit")) == "dim"
    assert _color_for(_entry(tool="Write")) == "dim"


def test_color_for_web_amarelo() -> None:
    assert _color_for(_entry(tool="WebFetch")) == "yellow"
    assert _color_for(_entry(tool="WebSearch")) == "yellow"


def test_color_for_agent_skill_mcp_azul() -> None:
    assert _color_for(_entry(tool="Agent")) == "blue"
    assert _color_for(_entry(tool="Skill")) == "blue"
    assert _color_for(_entry(tool="mcp__maestra__queue")) == "blue"


# ---- Render ----------------------------------------------------------


def test_render_table_nao_crasha_vazio() -> None:
    table = render_table([])
    # 7 colunas: time, sess, host, tool, dur_ms, in, out (host adicionado em #55)
    assert len(table.columns) == 7


def test_render_table_inclui_subagent_type_no_label() -> None:
    e = _entry(tool="Agent", subagent_type="general-purpose")
    table = render_table([e])
    # tool agora esta na coluna idx 3 (host inserido antes em #55)
    cell = next(iter(table.columns[3].cells))
    assert "Agent" in cell
    assert "general-purpose" in cell


def test_render_footer_vazio() -> None:
    txt = render_footer([])
    assert "sem entries" in str(txt)


def test_render_footer_com_entries() -> None:
    entries = [_entry(tool="Bash") for _ in range(3)] + [_entry(tool="Read")]
    txt = render_footer(entries)
    s = str(txt)
    assert "total=4" in s
    assert "Bash" in s


def test_render_footer_taxa_erro() -> None:
    entries = [_entry(status="success"), _entry(status="error")]
    txt = render_footer(entries)
    s = str(txt)
    assert "err=1" in s


# ---- Filter input parsing -------------------------------------------


def test_parse_filter_vazio_limpa() -> None:
    assert parse_filter_input("") == {}
    assert parse_filter_input("/") == {}


def test_parse_filter_atalho_error() -> None:
    assert parse_filter_input("/error") == {"status": "error"}
    assert parse_filter_input("error") == {"status": "error"}


def test_parse_filter_tool() -> None:
    assert parse_filter_input("/tool=Bash") == {"tool": "Bash"}


def test_parse_filter_session() -> None:
    assert parse_filter_input("/session=0efd3") == {"session_prefix": "0efd3"}


def test_parse_filter_chave_invalida() -> None:
    assert parse_filter_input("/foo=bar") == {}


# ---- Smoke test da TUI ----------------------------------------------


def _run(coro):
    return asyncio.run(coro)


def test_tui_tem_aba_audit() -> None:
    async def run():
        app = DashboardApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.3)
            tc = app.query_one(TabbedContent)
            tab_ids = [pane.id for pane in tc.query("TabPane")]
            assert "tab-audit" in tab_ids
    _run(run())


def test_tecla_5_ativa_aba_audit() -> None:
    async def run():
        app = DashboardApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.3)
            await pilot.press("5")
            await pilot.pause(0.2)
            tc = app.query_one(TabbedContent)
            assert tc.active == "tab-audit"
    _run(run())


def test_tecla_t_cicla_janela() -> None:
    async def run():
        app = DashboardApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.3)
            await pilot.press("5")
            await pilot.pause(0.2)
            initial = app._audit_window_hours
            await pilot.press("t")
            await pilot.pause(0.1)
            assert app._audit_window_hours != initial
    _run(run())


def test_tecla_question_toggle_test_sessions() -> None:
    async def run():
        app = DashboardApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.3)
            await pilot.press("5")
            await pilot.pause(0.2)
            initial = app._audit_show_tests
            await pilot.press("question_mark")
            await pilot.pause(0.1)
            assert app._audit_show_tests != initial
    _run(run())


def test_marked_session_ids_resolve_de_tool_use_ids_no_buffer() -> None:
    """#67-followup: _marked_session_ids resolve sids unicos do conjunto marcado."""
    async def run():
        from datetime import datetime as _dt
        from datetime import timedelta as _td
        from datetime import timezone as _tz

        from claude_dash.audit.models import AuditEntry

        app = DashboardApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.3)

            # 3 entries de 2 sessoes diferentes (independente do buffer real)
            base = _dt(2026, 4, 28, 0, 0, 0, tzinfo=_tz(_td(hours=-3)))
            sid_a = "0efd3cf4-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
            sid_b = "a1b2cccc-cccc-cccc-cccc-cccccccccccc"
            for i in range(3):
                app._audit_entries.append(AuditEntry(
                    timestamp=base + _td(seconds=i),
                    hostname="host", pid="1",
                    session_id=sid_a if i < 2 else sid_b,
                    tool="Bash", tool_use_id=f"synthetic-tu-{i}", status="success",
                    duration_ms=10, perm_mode="auto", input_sha="0",
                    input_bytes=10, output_bytes=20,
                ))

            # Marca tool_use_ids manualmente (simula o que action_audit_toggle_mark
            # faria via Space) — uma de cada session
            app._audit_marked.add("synthetic-tu-0")  # sid_a
            app._audit_marked.add("synthetic-tu-2")  # sid_b

            sids = app._marked_session_ids()
            assert sid_a in sids
            assert sid_b in sids
    _run(run())


def test_rowselected_event_dispara_compare() -> None:
    """Regressao explicita do fluxo do evento: DataTable.RowSelected na
    audit-table deve disparar on_data_table_row_selected, que chama
    action_audit_enter_action, que faz compare quando ha 2+ marcas em
    sessoes distintas.

    Posta a mensagem RowSelected diretamente em vez de simular Enter via
    pilot.press: pilot.press('enter') eh flaky em CI headless com Textual
    8.2.5+ (entrega de teclado simulada nao chega ao DataTable de forma
    deterministica). Postar a mensagem testa o mesmo handler de producao
    pelo mesmo caminho real do Textual, sem depender da camada de
    traducao tecla->evento.
    """
    async def run():
        from datetime import datetime as _dt
        from datetime import timedelta as _td
        from datetime import timezone as _tz

        from claude_dash.audit.models import AuditEntry

        app = DashboardApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.3)
            await pilot.press("5")  # ativa Audit + foca DataTable
            await pilot.pause(0.3)

            # Desliga filtro implicito por hostname: entries sinteticas
            # tem hostname="host", que nao casa com socket.gethostname()
            # em CI runner — sem isso, _audit_filter_entries remove tudo
            # e dt.rows fica [], quebrando o teste em CI mesmo com
            # _audit_entries populado. Local mascarava porque o buffer
            # ja tinha entries reais do hostname local.
            app._audit_host_only_current = False

            # Popula buffer com entries de 2 sessoes
            base = _dt(2026, 4, 28, 0, 0, 0, tzinfo=_tz(_td(hours=-3)))
            sid_a = "0efd3cf4-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
            sid_b = "a1b2cccc-cccc-cccc-cccc-cccccccccccc"
            for i in range(3):
                app._audit_entries.append(AuditEntry(
                    timestamp=base + _td(seconds=i),
                    hostname="host", pid="1",
                    session_id=sid_a if i < 2 else sid_b,
                    tool="Bash", tool_use_id=f"synthetic-tu-{i}",
                    status="success", duration_ms=10, perm_mode="auto",
                    input_sha="0", input_bytes=10, output_bytes=20,
                ))
            app._refresh_audit()  # popula a DataTable
            await pilot.pause(0.2)
            app._audit_marked.add("synthetic-tu-0")
            app._audit_marked.add("synthetic-tu-2")
            assert len(app._marked_session_ids()) == 2

            # Posta o evento RowSelected diretamente — dispara o mesmo
            # handler que pilot.press('enter') dispararia, sem depender
            # da entrega de teclado simulada do Pilot (flaky em CI
            # headless com Textual 8.2.5+).
            from textual.widgets import DataTable
            dt = app.query_one("#audit-table", DataTable)
            row_keys = list(dt.rows)
            assert row_keys, "audit-table deve ter rows apos _refresh_audit"
            dt.post_message(DataTable.RowSelected(
                data_table=dt,
                cursor_row=0,
                row_key=row_keys[0],
            ))
            await pilot.pause(0.3)

            # Marcas limpas confirmam que compare path executou
            assert len(app._audit_marked) == 0
    _run(run())


def test_enter_com_2plus_marcadas_dispara_compare() -> None:
    """#67-followup-2: Enter via RowSelected delega pra compare quando ha 2+."""
    async def run():
        from datetime import datetime as _dt
        from datetime import timedelta as _td
        from datetime import timezone as _tz

        from claude_dash.audit.models import AuditEntry

        app = DashboardApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.3)
            await pilot.press("5")  # ativa aba Audit
            await pilot.pause(0.2)

            base = _dt(2026, 4, 28, 0, 0, 0, tzinfo=_tz(_td(hours=-3)))
            sid_a = "0efd3cf4-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
            sid_b = "a1b2cccc-cccc-cccc-cccc-cccccccccccc"
            for i in range(3):
                app._audit_entries.append(AuditEntry(
                    timestamp=base + _td(seconds=i),
                    hostname="host", pid="1",
                    session_id=sid_a if i < 2 else sid_b,
                    tool="Bash", tool_use_id=f"synthetic-tu-{i}",
                    status="success", duration_ms=10, perm_mode="auto",
                    input_sha="0", input_bytes=10, output_bytes=20,
                ))

            # Marca duas entries de sessoes diferentes
            app._audit_marked.add("synthetic-tu-0")
            app._audit_marked.add("synthetic-tu-2")
            assert len(app._marked_session_ids()) == 2

            # Action enter dispara — com 2+ marks, chama _handle_audit_compare
            # que limpa marcas. Verifica side-effect.
            app.action_audit_enter_action()
            await pilot.pause(0.1)
            # Apos compare, marcas sao limpas
            assert len(app._audit_marked) == 0
    _run(run())


def test_marks_filtram_outras_entries_da_sessao_mantem_marcada() -> None:
    """#67-followup-6: ao marcar entry, OUTRAS entries da mesma session
    somem mas a propria marcada permanece visivel (com ✓). Sessoes nao
    marcadas continuam totalmente visiveis.
    """
    async def run():
        from datetime import datetime as _dt
        from datetime import timedelta as _td
        from datetime import timezone as _tz

        from claude_dash.audit.models import AuditEntry

        app = DashboardApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.3)
            await pilot.press("5")
            await pilot.pause(0.3)

            app._audit_entries.clear()
            app._audit_host_only_current = False

            base = _dt.now(tz=_tz(_td(hours=-3))) - _td(minutes=5)
            sid_a = "0efd3cf4-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
            sid_b = "a1b2cccc-cccc-4ccc-8ccc-cccccccccccc"
            # 4 entries: tu-0 e tu-1 da sid_a; tu-2 e tu-3 da sid_b
            for i in range(4):
                app._audit_entries.append(AuditEntry(
                    timestamp=base + _td(seconds=i),
                    hostname="host", pid="1",
                    session_id=sid_a if i < 2 else sid_b,
                    tool="Bash", tool_use_id=f"synthetic-tu-{i}",
                    status="success", duration_ms=10, perm_mode="auto",
                    input_sha="0", input_bytes=10, output_bytes=20,
                ))
            app._refresh_audit()
            await pilot.pause(0.2)

            # Antes da marca: 4 entries visiveis
            assert len(app._audit_visible_cache) == 4

            # Marca tu-0 (de sid_a)
            app._audit_marked.add("synthetic-tu-0")
            app._audit_table_signature = None
            app._refresh_audit()
            await pilot.pause(0.2)

            visible_tuids = {e.tool_use_id for e in app._audit_visible_cache}
            # tu-0 (marcada) ainda visivel
            assert "synthetic-tu-0" in visible_tuids
            # tu-1 (mesma sessao, nao marcada) escondida
            assert "synthetic-tu-1" not in visible_tuids
            # tu-2 e tu-3 (sid_b, nao marcada) ainda visiveis
            assert "synthetic-tu-2" in visible_tuids
            assert "synthetic-tu-3" in visible_tuids


def test_marks_em_todas_as_sessoes_mantem_marcadas_visiveis() -> None:
    """#67-followup-6: edge case — usuario marcou entries de TODAS as
    sessoes no buffer. Antes a tabela ficava vazia e Enter nao disparava
    (RowSelected nao emitia em DataTable vazia). Agora as marcadas
    permanecem visiveis pra que Enter funcione e abra o compare.
    """
    async def run():
        from datetime import datetime as _dt
        from datetime import timedelta as _td
        from datetime import timezone as _tz

        from claude_dash.audit.models import AuditEntry

        app = DashboardApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.3)
            await pilot.press("5")
            await pilot.pause(0.3)

            app._audit_entries.clear()
            app._audit_host_only_current = False

            base = _dt.now(tz=_tz(_td(hours=-3))) - _td(minutes=5)
            sid_a = "0efd3cf4-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
            sid_b = "a1b2cccc-cccc-4ccc-8ccc-cccccccccccc"
            # 2 entries, uma de cada sessao — buffer compacto
            for i in range(2):
                app._audit_entries.append(AuditEntry(
                    timestamp=base + _td(seconds=i),
                    hostname="host", pid="1",
                    session_id=sid_a if i == 0 else sid_b,
                    tool="Bash", tool_use_id=f"synthetic-tu-{i}",
                    status="success", duration_ms=10, perm_mode="auto",
                    input_sha="0", input_bytes=10, output_bytes=20,
                ))

            # Marca AMBAS as entries (cada uma e' a unica de sua sessao)
            app._audit_marked.add("synthetic-tu-0")
            app._audit_marked.add("synthetic-tu-1")
            app._audit_table_signature = None
            app._refresh_audit()
            await pilot.pause(0.2)

            # Tabela NAO esta vazia — as 2 marcadas ficam visiveis
            assert len(app._audit_visible_cache) == 2
            # Enter dispara compare
            app.action_audit_enter_action()
            await pilot.pause(0.1)
            # Compare path executou: marcas limpas
            assert len(app._audit_marked) == 0
    _run(run())


def test_esc_limpa_marcas_quando_sem_prompt_ativo() -> None:
    """#67-followup-5: Esc na aba Audit sem prompts ativos limpa marcas."""
    async def run():
        app = DashboardApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.3)
            await pilot.press("5")
            await pilot.pause(0.3)

            app._audit_marked.add("any-key-1")
            app._audit_marked.add("any-key-2")
            assert len(app._audit_marked) == 2

            await pilot.press("escape")
            await pilot.pause(0.2)

            assert len(app._audit_marked) == 0
    _run(run())


def test_clear_marks_and_refresh() -> None:
    """_clear_marks_and_refresh esvazia o set."""
    async def run():
        app = DashboardApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.3)
            app._audit_marked.add("any-key")
            app._audit_marked.add("other-key")
            assert len(app._audit_marked) == 2
            app._clear_marks_and_refresh()
            assert len(app._audit_marked) == 0
    _run(run())


def test_cursor_user_pausa_auto_tail() -> None:
    """Movimento de cursor pelo usuario deve pausar o auto-tail.

    Regressao: setas em DataTable nao bubblam pra App.on_key, entao
    _audit_last_user_action ficava em 0 e o tick seguinte forcava cursor
    de volta pro fim. Fix usa on_data_table_row_highlighted (que dispara
    pra qualquer movimento).
    """
    async def run():
        from datetime import datetime as _dt
        from datetime import timedelta as _td
        from datetime import timezone as _tz

        from claude_dash.audit.models import AuditEntry

        app = DashboardApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.3)
            await pilot.press("5")
            await pilot.pause(0.2)

            # Popula tabela com entries sintéticas.
            entries = [
                AuditEntry(
                    timestamp=_dt(2026, 4, 28, 0, 0, i, tzinfo=_tz(_td(hours=-3))),
                    hostname="host", pid="1",
                    session_id="0efd3cf4-96ef-41b7-9d41-f91ec539becd",
                    tool="Bash", tool_use_id=f"x-{i}", status="success",
                    duration_ms=10, perm_mode="auto", input_sha="0",
                    input_bytes=10, output_bytes=20,
                )
                for i in range(5)
            ]
            app._audit_entries.extend(entries)
            # Reseta last_user_action pra simular "auto-tail estavel".
            app._refresh_audit()
            app._audit_last_user_action = 0
            app._audit_paused = False
            await pilot.pause(0.1)

            # Simula user pressionando ↑ — DataTable consome e move cursor.
            # Setas NAO bubblam pra App.on_key; sem o handler de
            # RowHighlighted, _audit_last_user_action ficava em 0.
            await pilot.press("up")
            await pilot.pause(0.1)

            # on_data_table_row_highlighted detectou o movimento e marcou
            # a interacao -> pausa esta ativa.
            assert app._audit_last_user_action != 0
            assert app._is_audit_paused()
    _run(run())


@pytest.mark.parametrize("key", ["1", "2", "3", "4", "5"])
def test_todas_abas_sao_acessiveis(key: str) -> None:
    async def run():
        app = DashboardApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.3)
            await pilot.press(key)
            await pilot.pause(0.1)
    _run(run())


# ---- Export CSV/JSON (#36) -------------------------------------------


def test_export_csv_schema_e_roundtrip(tmp_path) -> None:
    import csv as _csv
    e1 = _entry(tool="Bash")
    e2 = _entry(tool="Agent", subagent_type="general-purpose", delta_seconds=5)
    out = tmp_path / "audit.csv"
    written = export_entries([e1, e2], "csv", out)
    assert written == out
    assert out.is_file()

    with out.open(encoding="utf-8") as f:
        reader = _csv.DictReader(f)
        assert list(reader.fieldnames or []) == list(_EXPORT_FIELDS)
        rows = list(reader)
    assert len(rows) == 2
    # ISO 8601 do timestamp
    assert rows[0]["timestamp"].startswith("2026-04-28T")
    # subagent_type=None vira string vazia em CSV
    assert rows[0]["subagent_type"] == ""
    assert rows[1]["subagent_type"] == "general-purpose"
    assert rows[0]["tool"] == "Bash"
    assert rows[1]["tool"] == "Agent"


def test_export_json_schema_e_null_para_subagent_type(tmp_path) -> None:
    import json as _json
    e1 = _entry(tool="Bash")
    e2 = _entry(tool="Agent", subagent_type="general-purpose", delta_seconds=5)
    out = tmp_path / "audit.json"
    export_entries([e1, e2], "json", out)
    data = _json.loads(out.read_text())
    assert isinstance(data, list) and len(data) == 2
    # JSON preserva None como null (vs "" do CSV)
    assert data[0]["subagent_type"] is None
    assert data[1]["subagent_type"] == "general-purpose"
    # Schema bate com _EXPORT_FIELDS
    assert set(data[0].keys()) == set(_EXPORT_FIELDS)


def test_export_atomico_nao_deixa_tmp_em_caso_de_sucesso(tmp_path) -> None:
    out = tmp_path / "audit.csv"
    export_entries([_entry()], "csv", out)
    assert out.is_file()
    assert not (tmp_path / "audit.csv.tmp").exists()


def test_export_formato_invalido_levanta() -> None:
    with pytest.raises(ValueError, match="Formato"):
        export_entries([_entry()], "xml", Path("/tmp/x.xml"))  # type: ignore[arg-type]


def test_default_export_path_inclui_timestamp_iso() -> None:
    now = datetime(2026, 4, 28, 14, 30, 52)
    p_csv = default_export_path("csv", now=now)
    p_json = default_export_path("json", now=now)
    assert p_csv.name == "audit-export-2026-04-28T143052.csv"
    assert p_json.name == "audit-export-2026-04-28T143052.json"


def test_export_path_nao_gravavel_levanta_oserror(tmp_path) -> None:
    # Diretorio nao-existente -> OSError no open
    bad = tmp_path / "no-such-dir" / "out.csv"
    with pytest.raises(OSError):
        export_entries([_entry()], "csv", bad)


# ---- Hostname: filter, render, parse (#55) ---------------------------


def _entry_host(host: str, tool: str = "Bash") -> AuditEntry:
    """Helper variante de _entry com hostname customizado."""
    base = datetime(2026, 4, 28, 0, 0, 0, tzinfo=timezone(timedelta(hours=-3)))
    return AuditEntry(
        timestamp=base,
        hostname=host,
        pid="1",
        session_id="0efd3cf4-96ef-41b7-9d41-f91ec539becd",
        tool=tool,
        tool_use_id=f"x-{host}",
        status="success",
        duration_ms=100,
        perm_mode="auto",
        input_sha="0",
        input_bytes=10,
        output_bytes=20,
    )


def test_filter_current_host_default_oculta_outros() -> None:
    aqui = _entry_host("RET-DTI-601048")
    la = _entry_host("PREDATOR")
    out = filter_entries(
        [aqui, la], window_hours=None, current_host="RET-DTI-601048"
    )
    assert out == [aqui]


def test_filter_current_host_none_mostra_todos() -> None:
    aqui = _entry_host("RET-DTI-601048")
    la = _entry_host("PREDATOR")
    out = filter_entries([aqui, la], window_hours=None, current_host=None)
    assert len(out) == 2


def test_filter_current_host_inclui_hostname_vazio() -> None:
    """Logs antigos pre-#55 sem hostname devem passar — sao 'sem host conhecido'."""
    aqui = _entry_host("RET-DTI-601048")
    sem_host = _entry_host("")
    out = filter_entries(
        [aqui, sem_host], window_hours=None, current_host="RET-DTI-601048"
    )
    assert len(out) == 2


def test_filter_host_explicit_prefix_match() -> None:
    pre = _entry_host("PREDATOR")
    ret = _entry_host("RET-DTI-601048")
    out = filter_entries(
        [pre, ret], filters={"host": "PRED"}, window_hours=None
    )
    assert out == [pre]


def test_parse_filter_host() -> None:
    assert parse_filter_input("/host=PREDATOR") == {"host": "PREDATOR"}
    assert parse_filter_input("/host=") == {}


def test_render_table_inclui_coluna_host() -> None:
    table = render_table([_entry_host("PREDATOR")])
    # 7 colunas: time, sess, host, tool, dur_ms, in, out
    assert len(table.columns) == 7
    # host esta na coluna idx 2
    cell = next(iter(table.columns[2].cells))
    assert "PREDATOR" in cell


# ---- correlate_start_end (#50) --------------------------------------


def _entry_event(
    *,
    event: str = "end",
    tool_use_id: str = "x",
    delta_seconds: float = 0,
    status: str = "success",
) -> AuditEntry:
    base = datetime(2026, 4, 28, 0, 0, 0, tzinfo=timezone(timedelta(hours=-3)))
    return AuditEntry(
        timestamp=base + timedelta(seconds=delta_seconds),
        hostname="host", pid="1",
        session_id="0efd3cf4-96ef-41b7-9d41-f91ec539becd",
        tool="Bash", tool_use_id=tool_use_id, status=status,
        duration_ms=100 if event == "end" else 0,
        perm_mode="auto", input_sha="0", input_bytes=10,
        output_bytes=20 if event == "end" else 0,
        event=event,  # type: ignore[arg-type]
    )


def test_correlate_start_seguido_de_end_substitui_in_place() -> None:
    s = _entry_event(event="start", tool_use_id="A", delta_seconds=0)
    e = _entry_event(event="end", tool_use_id="A", delta_seconds=2)
    out = correlate_start_end([s, e])
    assert len(out) == 1
    assert out[0].event == "end"
    assert out[0].duration_ms == 100


def test_correlate_start_sem_end_preservado_como_running() -> None:
    """Tool ainda em execucao — start fica no array."""
    s = _entry_event(event="start", tool_use_id="B")
    out = correlate_start_end([s])
    assert len(out) == 1
    assert out[0].event == "start"


def test_correlate_multiplos_pares_e_um_orfao() -> None:
    s1 = _entry_event(event="start", tool_use_id="A", delta_seconds=0)
    e1 = _entry_event(event="end", tool_use_id="A", delta_seconds=1)
    s2 = _entry_event(event="start", tool_use_id="B", delta_seconds=2)
    s3 = _entry_event(event="start", tool_use_id="C", delta_seconds=3)
    e3 = _entry_event(event="end", tool_use_id="C", delta_seconds=4)
    out = correlate_start_end([s1, e1, s2, s3, e3])
    # A: collapse pra end, B: stays start, C: collapse pra end
    tool_use_ids_e_events = [(o.tool_use_id, o.event) for o in out]
    assert tool_use_ids_e_events == [
        ("A", "end"),
        ("B", "start"),
        ("C", "end"),
    ]


def test_correlate_entries_sem_tool_use_id_passam_direto() -> None:
    """Agent legacy sem tool_use_id — sem chave de correlacao."""
    e1 = _entry_event(event="end", tool_use_id="")
    e2 = _entry_event(event="end", tool_use_id="")
    out = correlate_start_end([e1, e2])
    assert len(out) == 2


def test_render_table_dim_para_outro_host() -> None:
    pre = _entry_host("PREDATOR")
    table = render_table([pre], current_host="RET-DTI-601048")
    # Quando entry e' de outro host, render aplica row_styles="dim".
    # Rich.Table guarda style por linha; checamos qualquer celula
    # ter style 'dim' indicando que o flag pegou.
    rendered = list(table.rows)
    assert len(rendered) == 1
    # row.style ou cells inheritados — Rich mantem _row_styles na Table.
    # Mais simples: checamos que a Table tem pelo menos uma row com style dim.
    assert any("dim" in str(getattr(row, "style", "")) for row in rendered)


# ---- render_drill_down_human (#67-followup) -------------------------


def _human_text(panel) -> str:
    """Extrai conteudo plain text de um Rich Panel pra assertions."""
    from rich.console import Console
    console = Console(record=True, width=200)
    console.print(panel)
    return console.export_text()


def test_drill_down_human_inclui_todos_os_labels_principais() -> None:
    line = (
        '<134>1 2026-04-28T17:55:38.278-03:00 ret-dti-601048 claude-code 99999 '
        'TOOLCALL [audit@iris session="0efd3cf4-96ef-41b7-9d41-f91ec539becd" '
        'tool="Bash" tool_use_id="toolu_test" status="success" '
        'duration_ms="42" perm_mode="auto" input_sha="abc123" '
        "input_bytes=\"50\" output_bytes=\"100\"] cwd='/home/menzani' cmd='ls -la'"
    )
    panel = render_drill_down_human(line)
    txt = _human_text(panel)
    # Cabecalho do Panel inclui tool + horario
    assert "Bash" in txt
    # Labels canonicos
    for label in (
        "Timestamp:", "Hostname:", "PID:", "Event:", "Session:", "Tool:",
        "Tool use ID:", "Status:", "Duration:", "Permission mode:",
        "Input SHA:", "Input bytes:", "Output bytes:",
    ):
        assert label in txt, f"label ausente: {label}"
    # Valores
    assert "ret-dti-601048" in txt
    assert "0efd3cf4-96ef-41b7-9d41-f91ec539becd" in txt
    assert "toolu_test" in txt
    assert "42 ms" in txt
    # Trailing fields
    assert "CWD:" in txt
    assert "/home/menzani" in txt
    assert "Command:" in txt
    assert "ls -la" in txt


def test_drill_down_human_status_error_em_red() -> None:
    line = (
        '<134>1 2026-04-28T00:00:29.223-03:00 host claude-code 1 TOOLCALL '
        '[audit@iris session="abc" tool="Bash" tool_use_id="x" status="error" '
        'duration_ms="50" perm_mode="auto" input_sha="0" input_bytes="0" '
        'output_bytes="0"]'
    )
    panel = render_drill_down_human(line)
    # Border do Panel vira red quando status=error
    assert panel.border_style == "red"


def test_drill_down_human_event_start_destacado() -> None:
    """PreToolUse/TOOLSTART -> Event: start em estilo destacado."""
    line = (
        '<134>1 2026-04-28T00:00:29.223-03:00 host claude-code 1 TOOLSTART '
        '[audit@iris session="abc" tool="Bash" tool_use_id="x" status="running" '
        'duration_ms="0" perm_mode="auto" input_sha="0" input_bytes="0" '
        'output_bytes="0" event="start"]'
    )
    panel = render_drill_down_human(line)
    txt = _human_text(panel)
    assert "Event:" in txt
    assert "start" in txt
    assert "running" in txt


def test_drill_down_human_linha_invalida_nao_crasha() -> None:
    panel = render_drill_down_human("texto qualquer nao parseavel")
    assert panel.border_style == "red"
    txt = _human_text(panel)
    assert "Linha invalida" in txt or "invalida" in txt


def test_drill_down_human_path_de_read() -> None:
    line = (
        '<134>1 2026-04-28T00:00:29.223-03:00 host claude-code 1 TOOLCALL '
        '[audit@iris session="abc" tool="Read" tool_use_id="x" status="success" '
        'duration_ms="10" perm_mode="auto" input_sha="0" input_bytes="0" '
        "output_bytes=\"0\"] cwd='/tmp' path='/etc/hostname'"
    )
    panel = render_drill_down_human(line)
    txt = _human_text(panel)
    assert "Path:" in txt
    assert "/etc/hostname" in txt


def test_drill_down_human_subagent_em_agent() -> None:
    line = (
        '<134>1 2026-04-28T00:00:29.223-03:00 host claude-code 1 TOOLCALL '
        '[audit@iris session="abc" tool="Agent" tool_use_id="x" status="success" '
        'duration_ms="5000" perm_mode="auto" input_sha="0" input_bytes="0" '
        'output_bytes="0" subagent_type="general-purpose"]'
    )
    panel = render_drill_down_human(line)
    txt = _human_text(panel)
    assert "Subagent:" in txt
    assert "general-purpose" in txt
