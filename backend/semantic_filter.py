"""
semantic_filter.py — Filtro semântico pós-associação geométrica
Build: audit-68-layout

Após a associação geométrica (OpenCV ou Audiveris layout), filtra os blocos OCR
usando contexto musical do compasso: tonalidade, métrica e presença de notas.

Regras (sem inferência musical):
  - Blocos classificados como music_symbol_noise → rejeitados
  - Blocos de pontuação isolada → rejeitados
  - Cifras OCR validadas contra regex de acorde real
  - Letra OCR aceita apenas quando silaba tem comprimento mínimo
  - Editorial/cabeçalho (nome do compositor, título) → rejeitados do corpo musical
  - Confiança Tesseract < 20% → marcado como baixa_confiança (não rejeitado automaticamente)

Contrato CPP:
  - Nenhuma harmonia inferida
  - Nenhuma letra inferida
  - Blocos rejeitados ficam no protocolo com status rejected_semantic_filter
  - Nenhuma modificação ao OCR bruto
"""
from __future__ import annotations

import re
from typing import Any

SEMANTIC_VERSION = "audit-68"

CHORD_RE = re.compile(
    r"^[A-G](?:#|b)?(?:m|maj|min|dim|aug|sus|add)?\d*(?:\([^)]*\))?(?:/[A-G](?:#|b)?)?$"
)

# Tokens editoriais típicos de partitura — nunca são letra musical
EDITORIAL_TOKENS = {
    "departamento", "louvor", "arr", "cordas", "vocal", "coro",
    "soprano", "alto", "tenor", "baixo", "sop", "alt", "ten",
    "versão", "version", "copyright", "todos", "direitos",
    "reservados", "reserved", "score", "piano", "órgão",
}

# Classificações que sempre são ruído
NOISE_CLASSIFICATIONS = {
    "music_symbol_noise",
    "punctuation",
    "editorial_text",
}

# Padrões de ruído típico de OCR em partitura (complementa o filtro do fusion_engine)
_REPEATED = re.compile(r"^(.).\1{1,}$")
_SYMBOL_ONLY = re.compile(r"""(?x)^[|/<>=+~@#%^&*_`\[\]{}.()\-]+$""")
_ALL_DIGITS = re.compile(r"^\d+$")


def apply_semantic_filter(protocol: dict[str, Any]) -> dict[str, Any]:
    """
    Aplica filtro semântico a todos os blocos OCR associados a compassos.
    Modifica o status de blocos ruidosos sem remover do protocolo.
    """
    fusion = protocol.get("fusion", {})
    ocr_blocks = (
        fusion.get("text_blocks_index")
        or protocol.get("ocr", {}).get("text_blocks")
        or []
    )

    report = {
        "version": SEMANTIC_VERSION,
        "total_blocks": len(ocr_blocks),
        "blocks_rejected_noise": 0,
        "blocks_rejected_editorial": 0,
        "blocks_rejected_low_confidence": 0,
        "blocks_kept": 0,
        "blocks_unassigned": 0,
    }

    for block in ocr_blocks:
        assignment = block.get("assignment", {})
        status = assignment.get("status", "")

        # Pular não-associados e já rejeitados manualmente
        if status in ("rejected_human_batch", "rejected_semantic_filter"):
            continue
        if status not in ("assigned_geometry_auto", "assigned_human_batch"):
            report["blocks_unassigned"] += 1
            continue

        text = (block.get("text") or block.get("normalized_text") or "").strip()
        classification = block.get("classification", "")
        confidence = float(block.get("confidence") or 0)

        # 1. Classificações de ruído direto
        if classification in NOISE_CLASSIFICATIONS:
            _reject(block, "music_symbol_noise", report)
            continue

        # 2. Ruído por padrão de texto
        if not text or _SYMBOL_ONLY.match(text) or _REPEATED.match(text):
            _reject(block, "text_pattern_noise", report)
            continue

        # 3. Números isolados (números de compasso, dinâmicas numéricas)
        if _ALL_DIGITS.match(text) and len(text) <= 3:
            _reject(block, "isolated_number", report)
            continue

        # 4. Tokens editoriais
        if text.lower().rstrip(".,;:") in EDITORIAL_TOKENS:
            _reject(block, "editorial_token", report, counter="blocks_rejected_editorial")
            continue

        # 5. Cifra: validar contra regex
        if classification == "possible_chord":
            if not CHORD_RE.match(text):
                # Não é acorde válido — reclassificar como unknown
                block["classification_override"] = "invalid_chord_pattern"
                block["semantic_filter"] = {
                    "version": SEMANTIC_VERSION,
                    "status": "reclassified",
                    "reason": "chord_regex_failed",
                }
                report["blocks_kept"] += 1
                continue

        # 6. Letra: mínimo 2 caracteres não-hífens
        if classification in ("possible_lyric", "lyric_syllable_fragment"):
            clean = text.strip("-–— ")
            if len(clean) < 2:
                _reject(block, "lyric_too_short", report)
                continue

        # 7. Baixa confiança Tesseract (< 20%) — sinalizar mas não rejeitar
        if 0 < confidence < 0.20:
            block.setdefault("semantic_filter", {})["low_confidence"] = True
            block["semantic_filter"]["confidence"] = confidence
            report["blocks_rejected_low_confidence"] += 1
            # Não rejeitar — deixar para revisão humana

        report["blocks_kept"] += 1

    protocol["semantic_filter"] = report
    return protocol


def _reject(block: dict, reason: str, report: dict, counter: str = "blocks_rejected_noise") -> None:
    """Marca bloco como rejeitado pelo filtro semântico."""
    block.setdefault("assignment", {})["status"] = "rejected_semantic_filter"
    block["semantic_filter"] = {
        "version": SEMANTIC_VERSION,
        "status": "rejected",
        "reason": reason,
    }
    report[counter] += 1
