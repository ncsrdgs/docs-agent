"""Grafo LangGraph: OCR -> classificação -> extração -> validação, com roteamento.

Roteamento:
- falha de OCR/LLM                         -> failed
- tipo desconhecido ou confiança baixa      -> needs_review
- erro de extração (campo ausente, valor    -> nova tentativa (1x) com o modelo maior
  fora do texto)                              e, se persistir, needs_review
- anomalia do documento (somas, vigência)   -> needs_review, sem nova tentativa
- sem pendências                            -> ok
"""
from __future__ import annotations

from pathlib import Path
from typing import TypedDict

import pytesseract
from langgraph.graph import END, START, StateGraph

from pipeline import config
from pipeline.classify import classificar
from pipeline.extract import extrair
from pipeline.llm import ErroLLM, Uso
from pipeline.models import Registro
from pipeline.normalize import normalizar
from pipeline.ocr import OcrError, ocr_pdf
from pipeline.validate import validar

CONFIANCA_MIN = 0.7
MAX_TENTATIVAS = 2  # primeira extração + 1 nova tentativa


class Estado(TypedDict, total=False):
    caminho: str
    ocr: dict
    tipo: str
    confianca: float
    extraido: object
    dados: object
    erros: list[str]
    anomalias: list[str]
    avisos: list[str]
    tentativas: int
    tokens_in: int
    tokens_out: int
    latencia_ms: int
    falha: str
    status: str
    motivo: str


def _soma(s: Estado, uso: Uso) -> dict:
    return {
        "tokens_in": s.get("tokens_in", 0) + uso.tokens_in,
        "tokens_out": s.get("tokens_out", 0) + uso.tokens_out,
        "latencia_ms": s.get("latencia_ms", 0) + uso.latencia_ms,
    }


# --------------------------------------------------------------------------
# Nós
# --------------------------------------------------------------------------
def n_ocr(s: Estado) -> dict:
    try:
        return {"ocr": ocr_pdf(s["caminho"])}
    except OcrError as e:
        return {"falha": f"OCR: {e}"}


def n_classificar(s: Estado) -> dict:
    try:
        cls, uso, _ = classificar(s["ocr"]["texto"])
    except ErroLLM as e:
        return {"falha": f"classificação: {e}"}
    return {"tipo": cls.tipo, "confianca": cls.confianca, **_soma(s, uso)}


def n_extrair(s: Estado) -> dict:
    tentativa = s.get("tentativas", 0) + 1
    erro = "; ".join(s["erros"]) if s.get("erros") else None
    try:
        ext, uso = extrair(s["tipo"], s["ocr"]["texto"], erro)
    except ErroLLM as e:
        return {"tentativas": tentativa, "extraido": None, "erros": [str(e)]}
    return {"tentativas": tentativa, "extraido": ext, "erros": [], **_soma(s, uso)}


def n_validar(s: Estado) -> dict:
    tipo, texto = s["tipo"], s["ocr"]["texto"]
    try:
        final, avisos = normalizar(tipo, s["extraido"])
    except Exception as e:
        return {"erros": [f"normalização: {e}"], "anomalias": [], "avisos": []}
    v = validar(tipo, final, texto)
    return {
        "dados": final,
        "erros": v.erros,
        "anomalias": v.anomalias,
        "avisos": avisos + v.avisos,
    }


def n_fechar(s: Estado) -> dict:
    if s.get("falha"):
        return {"status": "failed", "motivo": s["falha"]}
    tipo, conf = s.get("tipo"), s.get("confianca", 0.0)
    if tipo == "desconhecido" or conf < CONFIANCA_MIN:
        return {
            "status": "needs_review",
            "motivo": f"classificação incerta (tipo={tipo}, confiança={conf})",
        }
    if s.get("extraido") is None:
        return {
            "status": "failed",
            "motivo": "extração falhou: " + "; ".join(s.get("erros", [])),
        }
    if s.get("erros"):
        return {
            "status": "needs_review",
            "motivo": f"extração não passou na validação após {s.get('tentativas', 0)} tentativa(s): "
            + "; ".join(s["erros"]),
        }
    if s.get("anomalias"):
        return {
            "status": "needs_review",
            "motivo": "inconsistência no documento: " + "; ".join(s["anomalias"]),
        }
    return {"status": "ok"}


# --------------------------------------------------------------------------
# Roteamento
# --------------------------------------------------------------------------
def r_ocr(s: Estado) -> str:
    return "fechar" if s.get("falha") else "classificar"


def r_classificar(s: Estado) -> str:
    if s.get("falha"):
        return "fechar"
    if s["tipo"] == "desconhecido" or s["confianca"] < CONFIANCA_MIN:
        return "fechar"
    return "extrair"


def r_extrair(s: Estado) -> str:
    if s.get("extraido") is not None:
        return "validar"
    return "extrair" if s["tentativas"] < MAX_TENTATIVAS else "fechar"


def r_validar(s: Estado) -> str:
    if s.get("erros") and s["tentativas"] < MAX_TENTATIVAS:
        return "extrair"
    return "fechar"


def _montar():
    g = StateGraph(Estado)
    g.add_node("ocr", n_ocr)
    g.add_node("classificar", n_classificar)
    g.add_node("extrair", n_extrair)
    g.add_node("validar", n_validar)
    g.add_node("fechar", n_fechar)

    g.add_edge(START, "ocr")
    g.add_conditional_edges("ocr", r_ocr, {"classificar": "classificar", "fechar": "fechar"})
    g.add_conditional_edges(
        "classificar", r_classificar, {"extrair": "extrair", "fechar": "fechar"}
    )
    g.add_conditional_edges(
        "extrair", r_extrair, {"validar": "validar", "extrair": "extrair", "fechar": "fechar"}
    )
    g.add_conditional_edges("validar", r_validar, {"extrair": "extrair", "fechar": "fechar"})
    g.add_edge("fechar", END)
    return g.compile()


GRAFO = _montar()


def processar(caminho) -> Registro:
    """Processa um PDF e devolve o Registro. Nunca levanta por causa do documento."""
    caminho = Path(caminho)
    try:
        s = GRAFO.invoke({"caminho": str(caminho)}, {"recursion_limit": 20})
    except pytesseract.TesseractNotFoundError:
        raise  # problema de configuração, não do documento
    except Exception as e:
        return Registro(arquivo=caminho.name, status="failed", motivo=f"erro inesperado: {e}")

    ocr = s.get("ocr") or {}
    tentativas = s.get("tentativas", 0)
    if tentativas > 1:
        modelo = f"{config.MODELO} -> {config.MODELO_FORTE}"
    else:
        modelo = config.MODELO if tentativas else None
    return Registro(
        arquivo=caminho.name,
        sha256=ocr.get("sha256"),
        tipo=s.get("tipo"),
        status=s["status"],
        motivo=s.get("motivo"),
        avisos=s.get("avisos", []),
        dados=s.get("dados"),
        ocr_conf_media=ocr.get("conf_media"),
        ocr_conf_min=ocr.get("conf_min"),
        modelo=modelo,
        tokens_in=s.get("tokens_in", 0),
        tokens_out=s.get("tokens_out", 0),
        latencia_ms=s.get("latencia_ms", 0),
        tentativas=tentativas,
    )


if __name__ == "__main__":
    import sys

    for nome in sys.argv[1:]:
        r = processar(Path("data/raw") / f"{nome}.pdf")
        print(
            f"{nome}  {r.status:12} tipo={r.tipo} tentativas={r.tentativas} "
            f"tokens={r.tokens_in}/{r.tokens_out} {r.latencia_ms}ms"
        )
        if r.motivo:
            print(f"    motivo: {r.motivo}")
