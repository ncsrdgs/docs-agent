"""Persistência em banco: interface e implementação em SQLite com upsert idempotente.

O JSON e os CSVs continuam sendo gravados por persist.py. Este módulo mostra o caminho
para um banco: a chave é o hash do arquivo, então reprocessar o mesmo PDF atualiza a
linha em vez de duplicar. O mesmo SQL (`ON CONFLICT ... DO UPDATE`) funciona em Postgres;
para trocar de banco basta outra classe que implemente `Repositorio`.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from pipeline.models import Registro


class Repositorio(Protocol):
    def salvar(self, registros: list[Registro]) -> None: ...


_ESQUEMA = """
CREATE TABLE IF NOT EXISTS documentos (
    chave          TEXT PRIMARY KEY,   -- sha256 do arquivo (ou 'arquivo:<nome>' se o OCR falhou)
    arquivo        TEXT NOT NULL,
    tipo           TEXT,
    status         TEXT NOT NULL,
    motivo         TEXT,
    tentativas     INTEGER NOT NULL DEFAULT 0,
    tokens_in      INTEGER NOT NULL DEFAULT 0,
    tokens_out     INTEGER NOT NULL DEFAULT 0,
    ocr_ms         INTEGER,
    dados_json     TEXT,               -- campos extraídos (JSON)
    registro_json  TEXT NOT NULL,      -- registro completo (JSON)
    atualizado_em  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_documentos_status ON documentos (status);
CREATE INDEX IF NOT EXISTS idx_documentos_tipo ON documentos (tipo);
"""

_UPSERT = """
INSERT INTO documentos (chave, arquivo, tipo, status, motivo, tentativas, tokens_in,
                        tokens_out, ocr_ms, dados_json, registro_json, atualizado_em)
VALUES (:chave, :arquivo, :tipo, :status, :motivo, :tentativas, :tokens_in,
        :tokens_out, :ocr_ms, :dados_json, :registro_json, :atualizado_em)
ON CONFLICT (chave) DO UPDATE SET
    arquivo = excluded.arquivo, tipo = excluded.tipo, status = excluded.status,
    motivo = excluded.motivo, tentativas = excluded.tentativas,
    tokens_in = excluded.tokens_in, tokens_out = excluded.tokens_out,
    ocr_ms = excluded.ocr_ms, dados_json = excluded.dados_json,
    registro_json = excluded.registro_json, atualizado_em = excluded.atualizado_em
"""


def _chave(r: Registro) -> str:
    return r.sha256 or f"arquivo:{r.arquivo}"


class RepositorioSqlite:
    def __init__(self, caminho: str | Path) -> None:
        self.caminho = Path(caminho)
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        with self._conectar() as con:
            con.executescript(_ESQUEMA)

    def _conectar(self) -> sqlite3.Connection:
        return sqlite3.connect(self.caminho)

    def salvar(self, registros: list[Registro]) -> None:
        agora = datetime.now(timezone.utc).isoformat(timespec="seconds")
        linhas = [
            {
                "chave": _chave(r),
                "arquivo": r.arquivo,
                "tipo": r.tipo,
                "status": r.status,
                "motivo": r.motivo,
                "tentativas": r.tentativas,
                "tokens_in": r.tokens_in,
                "tokens_out": r.tokens_out,
                "ocr_ms": r.ocr_ms,
                "dados_json": r.dados.model_dump_json() if r.dados is not None else None,
                "registro_json": r.model_dump_json(),
                "atualizado_em": agora,
            }
            for r in registros
        ]
        with self._conectar() as con:  # uma transação: ou grava tudo ou nada
            con.executemany(_UPSERT, linhas)

    def contar_por_status(self) -> dict[str, int]:
        with self._conectar() as con:
            return dict(con.execute("SELECT status, COUNT(*) FROM documentos GROUP BY status"))

    def listar(self, status: str | None = None) -> list[tuple[str, str, str | None]]:
        """(arquivo, status, motivo), por ordem de arquivo."""
        consulta = "SELECT arquivo, status, motivo FROM documentos"
        params: tuple = ()
        if status:
            consulta += " WHERE status = ?"
            params = (status,)
        with self._conectar() as con:
            return list(con.execute(consulta + " ORDER BY arquivo", params))
