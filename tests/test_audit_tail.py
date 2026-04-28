"""Testes do tail incremental do audit log."""
from __future__ import annotations

from pathlib import Path

from claude_dash.audit.tail import IncrementalTailer

LINE_TEMPLATE = (
    '<134>1 2026-04-28T00:{minute:02d}:{sec:02d}.000-03:00 host claude-code 1 TOOLCALL '
    '[audit@iris session="{sess}" tool="{tool}" tool_use_id="x" '
    'status="success" duration_ms="0" perm_mode="auto" input_sha="0" '
    'input_bytes="0" output_bytes="0"]\n'
)


def _make_line(sec: int, sess: str = "0efd3cf4-96ef-41b7-9d41-f91ec539becd",
               tool: str = "Bash") -> str:
    # ``sec`` pode ser >59; converte automaticamente em minute+sec
    # validos para nao bater contra o limite ISO 8601.
    return LINE_TEMPLATE.format(
        minute=(sec // 60) % 60, sec=sec % 60, sess=sess, tool=tool
    )


def test_arquivo_inexistente_retorna_lista_vazia(tmp_path: Path) -> None:
    tailer = IncrementalTailer(tmp_path / "nao-existe.log")
    assert tailer.read_new() == []


def test_primeira_leitura_le_arquivo_inteiro(tmp_path: Path) -> None:
    log = tmp_path / "audit.log"
    log.write_text(_make_line(1) + _make_line(2) + _make_line(3))

    tailer = IncrementalTailer(log)
    entries = tailer.read_new()
    assert len(entries) == 3


def test_leitura_incremental_so_pega_novas(tmp_path: Path) -> None:
    log = tmp_path / "audit.log"
    log.write_text(_make_line(1) + _make_line(2))

    tailer = IncrementalTailer(log)
    first = tailer.read_new()
    assert len(first) == 2

    # Segunda chamada sem mudanca -> vazio.
    assert tailer.read_new() == []

    # Append de uma linha -> so essa.
    with open(log, "a") as f:
        f.write(_make_line(3))
    second = tailer.read_new()
    assert len(second) == 1
    assert second[0].timestamp.second == 3


def test_rotacao_inode_reabre_do_inicio(tmp_path: Path) -> None:
    log = tmp_path / "audit.log"
    log.write_text(_make_line(1) + _make_line(2))

    tailer = IncrementalTailer(log)
    assert len(tailer.read_new()) == 2

    # Simula logrotate: remove e recria com conteudo novo (inode muda).
    log.unlink()
    log.write_text(_make_line(10))

    entries = tailer.read_new()
    assert len(entries) == 1
    assert entries[0].timestamp.second == 10


def test_truncate_reabre_do_inicio(tmp_path: Path) -> None:
    log = tmp_path / "audit.log"
    log.write_text(_make_line(1) + _make_line(2) + _make_line(3))

    tailer = IncrementalTailer(log)
    assert len(tailer.read_new()) == 3

    # Truncate sem mudar inode (mesmo path, st_size cai).
    log.write_text(_make_line(99))

    entries = tailer.read_new()
    assert len(entries) == 1
    # sec=99 -> minute=1, second=39
    assert entries[0].timestamp.minute == 1
    assert entries[0].timestamp.second == 39


def test_linhas_invalidas_sao_ignoradas(tmp_path: Path) -> None:
    log = tmp_path / "audit.log"
    log.write_text(
        _make_line(1) + "lixo no meio do arquivo\n" + _make_line(2) + "\n"
    )

    tailer = IncrementalTailer(log)
    entries = tailer.read_new()
    assert len(entries) == 2
