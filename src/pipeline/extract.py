"""Extração estruturada: texto do OCR -> modelo *Extraido (via LLM)."""
from __future__ import annotations

from pipeline import config
from pipeline.llm import Uso, extrair_estruturado
from pipeline.models import SCHEMA_EXTRACAO

PROMPTS = {
    "nota_fiscal": "extract_nota_fiscal",
    "contrato": "extract_contrato",
    "relatorio_manutencao": "extract_relatorio",
}


def extrair(tipo: str, texto: str, erro: str | None = None):
    """Extrai os campos do tipo informado. Devolve (modelo_extraido, Uso).

    Se `erro` vier preenchido (retry), ele é anexado ao prompt para o modelo corrigir.
    """
    if tipo not in SCHEMA_EXTRACAO:
        raise ValueError(f"tipo sem extração: {tipo}")
    instrucoes = config.carregar_prompt(PROMPTS[tipo])
    if erro:
        instrucoes += (
            f"\n\nA tentativa anterior foi rejeitada: {erro}\n"
            "Releia o documento e extraia de novo, copiando os valores como estão impressos."
        )
    return extrair_estruturado(SCHEMA_EXTRACAO[tipo], instrucoes, texto)


if __name__ == "__main__":
    import sys
    from pathlib import Path

    from pipeline.classify import classificar
    from pipeline.normalize import normalizar
    from pipeline.ocr import ocr_pdf

    for nome in sys.argv[1:]:
        ocr = ocr_pdf(Path("data/raw") / f"{nome}.pdf")
        cls, uso_c, metodo = classificar(ocr["texto"])
        print(f"\n=== {nome} | {cls.tipo} ({metodo}, conf {cls.confianca})")
        if cls.tipo == "desconhecido":
            continue
        ext, uso = extrair(cls.tipo, ocr["texto"])
        final, avisos = normalizar(cls.tipo, ext)
        print(final.model_dump_json(indent=2))
        print("avisos:", avisos)
        print(uso)
