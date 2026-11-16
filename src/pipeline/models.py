"""Schemas dos documentos e registro de processamento.

Cada tipo tem dois modelos:
- *Extraido: o que o LLM devolve, com textos exatamente como aparecem no documento.
- modelo final: tipado (datas, números), gerado por normalize.normalizar().
"""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

TipoDocumento = Literal["nota_fiscal", "contrato", "relatorio_manutencao"]
Status = Literal["ok", "needs_review", "failed"]


# --------------------------------------------------------------------------
# Classificação
# --------------------------------------------------------------------------
class Classificacao(BaseModel):
    tipo: Literal["nota_fiscal", "contrato", "relatorio_manutencao", "desconhecido"] = Field(
        description="Tipo do documento; 'desconhecido' se não for nenhum dos três."
    )
    confianca: float = Field(
        ge=0, le=1, description="Confiança na classificação, de 0 a 1."
    )


# --------------------------------------------------------------------------
# Extração (saída do LLM): textos como aparecem no documento
# --------------------------------------------------------------------------
class ItemExtraido(BaseModel):
    descricao: str | None = Field(None, description="Descrição do item.")
    quantidade: str | None = Field(None, description="Quantidade, como no documento.")
    valor_unitario: str | None = Field(None, description="Valor unitário, como no documento.")
    valor_total: str | None = Field(None, description="Valor total do item, como no documento.")


class NotaFiscalExtraida(BaseModel):
    fornecedor: str | None = Field(None, description="Nome/razão social do fornecedor ou emitente.")
    cnpj: str | None = Field(None, description="CNPJ do fornecedor, como no documento.")
    data: str | None = Field(None, description="Data de emissão, como no documento.")
    itens: list[ItemExtraido] = Field(default_factory=list, description="Todos os itens da nota.")
    valor_total: str | None = Field(
        None, description="Valor total impresso no documento (não recalcule)."
    )


class ParteExtraida(BaseModel):
    nome: str | None = Field(None, description="Nome ou razão social.")
    documento: str | None = Field(None, description="CPF ou CNPJ, como no documento.")


class ContratoExtraido(BaseModel):
    contratante: ParteExtraida = Field(
        default_factory=ParteExtraida,
        description="Quem contrata o serviço (tomador, cliente, parte A).",
    )
    contratado: ParteExtraida = Field(
        default_factory=ParteExtraida,
        description="Quem presta o serviço (fornecedor, prestador, parte B).",
    )
    objeto: str | None = Field(None, description="Objeto/escopo do contrato.")
    vigencia_inicio: str | None = Field(None, description="Data de início, como no documento.")
    vigencia_fim: str | None = Field(None, description="Data de término, como no documento.")
    valor_mensal: str | None = Field(None, description="Valor mensal, como no documento.")


class RelatorioExtraido(BaseModel):
    data: str | None = Field(None, description="Data do atendimento, como no documento.")
    tecnico: str | None = Field(
        None, description="Nome do técnico responsável, sem matrícula."
    )
    equipamento: str | None = Field(None, description="Equipamento atendido (tipo e modelo).")
    problema: str | None = Field(None, description="Descrição do problema/defeito/ocorrência.")
    solucao: str | None = Field(None, description="Solução aplicada/reparo/procedimento.")


# --------------------------------------------------------------------------
# Saída final: tipada
# --------------------------------------------------------------------------
class Item(BaseModel):
    descricao: str | None = None
    quantidade: float | None = None
    valor_unitario: float | None = None
    valor_total: float | None = None


class NotaFiscal(BaseModel):
    fornecedor: str | None = None
    cnpj: str | None = None
    data: date | None = None
    itens: list[Item] = Field(default_factory=list)
    valor_total: float | None = None


class Parte(BaseModel):
    nome: str | None = None
    documento: str | None = None


class Vigencia(BaseModel):
    inicio: date | None = None
    fim: date | None = None


class Contrato(BaseModel):
    contratante: Parte = Field(default_factory=Parte)
    contratado: Parte = Field(default_factory=Parte)
    objeto: str | None = None
    vigencia: Vigencia = Field(default_factory=Vigencia)
    valor_mensal: float | None = None


class RelatorioManutencao(BaseModel):
    data: date | None = None
    tecnico: str | None = None
    equipamento: str | None = None
    problema: str | None = None
    solucao: str | None = None


# --------------------------------------------------------------------------
# Registro de processamento (uma linha por arquivo no resultado)
# --------------------------------------------------------------------------
class Registro(BaseModel):
    arquivo: str
    sha256: str | None = None
    tipo: Literal["nota_fiscal", "contrato", "relatorio_manutencao", "desconhecido"] | None = None
    status: Status
    motivo: str | None = None
    avisos: list[str] = Field(default_factory=list)
    dados: NotaFiscal | Contrato | RelatorioManutencao | None = None

    ocr_conf_media: float | None = None
    ocr_conf_min: float | None = None
    modelo: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    latencia_ms: int = 0
    tentativas: int = 0


# Mapas usados pelo roteamento e pela extração
SCHEMA_EXTRACAO: dict[str, type[BaseModel]] = {
    "nota_fiscal": NotaFiscalExtraida,
    "contrato": ContratoExtraido,
    "relatorio_manutencao": RelatorioExtraido,
}
SCHEMA_FINAL: dict[str, type[BaseModel]] = {
    "nota_fiscal": NotaFiscal,
    "contrato": Contrato,
    "relatorio_manutencao": RelatorioManutencao,
}
