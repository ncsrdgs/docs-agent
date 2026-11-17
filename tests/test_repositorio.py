"""Testes da persistência em SQLite: upsert por hash do arquivo."""
from __future__ import annotations

import json
import sqlite3

from pipeline.models import NotaFiscal, Registro
from pipeline.repositorio import RepositorioSqlite


def _reg(arquivo, sha, status="ok", tipo="nota_fiscal", motivo=None, dados=None):
    return Registro(arquivo=arquivo, sha256=sha, tipo=tipo, status=status, motivo=motivo,
                    dados=dados, tentativas=1, tokens_in=100, tokens_out=20, ocr_ms=900)


def test_salvar_e_contar(tmp_path):
    repo = RepositorioSqlite(tmp_path / "db" / "r.db")
    repo.salvar([_reg("a.pdf", "h1"), _reg("b.pdf", "h2", status="needs_review", motivo="x")])
    assert repo.contar_por_status() == {"ok": 1, "needs_review": 1}
    assert repo.listar("needs_review") == [("b.pdf", "needs_review", "x")]


def test_reprocessar_o_mesmo_arquivo_nao_duplica(tmp_path):
    repo = RepositorioSqlite(tmp_path / "r.db")
    repo.salvar([_reg("a.pdf", "h1", status="needs_review", motivo="total difere")])
    repo.salvar([_reg("a.pdf", "h1", status="ok")])  # mesma chave: atualiza
    assert repo.contar_por_status() == {"ok": 1}
    assert repo.listar() == [("a.pdf", "ok", None)]


def test_arquivo_alterado_gera_outra_linha(tmp_path):
    repo = RepositorioSqlite(tmp_path / "r.db")
    repo.salvar([_reg("a.pdf", "h1")])
    repo.salvar([_reg("a.pdf", "h2")])  # conteúdo mudou (hash novo)
    assert len(repo.listar()) == 2


def test_documento_sem_hash_usa_o_nome(tmp_path):
    repo = RepositorioSqlite(tmp_path / "r.db")
    falha = Registro(arquivo="ruim.pdf", status="failed", motivo="OCR: texto vazio")
    repo.salvar([falha])
    repo.salvar([falha])
    assert repo.contar_por_status() == {"failed": 1}


def test_dados_ficam_em_json(tmp_path):
    repo = RepositorioSqlite(tmp_path / "r.db")
    repo.salvar([_reg("a.pdf", "h1", dados=NotaFiscal(fornecedor="Acme", valor_total=10.5))])
    with sqlite3.connect(tmp_path / "r.db") as con:
        (dados,) = con.execute("SELECT dados_json FROM documentos").fetchone()
    assert json.loads(dados)["fornecedor"] == "Acme"
