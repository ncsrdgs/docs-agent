"""Persistência dos resultados: JSON consolidado, CSV por tipo e resumo."""
from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

from pipeline import config
from pipeline.models import Registro


def _iso(d) -> str:
    return d.isoformat() if d else ""


def _escrever_csv(caminho: Path, colunas: list[str], linhas: list[dict]) -> None:
    with caminho.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=colunas)
        w.writeheader()
        w.writerows(linhas)


def _base(r: Registro) -> dict:
    return {"arquivo": r.arquivo, "status": r.status, "motivo": r.motivo or ""}


def _csvs(registros: list[Registro], saida: Path) -> None:
    notas, itens, contratos, relatorios = [], [], [], []
    for r in registros:
        d = r.dados
        if d is None:
            continue
        if r.tipo == "nota_fiscal":
            notas.append(
                {
                    **_base(r),
                    "fornecedor": d.fornecedor or "",
                    "cnpj": d.cnpj or "",
                    "data": _iso(d.data),
                    "valor_total": d.valor_total if d.valor_total is not None else "",
                    "qtd_itens": len(d.itens),
                }
            )
            for n, it in enumerate(d.itens, start=1):
                itens.append(
                    {
                        "arquivo": r.arquivo,
                        "item": n,
                        "descricao": it.descricao or "",
                        "quantidade": it.quantidade if it.quantidade is not None else "",
                        "valor_unitario": it.valor_unitario if it.valor_unitario is not None else "",
                        "valor_total": it.valor_total if it.valor_total is not None else "",
                    }
                )
        elif r.tipo == "contrato":
            contratos.append(
                {
                    **_base(r),
                    "contratante": d.contratante.nome or "",
                    "contratante_documento": d.contratante.documento or "",
                    "contratado": d.contratado.nome or "",
                    "contratado_documento": d.contratado.documento or "",
                    "objeto": d.objeto or "",
                    "vigencia_inicio": _iso(d.vigencia.inicio),
                    "vigencia_fim": _iso(d.vigencia.fim),
                    "valor_mensal": d.valor_mensal if d.valor_mensal is not None else "",
                }
            )
        elif r.tipo == "relatorio_manutencao":
            relatorios.append(
                {
                    **_base(r),
                    "data": _iso(d.data),
                    "tecnico": d.tecnico or "",
                    "equipamento": d.equipamento or "",
                    "problema": d.problema or "",
                    "solucao": d.solucao or "",
                }
            )

    base = ["arquivo", "status", "motivo"]
    _escrever_csv(
        saida / "notas_fiscais.csv",
        base + ["fornecedor", "cnpj", "data", "valor_total", "qtd_itens"],
        notas,
    )
    _escrever_csv(
        saida / "notas_fiscais_itens.csv",
        ["arquivo", "item", "descricao", "quantidade", "valor_unitario", "valor_total"],
        itens,
    )
    _escrever_csv(
        saida / "contratos.csv",
        base
        + [
            "contratante",
            "contratante_documento",
            "contratado",
            "contratado_documento",
            "objeto",
            "vigencia_inicio",
            "vigencia_fim",
            "valor_mensal",
        ],
        contratos,
    )
    _escrever_csv(
        saida / "relatorios_manutencao.csv",
        base + ["data", "tecnico", "equipamento", "problema", "solucao"],
        relatorios,
    )


def _resumo(registros: list[Registro]) -> dict:
    n = len(registros)
    confs = [r.ocr_conf_media for r in registros if r.ocr_conf_media is not None]
    lat = sum(r.latencia_ms for r in registros)
    tokens_in = sum(r.tokens_in for r in registros)
    tokens_out = sum(r.tokens_out for r in registros)

    # OCR: tempo medido quando o OCR foi feito (o cache guarda o tempo original)
    medidos = [r for r in registros if r.ocr_ms is not None and r.paginas]
    ocr_s = sum(r.ocr_ms for r in medidos) / 1000
    paginas = sum(r.paginas for r in medidos)

    resumo = {
        "total": n,
        "por_status": dict(Counter(r.status for r in registros)),
        "por_tipo": dict(Counter(r.tipo or "indefinido" for r in registros)),
        "com_nova_tentativa": sum(1 for r in registros if r.tentativas > 1),
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "tokens_in_medio_por_documento": round(tokens_in / n) if n else 0,
        "tokens_out_medio_por_documento": round(tokens_out / n) if n else 0,
        "latencia_llm_media_ms": round(lat / n) if n else 0,
        "ocr_conf_media": round(sum(confs) / len(confs), 1) if confs else None,
        "ocr_ms_medio_por_documento": round(ocr_s * 1000 / len(medidos)) if medidos else None,
        "ocr_paginas_por_segundo": round(paginas / ocr_s, 2) if ocr_s else None,
        "tesseract": sorted({r.tesseract for r in registros if r.tesseract}),
    }

    # Custo estimado: só quando os preços são informados (variáveis de ambiente).
    # Aplica o mesmo preço a todos os tokens, então é uma aproximação.
    if config.PRECO_ENTRADA is not None and config.PRECO_SAIDA is not None and n:
        custo = (tokens_in * config.PRECO_ENTRADA + tokens_out * config.PRECO_SAIDA) / 1_000_000
        resumo["custo_estimado_usd"] = round(custo, 4)
        resumo["custo_medio_por_documento_usd"] = round(custo / n, 6)

    resumo["pendentes"] = [
        {"arquivo": r.arquivo, "status": r.status, "motivo": r.motivo}
        for r in registros
        if r.status != "ok"
    ]
    return resumo


def gravar(registros: list[Registro], saida: Path) -> dict:
    """Grava results.json, os CSVs por tipo e summary.json. Devolve o resumo."""
    saida.mkdir(parents=True, exist_ok=True)
    (saida / "results.json").write_text(
        json.dumps([r.model_dump(mode="json") for r in registros], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _csvs(registros, saida)
    resumo = _resumo(registros)
    (saida / "summary.json").write_text(
        json.dumps(resumo, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return resumo
