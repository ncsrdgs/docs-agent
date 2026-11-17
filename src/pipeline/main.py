"""Executa o pipeline sobre uma pasta de PDFs e grava os resultados.

Uso: python -m pipeline.main [--entrada data/raw] [--saida data/out] [--workers 4]
"""
from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytesseract

from pipeline.graph import processar
from pipeline.llm import ErroConfiguracao
from pipeline.models import Registro
from pipeline.persist import gravar
from pipeline.repositorio import RepositorioSqlite


def main() -> None:
    ap = argparse.ArgumentParser(description="Pipeline de extração de documentos")
    ap.add_argument("--entrada", default="data/raw", help="pasta com os PDFs")
    ap.add_argument("--saida", default="data/out", help="pasta dos resultados")
    ap.add_argument("--workers", type=int, default=4, help="documentos em paralelo")
    ap.add_argument("--sqlite", help="grava também em um banco SQLite (upsert por hash do arquivo)")
    args = ap.parse_args()

    pdfs = sorted(p for p in Path(args.entrada).iterdir() if p.suffix.lower() == ".pdf")
    if not pdfs:
        sys.exit(f"nenhum PDF encontrado em {args.entrada}")

    inicio = time.perf_counter()
    registros: list[Registro] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futuros = {pool.submit(processar, p): p for p in pdfs}
        for n, fut in enumerate(as_completed(futuros), start=1):
            p = futuros[fut]
            try:
                r = fut.result()
            except pytesseract.TesseractNotFoundError:
                pool.shutdown(wait=False, cancel_futures=True)
                sys.exit("Tesseract não encontrado: instale-o ou defina TESSERACT_CMD no .env")
            except ErroConfiguracao as e:
                pool.shutdown(wait=False, cancel_futures=True)
                sys.exit(f"configuração do modelo: {e}")
            except Exception as e:
                r = Registro(arquivo=p.name, status="failed", motivo=f"erro inesperado: {e}")
            registros.append(r)
            print(f"[{n}/{len(pdfs)}] {r.arquivo:18} {r.status}")

    registros.sort(key=lambda r: r.arquivo)
    resumo = gravar(registros, Path(args.saida))
    if args.sqlite:
        RepositorioSqlite(args.sqlite).salvar(registros)
        print(f"banco: {args.sqlite}")
    segundos = time.perf_counter() - inicio

    print(f"\n{resumo['total']} documentos em {segundos:.0f}s -> {args.saida}/")
    print("status:", resumo["por_status"])
    print("tipos: ", resumo["por_tipo"])
    print(
        f"nova tentativa: {resumo['com_nova_tentativa']} | "
        f"tokens: {resumo['tokens_in']} entrada, {resumo['tokens_out']} saída"
    )

    if resumo["com_visao"]:
        print(
            f"fallback de visão: {resumo['com_visao']} documentos, "
            f"{resumo['visao_adotada']} com os dados adotados"
        )
    if resumo["ocr_ms_medio_por_documento"] is not None:
        print(
            f"OCR: {resumo['ocr_ms_medio_por_documento']} ms por documento, "
            f"{resumo['ocr_paginas_por_segundo']} páginas/s por worker "
            f"(Tesseract {', '.join(resumo['tesseract'])})"
        )
    if "custo_estimado_usd" in resumo:
        print(
            f"custo estimado: US$ {resumo['custo_estimado_usd']} "
            f"(US$ {resumo['custo_medio_por_documento_usd']} por documento)"
        )


if __name__ == "__main__":
    main()