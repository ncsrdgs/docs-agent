"""Testes do fallback de visão: roteamento do grafo, regra de adoção e cache."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from pipeline import graph, llm
from pipeline.llm import ErroLLM, Uso
from pipeline.models import Classificacao, NotaFiscalExtraida
from pipeline.ocr import OcrError
from pipeline.validate import Validacao

OCR = {"texto": "texto qualquer", "sha256": "abc123", "conf_media": 90.0, "conf_min": 70.0}


class Cenario:
    """Texto com a validação programada; a visão devolve a validação sem âncora programada."""

    def __init__(self, monkeypatch, *, texto: Validacao, visao: Validacao | None = None,
                 visao_falha: str | None = None):
        self.chamadas_visao: list[tuple] = []
        self.validacoes_visao: list[bool] = []
        self._visao = visao or Validacao()

        monkeypatch.setattr(graph.config, "VISAO", True)
        monkeypatch.setattr(graph, "ocr_pdf", lambda _c: OCR)
        monkeypatch.setattr(
            graph, "classificar",
            lambda _t: (Classificacao(tipo="nota_fiscal", confianca=0.95), Uso(10, 2, 5), "regras"),
        )
        monkeypatch.setattr(graph, "extrair", lambda _t, _x, erro=None: (NotaFiscalExtraida(), Uso(100, 20, 50)))

        def validar(_tipo, _dados, _texto, ancora=True):
            if not ancora:
                self.validacoes_visao.append(ancora)
                return self._visao
            return texto

        def imagens_do_pdf(caminho, lado, paginas):
            if visao_falha == "ocr":
                raise OcrError("sem páginas")
            return [b"jpeg"]

        def extrair_por_imagem(tipo, imagens, ident):
            self.chamadas_visao.append((tipo, imagens, ident))
            if visao_falha == "llm":
                raise ErroLLM("resposta fora do schema")
            return NotaFiscalExtraida(fornecedor="lido da imagem"), Uso(500, 80, 300)

        monkeypatch.setattr(graph, "validar", validar)
        monkeypatch.setattr(graph, "imagens_do_pdf", imagens_do_pdf)
        monkeypatch.setattr(graph, "extrair_por_imagem", extrair_por_imagem)


def test_documento_ok_nao_passa_pela_visao(monkeypatch):
    c = Cenario(monkeypatch, texto=Validacao())
    r = graph.processar("data/raw/a.pdf")
    assert r.status == "ok" and not r.visao_tentada
    assert c.chamadas_visao == []


def test_erro_persistente_resolvido_pela_visao_vira_ok(monkeypatch):
    c = Cenario(monkeypatch, texto=Validacao(erros=["item 2 sem quantidade"]), visao=Validacao())
    r = graph.processar("data/raw/a.pdf")
    assert r.status == "ok"
    assert r.visao_tentada and r.visao_adotada
    assert any("visão" in a for a in r.avisos)
    assert c.validacoes_visao == [False]  # validou sem a âncora do texto do OCR
    assert c.chamadas_visao[0][2] == "abc123:1800:3"  # id estável para o cache
    assert (r.tokens_in, r.tokens_out) == (100 + 100 + 10 + 500, 20 + 20 + 2 + 80)
    assert "visão" in r.modelo


def test_anomalia_real_continua_em_revisao_mas_usa_os_dados_da_imagem(monkeypatch):
    anomalia = ["soma dos itens difere do total"]
    Cenario(monkeypatch, texto=Validacao(anomalias=anomalia), visao=Validacao(anomalias=anomalia))
    r = graph.processar("data/raw/a.pdf")
    assert r.status == "needs_review"
    assert "soma dos itens" in r.motivo
    assert r.visao_adotada and r.dados.fornecedor == "lido da imagem"


def test_erro_e_anomalia_real_viram_so_anomalia(monkeypatch):
    # caso da 014_yfas: o texto perdeu uma quantidade; pela imagem o item aparece e a soma não fecha
    Cenario(monkeypatch, texto=Validacao(erros=["item 2 sem quantidade"]),
            visao=Validacao(anomalias=["soma dos itens difere do total"]))
    r = graph.processar("data/raw/a.pdf")
    assert r.status == "needs_review"
    assert r.motivo.startswith("inconsistência no documento")
    assert r.visao_adotada


def test_anomalia_que_some_na_imagem_continua_em_revisao(monkeypatch):
    # a visão não tem âncora: se ela "fechar a conta", o documento não pode sair como ok
    Cenario(monkeypatch, texto=Validacao(anomalias=["soma dos itens difere do total"]),
            visao=Validacao())
    r = graph.processar("data/raw/a.pdf")
    assert r.status == "needs_review"
    assert r.motivo.startswith("texto e imagem divergem")
    assert "soma dos itens" in r.motivo
    assert r.visao_adotada and r.dados.fornecedor == "lido da imagem"


def test_visao_com_erro_nao_substitui(monkeypatch):
    Cenario(monkeypatch, texto=Validacao(anomalias=["soma"]), visao=Validacao(erros=["valor ausente"]))
    r = graph.processar("data/raw/a.pdf")
    assert r.visao_tentada and not r.visao_adotada
    assert r.dados.fornecedor is None  # mantém a leitura do texto
    assert r.tokens_in == 100 + 10 + 500  # o custo da tentativa continua contado


def test_visao_com_mais_anomalias_nao_substitui(monkeypatch):
    Cenario(monkeypatch, texto=Validacao(anomalias=["a"]), visao=Validacao(anomalias=["a", "b"]))
    r = graph.processar("data/raw/a.pdf")
    assert not r.visao_adotada


@pytest.mark.parametrize("falha", ["llm", "ocr"])
def test_falha_na_visao_mantem_o_resultado_do_texto(monkeypatch, falha):
    Cenario(monkeypatch, texto=Validacao(anomalias=["soma"]), visao_falha=falha)
    r = graph.processar("data/raw/a.pdf")
    assert r.status == "needs_review" and "soma" in r.motivo
    assert r.visao_tentada and not r.visao_adotada
    assert r.dados is not None


def test_visao_desligada(monkeypatch):
    c = Cenario(monkeypatch, texto=Validacao(anomalias=["soma"]))
    monkeypatch.setattr(graph.config, "VISAO", False)
    r = graph.processar("data/raw/a.pdf")
    assert r.status == "needs_review" and not r.visao_tentada
    assert c.chamadas_visao == []


def test_visao_roda_uma_vez_so(monkeypatch):
    c = Cenario(monkeypatch, texto=Validacao(erros=["x"]), visao=Validacao(erros=["y"]))
    graph.processar("data/raw/a.pdf")
    assert len(c.chamadas_visao) == 1


# --- llm: mensagem com imagem e chave de cache ---------------------------------
def test_chave_de_texto_nao_muda_e_a_de_imagem_depende_do_id():
    from pipeline.models import RelatorioExtraido as S

    base = llm._chave(S, "i", "t", "m")
    assert base == llm._chave(S, "i", "t", "m", None)
    com_a = llm._chave(S, "i", "t", "m", "pdf1")
    assert com_a != base
    assert com_a != llm._chave(S, "i", "t", "m", "pdf2")


def test_mensagem_com_imagem_tem_bloco_image_url():
    msgs = llm._mensagens("sistema", "texto", [b"\xff\xd8abc"])
    conteudo = msgs[1].content
    assert conteudo[0] == {"type": "text", "text": "texto"}
    assert conteudo[1]["type"] == "image_url"
    assert conteudo[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert llm._mensagens("s", "t", None) == [("system", "s"), ("human", "t")]


def test_extracao_por_imagem_usa_o_cache(monkeypatch, tmp_path):
    from pipeline.models import RelatorioExtraido as S

    monkeypatch.setenv("LLM_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_CACHE", "1")
    chamadas = []

    class Falso:
        def with_structured_output(self, schema, include_raw=True):
            return self

        def invoke(self, mensagens):
            chamadas.append(mensagens)
            return {
                "parsed": S(equipamento="Servidor"),
                "parsing_error": None,
                "raw": SimpleNamespace(usage_metadata={"input_tokens": 9, "output_tokens": 3}),
            }

    monkeypatch.setattr(llm, "_modelo", lambda _n: Falso())
    a, _ = llm.extrair_estruturado(S, "i", "t", "m", imagens=[b"1"], imagens_id="pdf1")
    b, uso = llm.extrair_estruturado(S, "i", "t", "m", imagens=[b"2"], imagens_id="pdf1")
    assert len(chamadas) == 1  # mesmo id: a segunda vem do disco, mesmo com bytes diferentes
    assert b.equipamento == "Servidor" and uso.latencia_ms == 0
    llm.extrair_estruturado(S, "i", "t", "m", imagens=[b"1"], imagens_id="pdf2")
    assert len(chamadas) == 2
    assert len(list(tmp_path.glob("*.json"))) == 2


# --- validate: sem âncora -----------------------------------------------------------
def test_validar_sem_ancora_nao_exige_numeros_no_texto():
    from pipeline.models import Item, NotaFiscal
    from pipeline.validate import validar

    nota = NotaFiscal(
        fornecedor="F", cnpj="12.345.678/0001-90", data="2024-01-01",
        itens=[Item(descricao="x", quantidade=2, valor_unitario=10.0, valor_total=20.0)],
        valor_total=20.0,
    )
    com = validar("nota_fiscal", nota, "texto sem numeros")
    sem = validar("nota_fiscal", nota, "texto sem numeros", ancora=False)
    assert com.erros  # valores e cnpj não aparecem no texto
    assert sem.erros == [] and sem.anomalias == []
