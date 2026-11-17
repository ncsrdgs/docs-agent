from datetime import date

import pytest

from pipeline.normalize import formatar_documento, parse_data, parse_valor


@pytest.mark.parametrize(
    "texto,esperado",
    [
        ("15/03/23", date(2023, 3, 15)),
        ("18/11/23", date(2023, 11, 18)),
        ("14.09.2023", date(2023, 9, 14)),
        ("15 de junho de 2024", date(2024, 6, 15)),
        ("2024-02-10", date(2024, 2, 10)),
        ("31/02/2024", None),
        ("sem data", None),
        (None, None),
    ],
)
def test_parse_data(texto, esperado):
    assert parse_data(texto) == esperado


@pytest.mark.parametrize(
    "texto,esperado",
    [
        ("R$ 3899,99", 3899.99),
        ("BRL 19.499,95", 19499.95),
        ("R$ 1.950,00", 1950.0),
        ("R$6.500,00", 6500.0),
        ("28.500,00 por mês", 28500.0),
        ("745,00", 745.0),
        ("50", 50.0),
        ("abc", None),
        (None, None),
    ],
)
def test_parse_valor(texto, esperado):
    assert parse_valor(texto) == esperado


def test_formatar_cnpj_com_e_sem_pontuacao():
    assert formatar_documento("12345678000190") == ("12.345.678/0001-90", True)
    assert formatar_documento("12.345.678/000190") == ("12.345.678/0001-90", True)


def test_formatar_cpf():
    assert formatar_documento("12345678900") == ("123.456.789-00", True)


def test_formatar_documento_invalido_e_vazio():
    assert formatar_documento("123") == ("123", False)
    assert formatar_documento(None) == (None, True)

# --- marcador de lista na descrição dos itens ---------------------------------------
@pytest.mark.parametrize(
    "entrada, esperado",
    [
        ("- Cabo HDMI 2.0 1,8m", "Cabo HDMI 2.0 1,8m"),
        ("– Areia média m³", "Areia média m³"),
        ("• Brita 1 m³", "Brita 1 m³"),
        ("  -  Cimento CP II 50kg ", "Cimento CP II 50kg"),
        ("Cabo HDMI 2.0 1,8m", "Cabo HDMI 2.0 1,8m"),
        ("Cabo USB-C 2m", "Cabo USB-C 2m"),  # hífen no meio não é marcador
        ("-5V fonte", "-5V fonte"),  # sem espaço depois do hífen: mantém
        ("-", "-"),
        (None, None),
    ],
)
def test_limpar_descricao(entrada, esperado):
    from pipeline.normalize import limpar_descricao

    assert limpar_descricao(entrada) == esperado


def test_normalizar_nota_limpa_o_marcador_dos_itens():
    from pipeline.models import ItemExtraido, NotaFiscalExtraida
    from pipeline.normalize import normalizar

    ext = NotaFiscalExtraida(
        fornecedor="F",
        itens=[ItemExtraido(descricao="- Cabo HDMI", quantidade="2", valor_unitario="R$ 10,00",
                            valor_total="R$ 20,00")],
        valor_total="R$ 20,00",
    )
    final, _ = normalizar("nota_fiscal", ext)
    assert final.itens[0].descricao == "Cabo HDMI"
