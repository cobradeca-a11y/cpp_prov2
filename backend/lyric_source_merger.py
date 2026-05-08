"""
lyric_source_merger.py — MusicXML como fonte primária de letra; OCR como fallback
Build: audit-68-omr-layout

O Audiveris, ao gerar MusicXML, inclui a letra já silabada e associada a notas
(elementos <lyric>/<text> dentro de <note>). O parser MusicXML do CPP já extrai
essas sílabas como markers do tipo 'syllable' em cada compasso.

Este módulo:
  1. Coleta as sílabas extraídas do MusicXML por compasso
  2. Marca blocos OCR de letra já cobertos pelo MusicXML como 'covered_by_musicxml'
  3. Só deixa como candidatos OCR os blocos de letra sem correspondência no MusicXML
  4. Gera a letra consolidada por compasso (musicxml preferido, ocr como fallback)

Contrato CPP preservado:
  - Nenhuma letra inventada
  - Toda sílaba marcada com sua fonte (musicxml ou ocr_fallback)
  - Sílabas do MusicXML têm confiança 'high'; OCR tem confiança 'medium'
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

MERGER_VERSION = "audit-68"

# Normalização para comparação de sílabas
def _norm(text: str) -> str:
    """Normaliza texto para comparação fuzzy: minúsculas, sem acentos, sem pontuação."""
    nfkd = unicodedata.normalize("NFKD", text.lower())
    ascii_text = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", ascii_text)


def merge_lyric_sources(protocol: dict[str, Any]) -> dict[str, Any]:
    """Funde fontes de letra (MusicXML + OCR) no protocolo.

    Injeta em cada compasso:
      - measure['lyric_merged']: lista de sílabas consolidadas com fonte
      - measure['lyric_coverage']: estatísticas de cobertura

    Injeta no protocolo:
      - protocol['lyric_merger']: relatório da fusão

    Modifica blocos OCR na fusion:
      - blocks classificados como possible_lyric/lyric_syllable_fragment que
        já estão cobertos pelo MusicXML recebem assignment.status='covered_by_musicxml'
    """
    report: dict[str, Any] = {
        "engine": "lyric_source_merger",
        "version": MERGER_VERSION,
        "measures_with_musicxml_lyrics": 0,
        "measures_with_ocr_only_lyrics": 0,
        "measures_with_no_lyrics": 0,
        "ocr_blocks_covered_by_musicxml": 0,
        "ocr_blocks_promoted_as_fallback": 0,
        "warnings": [],
    }

    measures = protocol.get("measures") or []
    fusion = protocol.get("fusion") or {}
    ocr_blocks = fusion.get("text_blocks_index") or []

    # Índice: measure_id → lista de sílabas MusicXML
    musicxml_syllables_by_measure = _collect_musicxml_syllables(measures)

    # Índice: measure_id → blocos OCR já associados (por geometry ou humano)
    ocr_blocks_by_measure = _collect_assigned_ocr_blocks(ocr_blocks)

    # Conjunto de sílabas MusicXML já vistas (para dedupe global)
    all_musicxml_norms: set[str] = set()
    for syllables in musicxml_syllables_by_measure.values():
        for s in syllables:
            all_musicxml_norms.add(_norm(s["value"]))

    # Processar cada compasso
    for measure in measures:
        m_id = measure.get("measure_id") or measure.get("id") or ""
        musicxml_syllables = musicxml_syllables_by_measure.get(m_id, [])
        assigned_ocr = ocr_blocks_by_measure.get(m_id, [])

        lyric_merged, coverage = _merge_for_measure(
            m_id, musicxml_syllables, assigned_ocr, all_musicxml_norms, report
        )
        measure["lyric_merged"] = lyric_merged
        measure["lyric_coverage"] = coverage

        if musicxml_syllables:
            report["measures_with_musicxml_lyrics"] += 1
        elif lyric_merged:
            report["measures_with_ocr_only_lyrics"] += 1
        else:
            report["measures_with_no_lyrics"] += 1

    # Marcar blocos OCR cobertos pelo MusicXML globalmente
    _mark_covered_ocr_blocks(ocr_blocks, all_musicxml_norms, report)

    # Persistir blocos atualizados
    if fusion.get("text_blocks_index") is not None:
        protocol["fusion"]["text_blocks_index"] = ocr_blocks

    protocol["lyric_merger"] = report
    return protocol


# ---------------------------------------------------------------------------
# Coleta de sílabas do MusicXML por compasso
# ---------------------------------------------------------------------------

def _collect_musicxml_syllables(
    measures: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Coleta sílabas dos markers MusicXML por measure_id."""
    result: dict[str, list[dict[str, Any]]] = {}
    for measure in measures:
        m_id = measure.get("measure_id") or measure.get("id") or ""
        syllables = measure.get("detected_elements", {}).get("syllables", [])
        if syllables:
            result[m_id] = [
                {
                    "value": s.get("value", ""),
                    "beat": s.get("beat", ""),
                    "source": "musicxml",
                    "confidence": "high",
                    "marker_id": s.get("marker_id", ""),
                }
                for s in syllables
                if s.get("value")
            ]
    return result


# ---------------------------------------------------------------------------
# Coleta de blocos OCR já associados por compasso
# ---------------------------------------------------------------------------

def _collect_assigned_ocr_blocks(
    ocr_blocks: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Agrupa blocos OCR pelo measure_id do assignment."""
    result: dict[str, list[dict[str, Any]]] = {}
    lyric_classes = {"possible_lyric", "lyric_syllable_fragment"}
    for block in ocr_blocks:
        classification = block.get("classification", "")
        if classification not in lyric_classes:
            continue
        assignment = block.get("assignment") or {}
        m_id = assignment.get("measure_id")
        status = assignment.get("status", "")
        if m_id and status not in ("unassigned_no_musicxml_layout", "covered_by_musicxml", "rejected_human_batch"):
            result.setdefault(m_id, []).append(block)
    return result


# ---------------------------------------------------------------------------
# Fusão por compasso
# ---------------------------------------------------------------------------

def _merge_for_measure(
    measure_id: str,
    musicxml_syllables: list[dict[str, Any]],
    assigned_ocr: list[dict[str, Any]],
    all_musicxml_norms: set[str],
    report: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Funde sílabas MusicXML com blocos OCR para um compasso.

    Retorna (lyric_merged, coverage_stats).
    """
    merged: list[dict[str, Any]] = []

    # 1. Sílabas do MusicXML como fonte primária
    for syl in musicxml_syllables:
        merged.append({
            "value": syl["value"],
            "beat": syl.get("beat", ""),
            "source": "musicxml",
            "confidence": "high",
            "marker_id": syl.get("marker_id", ""),
        })

    # 2. Blocos OCR como fallback — só adiciona o que NÃO está no MusicXML
    ocr_added = 0
    for block in assigned_ocr:
        text = block.get("normalized_text") or block.get("text", "")
        norm = _norm(text)
        if not norm:
            continue
        if norm in all_musicxml_norms:
            # Já coberto pelo MusicXML — não duplicar
            continue
        # Verificar se não é simplesmente um fragmento de uma sílaba MusicXML
        covered = any(
            norm in _norm(s["value"]) or _norm(s["value"]) in norm
            for s in musicxml_syllables
        )
        if covered:
            continue
        merged.append({
            "value": text,
            "beat": "",
            "source": "ocr_fallback",
            "confidence": "medium",
            "fusion_id": block.get("fusion_id", ""),
        })
        ocr_added += 1

    report["ocr_blocks_promoted_as_fallback"] += ocr_added

    coverage: dict[str, Any] = {
        "musicxml_syllables": len(musicxml_syllables),
        "ocr_fallback_added": ocr_added,
        "total": len(merged),
        "primary_source": "musicxml" if musicxml_syllables else ("ocr" if merged else "none"),
    }

    return merged, coverage


# ---------------------------------------------------------------------------
# Marcação global de blocos OCR cobertos pelo MusicXML
# ---------------------------------------------------------------------------

def _mark_covered_ocr_blocks(
    ocr_blocks: list[dict[str, Any]],
    all_musicxml_norms: set[str],
    report: dict[str, Any],
) -> None:
    """Marca blocos OCR de letra que já estão cobertos pelo MusicXML."""
    lyric_classes = {"possible_lyric", "lyric_syllable_fragment"}
    covered = 0
    for block in ocr_blocks:
        if block.get("classification") not in lyric_classes:
            continue
        assignment = block.get("assignment") or {}
        if assignment.get("status") in ("assigned_human_batch", "rejected_human_batch"):
            continue
        text = block.get("normalized_text") or block.get("text", "")
        norm = _norm(text)
        if not norm:
            continue
        if norm in all_musicxml_norms:
            block["assignment"] = {
                **assignment,
                "status": "covered_by_musicxml",
                "source": "lyric_merger_audit68",
                "note": "Sílaba já presente no MusicXML — não requer revisão humana.",
            }
            covered += 1
    report["ocr_blocks_covered_by_musicxml"] = covered


# ---------------------------------------------------------------------------
# Função de sincronização (para chamar no pipeline do main.py)
# ---------------------------------------------------------------------------

def sync_lyric_merger(protocol: dict[str, Any]) -> dict[str, Any]:
    """Wrapper para integrar no pipeline finalize_protocol."""
    return merge_lyric_sources(protocol)
