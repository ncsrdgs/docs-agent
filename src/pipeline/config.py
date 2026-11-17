"""Configuração central: caminhos, modelo e parâmetros."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

RAIZ = Path(__file__).resolve().parents[2]
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

MODELO = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
MODELO_FORTE = os.getenv("GEMINI_MODEL_FORTE", "gemini-3.5-flash")
PROJETO_GCP = os.getenv("GOOGLE_CLOUD_PROJECT")
LOCAL_GCP = os.getenv("GOOGLE_CLOUD_LOCATION", "global")
USAR_VERTEX = bool(PROJETO_GCP)
TEMPERATURA = 0.0
MAX_RETRIES = 5
TIMEOUT_S = 60

# Trecho do texto que a classificação (regras e LLM) considera
CABECALHO_REGRAS = 100
TRECHO_CLASSIFICACAO = 3000


def carregar_prompt(nome: str) -> str:
    """Lê um prompt de src/pipeline/prompts/<nome>.md."""
    return (PROMPTS_DIR / f"{nome}.md").read_text(encoding="utf-8").strip()


def _preco(nome: str) -> float | None:
    """Preço em dólares por milhão de tokens, vindo do ambiente (None se não informado)."""
    try:
        return float(os.environ[nome])
    except (KeyError, ValueError):
        return None


PRECO_ENTRADA = _preco("PRECO_ENTRADA_USD_POR_MILHAO")
PRECO_SAIDA = _preco("PRECO_SAIDA_USD_POR_MILHAO")