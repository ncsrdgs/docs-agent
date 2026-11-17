"""Testes do resumo: tempo de OCR, versão do Tesseract e custo estimado."""
from __future__ import annotations

import pytest

from pipeline import config, persist
from pipeline.models import Registro


def _reg(nome, ocr_ms=None, paginas=None, tesseract=None, tin=0, tout=0):
    return Registro(
        arquivo=nome, status="ok", tipo="contrato", tokens_in=tin, tokens_out=tout,
        ocr_ms=ocr_ms, paginas=paginas, tesseract=tesseract,
    )


def test_resumo_de_ocr():
    regs = [_reg("a.pdf", 2000, 1, "5.4.0"), _reg("b.pdf", 1000, 1, "5.4.0")]
    r = persist._resumo(regs)
    assert r["ocr_ms_medio_por_documento"] == 1500
    assert r["ocr_paginas_por_segundo"] == pytest.approx(0.67)
    assert r["tesseract"] == ["5.4.0"]


def test_resumo_sem_medicao_de_ocr_nao_quebra():
    # resultados antigos (cache sem tempo de OCR)
    r = persist._resumo([_reg("a.pdf")])
    assert r["ocr_ms_medio_por_documento"] is None
    assert r["ocr_paginas_por_segundo"] is None
    assert r["tesseract"] == []


def test_custo_so_aparece_com_precos(monkeypatch):
    regs = [_reg("a.pdf", tin=1_000_000, tout=500_000), _reg("b.pdf", tin=1_000_000, tout=500_000)]
    monkeypatch.setattr(config, "PRECO_ENTRADA", None)
    monkeypatch.setattr(config, "PRECO_SAIDA", None)
    assert "custo_estimado_usd" not in persist._resumo(regs)

    monkeypatch.setattr(config, "PRECO_ENTRADA", 0.10)
    monkeypatch.setattr(config, "PRECO_SAIDA", 0.40)
    r = persist._resumo(regs)
    # 2M entrada x 0,10 + 1M saída x 0,40 = 0,60
    assert r["custo_estimado_usd"] == pytest.approx(0.60)
    assert r["custo_medio_por_documento_usd"] == pytest.approx(0.30)
