"""Testes do classificador: regras e caminho do LLM (simulado)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline import classify
from pipeline.llm import Uso
from pipeline.models import Classificacao

HOLDOUT = json.loads(
    (Path(__file__).parent.parent / "data" / "holdout" / "classificacao.json").read_text(
        encoding="utf-8"
    )
)


@pytest.mark.parametrize(
    "texto, esperado",
    [
        ("FICHA DE SERVIÇO\nDt. Execução 10/07/2023", "relatorio_manutencao"),
        ("Ordem de Serviço\nOS Nº 83681", "relatorio_manutencao"),
        ("Relatório Técnico\nData: 14.09.2023", "relatorio_manutencao"),
        ("FATURA\nEmitido em: 15/03/23", "nota_fiscal"),
        ("DOCUMENTO FISCAL Nº 83236", "nota_fiscal"),
        ("NF-e Razão Social: Empresa", "nota_fiscal"),
        ("INSTRUMENTO PARTICULAR DE CONTRATO\n1. TOMADOR", "contrato"),
        ("ACORDO DE SERVIÇOS\nParte A", "contrato"),
        ("Contrato de Serviço\nCliente", "contrato"),
    ],
)
def test_regras_reconhecem_cabecalhos_conhecidos(texto, esperado):
    assert classify.classificar_por_regras(texto) == esperado


def test_regras_sem_cabecalho_conhecido_devolvem_none():
    assert classify.classificar_por_regras("Receita de bolo de cenoura") is None


def test_regras_ambiguas_devolvem_none():
    # dois tipos no cabeçalho: não decide, deixa para o LLM
    assert classify.classificar_por_regras("FATURA e CONTRATO DE SERVIÇO") is None


def test_regras_so_olham_o_cabecalho():
    # a menção a "nota fiscal" no meio do texto não faz um recibo virar nota fiscal
    texto = "RECIBO DE PAGAMENTO\n" + "x" * 150 + " nota fiscal"
    assert classify.classificar_por_regras(texto) is None


def test_conjunto_fora_dos_50_regras_nunca_respondem_errado():
    for item in HOLDOUT:
        r = classify.classificar_por_regras(item["texto"])
        assert r in (None, item["esperado"]), item["id"]


def test_regra_resolvida_nao_chama_o_llm(monkeypatch):
    def nao_deveria(*a, **k):
        raise AssertionError("LLM chamado")

    monkeypatch.setattr(classify, "extrair_estruturado", nao_deveria)
    cls, uso, metodo = classify.classificar("FATURA\nEmitido em 01/01/24")
    assert (cls.tipo, cls.confianca, metodo) == ("nota_fiscal", 1.0, "regra")
    assert uso.tokens_in == 0


def test_sem_regra_cai_no_llm(monkeypatch):
    chamadas = []

    def falso(schema, instrucoes, texto, modelo=None):
        chamadas.append(texto)
        return Classificacao(tipo="contrato", confianca=0.9), Uso(50, 5, 10)

    monkeypatch.setattr(classify, "extrair_estruturado", falso)
    cls, uso, metodo = classify.classificar("TERMO DE PRESTAÇÃO DE SERVIÇOS ...")
    assert (cls.tipo, metodo) == ("contrato", "llm")
    assert len(chamadas) == 1


def test_usar_regras_false_vai_direto_ao_llm(monkeypatch):
    monkeypatch.setattr(
        classify,
        "extrair_estruturado",
        lambda *a, **k: (Classificacao(tipo="nota_fiscal", confianca=0.8), Uso(1, 1, 1)),
    )
    _, _, metodo = classify.classificar("FATURA\n...", usar_regras=False)
    assert metodo == "llm"
