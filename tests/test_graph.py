"""Testes do roteamento do grafo, com OCR, classificação, extração e validação simulados."""
from __future__ import annotations

import pytest

from pipeline import graph
from pipeline.llm import ErroLLM, Uso
from pipeline.models import Classificacao, NotaFiscalExtraida
from pipeline.ocr import OcrError
from pipeline.validate import Validacao

OCR = {"texto": "texto qualquer", "sha256": "abc", "conf_media": 90.0, "conf_min": 70.0}


class Simulacao:
    """Registra as chamadas e devolve respostas programadas."""

    def __init__(self, monkeypatch, *, classificacao=("nota_fiscal", 0.95), validacoes=None,
                 extracao_falha=False, ocr_falha=False):
        self.extracoes: list[str | None] = []
        self._validacoes = list(validacoes or [Validacao(erros=[], anomalias=[], avisos=[])])

        def ocr_pdf(_caminho):
            if ocr_falha:
                raise OcrError("texto praticamente vazio")
            return OCR

        def classificar(_texto):
            tipo, conf = classificacao
            return Classificacao(tipo=tipo, confianca=conf), Uso(10, 2, 5), "regras"

        def extrair(_tipo, _texto, erro=None):
            self.extracoes.append(erro)
            if extracao_falha:
                raise ErroLLM("resposta fora do schema")
            return NotaFiscalExtraida(), Uso(100, 20, 50)

        def validar(_tipo, _dados, _texto):
            return self._validacoes.pop(0) if len(self._validacoes) > 1 else self._validacoes[0]

        monkeypatch.setattr(graph, "ocr_pdf", ocr_pdf)
        monkeypatch.setattr(graph, "classificar", classificar)
        monkeypatch.setattr(graph, "extrair", extrair)
        monkeypatch.setattr(graph, "validar", validar)
        monkeypatch.setattr(graph.config, "VISAO", False)  # a visão tem testes próprios


def test_ok_sem_pendencias(monkeypatch):
    sim = Simulacao(monkeypatch)
    r = graph.processar("data/raw/a.pdf")
    assert r.status == "ok"
    assert r.tentativas == 1
    assert len(sim.extracoes) == 1
    assert r.tokens_in == 110 and r.tokens_out == 22


def test_erro_corrigido_na_segunda_tentativa(monkeypatch):
    sim = Simulacao(
        monkeypatch,
        validacoes=[Validacao(erros=["valor_total ausente"], anomalias=[], avisos=[]), Validacao()],
    )
    r = graph.processar("data/raw/a.pdf")
    assert r.status == "ok"
    assert r.tentativas == 2
    assert sim.extracoes[0] is None
    assert "valor_total ausente" in sim.extracoes[1]  # o erro volta como feedback
    assert graph.config.MODELO_FORTE in r.modelo


def test_erro_persistente_vai_para_revisao(monkeypatch):
    sim = Simulacao(
        monkeypatch, validacoes=[Validacao(erros=["cnpj fora do texto"], anomalias=[], avisos=[])]
    )
    r = graph.processar("data/raw/a.pdf")
    assert r.status == "needs_review"
    assert r.tentativas == graph.MAX_TENTATIVAS
    assert len(sim.extracoes) == graph.MAX_TENTATIVAS  # não entra em loop
    assert "cnpj fora do texto" in r.motivo


def test_anomalia_vai_para_revisao_sem_nova_tentativa(monkeypatch):
    sim = Simulacao(
        monkeypatch,
        validacoes=[Validacao(erros=[], anomalias=["soma dos itens difere do total"], avisos=[])],
    )
    r = graph.processar("data/raw/a.pdf")
    assert r.status == "needs_review"
    assert r.tentativas == 1
    assert len(sim.extracoes) == 1
    assert "soma dos itens" in r.motivo
    assert r.dados is not None  # os dados extraídos são mantidos para o revisor


@pytest.mark.parametrize("classificacao", [("desconhecido", 0.9), ("nota_fiscal", 0.3)])
def test_classificacao_incerta_nao_extrai(monkeypatch, classificacao):
    sim = Simulacao(monkeypatch, classificacao=classificacao)
    r = graph.processar("data/raw/a.pdf")
    assert r.status == "needs_review"
    assert sim.extracoes == []


def test_falha_de_ocr(monkeypatch):
    sim = Simulacao(monkeypatch, ocr_falha=True)
    r = graph.processar("data/raw/a.pdf")
    assert r.status == "failed"
    assert "OCR" in r.motivo
    assert sim.extracoes == []


def test_falha_do_llm_na_extracao(monkeypatch):
    sim = Simulacao(monkeypatch, extracao_falha=True)
    r = graph.processar("data/raw/a.pdf")
    assert r.status == "failed"
    assert len(sim.extracoes) == graph.MAX_TENTATIVAS
    assert "extração falhou" in r.motivo
    # a mensagem da exceção não volta ao modelo como se fosse erro de validação
    assert sim.extracoes == [None, None]
