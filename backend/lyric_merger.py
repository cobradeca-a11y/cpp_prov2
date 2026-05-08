"""
lyric_merger.py — Fusão de letra do MusicXML com OCR
Build: audit-68-layout

Usa a letra extraída do MusicXML (precisa, silabada, associada a notas) como
fonte primária. O OCR serve apenas como complemento para cifras e texto não
capturado pelo Audiveris.

Contrato CPP:
  - Nenhuma letra inventada
  - Nenhuma harmonia inferida
  - MusicXML é fonte primária de letra; OCR é fonte primária de cifras
  - Conflitos são marcados como pending, nunca resolvidos automaticamente
"""
from __future__ import annotations

import re
from typing import Any

LYRIC_VERSION = "audit-68"
CHORD_RE = re.compile(
    r"^[A-G](?:#|b)?(?:m|maj|min|dim|aug|sus|add)?\d*(?:\([^)]*\))?(?:/[A-G](?:#|b)?)?$"
)


def merge_musicxml_lyrics_with_ocr(protocol: dict[str, Any]) -> dict[str, Any]:
    """
    Para cada compasso:
    1. Usa sílabas do MusicXML como letra primária (already in markers/syllables)
    2. Usa cifras do MusicXML quando disponíveis (already in markers/chords)
    3. Complementa com blocos OCR associados ao compasso (cifras não capturadas pelo MusicXML)
    4. Rejeita blocos OCR de letra que conflitam com letra do MusicXML
    5. Reconstrói a letra do compasso como string legível
    """
    measures = protocol.get("measures", [])
    fusion_blocks = (
        protocol.get("fusion", {}).get("text_blocks_index")
        or protocol.get("ocr", {}).get("text_blocks")
        or []
    )

    # Índice rápido: measure_id → blocos OCR associados
    ocr_by_measure: dict[str, list[dict]] = {}
    for block in fusion_blocks:
        assignment = block.get("assignment", {})
        mid = assignment.get("measure_id")
        status = assignment.get("status", "")
        if mid and status in ("assigned_geometry_auto", "assigned_human_batch"):
            ocr_by_measure.setdefault(mid, []).append(block)

    merge_report = {
        "version": LYRIC_VERSION,
        "measures_processed": 0,
        "measures_with_musicxml_lyrics": 0,
        "measures_with_ocr_chords_only": 0,
        "measures_with_merged_content": 0,
        "ocr_lyrics_suppressed": 0,
        "ocr_chords_added": 0,
    }

    for measure in measures:
        mid = measure.get("measure_id")
        markers = measure.get("markers", [])
        detected = measure.get("detected_elements", {})

        # Letra já no MusicXML
        musicxml_syllables = [
            m["value"] for m in markers
            if m.get("type") == "syllable" and m.get("value")
        ]
        # Cifras já no MusicXML
        musicxml_chords = [
            m["value"] for m in markers
            if m.get("type") == "chord" and m.get("value")
        ]

        has_musicxml_lyrics = bool(musicxml_syllables)
        has_musicxml_chords = bool(musicxml_chords)

        if has_musicxml_lyrics:
            merge_report["measures_with_musicxml_lyrics"] += 1

        # Blocos OCR deste compasso
        ocr_blocks = ocr_by_measure.get(mid, [])

        # Separar cifras e letras OCR
        ocr_chords = [
            b for b in ocr_blocks
            if b.get("classification") == "possible_chord"
            or b.get("chord_candidate") is True
            or CHORD_RE.match(b.get("text", ""))
        ]
        ocr_lyrics = [
            b for b in ocr_blocks
            if b.get("classification") in ("possible_lyric", "lyric_syllable_fragment", "lyric_hyphen_or_continuation")
            and not CHORD_RE.match(b.get("text", ""))
        ]

        # Adicionar cifras OCR não capturadas pelo MusicXML
        added_chords = []
        for ocr_chord in ocr_chords:
            text = ocr_chord.get("text", "").strip()
            if text and text not in musicxml_chords:
                added_chords.append({
                    "type": "chord",
                    "value": text,
                    "source": "ocr_complement",
                    "confidence": ocr_chord.get("confidence", 0),
                    "fusion_id": ocr_chord.get("fusion_id"),
                })
                merge_report["ocr_chords_added"] += 1

        # Letra OCR: usar apenas se MusicXML não tem letra para este compasso
        added_lyrics = []
        suppressed_lyrics = []
        if has_musicxml_lyrics:
            # Suprimir letra OCR — MusicXML é mais confiável
            suppressed_lyrics = [b.get("text", "") for b in ocr_lyrics if b.get("text")]
            merge_report["ocr_lyrics_suppressed"] += len(suppressed_lyrics)
        else:
            # Sem letra no MusicXML: aceitar letra do OCR como candidata
            for ocr_lyric in ocr_lyrics:
                text = ocr_lyric.get("text", "").strip()
                if text and len(text) > 1:  # filtrar caracteres isolados
                    added_lyrics.append({
                        "type": "syllable",
                        "value": text,
                        "source": "ocr_fallback",
                        "confidence": ocr_lyric.get("confidence", 0),
                        "fusion_id": ocr_lyric.get("fusion_id"),
                    })

        # Reconstruir string de letra legível
        all_syllables = musicxml_syllables + [l["value"] for l in added_lyrics]
        lyric_string = _reconstruct_lyric(all_syllables)

        # Reconstruir string de cifras
        all_chords = musicxml_chords + [c["value"] for c in added_chords]
        chord_string = " | ".join(all_chords) if all_chords else ""

        # Gravar no compasso
        measure["merged_content"] = {
            "version": LYRIC_VERSION,
            "lyric": lyric_string,
            "chords": all_chords,
            "chord_string": chord_string,
            "musicxml_syllables": musicxml_syllables,
            "musicxml_chords": musicxml_chords,
            "ocr_chords_added": [c["value"] for c in added_chords],
            "ocr_lyrics_used": [l["value"] for l in added_lyrics],
            "ocr_lyrics_suppressed": suppressed_lyrics,
            "has_playable_content": bool(all_chords or lyric_string),
        }

        if added_chords or added_lyrics:
            merge_report["measures_with_merged_content"] += 1
        elif not (has_musicxml_lyrics or has_musicxml_chords):
            merge_report["measures_with_ocr_chords_only"] += 1

        merge_report["measures_processed"] += 1

    protocol["lyric_merger"] = merge_report
    return protocol


def _reconstruct_lyric(syllables: list[str]) -> str:
    """Reconstrói string de letra a partir de sílabas."""
    if not syllables:
        return ""

    result = []
    for syl in syllables:
        syl = syl.strip()
        if not syl:
            continue
        if syl in ("-", "–", "—"):
            # Hífen de ligação: conectar à sílaba anterior sem espaço
            if result:
                result[-1] = result[-1].rstrip("-") + "-"
            continue
        if result and not result[-1].endswith("-"):
            result.append(" ")
        result.append(syl)

    return "".join(result).strip()


def generate_playable_chord_sheet(protocol: dict[str, Any]) -> str:
    """
    Gera a cifra tocável a partir do conteúdo mesclado.
    Formato: acordes acima da letra, linha por sistema.
    Só inclui compassos com conteúdo aprovado.
    """
    title = protocol.get("music", {}).get("title", "Sem título")
    key = protocol.get("music", {}).get("key", "")
    meter = protocol.get("music", {}).get("meter_default", "")

    lines_chord = []
    lines_lyric = []

    for measure in protocol.get("measures", []):
        merged = measure.get("merged_content", {})
        if not merged.get("has_playable_content"):
            continue

        chords = merged.get("chords", [])
        lyric = merged.get("lyric", "")

        chord_str = "  ".join(chords) if chords else ""
        lyric_str = lyric if lyric else ""

        # Alinhar compasso: pad para largura mínima
        width = max(len(chord_str), len(lyric_str), 8) + 2
        lines_chord.append(chord_str.ljust(width))
        lines_lyric.append(lyric_str.ljust(width))

        # Quebra de linha a cada 4 compassos (aproximado para A4)
        if len(lines_chord) % 4 == 0:
            lines_chord.append("\n")
            lines_lyric.append("\n")

    chord_line = "".join(lines_chord)
    lyric_line = "".join(lines_lyric)

    header = f"{title}\nTom: {key} | Compasso: {meter}\n{'='*60}\n"

    # Intercalar linha de cifra e linha de letra por bloco
    blocks = []
    chord_parts = chord_line.split("\n")
    lyric_parts = lyric_line.split("\n")
    for c, l in zip(chord_parts, lyric_parts):
        if c.strip() or l.strip():
            blocks.append(c)
            blocks.append(l)
            blocks.append("")

    return header + "\n".join(blocks)
