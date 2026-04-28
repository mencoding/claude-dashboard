"""Tail incremental do audit log.

Mantem em memoria o offset e o inode do arquivo para ler so bytes
novos a cada `read_new()`. Detecta:

- **Rotacao por logrotate**: inode muda -> reabre do inicio (o arquivo
  novo, vazio ou com algumas linhas).
- **Truncate**: tamanho atual menor que offset -> reabre do inicio.
- **Arquivo inexistente**: silenciosamente retorna lista vazia (caso
  o usuario nao tenha rodado `setup-audit` ainda).

Performance: O(novos_bytes) por chamada, **nao** O(arquivo_inteiro).
"""
from __future__ import annotations

import os
from pathlib import Path

from claude_dash.audit.models import AuditEntry
from claude_dash.audit.parser import parse_line


class IncrementalTailer:
    """Le novas linhas do audit log mantendo offset+inode em memoria."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._offset: int = 0
        self._inode: int | None = None

    def _stat(self) -> os.stat_result | None:
        try:
            return self.path.stat()
        except (FileNotFoundError, PermissionError):
            return None

    def read_new(self) -> list[AuditEntry]:
        """Le e retorna entries novas desde a ultima chamada.

        Primeira chamada le o arquivo inteiro. Chamadas subsequentes
        leem so bytes novos. Em rotacao/truncate, reabre do zero.
        """
        st = self._stat()
        if st is None:
            return []

        # Detecta rotacao (inode mudou) ou truncate (arquivo encolheu)
        # e reabre do inicio.
        rotated = self._inode is not None and st.st_ino != self._inode
        shrunk = st.st_size < self._offset
        if rotated or shrunk:
            self._offset = 0

        self._inode = st.st_ino

        # Nada novo a ler.
        if st.st_size == self._offset:
            return []

        entries: list[AuditEntry] = []
        try:
            with open(self.path, encoding="utf-8", errors="replace") as f:
                f.seek(self._offset)
                chunk = f.read()
                self._offset = f.tell()
        except OSError:
            return []

        for raw_line in chunk.splitlines():
            entry = parse_line(raw_line)
            if entry is not None:
                entries.append(entry)

        return entries
