"""PDF -> imagem -> texto e confiança."""
import hashlib
import io
import json
import os
from pathlib import Path

import pymupdf
import pytesseract
from dotenv import load_dotenv
from PIL import Image

load_dotenv()

if os.getenv("TESSERACT_CMD"):
    pytesseract.pytesseract.tesseract_cmd = os.environ["TESSERACT_CMD"]

CACHE_DIR = Path("data/cache/ocr")
DPI = 200
LANG = "por"
MIN_CHARS = 20


class OcrError(Exception):
    """Falha controlada ao ler ou fazer OCR de um arquivo."""


def _sha256(caminho: Path) -> str:
    return hashlib.sha256(caminho.read_bytes()).hexdigest()


def _imagem_da_pagina(doc, pagina) -> Image.Image:
    """Imagem da página para o OCR.

    Se a página é um único scan, usa a imagem original embutida no PDF.
    Reamostrar (renderizar em outro dpi) piora o OCR: em testes, linhas
    inteiras de tabelas sumiram. Só renderiza quando não há imagem única.
    """
    imagens = pagina.get_images(full=True)
    if len(imagens) == 1:
        try:
            dados = doc.extract_image(imagens[0][0])["image"]
            return Image.open(io.BytesIO(dados)).convert("RGB")
        except Exception:
            pass  # cai para a renderização
    pix = pagina.get_pixmap(dpi=DPI)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def _ocr_pagina(img: Image.Image) -> tuple[str, list[float]]:
    """Texto da página e confiança de cada palavra reconhecida."""
    texto = pytesseract.image_to_string(img, lang=LANG)
    dados = pytesseract.image_to_data(
        img, lang=LANG, output_type=pytesseract.Output.DICT
    )
    confs = [
        float(c)
        for palavra, c in zip(dados["text"], dados["conf"])
        if palavra.strip() and float(c) != -1
    ]
    return texto, confs


def ocr_pdf(caminho, usar_cache: bool = True) -> dict:
    """Faz OCR de todas as páginas.

    Devolve {'arquivo', 'sha256', 'paginas', 'texto', 'conf_media', 'conf_min'}.
    Levanta OcrError em falhas esperadas (arquivo inválido, sem texto).
    """
    caminho = Path(caminho)
    try:
        sha = _sha256(caminho)
    except OSError as e:
        raise OcrError(f"não foi possível ler o arquivo: {e}") from e

    cache = CACHE_DIR / f"{caminho.stem}.json"
    if usar_cache and cache.exists():
        try:
            guardado = json.loads(cache.read_text(encoding="utf-8"))
            if guardado.get("sha256") == sha:
                return guardado
        except (json.JSONDecodeError, OSError):
            pass  # cache inválido: refaz

    try:
        doc = pymupdf.open(caminho)
    except Exception as e:
        raise OcrError(f"não foi possível abrir como PDF: {e}") from e

    try:
        if len(doc) == 0:
            raise OcrError("PDF sem páginas")

        partes, confs, pagina_atual = [], [], 0
        try:
            for pagina_atual, pagina in enumerate(doc, start=1):
                texto, c = _ocr_pagina(_imagem_da_pagina(doc, pagina))
                partes.append((pagina_atual, texto.strip()))
                confs.extend(c)
        except pytesseract.TesseractNotFoundError:
            raise  # problema de configuração, não do arquivo
        except Exception as e:
            raise OcrError(f"falha no OCR da página {pagina_atual}: {e}") from e
    finally:
        doc.close()

    if sum(len(t) for _, t in partes) < MIN_CHARS:
        raise OcrError("texto praticamente vazio")

    resultado = {
        "arquivo": caminho.name,
        "sha256": sha,
        "paginas": len(partes),
        "texto": "\n\n".join(f"--- página {n} ---\n{t}" for n, t in partes),
        "conf_media": round(sum(confs) / len(confs), 1) if confs else 0.0,
        "conf_min": round(min(confs), 1) if confs else 0.0,
    }

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(
        json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return resultado


if __name__ == "__main__":
    pdfs = sorted(Path("data/raw").glob("*.pdf"))
    falhas = 0
    for pdf in pdfs:
        try:
            r = ocr_pdf(pdf)
            print(
                f"ok    {pdf.name}  pág={r['paginas']}  "
                f"conf_média={r['conf_media']}  conf_mín={r['conf_min']}"
            )
        except OcrError as e:
            falhas += 1
            print(f"FALHA {pdf.name}: {e}")
    print(f"\n{len(pdfs)} arquivos, {falhas} falhas")
