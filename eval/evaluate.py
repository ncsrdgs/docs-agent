"""Mede a acurácia da extração contra o gabarito manual (data/gold/gold.json).

Uso:
    python eval/evaluate.py [--resultados data/out/results.json] [--gabarito data/gold/gold.json]

Duas métricas por campo:
- exata: números com tolerância de 0,01, datas em ISO, CNPJ/CPF só dígitos e textos
  iguais depois de remover acento, caixa e espaços repetidos;
- tolerante: como a exata, mas textos aceitam similaridade >= 0,95 (nomes, equipamento)
  ou >= 0,90 (textos longos: objeto, problema, solução).
"""
from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

LIMIAR_NOME = 0.95
LIMIAR_TEXTO = 0.90
TEXTOS_LONGOS = {"objeto", "problema", "solucao"}
TOL = 0.01


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().casefold()


def _sim(a: str, b: str) -> float:
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def comparar(campo: str, esperado, obtido) -> tuple[bool, bool]:
    """Devolve (exato, tolerante)."""
    if esperado is None or obtido is None:
        ok = esperado is None and obtido is None
        return ok, ok
    if isinstance(esperado, (int, float)):
        try:
            ok = abs(float(esperado) - float(obtido)) <= TOL
        except (TypeError, ValueError):
            ok = False
        return ok, ok
    if campo in {"cnpj", "documento"}:
        ok = re.sub(r"\D", "", str(esperado)) == re.sub(r"\D", "", str(obtido))
        return ok, ok
    if campo in {"data", "inicio", "fim"}:
        ok = str(esperado) == str(obtido)
        return ok, ok
    exato = _norm(str(esperado)) == _norm(str(obtido))
    limiar = LIMIAR_TEXTO if campo in TEXTOS_LONGOS else LIMIAR_NOME
    return exato, exato or _sim(str(esperado), str(obtido)) >= limiar


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
            saida[f"{prefixo}.qtd"] = ("itens_qtd", len(valor))
            for i, item in enumerate(valor):
                visitar(f"{prefixo}[{i}]", item)
        else:
            saida[prefixo] = (prefixo.split(".")[-1], valor)

    visitar("", dados)
    return saida


def inconsistencia_real(esp: dict) -> list[str]:
    """Problemas do próprio documento, calculados só a partir do gabarito."""
    d, achados = esp["dados"], []
    if esp["tipo"] == "nota_fiscal":
        soma = round(sum(i["valor_total"] for i in d["itens"]), 2)
        if abs(soma - d["valor_total"]) > 0.02:
            achados.append(f"soma dos itens {soma:.2f} difere do total {d['valor_total']:.2f}")
    if esp["tipo"] == "contrato":
        v = d["vigencia"]
        if v["inicio"] and v["fim"] and v["fim"] < v["inicio"]:
            achados.append("vigência termina antes de começar")
    return achados


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

    exato: dict[str, list[bool]] = defaultdict(list)
    toler: dict[str, list[bool]] = defaultdict(list)
    erros_doc: dict[str, list[str]] = {}
    tipo_certo = 0

    for stem, esp in gold.items():
        r = res.get(stem)
        if r is None:
            erros_doc[stem] = ["ausente nos resultados"]
            continue
        tipo_certo += r.get("tipo") == esp["tipo"]
        a, o = achatar(esp["dados"]), achatar(r.get("dados"))
        erros = []
        for caminho, (campo, valor) in a.items():
            obtido = o.get(caminho, (campo, None))[1]
            ex, tol = comparar(campo, valor, obtido)
            nome = re.sub(r"\[\d+\]", "[]", caminho).lstrip(".")
            exato[nome].append(ex)
            toler[nome].append(tol)
            if not tol:
                erros.append(f"{caminho}: esperado={valor!r} obtido={obtido!r}")
        erros_doc[stem] = erros

    n = len(gold)
    todos_ex = [v for lst in exato.values() for v in lst]
    todos_tol = [v for lst in toler.values() for v in lst]
    perfeitos = [s for s, e in erros_doc.items() if not e]

    print(f"Gabarito: {n} documentos | {len(todos_tol)} campos comparados")
    print(f"Classificação correta: {tipo_certo}/{n}")
    print(f"Acurácia por campo: exata {sum(todos_ex)/len(todos_ex):.1%} | "
          f"tolerante {sum(todos_tol)/len(todos_tol):.1%}")
    print(f"Documentos 100% corretos (tolerante): {len(perfeitos)}/{n}\n")

    print(f"{'campo':<32} {'exata':>9} {'tolerante':>10}")
    for nome in sorted(toler):
        ex, tl = exato[nome], toler[nome]
        print(f"  {nome:<30} {sum(ex):>3}/{len(ex):<4} {sum(tl):>4}/{len(tl):<4}")

    ok_docs = [s for s in gold if res.get(s, {}).get("status") == "ok"]
    rev_docs = [s for s in gold if res.get(s, {}).get("status") == "needs_review"]
    falhos = [s for s in gold if res.get(s, {}).get("status") == "failed"]
    ok_errados = [s for s in ok_docs if erros_doc[s]]
    com_erro = [s for s in gold if erros_doc[s]]
    rev_com_erro = [s for s in rev_docs if erros_doc[s]]
    print("\nRoteamento:")
    print(f"  ok: {len(ok_docs)} | needs_review: {len(rev_docs)} | failed: {len(falhos)}")
    if ok_docs:
        print(f"  precisão do ok: {1 - len(ok_errados)/len(ok_docs):.1%} "
              f"({len(ok_errados)} ok com erro real)")
    if com_erro:
        print(f"  erros de extração que foram para revisão: {len(rev_com_erro)}/{len(com_erro)}")

    # Inconsistências reais do documento (calculadas só do gabarito) vs. o que o sistema sinalizou
    reais = {s: inconsistencia_real(e) for s, e in gold.items() if inconsistencia_real(e)}
    sinalizadas = [s for s in reais if res.get(s, {}).get("status") == "needs_review"]
    print("\nInconsistências do próprio documento (soma dos itens x total impresso):")
    print(f"  reais pelo gabarito: {len(reais)} | sinalizadas como needs_review: "
          f"{len(sinalizadas)}/{len(reais)}")
    for s in reais:
        if s not in sinalizadas:
            print(f"    não sinalizada: {s} ({'; '.join(reais[s])}) status={res.get(s, {}).get('status')}")
    falso_alarme = [s for s in rev_docs if s not in reais and not erros_doc[s]]
    print(f"  needs_review sem inconsistência real nem erro de extração: {len(falso_alarme)}")
    for s in falso_alarme:
        print(f"    {s}: {res[s].get('motivo')}")

    print("\nDivergências de extração (tolerante):")
    for stem in gold:
        if erros_doc[stem]:
            print(f"  {stem} [{res.get(stem, {}).get('status')}]")
            for e in erros_doc[stem]:
                print(f"    - {e}")
    if not com_erro:
        print("  nenhuma")


if __name__ == "__main__":
    main()
