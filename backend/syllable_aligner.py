"""
syllable_aligner.py — Alinhamento sílaba→nota por grafo de posição
Build: audit-70-syllable-align

Implementa o mesmo princípio do ScoreCloud: cada sílaba é atribuída
à nota mais próxima horizontalmente dentro do compasso.

Pipeline:
  1. Para cada compasso com notas no MusicXML (beat conhecido)
  2. Converte beat → posição X relativa dentro do compasso (usando bbox do compasso)
  3. Para cada bloco OCR de letra associado ao compasso, calcula X do bloco
  4. Atribui cada sílaba OCR à nota mais próxima por X (grafo bipartido, matching guloso)
  5. Produz alinhamento {syllable, note_pitch, beat, x_note, x_ocr, distance}

Contrato CPP:
  - Nenhuma letra inventada
  - Nenhuma nota inferida
  - Alinhamento apenas quando bbox do compasso E bbox do bloco OCR existem
  - Ambiguidade (distância > threshold) → pendente
"""
from __future__ import annotations

from typing import Any

ALIGN_VERSION = "audit-70"
MAX_X_DISTANCE_PX = 120   # máximo de pixels de distância para considerar alinhamento
MIN_BEAT_COVERAGE = 0.5   # mínimo de notas com beat para tentar alinhamento


def align_syllables_to_notes(protocol: dict[str, Any]) -> dict[str, Any]:
    """
    Alinha sílabas OCR às notas do MusicXML usando posição X dentro do compasso.
    Adiciona 'note_alignment' a cada sílaba no merged_content.
    """
    report = {
        "version": ALIGN_VERSION,
        "measures_processed": 0,
        "syllables_aligned": 0,
        "syllables_pending": 0,
        "syllables_skipped_no_bbox": 0,
        "warnings": [],
    }

    fusion = protocol.get("fusion", {})
    ocr_blocks = (
        fusion.get("text_blocks_index")
        or protocol.get("ocr", {}).get("text_blocks")
        or []
    )

    # Índice rápido: measure_id → blocos OCR de letra associados
    lyric_blocks_by_measure: dict[str, list[dict]] = {}
    for block in ocr_blocks:
        assignment = block.get("assignment", {})
        mid = assignment.get("measure_id")
        status = assignment.get("status", "")
        cls = block.get("classification", "")
        if (
            mid
            and status in ("assigned_geometry_auto", "assigned_human_batch")
            and cls in ("possible_lyric", "lyric_syllable_fragment", "lyric_hyphen_or_continuation")
        ):
            lyric_blocks_by_measure.setdefault(mid, []).append(block)

    for measure in protocol.get("measures", []):
        mid = measure.get("measure_id")
        geometry = measure.get("geometry")
        markers = measure.get("markers", [])

        # Notas com beat e pitch do MusicXML
        note_markers = [
            m for m in markers
            if m.get("type") == "note_head" and m.get("beat")
        ]

        if not note_markers:
            continue

        # Verificar se temos bbox do compasso para calcular posição X
        if not geometry or not geometry.get("bbox"):
            report["syllables_skipped_no_bbox"] += len(lyric_blocks_by_measure.get(mid, []))
            continue

        m_bbox = geometry["bbox"]
        m_x = m_bbox["x"]
        m_w = m_bbox["w"]

        if m_w <= 0:
            continue

        # Converter beat → X relativo dentro do compasso
        # Beat é string como "1", "1.5", "2", "3.25"
        note_positions = []
        for nm in note_markers:
            beat_str = nm.get("beat", "1")
            beat_float = _parse_beat(beat_str)
            # Estimar posição X: distribuição uniforme dentro da bbox do compasso
            # (refinamento futuro: usar coordenadas reais do .omr)
            x_relative = _beat_to_x(beat_float, note_markers, m_x, m_w)
            note_positions.append({
                "pitch": nm.get("value", "?"),
                "beat": beat_float,
                "beat_str": beat_str,
                "x": x_relative,
                "marker": nm,
            })

        # Sílabas OCR neste compasso, ordenadas por X
        lyric_blocks = sorted(
            lyric_blocks_by_measure.get(mid, []),
            key=lambda b: _block_x(b),
        )

        if not lyric_blocks:
            report["measures_processed"] += 1
            continue

        # Matching guloso: cada sílaba → nota mais próxima por X
        # Cada nota pode receber no máximo uma sílaba
        used_notes: set[int] = set()
        alignments = []

        for block in lyric_blocks:
            syl_x = _block_x(block)
            if syl_x < 0:
                report["syllables_skipped_no_bbox"] += 1
                continue

            best_note = None
            best_dist = float("inf")

            for i, np in enumerate(note_positions):
                if i in used_notes:
                    continue
                dist = abs(syl_x - np["x"])
                if dist < best_dist:
                    best_dist = dist
                    best_note = (i, np)

            if best_note and best_dist <= MAX_X_DISTANCE_PX:
                idx, np = best_note
                used_notes.add(idx)
                alignment = {
                    "syllable": block.get("text", ""),
                    "fusion_id": block.get("fusion_id"),
                    "note_pitch": np["pitch"],
                    "beat": np["beat_str"],
                    "beat_float": np["beat"],
                    "x_note": np["x"],
                    "x_ocr": syl_x,
                    "distance_px": round(best_dist, 1),
                    "confidence": round(1.0 - (best_dist / MAX_X_DISTANCE_PX), 3),
                    "status": "aligned",
                }
                alignments.append(alignment)

                # Gravar no bloco OCR
                block["note_alignment"] = {
                    "note_pitch": np["pitch"],
                    "beat": np["beat_str"],
                    "distance_px": round(best_dist, 1),
                    "status": "aligned",
                    "version": ALIGN_VERSION,
                }
                report["syllables_aligned"] += 1
            else:
                # Nenhuma nota próxima — pendente
                block["note_alignment"] = {
                    "status": "pending_no_close_note",
                    "x_ocr": syl_x,
                    "version": ALIGN_VERSION,
                }
                report["syllables_pending"] += 1

        # Gravar alinhamentos no compasso
        if alignments:
            measure.setdefault("merged_content", {})["note_alignments"] = alignments
            # Reconstruir lyric com alinhamento por beat
            measure["merged_content"]["lyric_aligned"] = _build_aligned_lyric(alignments)

        report["measures_processed"] += 1

    # Atualizar blocos de volta no protocolo
    if fusion.get("text_blocks_index") is not None:
        protocol["fusion"]["text_blocks_index"] = ocr_blocks
    elif protocol.get("ocr", {}).get("text_blocks") is not None:
        protocol["ocr"]["text_blocks"] = ocr_blocks

    protocol["syllable_aligner"] = report
    return protocol


def generate_scorecloud_style_sheet(protocol: dict[str, Any]) -> str:
    """
    Gera cifra no estilo ScoreCloud: acorde acima da nota, sílaba abaixo.
    
    Formato por compasso:
        [Cm]     [Ab]     [Fm]     [G]
        in  -  da  -  qui  -  fa  - rei
    """
    title = protocol.get("music", {}).get("title", "Sem título")
    key = protocol.get("music", {}).get("key", "")
    meter = protocol.get("music", {}).get("meter_default", "")

    output_lines = [f"{title}", f"Tom: {key} | Compasso: {meter}", "=" * 60, ""]

    for measure in protocol.get("measures", []):
        merged = measure.get("merged_content", {})
        if not merged.get("has_playable_content") and not merged.get("note_alignments"):
            continue

        alignments = merged.get("note_alignments", [])
        chords = merged.get("chords", [])
        m_num = measure.get("number", "?")

        if alignments:
            # Linha de sílabas alinhadas por beat
            lyric_line = merged.get("lyric_aligned", merged.get("lyric", ""))
            chord_line = "  ".join(chords) if chords else ""
            output_lines.append(f"[compasso {m_num}]")
            if chord_line:
                output_lines.append(f"  {chord_line}")
            output_lines.append(f"  {lyric_line}")
        else:
            lyric = merged.get("lyric", "")
            chord_line = "  ".join(chords) if chords else ""
            if chord_line or lyric:
                output_lines.append(f"[compasso {m_num}]  {chord_line}")
                if lyric:
                    output_lines.append(f"  {lyric}")

        output_lines.append("")

    return "\n".join(output_lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_beat(beat_str: str) -> float:
    """Converte string de beat para float."""
    try:
        return float(beat_str)
    except (ValueError, TypeError):
        return 1.0


def _beat_to_x(beat: float, all_notes: list[dict], m_x: int, m_w: int) -> int:
    """
    Converte beat em posição X dentro do compasso.
    Usa distribuição proporcional baseada nos beats de todas as notas.
    """
    if not all_notes:
        return m_x

    beats = sorted(set(_parse_beat(n.get("beat", "1")) for n in all_notes))
    if len(beats) == 1:
        return m_x + m_w // 2

    min_beat = beats[0]
    max_beat = beats[-1]
    beat_range = max(max_beat - min_beat, 1.0)

    ratio = (beat - min_beat) / beat_range
    # Margem de 10% nas bordas
    effective_w = int(m_w * 0.8)
    offset = int(m_w * 0.1)
    return m_x + offset + int(ratio * effective_w)


def _block_x(block: dict) -> int:
    """Extrai posição X central de um bloco OCR."""
    verts = block.get("bbox", {}).get("vertices", [])
    if len(verts) >= 2:
        x0 = verts[0].get("x", -1)
        x1 = verts[1].get("x", x0)
        return (x0 + x1) // 2
    return -1


def _build_aligned_lyric(alignments: list[dict]) -> str:
    """Constrói string de letra ordenada por beat."""
    sorted_aligns = sorted(alignments, key=lambda a: a.get("beat_float", 0))
    syllables = []
    for a in sorted_aligns:
        syl = a.get("syllable", "").strip()
        if syl:
            syllables.append(syl)
    return " ".join(syllables)
