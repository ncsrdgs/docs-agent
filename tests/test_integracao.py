"""Teste ponta a ponta: PDFs reais, OCR real e LLM simulado, até os arquivos de saída.

Roda o fluxo completo (OCR -> classificação por regras -> extração -> normalização ->
validação -> grafo -> gravação) com um modelo falso que devolve a leitura correta de
quatro documentos. Não chama a API; é pulado se o Tesseract ou os PDFs não existirem.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytesseract

from pipeline import graph, llm
from pipeline.models import (
    ContratoExtraido,
    ItemExtraido,
    NotaFiscalExtraida,
    ParteExtraida,
    RelatorioExtraido,
)
from pipeline.persist import gravar

RAIZ = Path(__file__).parent.parent
PDFS = RAIZ / "data" / "raw"
DOCUMENTOS = ["015_a7mn", "001_rljd", "001_z7fp", "001_pjpo"]

try:
    pytesseract.get_tesseract_version()
except Exception:  # Tesseract ausente
    pytest.skip("Tesseract não encontrado", allow_module_level=True)

if not all((PDFS / f"{d}.pdf").exists() for d in DOCUMENTOS):
    pytest.skip("PDFs de exemplo não encontrados em data/raw", allow_module_level=True)


def _item(d, q, u, t):
    return ItemExtraido(descricao=d, quantidade=q, valor_unitario=u, valor_total=t)


# Leitura correta de cada documento, com os valores como estão impressos.
RESPOSTAS = {
    "cadeira": NotaFiscalExtraida(  # 015_a7mn: soma dos itens bate com o total
        fornecedor="Móveis Escritório Conforto ME",
        cnpj="66.777.888/0001-99",
        data="28.10.2024",
        itens=[
            _item("Cadeira presidente ergonômica", "5", "1.290,00", "R$ 6450,00"),
            _item("Mesa escritório 1,50m c/ gavetas", "5", "R$850,00", "R$ 4.250,00"),
            _item("Armário alto 2 portas", "3", "BRL 720,00", "BRL 2.160,00"),
            _item("Gaveteiro volante 3 gavetas", "5", "R$ 380,00", "1.900,00"),
        ],
        valor_total="BRL 14.760,00",
    ),
    "notebook": NotaFiscalExtraida(  # 001_rljd: total impresso difere da soma dos itens
        fornecedor="Tech Solutions Ltda",
        cnpj="12.345.678/0001-90",
        data="15/03/23",
        itens=[
            _item("Notebook Dell Inspiron 15", "5", "R$ 3899,99", "BRL 19.499,95"),
            _item("Mouse sem fio Logitech M280", "10", "R$89,90", "R$ 899,00"),
            _item("Teclado mecânico Redragon Kumara", "8", "R$ 249,00", "R$ 1992,00"),
            _item("Monitor LG 24 polegadas Full HD", "5", "899,00", "BRL 4.495,00"),
            _item("Cabo HDMI 2.0 1,8m", "15", "R$29,90", "BRL 448,50"),
        ],
        valor_total="R$ 25791,45",
    ),
    "inventário": ContratoExtraido(  # 001_z7fp
        contratante=ParteExtraida(nome="Maria Aparecida Santos Silva", documento="123.456.789-00"),
        contratado=ParteExtraida(
            nome="Advocacia Especializada Dr. Roberto Mendes", documento="45.678.901/0001-23"
        ),
        objeto="Prestação de serviços advocatícios para acompanhamento de processo de inventário e partilha de bens",
        vigencia_inicio="2024-02-10",
        vigencia_fim="10/08/2024",
        valor_mensal="R$ 2.800,00",
    ),
    "poweredge": RelatorioExtraido(  # 001_pjpo
        data="2023-07-10",
        tecnico="Fernanda Costa Santo",
        equipamento="Servidor Dell PowerEdge R740",
        problema="Servidor apresentando lentidão extrema e timeouts frequentes nas aplicações.",
        solucao="Realizada limpeza completa interna do servidor com ar comprimido.",
    ),
}


class ModeloFalso:
    """Escolhe a resposta pela palavra-chave que aparece no texto enviado."""

    def with_structured_output(self, schema, include_raw=True):
        return self

    def invoke(self, mensagens):
        texto = mensagens[-1][1].lower()
        for chave, objeto in RESPOSTAS.items():
            if chave.lower() in texto:
                return {
                    "parsed": objeto,
                    "parsing_error": None,
                    "raw": SimpleNamespace(
                        usage_metadata={"input_tokens": 700, "output_tokens": 300}
                    ),
                }
        raise RuntimeError("texto do OCR sem palavra-chave conhecida")


@pytest.fixture(scope="module")
def registros(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setattr(llm, "_modelo", lambda _nome: ModeloFalso())
    mp.setattr(graph.config, "VISAO", False)  # o modelo falso só responde a texto
    mp.setenv("LLM_CACHE", "0")  # sem cache: o modelo falso é sempre consultado
    try:
        yield [graph.processar(PDFS / f"{d}.pdf") for d in DOCUMENTOS]
    finally:
        mp.undo()


def _por_arquivo(registros):
    return {r.arquivo: r for r in registros}


def test_status_e_tipo_de_cada_documento(registros):
    r = _por_arquivo(registros)
    assert r["015_a7mn.pdf"].status == "ok"
    assert r["015_a7mn.pdf"].tipo == "nota_fiscal"
    assert r["001_z7fp.pdf"].status == "ok"
    assert r["001_z7fp.pdf"].tipo == "contrato"
    assert r["001_pjpo.pdf"].status == "ok"
    assert r["001_pjpo.pdf"].tipo == "relatorio_manutencao"
    # total impresso diferente da soma dos itens: vai para revisão com o motivo
    assert r["001_rljd.pdf"].status == "needs_review"
    assert "soma" in r["001_rljd.pdf"].motivo.lower()


def test_dados_normalizados_com_tipos_finais(registros):
    nota = _por_arquivo(registros)["015_a7mn.pdf"].dados
    assert str(nota.data) == "2024-10-28"
    assert nota.valor_total == pytest.approx(14760.00)
    assert [i.valor_total for i in nota.itens] == [6450.0, 4250.0, 2160.0, 1900.0]
    contrato = _por_arquivo(registros)["001_z7fp.pdf"].dados
    assert str(contrato.vigencia.fim) == "2024-08-10"
    assert contrato.valor_mensal == pytest.approx(2800.00)


def test_uso_do_modelo_e_registrado(registros):
    for r in registros:
        assert r.tentativas == 1
        assert (r.tokens_in, r.tokens_out) == (700, 300)


def test_gravacao_dos_arquivos_de_saida(registros, tmp_path):
    gravar(registros, tmp_path)
    for nome in ["results.json", "summary.json", "notas_fiscais.csv", "notas_fiscais_itens.csv",
                 "contratos.csv", "relatorios_manutencao.csv"]:
        assert (tmp_path / nome).exists(), nome

    resultados = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert len(resultados) == 4
    resumo = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert resumo["total"] == 4
    assert resumo["por_status"] == {"ok": 3, "needs_review": 1}

    with open(tmp_path / "notas_fiscais.csv", encoding="utf-8-sig", newline="") as f:
        notas = list(csv.DictReader(f))
    assert len(notas) == 2  # a nota ok e a nota em revisão
    assert {n["status"] for n in notas} == {"ok", "needs_review"}
    with open(tmp_path / "notas_fiscais_itens.csv", encoding="utf-8-sig", newline="") as f:
        assert len(list(csv.DictReader(f))) == 9  # 4 + 5 itens
