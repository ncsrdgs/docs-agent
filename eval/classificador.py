"""Mede o classificador: regras, fallback de LLM e um conjunto de teste fora dos 50 documentos.

Uso:
    python eval/classificador.py [--entrada data/raw] [--gabarito data/gold/gold.json]
                                 [--holdout data/holdout/classificacao.json] [--sem-llm]

Parte 1 (50 documentos): compara com o gabarito (a) as regras sozinhas e (b) o LLM sozinho,
sem as regras, que mostra se o fallback funcionaria nos casos que as regras não cobrem.
Parte 2 (data/holdout): textos com cabeçalhos que não aparecem nos 50 documentos, inclusive
documentos de outros tipos e casos armadilha. As respostas do LLM usam o cache em disco.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pipeline import config
from pipeline.classify import classificar, classificar_por_regras
from pipeline.ocr import ocr_pdf


def acertou(esperado: str, tipo: str, confianca: float) -> bool:
    if esperado == "desconhecido":
        return tipo == "desconhecido" or confianca < 0.7
    return tipo == esperado


def avaliar(itens: list[dict], usar_llm: bool, rotulo: str) -> None:
    """itens: [{id, esperado, texto}]"""
    n = len(itens)
    regra = {i["id"]: classificar_por_regras(i["texto"]) for i in itens}
    responde = {k: v for k, v in regra.items() if v}
    certas = sum(1 for i in itens if i["id"] in responde and responde[i["id"]] == i["esperado"])
    print(f"\n== {rotulo} ({n} documentos) ==")
    print(f"Regras: respondem {len(responde)}/{n}; entre as respostas, corretas {certas}/{len(responde)}")
    for i in itens:
        r = responde.get(i["id"])
        if r and r != i["esperado"]:
            print(f"  regra errada: {i['id']} esperado={i['esperado']} obtido={r}")
    if not usar_llm:
        return

    def so_llm(i):
        cls, uso, _ = classificar(i["texto"], usar_regras=False)
        return i, cls, uso

    with ThreadPoolExecutor(max_workers=4) as ex:
        res = list(ex.map(so_llm, itens))
    ok = [(i, c) for i, c, _ in res if acertou(i["esperado"], c.tipo, c.confianca)]
    print(f"LLM sem regras: acertos {len(ok)}/{n} ({len(ok)/n:.0%})")
    fora = [i for i in itens if i["id"] not in responde]
    if fora:
        ids = {i["id"] for i in fora}
        n_ok = sum(1 for i, c in ok if i["id"] in ids)
        print(f"  só nos que as regras não cobrem: {n_ok}/{len(fora)}")
    tokens = sum(u.tokens_in + u.tokens_out for _, _, u in res)
    print(f"  tokens (cache pode zerar a latência): {tokens} no total, ~{tokens//n} por documento")
    for i, c, _ in res:
        if not acertou(i["esperado"], c.tipo, c.confianca):
            print(f"  erro: {i['id']} esperado={i['esperado']} obtido={c.tipo} (conf {c.confianca:.2f})")
    print("  confusão (esperado -> obtido):", dict(Counter((i["esperado"], c.tipo) for i, c, _ in res)))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--entrada", default="data/raw")
    ap.add_argument("--gabarito", default="data/gold/gold.json")
    ap.add_argument("--holdout", default="data/holdout/classificacao.json")
    ap.add_argument("--sem-llm", action="store_true", help="só as regras, sem chamar o modelo")
    args = ap.parse_args()
    usar_llm = not args.sem_llm
    if usar_llm:
        print(f"Modelo: {config.MODELO} | cache do LLM: ligado por padrão (LLM_CACHE=0 desliga)")

    gold = json.loads(Path(args.gabarito).read_text(encoding="utf-8"))["documentos"]
    docs = []
    for stem, esp in gold.items():
        texto = ocr_pdf(Path(args.entrada) / f"{stem}.pdf")["texto"]
        docs.append({"id": stem, "esperado": esp["tipo"], "texto": texto})
    avaliar(docs, usar_llm, "50 documentos do desafio")

    holdout = json.loads(Path(args.holdout).read_text(encoding="utf-8"))
    avaliar(holdout, usar_llm, "conjunto de teste fora dos 50 documentos")


if __name__ == "__main__":
    main()
