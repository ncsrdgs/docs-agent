"""Normalização de datas, valores e documentos (CPF/CNPJ).

Converte o que o LLM extraiu (textos como no documento) nos modelos finais.
Texto que não puder ser interpretado vira None e gera um aviso, em vez de erro.
"""
from __future__ import annotations

import re
from datetime import date

from pipeline.models import (
    Contrato,
    ContratoExtraido,
    Item,
    NotaFiscal,
    NotaFiscalExtraida,
    Parte,
    RelatorioExtraido,
    RelatorioManutencao,
    Vigencia,
)

MESES = {
    "janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4,
    "maio": 5, "junho": 6, "julho": 7, "agosto": 8, "setembro": 9,
    "outubro": 10, "novembro": 11, "dezembro": 12,
}


def parse_data(texto: str | None) -> date | None:
    """Interpreta datas brasileiras e ISO. Ano de 2 dígitos vira 20xx."""
    if not texto:
        return None
    t = texto.strip().lower()

    m = re.search(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)", t)
    if m:
        a, mes, d = (int(x) for x in m.groups())
    else:
        m = re.search(r"(?<!\d)(\d{1,2})\s+de\s+([a-zç]+)\s+de\s+(\d{4})(?!\d)", t)
        if m and m.group(2) in MESES:
            d, mes, a = int(m.group(1)), MESES[m.group(2)], int(m.group(3))
        else:
            m = re.search(r"(?<!\d)(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4}|\d{2})(?!\d)", t)
            if not m:
                return None
            d, mes, a = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if a < 100:
                a += 2000
    try:
        return date(a, mes, d)
    except ValueError:
        return None


def parse_valor(texto: str | None) -> float | None:
    """Interpreta valores como 'R$ 1.950,00', 'BRL 3899,99', '28.500,00 por mês'."""
    if not texto:
        return None
    m = re.search(r"\d[\d.,]*\d|\d", texto)
    if not m:
        return None
    n = m.group()
    if "," in n and "." in n:
        if n.rfind(",") > n.rfind("."):
            n = n.replace(".", "").replace(",", ".")
        else:
            n = n.replace(",", "")
    elif "," in n:
        n = n.replace(",", ".")
    elif "." in n and re.fullmatch(r"\d{1,3}(\.\d{3})+", n):
        n = n.replace(".", "")
    try:
        return round(float(n), 2)
    except ValueError:
        return None


def formatar_documento(texto: str | None) -> tuple[str | None, bool]:
    """Formata CNPJ (14 dígitos) ou CPF (11). Devolve (texto, formato_ok)."""
    if not texto or not texto.strip():
        return None, True
    d = re.sub(r"\D", "", texto)
    if len(d) == 14:
        return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}", True
    if len(d) == 11:
        return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}", True
    return texto.strip(), False


# --------------------------------------------------------------------------
def _data(texto, rotulo, avisos):
    r = parse_data(texto)
    if texto and texto.strip() and r is None:
        avisos.append(f"{rotulo}: não consegui interpretar '{texto}'")
    return r


def _valor(texto, rotulo, avisos):
    r = parse_valor(texto)
    if texto and texto.strip() and r is None:
        avisos.append(f"{rotulo}: não consegui interpretar '{texto}'")
    return r


def _documento(texto, rotulo, avisos):
    r, ok = formatar_documento(texto)
    if not ok:
        avisos.append(f"{rotulo}: formato inesperado '{texto}'")
    return r


_MARCADOR_LISTA = re.compile(r"^\s*[-–—•*·]+\s+")


def limpar_descricao(texto: str | None) -> str | None:
    """Tira o marcador de lista ("- Item") que alguns documentos imprimem antes da descrição."""
    if texto is None:
        return None
    limpo = _MARCADOR_LISTA.sub("", texto).strip()
    return limpo or texto.strip()


def normalizar(tipo: str, extraido) -> tuple[object, list[str]]:
    """Converte o modelo *Extraido no modelo final. Devolve (modelo, avisos)."""
    avisos: list[str] = []

    if tipo == "nota_fiscal":
        e: NotaFiscalExtraida = extraido
        itens = [
            Item(
                descricao=limpar_descricao(i.descricao),
                quantidade=_valor(i.quantidade, f"item {n} quantidade", avisos),
                valor_unitario=_valor(i.valor_unitario, f"item {n} valor unitário", avisos),
                valor_total=_valor(i.valor_total, f"item {n} valor total", avisos),
            )
            for n, i in enumerate(e.itens, start=1)
        ]
        final = NotaFiscal(
            fornecedor=e.fornecedor,
            cnpj=_documento(e.cnpj, "cnpj", avisos),
            data=_data(e.data, "data", avisos),
            itens=itens,
            valor_total=_valor(e.valor_total, "valor total", avisos),
        )
        return final, avisos

    if tipo == "contrato":
        e: ContratoExtraido = extraido
        final = Contrato(
            contratante=Parte(
                nome=e.contratante.nome,
                documento=_documento(e.contratante.documento, "documento do contratante", avisos),
            ),
            contratado=Parte(
                nome=e.contratado.nome,
                documento=_documento(e.contratado.documento, "documento do contratado", avisos),
            ),
            objeto=e.objeto,
            vigencia=Vigencia(
                inicio=_data(e.vigencia_inicio, "início da vigência", avisos),
                fim=_data(e.vigencia_fim, "fim da vigência", avisos),
            ),
            valor_mensal=_valor(e.valor_mensal, "valor mensal", avisos),
        )
        return final, avisos

    if tipo == "relatorio_manutencao":
        e: RelatorioExtraido = extraido
        final = RelatorioManutencao(
            data=_data(e.data, "data", avisos),
            tecnico=e.tecnico,
            equipamento=e.equipamento,
            problema=e.problema,
            solucao=e.solucao,
        )
        return final, avisos

    raise ValueError(f"tipo de documento desconhecido: {tipo}")
