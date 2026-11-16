"""Validação determinística dos dados extraídos (sem LLM).

Separa três coisas:
- erros: problema de extração (campo obrigatório ausente, valor que não existe no
  texto do OCR). Vale uma nova tentativa com esse feedback.
- anomalias: o documento em si é inconsistente (soma dos itens diferente do total,
  quantidade x unitário diferente do total do item). Vai para revisão, sem retry.
- avisos: informativos (ex.: dígito verificador de CNPJ/CPF inválido, comum em
  documentos sintéticos). Nunca bloqueiam.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from pipeline.models import Contrato, NotaFiscal, RelatorioManutencao
from pipeline.normalize import parse_valor

TOL = 0.02


@dataclass
class Validacao:
    erros: list[str] = field(default_factory=list)
    anomalias: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Auxiliares
# --------------------------------------------------------------------------
def _numeros_do_texto(texto: str) -> set[float]:
    """Todos os números que aparecem no texto do OCR, já interpretados."""
    nums = set()
    for tok in re.findall(r"\d[\d.,]*\d|\d", texto):
        v = parse_valor(tok)
        if v is not None:
            nums.add(round(v, 2))
    return nums


def _cnpj_ok(d: str) -> bool:
    if len(set(d)) == 1:
        return False

    def dv(base, pesos):
        r = sum(int(x) * p for x, p in zip(base, pesos)) % 11
        return "0" if r < 2 else str(11 - r)

    p1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    d1 = dv(d[:12], p1)
    d2 = dv(d[:12] + d1, [6] + p1)
    return d[12:] == d1 + d2


def _cpf_ok(d: str) -> bool:
    if len(set(d)) == 1:
        return False

    def dv(base):
        r = (sum(int(x) * p for x, p in zip(base, range(len(base) + 1, 1, -1))) * 10) % 11
        return "0" if r == 10 else str(r)

    d1 = dv(d[:9])
    d2 = dv(d[:9] + d1)
    return d[9:] == d1 + d2


def _checar_documento(valor, rotulo, digitos_texto, v: Validacao) -> None:
    if not valor:
        return
    d = re.sub(r"\D", "", valor)
    if d and d not in digitos_texto:
        v.erros.append(f"{rotulo} '{valor}' não aparece no texto do documento")
        return
    if len(d) == 14 and not _cnpj_ok(d):
        v.avisos.append(f"{rotulo}: dígito verificador de CNPJ inválido (informativo)")
    elif len(d) == 11 and not _cpf_ok(d):
        v.avisos.append(f"{rotulo}: dígito verificador de CPF inválido (informativo)")


def _checar_numeros(valores: list[tuple[str, float | None]], nums, v: Validacao) -> None:
    fora = [f"{rot}={val}" for rot, val in valores if val is not None and round(val, 2) not in nums]
    if fora:
        v.erros.append("valores que não aparecem no texto do documento: " + ", ".join(fora))


def _faltando(campos: list[tuple[str, object]], v: Validacao) -> None:
    for rotulo, valor in campos:
        if valor is None or (isinstance(valor, str) and not valor.strip()):
            v.erros.append(f"campo obrigatório ausente: {rotulo}")


# --------------------------------------------------------------------------
# Por tipo
# --------------------------------------------------------------------------
def _nota(n: NotaFiscal, nums, digitos, v: Validacao) -> None:
    _faltando([("fornecedor", n.fornecedor), ("data", n.data), ("valor total", n.valor_total)], v)
    if not n.itens:
        v.erros.append("nenhum item extraído")
    _checar_documento(n.cnpj, "cnpj", digitos, v)

    valores = [("valor total", n.valor_total)]
    completo = bool(n.itens)
    soma = 0.0
    for i, it in enumerate(n.itens, start=1):
        faltam = [
            r
            for r, x in (
                ("quantidade", it.quantidade),
                ("valor unitário", it.valor_unitario),
                ("valor total", it.valor_total),
            )
            if x is None
        ]
        if faltam:
            v.erros.append(f"item {i} sem {', '.join(faltam)}")
            completo = False
            continue
        valores += [
            (f"item {i} quantidade", it.quantidade),
            (f"item {i} valor unitário", it.valor_unitario),
            (f"item {i} valor total", it.valor_total),
        ]
        soma += it.valor_total
        if abs(it.quantidade * it.valor_unitario - it.valor_total) > TOL:
            v.anomalias.append(
                f"item {i}: {it.quantidade} x {it.valor_unitario} = "
                f"{round(it.quantidade * it.valor_unitario, 2)}, mas o total do item é {it.valor_total}"
            )
    _checar_numeros(valores, nums, v)

    if completo and n.valor_total is not None and abs(soma - n.valor_total) > TOL:
        v.anomalias.append(
            f"soma dos itens ({round(soma, 2)}) diferente do total do documento ({n.valor_total})"
        )


def _contrato(c: Contrato, nums, digitos, v: Validacao) -> None:
    _faltando(
        [
            ("contratante", c.contratante.nome),
            ("contratado", c.contratado.nome),
            ("valor mensal", c.valor_mensal),
        ],
        v,
    )
    _checar_documento(c.contratante.documento, "documento do contratante", digitos, v)
    _checar_documento(c.contratado.documento, "documento do contratado", digitos, v)
    _checar_numeros([("valor mensal", c.valor_mensal)], nums, v)

    ini, fim = c.vigencia.inicio, c.vigencia.fim
    if ini is None or fim is None:
        v.anomalias.append("vigência incompleta (início ou fim ausente)")
    elif fim < ini:
        v.anomalias.append(f"vigência incoerente: fim ({fim}) anterior ao início ({ini})")


def _relatorio(r: RelatorioManutencao, v: Validacao) -> None:
    _faltando(
        [
            ("data", r.data),
            ("técnico", r.tecnico),
            ("equipamento", r.equipamento),
            ("problema", r.problema),
            ("solução", r.solucao),
        ],
        v,
    )


def validar(tipo: str, dados, texto: str) -> Validacao:
    """Valida o modelo final contra regras e contra o texto do OCR."""
    v = Validacao()
    nums = _numeros_do_texto(texto)
    digitos = re.sub(r"\D", "", texto)
    if tipo == "nota_fiscal":
        _nota(dados, nums, digitos, v)
    elif tipo == "contrato":
        _contrato(dados, nums, digitos, v)
    elif tipo == "relatorio_manutencao":
        _relatorio(dados, v)
    else:
        raise ValueError(f"tipo sem validação: {tipo}")
    return v


if __name__ == "__main__":
    import sys
    from pathlib import Path

    from pipeline.classify import classificar
    from pipeline.extract import extrair
    from pipeline.normalize import normalizar
    from pipeline.ocr import ocr_pdf

    nomes = sys.argv[1:] or [p.stem for p in sorted(Path("data/raw").glob("*.pdf"))]
    for nome in nomes:
        try:
            ocr = ocr_pdf(Path("data/raw") / f"{nome}.pdf")
            cls, _, _ = classificar(ocr["texto"])
            if cls.tipo == "desconhecido":
                print(f"{nome}  desconhecido")
                continue
            ext, _ = extrair(cls.tipo, ocr["texto"])
            final, _ = normalizar(cls.tipo, ext)
            r = validar(cls.tipo, final, ocr["texto"])
            if not (r.erros or r.anomalias or r.avisos):
                print(f"{nome}  {cls.tipo}  ok")
            else:
                print(f"{nome}  {cls.tipo}")
                for rot, itens in (("ERRO", r.erros), ("ANOMALIA", r.anomalias), ("aviso", r.avisos)):
                    for i in itens:
                        print(f"    {rot}: {i}")
        except Exception as e:
            print(f"{nome}  FALHA {e}")
