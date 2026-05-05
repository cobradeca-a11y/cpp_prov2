"""
ocr_engine_local.py — Motor OCR local via pytesseract (sem credenciais externas).

Build: audit-66-local-ocr
Ativado via: OCR_ENGINE=tesseract no .env

Contrato CPP preservado:
  - OCR bruto preservado, nunca inventado
  - Nenhuma harmonia inferida
  - Nenhuma letra inferida
  - Nenhum bloco alinhado a compasso sem geometria confiável
  - Toda evidência incerta permanece pendente
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any

TESSERACT_ENGINE = "tesseract"

_DEFAULT_PSM = 6   # bloco uniforme — partitura impressa
_CHORD_RE = re.compile(
    r"^[A-G](?:#|b)?(?:m|maj|min|dim|aug|sus|add)?\d*(?:\([^)]*\))?(?:/[A-G](?:#|b)?)?$"
)

# Suporte a TESSERACT_CMD no .env para Windows (sem precisar alterar PATH do sistema)
# Exemplo: TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe
def _configure_tesseract_cmd() -> None:
    cmd = os.environ.get("TESSERACT_CMD", "").strip().strip('"')
    if cmd:
        try:
            import pytesseract
            pytesseract.pytesseract.tesseract_cmd = cmd
        except ImportError:
            pass

_configure_tesseract_cmd()

_TESSERACT_NOT_FOUND_MSG = (
    "Tesseract não encontrado. "
    "Instale em https://github.com/UB-Mannheim/tesseract/wiki (Windows) "
    "ou `apt install tesseract-ocr tesseract-ocr-por` (Linux). "
    "Após instalar, defina TESSERACT_CMD no .env apontando para o executável, "
    "ex: TESSERACT_CMD=C:\\Program Files\\Tesseract-OCR\\tesseract.exe"
)


def run_tesseract_image_ocr(source_path: Path) -> dict[str, Any]:
    from ocr_cache import cache_key_for_image, cached_text_blocks, write_ocr_cache
    from ocr_engine import create_empty_ocr_contract, finalize_ocr_multipage_metadata, normalize_ocr_pages

    if not source_path or not source_path.exists():
        c = create_empty_ocr_contract(status="failed", engine=TESSERACT_ENGINE)
        c["warnings"].append("Arquivo de imagem não encontrado para OCR local.")
        return finalize_ocr_multipage_metadata(c)

    cache_key = cache_key_for_image(source_path, TESSERACT_ENGINE, "DOCUMENT_TEXT_DETECTION")
    cached = cached_text_blocks(cache_key)
    if cached is not None:
        c = create_empty_ocr_contract(status="success", engine=TESSERACT_ENGINE)
        c["text_blocks"] = normalize_ocr_pages(cached, default_page=1)
        c["warnings"].append("OCR local carregado do cache por hash de imagem.")
        return finalize_ocr_multipage_metadata(c)

    try:
        text_blocks = _run_tesseract_on_image(source_path, page=1)
    except ImportError:
        c = create_empty_ocr_contract(status="unavailable", engine=TESSERACT_ENGINE)
        c["warnings"].append("pytesseract não instalado. Execute: pip install pytesseract")
        return finalize_ocr_multipage_metadata(c)
    except Exception as exc:
        msg = str(exc)
        c = create_empty_ocr_contract(status="failed", engine=TESSERACT_ENGINE)
        if "not installed" in msg or "not in your PATH" in msg or "TesseractNotFound" in msg:
            c["warnings"].append(_TESSERACT_NOT_FOUND_MSG)
        else:
            c["warnings"].append(f"Falha no OCR local: {exc}")
        return finalize_ocr_multipage_metadata(c)

    write_ocr_cache(cache_key, text_blocks, {"engine": TESSERACT_ENGINE, "page": 1})
    c = create_empty_ocr_contract(status="success" if text_blocks else "failed", engine=TESSERACT_ENGINE)
    c["text_blocks"] = text_blocks
    if not text_blocks:
        c["warnings"].append("Tesseract executou, mas não retornou blocos de texto nesta imagem.")
    return finalize_ocr_multipage_metadata(c)


def run_tesseract_pdf_ocr(source_path: Path) -> dict[str, Any]:
    from ocr_cache import cache_key_for_image, cached_text_blocks, write_ocr_cache
    from ocr_engine import (
        create_empty_ocr_contract,
        finalize_ocr_multipage_metadata,
        normalize_ocr_pages,
        convert_pdf_to_page_images,
    )

    if not source_path or not source_path.exists():
        c = create_empty_ocr_contract(status="failed", engine=TESSERACT_ENGINE)
        c["warnings"].append("Arquivo PDF não encontrado para OCR local.")
        return finalize_ocr_multipage_metadata(c)

    cache_hits = 0
    cache_misses = 0

    try:
        with tempfile.TemporaryDirectory(prefix="cpp_pdf_tesseract_") as tmp:
            page_images = convert_pdf_to_page_images(source_path, Path(tmp))
            if not page_images:
                c = create_empty_ocr_contract(status="failed", engine=TESSERACT_ENGINE)
                c["warnings"].append("Conversão PDF→imagem não retornou páginas para OCR local.")
                return finalize_ocr_multipage_metadata(c, expected_pages=[])

            all_blocks: list[dict[str, Any]] = []
            warnings: list[str] = []

            for page_number, image_path in page_images:
                try:
                    ck = cache_key_for_image(image_path, TESSERACT_ENGINE, "DOCUMENT_TEXT_DETECTION", page=page_number)
                    cached = cached_text_blocks(ck)
                    if cached is not None:
                        cache_hits += 1
                        all_blocks.extend(normalize_ocr_pages(cached, default_page=page_number, force_page=True))
                        continue

                    cache_misses += 1
                    page_blocks = _run_tesseract_on_image(image_path, page=page_number)
                    page_blocks = normalize_ocr_pages(page_blocks, default_page=page_number, force_page=True)
                    write_ocr_cache(ck, page_blocks, {"engine": TESSERACT_ENGINE, "page": page_number})
                    all_blocks.extend(page_blocks)
                except Exception as exc:
                    msg = str(exc)
                    if "not installed" in msg or "not in your PATH" in msg or "TesseractNotFound" in msg:
                        warnings.append(f"Página {page_number}: {_TESSERACT_NOT_FOUND_MSG}")
                    else:
                        warnings.append(f"Falha no OCR local da página {page_number}: {exc}")

    except ImportError as exc:
        c = create_empty_ocr_contract(status="unavailable", engine=TESSERACT_ENGINE)
        c["warnings"].append(f"Dependência de conversão PDF→imagem indisponível: {exc}")
        return finalize_ocr_multipage_metadata(c)
    except Exception as exc:
        c = create_empty_ocr_contract(status="failed", engine=TESSERACT_ENGINE)
        c["warnings"].append(f"Falha ao converter PDF→imagem para OCR local: {exc}")
        return finalize_ocr_multipage_metadata(c)

    c = create_empty_ocr_contract(
        status="success" if all_blocks else "failed",
        engine=TESSERACT_ENGINE,
    )
    c["text_blocks"] = all_blocks
    c["warnings"].append("PDF convertido página→imagem para OCR local (tesseract). Sem credenciais externas.")
    c["warnings"].append(f"Cache OCR: {cache_hits} hit(s), {cache_misses} miss(es).")
    c["warnings"].extend(warnings)
    if not all_blocks:
        c["warnings"].append("Tesseract executou nas páginas convertidas, mas não retornou blocos de texto.")

    return finalize_ocr_multipage_metadata(c, expected_pages=[p for p, _ in page_images])


def _run_tesseract_on_image(image_path: Path, page: int = 1) -> list[dict[str, Any]]:
    import pytesseract
    from PIL import Image

    img = Image.open(str(image_path))

    try:
        data = pytesseract.image_to_data(
            img,
            lang="por+eng",
            config=f"--psm {_DEFAULT_PSM}",
            output_type=pytesseract.Output.DICT,
        )
        return _tesseract_data_to_blocks(data, page=page)
    except Exception:
        raw = pytesseract.image_to_string(img, lang="por+eng", config=f"--psm {_DEFAULT_PSM}")
        return _raw_text_to_blocks(raw, page=page)


def _tesseract_data_to_blocks(data: dict, page: int) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    n = len(data.get("text", []))
    for i in range(n):
        text = (data["text"][i] or "").strip()
        conf = int(data.get("conf", [0] * n)[i] or 0)
        if not text or conf < 10:
            continue
        x = int(data.get("left", [0] * n)[i] or 0)
        y = int(data.get("top", [0] * n)[i] or 0)
        w = int(data.get("width", [0] * n)[i] or 0)
        h = int(data.get("height", [0] * n)[i] or 0)
        vertices = [
            {"x": x, "y": y},
            {"x": x + w, "y": y},
            {"x": x + w, "y": y + h},
            {"x": x, "y": y + h},
        ]
        is_chord = bool(_CHORD_RE.match(text))
        blocks.append({
            "text": text,
            "confidence": round(conf / 100, 3),
            "bbox": {"vertices": vertices},
            "page": page,
            "source": "ocr",
            "ocr_engine": "tesseract",
            "chord_candidate": is_chord,
            "classification": "possible_chord" if is_chord else "ocr_text",
        })
    return blocks


def _raw_text_to_blocks(raw: str, page: int) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for line in raw.splitlines():
        for token in line.split():
            token = token.strip()
            if not token:
                continue
            is_chord = bool(_CHORD_RE.match(token))
            blocks.append({
                "text": token,
                "confidence": 0.0,
                "bbox": None,
                "page": page,
                "source": "ocr",
                "ocr_engine": "tesseract_fallback",
                "chord_candidate": is_chord,
                "classification": "possible_chord" if is_chord else "ocr_text",
                "warning": "bbox indisponível — extração fallback sem coordenadas",
            })
    return blocks
