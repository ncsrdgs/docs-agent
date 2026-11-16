from datetime import date

from pipeline.models import (
    Contrato,
    Item,
    NotaFiscal,
    Parte,
    RelatorioManutencao,
    Vigencia,
)
from pipeline.validate import validar


def _nota(itens, total, cnpj=None):
    return NotaFiscal(
        fornecedor="Fornecedor Ltda",
        cnpj=cnpj,
        data=date(2024, 1, 10),
        itens=itens,
        valor_total=total,
    )


def _item(qtd=2.0, unit=10.0, total=20.0):
    return Item(descricao="Produto", quantidade=qtd, valor_unitario=unit, valor_total=total)


def test_nota_consistente_sem_pendencias():
    r = validar("nota_fiscal", _nota([_item()], 20.0), "2 x 10,00 20,00 20,00")
    assert r.erros == [] and r.anomalias == []


def test_soma_diferente_do_total_e_anomalia_sem_erro():
    r = validar("nota_fiscal", _nota([_item()], 25.0), "2 x 10,00 20,00 25,00")
    assert r.erros == []
    assert len(r.anomalias) == 1
    assert "soma dos itens" in r.anomalias[0]


def test_item_sem_valor_total_e_erro_de_extracao():
    r = validar("nota_fiscal", _nota([_item(total=None)], 20.0), "2 x 10,00 20,00")
    assert "item 1 sem valor total" in r.erros


def test_valor_que_nao_esta_no_texto_e_erro():
    r = validar("nota_fiscal", _nota([_item()], 20.0), "2 x 10,00")
    assert any("não aparecem no texto" in e for e in r.erros)
    assert r.anomalias == []


def test_cnpj_com_digito_invalido_e_so_aviso():
    texto = "12.345.678/0001-90 2 x 10,00 20,00"
    r = validar("nota_fiscal", _nota([_item()], 20.0, cnpj="12.345.678/0001-90"), texto)
    assert r.erros == []
    assert any("CNPJ" in a for a in r.avisos)


def test_contrato_com_vigencia_incoerente_e_anomalia():
    c = Contrato(
        contratante=Parte(nome="A"),
        contratado=Parte(nome="B"),
        vigencia=Vigencia(inicio=date(2024, 6, 1), fim=date(2024, 1, 1)),
        valor_mensal=100.0,
    )
    r = validar("contrato", c, "valor 100,00")
    assert r.erros == []
    assert any("vigência incoerente" in a for a in r.anomalias)


def test_relatorio_sem_tecnico_e_erro():
    rel = RelatorioManutencao(
        data=date(2024, 1, 1), equipamento="Servidor", problema="p", solucao="s"
    )
    r = validar("relatorio_manutencao", rel, "texto")
    assert "campo obrigatório ausente: técnico" in r.erros