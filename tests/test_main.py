"""Testes da linha de comando: descoberta dos PDFs e aborto por erro de configuração."""
from __future__ import annotations

import json
import sys

import pytest

from pipeline import main as cli
from pipeline.llm import ErroConfiguracao
from pipeline.models import Registro


def _argv(monkeypatch, entrada, saida):
    monkeypatch.setattr(sys, "argv", ["pipeline", "--entrada", str(entrada), "--saida", str(saida),
                                       "--workers", "1"])


def test_aceita_extensao_em_maiusculo(monkeypatch, tmp_path):
    entrada, saida = tmp_path / "in", tmp_path / "out"
    entrada.mkdir()
    for nome in ("a.pdf", "B.PDF", "c.txt"):
        (entrada / nome).write_bytes(b"x")
    monkeypatch.setattr(cli, "processar", lambda p: Registro(arquivo=p.name, status="ok"))
    _argv(monkeypatch, entrada, saida)
    cli.main()
    arquivos = [r["arquivo"] for r in json.loads((saida / "results.json").read_text(encoding="utf-8"))]
    assert arquivos == ["B.PDF", "a.pdf"]


def test_credencial_ausente_aborta_o_lote(monkeypatch, tmp_path):
    entrada = tmp_path / "in"
    entrada.mkdir()
    (entrada / "a.pdf").write_bytes(b"x")

    def sem_credencial(_p):
        raise ErroConfiguracao("não foi possível criar o cliente do modelo")

    monkeypatch.setattr(cli, "processar", sem_credencial)
    _argv(monkeypatch, entrada, tmp_path / "out")
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert "configuração do modelo" in str(e.value)
    assert not (tmp_path / "out").exists()  # nada gravado pela metade
