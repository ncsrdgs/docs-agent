"""Cliente do modelo (Gemini) com saída estruturada, medição de uso e cache em disco."""
from __future__ import annotations

import hashlib
import json
import os
import time
import warnings
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel

from pipeline import config

warnings.filterwarnings("ignore", message=".*fixed sampling defaults.*")


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


# --------------------------------------------------------------------------
# Cache de respostas
# --------------------------------------------------------------------------
# Os modelos usados ignoram a temperatura, então a mesma entrada pode gerar saídas
# diferentes. O cache guarda a primeira resposta válida de cada entrada: reprocessar
# o mesmo documento devolve o mesmo resultado, sem nova chamada e sem custo.
def _cache_ativo() -> bool:
    return os.getenv("LLM_CACHE", "1") != "0"


def _cache_dir() -> Path:
    return Path(os.getenv("LLM_CACHE_DIR", "data/cache/llm"))


def _chave(schema: type[BaseModel], instrucoes: str, texto: str, modelo: str) -> str:
    base = json.dumps(
        [modelo, instrucoes, texto, schema.model_json_schema()],
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def _ler_cache(chave: str, schema: type[BaseModel]) -> tuple[BaseModel, Uso] | None:
    arq = _cache_dir() / f"{chave}.json"
    try:
        bruto = json.loads(arq.read_text(encoding="utf-8"))
        objeto = schema.model_validate(bruto["objeto"])
        u = bruto["uso"]
        # latência zero: a resposta veio do disco, não do modelo
        return objeto, Uso(int(u["tokens_in"]), int(u["tokens_out"]), 0)
    except (OSError, ValueError, KeyError, TypeError):
        return None  # ausente ou corrompido: refaz a chamada


def _gravar_cache(chave: str, objeto: BaseModel, uso: Uso) -> None:
    pasta = _cache_dir()
    try:
        pasta.mkdir(parents=True, exist_ok=True)
        tmp = pasta / f"{chave}.{os.getpid()}.{time.monotonic_ns()}.tmp"
        tmp.write_text(
            json.dumps(
                {
                    "objeto": objeto.model_dump(mode="json"),
                    "uso": {"tokens_in": uso.tokens_in, "tokens_out": uso.tokens_out},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        tmp.replace(pasta / f"{chave}.json")  # escrita atômica (vários workers)
    except OSError:
        pass  # cache é otimização; falha ao gravar não derruba o processamento


def extrair_estruturado(
    schema: type[BaseModel], instrucoes: str, texto: str, modelo: str | None = None
) -> tuple[BaseModel, Uso]:
    """Chama o modelo e devolve (objeto validado no schema, uso de tokens e tempo)."""
    nome = modelo or config.MODELO

    chave = _chave(schema, instrucoes, texto, nome) if _cache_ativo() else None
    if chave:
        achado = _ler_cache(chave, schema)
        if achado:
            return achado

    cadeia = _modelo(nome).with_structured_output(schema, include_raw=True)

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
    if chave:
        _gravar_cache(chave, objeto, uso)
    return objeto, uso
