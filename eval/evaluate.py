"""Mede a acurácia da extração contra o gabarito manual (data/gold/gold.json).

Uso:
    python eval/evaluate.py [--resultados data/out/results.json] [--gabarito data/gold/gold.json]

Regras de comparação:
- números: tolerância de 0,01
- datas: igualdade exata (ISO)
- CNPJ/CPF: só os dígitos
- nomes e equipamento: sem acento/caixa, similaridade >= 0,90
- textos longos (objeto, problema, solução): similaridade >= 0,85
"""
from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

LIMIAR_NOME = 0.90
LIMIAR_TEXTO = 0.85
TEXTOS_LONGOS = {"objeto", "problema", "solucao"}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().casefold()


def _sim(a: str, b: str) -> float:
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def comparar(campo: str, esperado, obtido) -> bool:
    if esperado is None or obtido is None:
        return esperado is None and obtido is None
    if isinstance(esperado, (int, float)):
        try:
            return abs(float(esperado) - float(obtido)) <= 0.01
        except (TypeError, ValueError):
            return False
    if campo in {"cnpj", "documento"}:
        return re.sub(r"\D", "", str(esperado)) == re.sub(r"\D", "", str(obtido))
    if campo in {"data", "inicio", "fim"}:
        return str(esperado) == str(obtido)
    limiar = LIMIAR_TEXTO if campo in TEXTOS_LONGOS else LIMIAR_NOME
    return _sim(str(esperado), str(obtido)) >= limiar


def achatar(dados: dict | None) -> dict[str, tuple]:
    """Transforma o JSON aninhado em {caminho: (campo, valor)}."""
    saida: dict[str, tuple] = {}
    if not dados:
        return saida

    def visitar(prefixo: str, valor):
        if isinstance(valor, dict):
            for k, v in valor.items():
                visitar(f"{prefixo}.{k}" if prefixo else k, v)
        elif isinstance(valor, list):
            saida[f"{prefixo}.__n"] = ("itens_qtd", len(valor))
            for i, item in enumerate(valor):
                visitar(f"{prefixo}[{i}]", item)
        else:
            saida[prefixo] = (prefixo.split(".")[-1], valor)

    visitar("", dados)
    return saida


def carregar_resultados(caminho: Path) -> dict[str, dict]:
    bruto = json.loads(caminho.read_text(encoding="utf-8"))
    if isinstance(bruto, dict):
        bruto = bruto.get("registros") or bruto.get("results") or list(bruto.values())
    return {Path(r["arquivo"]).stem: r for r in bruto}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--resultados", default="data/out/results.json")
    ap.add_argument("--gabarito", default="data/gold/gold.json")
    args = ap.parse_args()

    gold = json.loads(Path(args.gabarito).read_text(encoding="utf-8"))["documentos"]
    res = carregar_resultados(Path(args.resultados))

    por_campo: dict[str, list[bool]] = defaultdict(list)
    por_doc: dict[str, list[str]] = {}
    tipo_certo = 0

    for stem, esp in gold.items():
        r = res.get(stem)
        if r is None:
            por_doc[stem] = ["ausente nos resultados"]
            continue
        tipo_certo += r.get("tipo") == esp["tipo"]
        a = achatar(esp["dados"])
        o = achatar(r.get("dados"))
        erros = []
        for caminho, (campo, valor) in a.items():
            ok = comparar(campo, valor, o.get(caminho, (campo, None))[1])
            nome = re.sub(r"\[\d+\]", "[]", caminho).lstrip(".")
            por_campo[nome].append(ok)
            if not ok:
                erros.append(f"{caminho}: esperado={valor!r} obtido={o.get(caminho, (None, None))[1]!r}")
        por_doc[stem] = erros

    total_docs = len(gold)
    docs_perfeitos = [s for s, e in por_doc.items() if not e]
    campos = [v for lst in por_campo.values() for v in lst]

    print(f"Gabarito: {total_docs} documentos | {len(campos)} campos comparados")
    print(f"Classificação correta: {tipo_certo}/{total_docs}")
    print(f"Acurácia por campo (global): {sum(campos)/len(campos):.1%}")
    print(f"Documentos 100% corretos: {len(docs_perfeitos)}/{total_docs}\n")

    print("Acurácia por campo:")
    for nome in sorted(por_campo):
        v = por_campo[nome]
        print(f"  {nome:<32} {sum(v)}/{len(v)}  {sum(v)/len(v):.0%}")

    # Qualidade do roteamento: o status 'ok' é confiável?
    ok_docs = [s for s in gold if res.get(s, {}).get("status") == "ok"]
    ok_errados = [s for s in ok_docs if por_doc[s]]
    rev_docs = [s for s in gold if res.get(s, {}).get("status") == "needs_review"]
    rev_com_erro = [s for s in rev_docs if por_doc[s]]
    com_erro = [s for s in gold if por_doc[s]]
    print("\nRoteamento:")
    print(f"  status ok: {len(ok_docs)} | com erro real: {len(ok_errados)} "
          f"(precisão do ok: {1 - len(ok_errados)/len(ok_docs):.0%})" if ok_docs else "  nenhum ok")
    print(f"  needs_review: {len(rev_docs)} | com erro de extração: {len(rev_com_erro)}")
    if com_erro:
        print(f"  erros de extração capturados pela revisão: {len(rev_com_erro)}/{len(com_erro)}")

    print("\nDivergências:")
    for stem in gold:
        if por_doc[stem]:
            print(f"  {stem} [{res.get(stem, {}).get('status')}]")
            for e in por_doc[stem]:
                print(f"    - {e}")
    if not com_erro:
        print("  nenhuma")


if __name__ == "__main__":
    main()
