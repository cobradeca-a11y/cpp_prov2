"""
omr_layout_parser.py — Parse do layout real do Audiveris via arquivo .omr
Build: audit-68-layout

O Audiveris gera um arquivo .omr (ZIP) com XML interno contendo coordenadas
precisas de cada compasso, sistema e pauta na página. Este módulo extrai essas
coordenadas e as injeta no protocolo CPP, substituindo a geometria aproximada
do OpenCV por coordenadas reais pixel a pixel.

Contrato CPP preservado:
  - Nenhuma harmonia inferida
  - Nenhuma letra inferida
  - Coordenadas extraídas diretamente do Audiveris, não calculadas
"""
from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

LAYOUT_VERSION = "audit-68"


def parse_omr_layout(omr_path: Path) -> dict[str, Any]:
    """Extrai geometria de compassos do arquivo .omr do Audiveris."""
    result: dict[str, Any] = {
        "version": LAYOUT_VERSION,
        "source": str(omr_path),
        "pages": {},
        "systems": [],
        "measure_bboxes": {},  # measure_number (int) → {page, bbox}
        "warnings": [],
    }

    if not omr_path or not omr_path.exists():
        result["warnings"].append(f"Arquivo .omr não encontrado: {omr_path}")
        return result

    try:
        with zipfile.ZipFile(str(omr_path), "r") as zf:
            xml_files = [f for f in zf.namelist() if f.endswith(".xml")]
            sheet_files = [f for f in xml_files if "sheet" in f.lower() or "book" in f.lower()]
            target_files = sheet_files or xml_files

            for xml_name in target_files:
                try:
                    with zf.open(xml_name) as fh:
                        tree = ET.parse(fh)
                        root = tree.getroot()
                        _extract_from_xml(root, result, xml_name)
                except Exception as exc:
                    result["warnings"].append(f"Erro ao parsear {xml_name}: {exc}")

    except zipfile.BadZipFile:
        result["warnings"].append("Arquivo .omr não é um ZIP válido.")
    except Exception as exc:
        result["warnings"].append(f"Erro ao abrir .omr: {exc}")

    return result


def _extract_from_xml(root: ET.Element, result: dict, source: str) -> None:
    """Extrai compassos e suas bboxes do XML interno do .omr."""
    ns = _detect_namespace(root)

    # Tentar Sheet → System → Measure (estrutura Audiveris 5.x)
    for page_idx, page_el in enumerate(_find_all(root, "Sheet", ns), start=1):
        page_num = int(page_el.get("number", str(page_idx)))
        page_width = int(page_el.get("width", 0))
        page_height = int(page_el.get("height", 0))
        result["pages"][page_num] = {"width": page_width, "height": page_height}

        system_idx = 0
        for system_el in _find_all(page_el, "SystemInfo", ns) or _find_all(page_el, "System", ns):
            system_idx += 1
            sys_bbox = _get_bounds(system_el)
            sys_id = f"s{page_num:02d}_{system_idx:02d}"

            system_entry = {
                "system_id": sys_id,
                "page": page_num,
                "bbox": sys_bbox,
                "measures": [],
            }

            for measure_el in _find_all(system_el, "MeasureStack", ns) or _find_all(system_el, "Measure", ns):
                m_num_raw = measure_el.get("number") or measure_el.get("id") or "0"
                try:
                    m_num = int(m_num_raw)
                except ValueError:
                    continue

                m_bbox = _get_bounds(measure_el)
                if not m_bbox:
                    # Tenta pegar de filhos
                    for staff_el in _find_all(measure_el, "Staff", ns):
                        s_bbox = _get_bounds(staff_el)
                        if s_bbox:
                            m_bbox = s_bbox
                            break

                if m_bbox:
                    measure_entry = {
                        "measure_number": m_num,
                        "page": page_num,
                        "system_id": sys_id,
                        "bbox": m_bbox,
                        "confidence": "audiveris_layout",
                    }
                    system_entry["measures"].append(measure_entry)
                    result["measure_bboxes"][m_num] = {
                        "page": page_num,
                        "system_id": sys_id,
                        "bbox": m_bbox,
                    }

            if system_entry["measures"]:
                result["systems"].append(system_entry)

    # Fallback: Audiveris 5.3+ usa estrutura plana com <Measure id="N" left="X" top="Y" ...>
    if not result["measure_bboxes"]:
        _extract_flat_measures(root, result, ns)


def _extract_flat_measures(root: ET.Element, result: dict, ns: str) -> None:
    """Fallback para estrutura plana de compassos."""
    page_num = 1
    for el in root.iter():
        tag = el.tag.replace(ns, "") if ns else el.tag
        if tag in ("Measure", "MeasureStack", "measure"):
            m_id = el.get("number") or el.get("id") or el.get("measure") or ""
            try:
                m_num = int(m_id)
            except (ValueError, TypeError):
                continue

            bbox = _get_bounds(el)
            if bbox and m_num not in result["measure_bboxes"]:
                result["measure_bboxes"][m_num] = {
                    "page": page_num,
                    "system_id": f"s{page_num:02d}_01",
                    "bbox": bbox,
                }


def _get_bounds(el: ET.Element) -> dict | None:
    """Extrai bbox de um elemento XML do Audiveris."""
    # Formato: left, top, width, height  ou  x, y, w, h  ou bounds atributo
    for combo in [
        ("left", "top", "width", "height"),
        ("x", "y", "w", "h"),
        ("left", "top", "right", "bottom"),
    ]:
        vals = [el.get(k) for k in combo]
        if all(v is not None for v in vals):
            try:
                nums = [int(v) for v in vals]
                x, y = nums[0], nums[1]
                if combo[2] == "right":
                    w, h = nums[2] - nums[0], nums[3] - nums[1]
                else:
                    w, h = nums[2], nums[3]
                if w > 0 and h > 0:
                    return {"x": x, "y": y, "w": w, "h": h}
            except (ValueError, TypeError):
                pass

    # Tenta <Bounds> filho
    for child in el:
        child_tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if child_tag == "Bounds":
            return _get_bounds(child)

    return None


def _detect_namespace(root: ET.Element) -> str:
    if root.tag.startswith("{"):
        return root.tag.split("}")[0] + "}"
    return ""


def _find_all(el: ET.Element, tag: str, ns: str) -> list:
    results = el.findall(f"{ns}{tag}")
    if not results:
        results = list(el.iter(f"{ns}{tag}"))
    return results


def inject_omr_layout_into_protocol(
    protocol: dict[str, Any],
    omr_layout: dict[str, Any],
) -> dict[str, Any]:
    """
    Injeta geometria real do Audiveris nos compassos do protocolo.
    Substitui a geometria do OpenCV quando disponível, preserva OpenCV como fallback.
    """
    measure_bboxes = omr_layout.get("measure_bboxes", {})
    if not measure_bboxes:
        protocol.setdefault("omr_layout", {})["status"] = "no_measure_bboxes"
        protocol["omr_layout"]["warnings"] = omr_layout.get("warnings", [])
        return protocol

    injected = 0
    for measure in protocol.get("measures", []):
        m_num = measure.get("number")
        if m_num is None:
            continue

        layout_entry = measure_bboxes.get(m_num)
        if not layout_entry:
            continue

        measure["geometry"] = {
            "status": "available",
            "source": "audiveris_layout",
            "version": LAYOUT_VERSION,
            "page": layout_entry["page"],
            "system_id": layout_entry["system_id"],
            "bbox": layout_entry["bbox"],
            "confidence": 0.97,  # Alta confiança — dados diretos do Audiveris
        }
        injected += 1

    protocol["omr_layout"] = {
        "status": "injected",
        "version": LAYOUT_VERSION,
        "measures_with_layout": injected,
        "total_layout_entries": len(measure_bboxes),
        "systems": omr_layout.get("systems", []),
        "warnings": omr_layout.get("warnings", []),
    }

    return protocol
