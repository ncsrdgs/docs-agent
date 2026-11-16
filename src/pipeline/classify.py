"""Classificação do tipo de documento.

Primeiro regras de palavra-chave no cabeçalho (custo zero). O LLM só é chamado
quando nenhuma regra casa ou quando mais de uma casa.
"""
from __future__ import annotations

import re

from pipeline import config
from pipeline.llm import Uso, extrair_estruturado
from pipeline.models import Classificacao

REGRAS: dict[str, list[str]] = {
    "relatorio_manutencao": [
        r"FICHA DE SERVI[ÇC]O",
        r"ORDEM DE SERVI[ÇC]O",
        r"RELAT[ÓO]RIO T[ÉE]CNICO",
    ],
    "nota_fiscal": [
        r"\bFATURA\b",
        r"DOCUMENTO FISCAL",
        r"\bNF-?E\b",
        r"NOTA FISCAL",
    ],
    "contrato": [
        r"INSTRUMENTO PARTICULAR DE CONTRATO",
        r"CONTRATO DE PRESTA[ÇC][ÃA]O DE SERVI[ÇC]OS",
        r"CONTRATO DE SERVI[ÇC]O",
        r"ACORDO DE SERVI[ÇC]OS",
    ],
}


def classificar_por_regras(texto: str) -> str | None:
    """Tipo se exatamente um tipo casa no cabeçalho; senão None."""
    cabecalho = texto[: config.CABECALHO_REGRAS].upper()
    tipos = {
        tipo
        for tipo, padroes in REGRAS.items()
        if any(re.search(p, cabecalho) for p in padroes)
    }
    return tipos.pop() if len(tipos) == 1 else None


def classificar(texto: str) -> tuple[Classificacao, Uso, str]:
    """Devolve (classificação, uso do LLM, método: 'regra' ou 'llm')."""
    tipo = classificar_por_regras(texto)
    if tipo:
        return Classificacao(tipo=tipo, confianca=1.0), Uso(), "regra"

    resultado, uso = extrair_estruturado(
        Classificacao,
        config.carregar_prompt("classify"),
        texto[: config.TRECHO_CLASSIFICACAO],
    )
    return resultado, uso, "llm"
