"""Cliente do modelo (Gemini) com saída estruturada e medição de uso."""
from __future__ import annotations

import time
from dataclasses import dataclass
from functools import lru_cache

from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel

from pipeline import config


class ErroLLM(Exception):
    """Falha ao obter uma resposta estruturada válida do modelo."""


@dataclass
class Uso:
    tokens_in: int = 0
    tokens_out: int = 0
    latencia_ms: int = 0

    def somar(self, outro: "Uso") -> None:
        self.tokens_in += outro.tokens_in
        self.tokens_out += outro.tokens_out
        self.latencia_ms += outro.latencia_ms


@lru_cache(maxsize=4)
def _modelo(nome: str) -> ChatGoogleGenerativeAI:
    opcoes = dict(
        model=nome,
        temperature=config.TEMPERATURA,
        max_retries=config.MAX_RETRIES,
        timeout=config.TIMEOUT_S,
    )
    if config.USAR_VERTEX:
        opcoes.update(vertexai=True, project=config.PROJETO_GCP, location=config.LOCAL_GCP)
    return ChatGoogleGenerativeAI(**opcoes)


def extrair_estruturado(
    schema: type[BaseModel], instrucoes: str, texto: str, modelo: str | None = None
) -> tuple[BaseModel, Uso]:
    """Chama o modelo e devolve (objeto validado no schema, uso de tokens e tempo)."""
    cadeia = _modelo(modelo or config.MODELO).with_structured_output(schema, include_raw=True)

    inicio = time.perf_counter()
    try:
        resp = cadeia.invoke([("system", instrucoes), ("human", texto)])
    except Exception as e:
        raise ErroLLM(f"falha na chamada ao modelo: {e}") from e
    latencia = int((time.perf_counter() - inicio) * 1000)

    if resp.get("parsing_error") or resp.get("parsed") is None:
        raise ErroLLM(f"resposta fora do schema: {resp.get('parsing_error')}")

    parsed = resp["parsed"]
    try:
        objeto = parsed if isinstance(parsed, schema) else schema.model_validate(parsed)
    except Exception as e:
        raise ErroLLM(f"resposta inválida para {schema.__name__}: {e}") from e

    meta = getattr(resp.get("raw"), "usage_metadata", None) or {}
    uso = Uso(
        tokens_in=int(meta.get("input_tokens", 0)),
        tokens_out=int(meta.get("output_tokens", 0)),
        latencia_ms=latencia,
    )
    return objeto, uso
