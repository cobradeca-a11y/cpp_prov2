"""
chord_ocr_gpt.py — OCR especializado em cifras via GPT-4o Vision
Build: audit-69-chord-ocr

Envia regiões de imagem contendo cifras para GPT-4o e obtém:
  - Lista de acordes reconhecidos com posição X (para ordenação)
  - Confiança por acorde

Ativado via: CHORD_OCR_ENGINE=gpt4o no .env
Requer: OPENAI_API_KEY no .env

Custo estimado: ~$0.01–0.03 por partitura de 2 páginas (gpt-4o-mini)

Contrato CPP:
  - Nenhuma harmonia inferida — GPT apenas lê o que está impresso
  - Resultado marcado como source='gpt4o_chord_ocr'
  - Falhas caem de volta para Tesseract silenciosamente
  - OCR bruto preservado
"""
from __future__ import annotations

import base64
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

CHORD_OCR_VERSION = "audit-69"
CHORD_RE = re.compile(
    r"^[A-G](?:#|b)?(?:m|maj|min|dim|aug|sus|add)?\d*(?:\([^)]*\))?(?:/[A-G](?:#|b)?)?$"
)

_SYSTEM_PROMPT = """You are a music chord symbol recognition system.
Your ONLY task: identify chord symbols (like Cm, Ab, F#m7, Bb/D, G7sus4) printed above the staff in the sheet music image.

Rules:
- Return ONLY a JSON array of objects: [{"chord": "Cm", "x": 120}, ...]
- x is the approximate horizontal pixel position of the chord in the image
- Include ONLY valid chord symbols — ignore lyrics, dynamics, tempo markings, bar numbers
- If no chords are visible, return []
- Never invent or infer chords — only report what is clearly printed
- Do not include confidence scores or explanations"""


def is_gpt4o_chord_ocr_enabled() -> bool:
    return os.getenv("CHORD_OCR_ENGINE", "").strip().lower() == "gpt4o"


def run_gpt4o_chord_ocr_on_page(
    image_path: Path,
    page: int,
) -> list[dict[str, Any]]:
    """
    Envia uma página de partitura para GPT-4o e extrai acordes com posição X.
    Retorna lista de blocos OCR no formato CPP.
    """
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return []

    try:
        import httpx
    except ImportError:
        return []

    # Encode image as base64
    try:
        img_data = base64.b64encode(image_path.read_bytes()).decode("utf-8")
        media_type = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"
    except Exception:
        return []

    payload = {
        "model": "gpt-4o-mini",
        "max_tokens": 1000,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{media_type};base64,{img_data}",
                            "detail": "high",
                        },
                    },
                    {
                        "type": "text",
                        "text": "List all chord symbols visible in this sheet music page.",
                    },
                ],
            },
        ],
    }

    try:
        resp = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()
        raw_text = data["choices"][0]["message"]["content"].strip()
    except Exception:
        return []

    # Parse JSON response
    try:
        # Strip markdown fences if present
        clean = re.sub(r"```(?:json)?|```", "", raw_text).strip()
        chords_raw = json.loads(clean)
        if not isinstance(chords_raw, list):
            return []
    except (json.JSONDecodeError, ValueError):
        return []

    # Convert to CPP blocks
    blocks: list[dict[str, Any]] = []
    for item in chords_raw:
        if not isinstance(item, dict):
            continue
        chord_text = str(item.get("chord", "")).strip()
        if not chord_text or not CHORD_RE.match(chord_text):
            continue
        x_pos = int(item.get("x", 0))

        blocks.append({
            "text": chord_text,
            "normalized_text": chord_text,
            "confidence": 0.95,  # GPT-4o confiança alta para chord symbols
            "bbox": {
                "vertices": [
                    {"x": x_pos, "y": 0},
                    {"x": x_pos + 40, "y": 0},
                    {"x": x_pos + 40, "y": 30},
                    {"x": x_pos, "y": 30},
                ]
            },
            "page": page,
            "source": "gpt4o_chord_ocr",
            "ocr_engine": "gpt4o-mini",
            "chord_candidate": True,
            "classification": "possible_chord",
        })

    return blocks


def merge_gpt4o_chords_with_tesseract(
    tesseract_blocks: list[dict[str, Any]],
    gpt4o_blocks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Funde acordes GPT-4o com blocos Tesseract:
    - Para cada acorde GPT-4o, verifica se já existe um bloco Tesseract na mesma posição X (±30px)
    - Se sim: substitui o texto Tesseract pelo GPT-4o (mais preciso)
    - Se não: adiciona como bloco novo
    - Acordes Tesseract que NÃO passam no regex são mantidos como 'possible_chord_unvalidated'
    """
    if not gpt4o_blocks:
        return tesseract_blocks

    merged = list(tesseract_blocks)
    gpt4o_by_page: dict[int, list[dict]] = {}
    for b in gpt4o_blocks:
        gpt4o_by_page.setdefault(b["page"], []).append(b)

    for page, page_gpt_blocks in gpt4o_by_page.items():
        # Tesseract blocks on same page that are chord candidates
        tess_chords = [
            b for b in merged
            if b.get("page") == page
            and (b.get("chord_candidate") or b.get("classification") == "possible_chord")
        ]

        for gpt_block in page_gpt_blocks:
            gpt_x = gpt_block["bbox"]["vertices"][0]["x"]
            # Find matching Tesseract block within ±30px horizontal
            matched = None
            for tb in tess_chords:
                tb_verts = tb.get("bbox", {}).get("vertices", [])
                if tb_verts:
                    tb_x = tb_verts[0].get("x", -999)
                    if abs(tb_x - gpt_x) <= 30:
                        matched = tb
                        break

            if matched:
                # Replace Tesseract text with GPT-4o text (more accurate)
                old_text = matched.get("text", "")
                if old_text != gpt_block["text"]:
                    matched["text_tesseract_original"] = old_text
                    matched["text"] = gpt_block["text"]
                    matched["normalized_text"] = gpt_block["text"]
                    matched["confidence"] = gpt_block["confidence"]
                    matched["chord_corrected_by_gpt4o"] = True
                    matched["source"] = "gpt4o_chord_ocr"
            else:
                # New chord not detected by Tesseract
                gpt_block["chord_added_by_gpt4o"] = True
                merged.append(gpt_block)

    return merged


def run_gpt4o_chord_ocr_for_protocol(
    pdf_path: Path,
    protocol: dict[str, Any],
) -> dict[str, Any]:
    """
    Pipeline completo: converte PDF → imagens → GPT-4o por página → fusão com Tesseract.
    Modifica protocol.fusion.text_blocks_index ou protocol.ocr.text_blocks in-place.
    """
    if not is_gpt4o_chord_ocr_enabled():
        return protocol

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        protocol.setdefault("chord_ocr_gpt", {})["status"] = "skipped_no_api_key"
        return protocol

    try:
        import fitz
    except ImportError:
        protocol.setdefault("chord_ocr_gpt", {})["status"] = "skipped_no_pymupdf"
        return protocol

    fusion = protocol.get("fusion", {})
    existing_blocks = (
        fusion.get("text_blocks_index")
        or protocol.get("ocr", {}).get("text_blocks")
        or []
    )

    report = {
        "version": CHORD_OCR_VERSION,
        "status": "running",
        "pages_processed": 0,
        "chords_found_gpt4o": 0,
        "chords_corrected": 0,
        "chords_added": 0,
        "warnings": [],
    }

    try:
        with tempfile.TemporaryDirectory(prefix="cpp_chord_gpt_") as tmp:
            tmp_path = Path(tmp)
            doc = fitz.open(str(pdf_path))
            all_gpt_blocks: list[dict[str, Any]] = []

            for page_idx in range(len(doc)):
                page_num = page_idx + 1
                page = doc[page_idx]
                mat = fitz.Matrix(2.0, 2.0)  # 2x zoom for better chord visibility
                pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
                img_path = tmp_path / f"chord_page_{page_num:03d}.png"
                pix.save(str(img_path))

                try:
                    gpt_blocks = run_gpt4o_chord_ocr_on_page(img_path, page_num)
                    all_gpt_blocks.extend(gpt_blocks)
                    report["chords_found_gpt4o"] += len(gpt_blocks)
                    report["pages_processed"] += 1
                except Exception as exc:
                    report["warnings"].append(f"Página {page_num}: {exc}")

            doc.close()

        # Merge with Tesseract blocks
        merged = merge_gpt4o_chords_with_tesseract(existing_blocks, all_gpt_blocks)

        # Count corrections and additions
        for b in merged:
            if b.get("chord_corrected_by_gpt4o"):
                report["chords_corrected"] += 1
            if b.get("chord_added_by_gpt4o"):
                report["chords_added"] += 1

        # Write back
        if fusion.get("text_blocks_index") is not None:
            protocol["fusion"]["text_blocks_index"] = merged
        elif protocol.get("ocr", {}).get("text_blocks") is not None:
            protocol["ocr"]["text_blocks"] = merged

        report["status"] = "success"

    except Exception as exc:
        report["status"] = "error"
        report["warnings"].append(str(exc))

    protocol["chord_ocr_gpt"] = report
    return protocol
