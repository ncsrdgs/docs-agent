"""Testes do cache de respostas do LLM (modelo simulado)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from pipeline import llm
from pipeline.models import RelatorioExtraido


class ModeloFalso:
    def __init__(self):
        self.chamadas = 0

    def with_structured_output(self, schema, include_raw=True):
        return self

    def invoke(self, _mensagens):
        self.chamadas += 1
        return {
            "parsed": RelatorioExtraido(tecnico="Ana", equipamento=f"Bomba {self.chamadas}"),
            "parsing_error": None,
            "raw": SimpleNamespace(usage_metadata={"input_tokens": 120, "output_tokens": 15}),
        }


@pytest.fixture
def modelo(monkeypatch, tmp_path):
    falso = ModeloFalso()
    monkeypatch.setattr(llm, "_modelo", lambda _nome: falso)
    monkeypatch.setenv("LLM_CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("LLM_CACHE", raising=False)
    return falso


def test_segunda_chamada_vem_do_cache(modelo):
    a, uso_a = llm.extrair_estruturado(RelatorioExtraido, "instr", "texto")
    b, uso_b = llm.extrair_estruturado(RelatorioExtraido, "instr", "texto")
    assert modelo.chamadas == 1
    assert a == b  # mesma resposta, mesmo com modelo não determinístico
    assert (uso_b.tokens_in, uso_b.tokens_out) == (120, 15)
    assert uso_b.latencia_ms == 0


def test_entrada_diferente_nao_reaproveita(modelo):
    llm.extrair_estruturado(RelatorioExtraido, "instr", "texto 1")
    llm.extrair_estruturado(RelatorioExtraido, "instr", "texto 2")
    llm.extrair_estruturado(RelatorioExtraido, "instr", "texto 1", modelo="outro-modelo")
    llm.extrair_estruturado(RelatorioExtraido, "instr com feedback", "texto 1")
    assert modelo.chamadas == 4


def test_cache_desligado(modelo, monkeypatch):
    monkeypatch.setenv("LLM_CACHE", "0")
    llm.extrair_estruturado(RelatorioExtraido, "instr", "texto")
    llm.extrair_estruturado(RelatorioExtraido, "instr", "texto")
    assert modelo.chamadas == 2


def test_arquivo_corrompido_refaz_a_chamada(modelo, tmp_path):
    llm.extrair_estruturado(RelatorioExtraido, "instr", "texto")
    for arq in tmp_path.glob("*.json"):
        arq.write_text("{ quebrado", encoding="utf-8")
    llm.extrair_estruturado(RelatorioExtraido, "instr", "texto")
    assert modelo.chamadas == 2


def test_credencial_ausente_vira_erro_de_configuracao(monkeypatch, tmp_path):
    def sem_credencial(_nome):
        raise ValueError("API key required")

    monkeypatch.setattr(llm, "_modelo", sem_credencial)
    monkeypatch.setenv("LLM_CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("LLM_CACHE", raising=False)
    with pytest.raises(llm.ErroConfiguracao, match="API key required"):
        llm.extrair_estruturado(RelatorioExtraido, "instr", "texto")


def test_resposta_em_cache_dispensa_credencial(modelo, monkeypatch):
    llm.extrair_estruturado(RelatorioExtraido, "instr", "texto")  # grava no cache

    def sem_credencial(_nome):
        raise ValueError("API key required")

    monkeypatch.setattr(llm, "_modelo", sem_credencial)
    obj, _ = llm.extrair_estruturado(RelatorioExtraido, "instr", "texto")  # não precisa do modelo
    assert obj.tecnico == "Ana"
